# app/routes/auth.py
import bcrypt
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, current_app
from app.utils.database import get_db_engine
from app.utils.email import send_reset_email, generate_reset_token, verify_reset_token
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
    if request.method == 'POST':
        email = request.form.get('username')
        password = request.form.get('password').encode('utf-8')

        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT HashPassword FROM FlaskLogin WHERE Email = :email"),
                {"email": email}
            ).fetchone()

        if result and bcrypt.checkpw(password, result.HashPassword.encode('utf-8')):
            with engine.connect() as conn:
                user_info = conn.execute(
                    text("EXEC FlaskLoginValidation :Email"),
                    {"Email": email}
                ).fetchone()

            # Store session data
            session.update({
                "logged_in": True,
                "user_role": user_info.Role,
                "user_id": user_info.ID,
                "user_admin": user_info.Admin,
                "user_email": email,
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
                "funder_lon": getattr(user_info, "Funder_Longitude", None)
            })

            return redirect(url_for("home_bp.home"))


        flash("Invalid credentials", "danger")
        return render_template("login.html")
    return render_template("login.html")
@auth_bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for("auth_bp.login"))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')

        # Example token generation
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        token = s.dumps(email, salt='password-reset-salt')

        send_reset_email(mail, email, token)

        flash('A password reset link has been sent to your email.', 'success')
        return redirect(url_for('auth_bp.login'))

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
                text("UPDATE FlaskLogin SET HashPassword = :hash WHERE Email = :email"),
                {"hash": hashed_pw, "email": email}
            )
        flash('Password updated.')
        return redirect(url_for('auth_bp.login'))

    return render_template('reset_password.html', token=token)


