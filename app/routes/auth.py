# app/routes/auth.py
import bcrypt
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, current_app
from app.utils.database import get_db_engine
from app.utils.custom_email import send_reset_email, generate_reset_token, verify_reset_token
from sqlalchemy import text
from itsdangerous import URLSafeTimedSerializer
from app.extensions import mail
auth_bp = Blueprint("auth_bp", __name__)
__all__ = ["auth_bp", "login_required"]

from functools import wraps
from flask import session, redirect, url_for, request
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        public_endpoints = {"auth_bp.login", "auth_bp.forgot_password", "auth_bp.reset_password", "static"}
        if not session.get("logged_in") and request.endpoint not in public_endpoints:
            return redirect(url_for("auth_bp.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'POST':
            email = request.form.get('username')
            password = request.form.get('password')

            engine = get_db_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :email"),
                    {"Request": "GetHashByEmail", "email": email}
                ).fetchone()

            if not result or 'HashPassword' not in result._mapping:
                flash("Email not found or invalid.", "danger")
                return render_template("login.html")

            stored_hash = result.HashPassword
            is_active = result.Active
            if not is_active:
                flash("Your account has been disabled. Please contact support.", "danger")
                return render_template("login.html")
            if not stored_hash:
                flash("Password not set for this account.", "danger")
                return render_template("login.html")

            if bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
                with engine.begin() as conn:
                    user_info = conn.execute(
                        text("EXEC FlaskLoginValidation :Email"),
                        {"Email": email}
                    ).fetchone()
                    print(user_info)
                session.update({
                    "logged_in": True,
                    "user_role": user_info.Role,
                    "user_id": user_info.ID,
                    "user_admin": user_info.Admin,
                    "user_email": email,
                    "user_email_alt": user_info.AlternateEmail,
                    "display_name": user_info.FirstName,
                    "user_firstname": user_info.FirstName,
                    "user_surname": user_info.Surname,
                    "last_login_nzt": str(user_info.LastLogin_NZT),
                    "desc": str(user_info.Desc),
                    "school_address": getattr(user_info, "StreetAddress", None),
                    "school_town": getattr(user_info, "TownCity", None),
                    "school_lat": getattr(user_info, "Latitude", None),
                    "school_lon": getattr(user_info, "Longitude", None),
                    "school_type": getattr(user_info, "SchoolTypeID", None),
                    "school_type_desc": getattr(user_info, "SchoolTypeDesc", None),
                    "funder_address": getattr(user_info, "Funder_Address", None),
                    "funder_lat": getattr(user_info, "Funder_Latitude", None),
                    "funder_lon": getattr(user_info, "Funder_Longitude", None),
                    "nearest_term": getattr(user_info,"CurrentTerm", None),
                    "nearest_year": getattr(user_info, "CurrentCalendarYear", None),
                    "provider_address": getattr(user_info, "Provider_Address", None),
                    "provider_lat": getattr(user_info, "Provider_Latitude", None),
                    "provider_lon": getattr(user_info, "Provider_Longitude", None)
                })
                if user_info.Role == "GRP":
                    with engine.connect() as conn:
                        result = conn.execute(
                            text("EXEC FlaskGetGroupEntities :Email"),
                            {"Email": email}
                        ).fetchall()

                    group_entities = {}
                    for row in result:
                        etype = row.EntityType  # 'PRO' or 'FUN'
                        group_entities.setdefault(etype, []).append({
                            "id": row.EntityID,
                            "name": row.Description
                        })

                    print("🗂 Populated group_entities =", group_entities)
                    session["group_entities"] = group_entities
                #else:
                #    session["provider_ids"] = [user_info.ID]
                return redirect(url_for("home_bp.home"))

            flash("Invalid password", "danger")
        return render_template("login.html")

    except Exception as e:
        print("LOGIN ERROR:", e)
        flash("Something went wrong. Please contact support.", "danger")
        return render_template("login.html")


@auth_bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for("auth_bp.login"))

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        try:
            email = request.form.get('email')
            engine = get_db_engine()

            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC FlaskAccountFunctions @Request = 'CheckEmailExists', @Email = :email"),
                    {"email": email}
                ).fetchone()

            email_count = result[0] if result else 0

            
            if email_count == 0:
                flash('No account found with that email address.', 'danger')
                return redirect(url_for('auth_bp.forgot_password'))

            # Generate token
            s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
            token = s.dumps(email, salt='password-reset-salt')

            # Send email
            send_reset_email(mail, email, token)

            flash('A password reset link has been sent to your email.', 'success')
            return redirect(url_for('auth_bp.login'))

        except Exception as e:
            print(f"❌ Forgot password error: {e}")
            flash("Something went wrong. Please try again.", "danger")
            return redirect(url_for('auth_bp.forgot_password'))

    return render_template('forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    email = verify_reset_token(token)
    if not email:
        flash('Invalid or expired token.', 'danger')
        return redirect(url_for('auth_bp.forgot_password'))

    if request.method == 'POST':
        new_password = request.form['password']
        hashed_pw = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request,
                        @Email = :email,
                        @HashPassword = :hash
                """),
                {
                    "request": "UpdatePassword",
                    "email": email,
                    "hash": hashed_pw
                }
            )
        flash('Password updated.')
        return redirect(url_for('auth_bp.login'))

    return render_template('reset_password.html', token=token)


