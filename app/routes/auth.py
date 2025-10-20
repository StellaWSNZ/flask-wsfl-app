# app/routes/auth.py
import bcrypt
from flask import Blueprint, abort, render_template, request, session, redirect, url_for, flash, current_app
from app.utils.database import get_db_engine, log_alert
from app.utils.custom_email import send_reset_email, generate_reset_token, verify_reset_token
from sqlalchemy import text
from itsdangerous import URLSafeTimedSerializer
from app.extensions import mail
auth_bp = Blueprint("auth_bp", __name__)
__all__ = ["auth_bp", "login_required"]

from functools import wraps
from flask import session, redirect, url_for, request
from urllib.parse import urlparse, urljoin

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        public_endpoints = {"auth_bp.login", "auth_bp.forgot_password", "auth_bp.reset_password", "static"}
        if not session.get("logged_in") and request.endpoint not in public_endpoints:
            return redirect(url_for("auth_bp.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc
from flask import request, render_template, redirect, url_for, flash, current_app, session
from sqlalchemy import text
import bcrypt

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get("next") or request.form.get("next")

    # Small helper to log to DB safely
    def _log_auth_alert(message, email_hint=""):
        try:
            log_alert(
                email=(email_hint or session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=message[:1800],
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (login): {log_err}")

    try:
        if request.method == 'POST':
            email = (request.form.get('username') or "").strip()
            password = request.form.get('password') or ""

            # Basic validation
            if not email or not password:
                flash("Please enter your email and password.", "warning")
                return render_template("login.html", next=next_url)

            engine = get_db_engine()

            # 1) Fetch stored hash + active flag
            try:
                with engine.begin() as conn:
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :email"),
                        {"Request": "GetHashByEmail", "email": email}
                    ).fetchone()
            except Exception as e:
                current_app.logger.exception("❌ GetHashByEmail failed")
                _log_auth_alert(f"GetHashByEmail DB error for {email}: {str(e)}", email_hint=email)
                flash("Something went wrong. Please contact support.", "danger")
                return render_template("login.html", next=next_url)

            if not result or 'HashPassword' not in result._mapping:
                # Log but don't reveal too much
                _log_auth_alert(f"Login attempt for unknown email: {email}", email_hint=email)
                flash("Email not found or invalid.", "danger")
                return render_template("login.html", next=next_url)

            stored_hash = result.HashPassword
            is_active   = result.Active

            if not is_active:
                _log_auth_alert(f"Login attempt for disabled account: {email}", email_hint=email)
                flash("Your account has been disabled. Please contact support.", "danger")
                return render_template("login.html", next=next_url)

            if not stored_hash:
                _log_auth_alert(f"Login attempt where password not set: {email}", email_hint=email)
                flash("Password not set for this account.", "danger")
                return render_template("login.html", next=next_url)

            # 2) Verify password
            try:
                ok = bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
            except Exception as e:
                current_app.logger.exception("❌ bcrypt.checkpw failed")
                _log_auth_alert(f"bcrypt error for {email}: {str(e)}", email_hint=email)
                flash("Something went wrong. Please contact support.", "danger")
                return render_template("login.html", next=next_url)

            if not ok:
                _log_auth_alert(f"Invalid password for {email}", email_hint=email)
                flash("Invalid password", "danger")
                return render_template("login.html", next=next_url)

            # 3) Load full user profile/session info
            try:
                with engine.begin() as conn:
                    user_info = conn.execute(
                        text("EXEC FlaskLoginValidation :Email"),
                        {"Email": email}
                    ).fetchone()
                if not user_info:
                    raise RuntimeError("FlaskLoginValidation returned no row.")
            except Exception as e:
                current_app.logger.exception("❌ FlaskLoginValidation failed")
                _log_auth_alert(f"FlaskLoginValidation error for {email}: {str(e)}", email_hint=email)
                flash("Something went wrong. Please contact support.", "danger")
                return render_template("login.html", next=next_url)

            # 4) Build session
            session.update({
                "logged_in": True,
                "user_role": user_info.Role,
                "user_id": user_info.ID,
                "user_admin": user_info.Admin,
                "user_email": email,
                "user_email_alt": getattr(user_info, "AlternateEmail", None),
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
                "nearest_term": getattr(user_info, "CurrentTerm", None),
                "nearest_year": getattr(user_info, "CurrentCalendarYear", None),
                "provider_address": getattr(user_info, "Provider_Address", None),
                "provider_lat": getattr(user_info, "Provider_Latitude", None),
                "provider_lon": getattr(user_info, "Provider_Longitude", None),
            })

            # GRP extra entities
            if user_info.Role == "GRP":
                try:
                    with engine.begin() as conn:
                        rows = conn.execute(
                            text("EXEC FlaskGetGroupEntities :Email"),
                            {"Email": email}
                        ).fetchall()
                    group_entities = {}
                    for row in rows or []:
                        etype = row.EntityType
                        group_entities.setdefault(etype, []).append({
                            "id": row.EntityID,
                            "name": row.Description
                        })
                    session["group_entities"] = group_entities
                except Exception as e:
                    current_app.logger.exception("⚠️ FlaskGetGroupEntities failed (non-fatal)")
                    _log_auth_alert(f"FlaskGetGroupEntities error for {email}: {str(e)}", email_hint=email)
                    # Continue; not fatal to login

            # 5) Redirect to next or home
            if next_url and is_safe_url(next_url):
                return redirect(next_url)
            return redirect(url_for("home_bp.home"))

        # GET
        return render_template("login.html", next=next_url)

    except Exception as e:
        # Catch-all — render login with preserved 'next' and log to DB
        current_app.logger.exception("❌ login route crashed")
        _log_auth_alert(f"login route crash: {str(e)}")
        flash("Something went wrong. Please contact support.", "danger")
        return render_template("login.html", next=next_url)

from flask import (
    request, render_template, redirect, url_for, flash,
    current_app, session, abort
)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text

@auth_bp.route('/logout', methods=['GET', 'POST'])
def logout():
    try:
        session.clear()
    except Exception as e:
        # Unlikely, but log it to DB as requested
        current_app.logger.exception("❌ logout failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"logout error: {str(e)[:1600]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (logout): {log_err}")
        # Don’t block logout if logging fails
    return redirect(url_for("auth_bp.login"))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()

        # Basic validation
        if not email:
            flash('Please enter your email address.', 'warning')
            return redirect(url_for('auth_bp.forgot_password'))

        try:
            engine = get_db_engine()
            # Check if email exists
            with engine.begin() as conn:
                result = conn.execute(
                    text("EXEC FlaskAccountFunctions @Request = 'CheckEmailExists', @Email = :email"),
                    {"email": email}
                ).fetchone()

            email_count = (result[0] if result else 0)

            # ——— If you prefer privacy-friendly behavior, use the commented block instead ———
            # if True:
            #     # Always behave as if it succeeded (don’t reveal account existence)
            #     pass
            # else:
            if email_count == 0:
                flash('No account found with that email address.', 'danger')
                return redirect(url_for('auth_bp.forgot_password'))

            # Generate token (expires when you verify it; default max_age checked at use-time)
            try:
                s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
                token = s.dumps(email, salt='password-reset-salt')
            except Exception as tok_err:
                current_app.logger.exception("❌ Token generation failed")
                try:
                    log_alert(
                        email=email[:320],
                        role="",  # unauthenticated
                        entity_id=None,
                        link=str(request.url)[:2048],
                        message=f"forgot_password token error: {str(tok_err)[:1600]}",
                    )
                except Exception as log_err:
                    current_app.logger.error(f"⚠️ Failed to log alert (token gen): {log_err}")
                flash("We couldn’t start the reset process. Please try again.", "danger")
                return redirect(url_for('auth_bp.forgot_password'))

            # Send email
            try:
                send_reset_email(mail, email, token)
            except Exception as mail_err:
                current_app.logger.exception("❌ Password reset email send failed")
                try:
                    log_alert(
                        email=email[:320],
                        role="",
                        entity_id=None,
                        link=str(request.url)[:2048],
                        message=f"forgot_password email send error: {str(mail_err)[:1600]}",
                    )
                except Exception as log_err:
                    current_app.logger.error(f"⚠️ Failed to log alert (email send): {log_err}")
                flash("We couldn’t send the reset email. Please try again later.", "danger")
                return redirect(url_for('auth_bp.forgot_password'))

            flash('A password reset link has been sent to your email.', 'success')
            return redirect(url_for('auth_bp.login'))

        except Exception as e:
            current_app.logger.exception("❌ forgot_password POST failed")
            # Best-effort DB alert; user may be anonymous here
            try:
                log_alert(
                    email=email[:320],
                    role="",
                    entity_id=None,
                    link=str(request.url)[:2048],
                    message=f"forgot_password error: {str(e)[:1800]}",
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log alert (forgot_password): {log_err}")
            flash("Something went wrong. Please try again.", "danger")
            return redirect(url_for('auth_bp.forgot_password'))

    # GET — guard template render
    try:
        return render_template('forgot_password.html')
    except Exception as e:
        current_app.logger.exception("❌ forgot_password template render failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"forgot_password template error: {str(e)[:1500]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (forgot_password render): {log_err}")
        return abort(500)



@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # --- verify token safely ---
    try:
        email = verify_reset_token(token)
    except Exception as e:
        current_app.logger.exception("❌ verify_reset_token raised")
        # Best-effort DB alert (user is anonymous here; use token/email if available)
        try:
            log_alert(
                email=((locals().get("email") or "")[:320]),   # may be empty
                role="",                                       # no role when unauthenticated
                entity_id=None,
                link=str(request.url)[:2048],
                message=f"verify_reset_token error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (verify_reset_token): {log_err}")
        flash('Invalid or expired token.', 'danger')
        return redirect(url_for('auth_bp.forgot_password'))

    if not email:
        flash('Invalid or expired token.', 'danger')
        return redirect(url_for('auth_bp.forgot_password'))

    # --- POST: change password ---
    if request.method == 'POST':
        try:
            new_password   = (request.form.get('password') or '').strip()
            confirm_pw     = (request.form.get('confirm_password') or '').strip()  # optional field
            min_len        = 8  # tweak to your policy

            # Basic validation (adjust to your policy)
            if not new_password:
                flash('Password cannot be empty.', 'danger')
                return redirect(url_for('auth_bp.reset_password', token=token))
            if len(new_password) < min_len:
                flash(f'Password must be at least {min_len} characters.', 'danger')
                return redirect(url_for('auth_bp.reset_password', token=token))
            if confirm_pw and new_password != confirm_pw:
                flash('Passwords do not match.', 'danger')
                return redirect(url_for('auth_bp.reset_password', token=token))

            # Hash
            hashed_pw = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

            # DB update
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific 
                             @Request      = :request,
                             @Email        = :email,
                             @HashPassword = :hash
                    """),
                    {
                        "request": "UpdatePassword",
                        "email": email,
                        "hash": hashed_pw
                    }
                )

            flash('Password updated.', 'success')
            return redirect(url_for('auth_bp.login'))

        except Exception as e:
            current_app.logger.exception("❌ reset_password POST failed")
            # Log to DB; user might still be anonymous
            try:
                log_alert(
                    email=(email or "")[:320],
                    role="",  # unauthenticated
                    entity_id=None,
                    link=str(request.url)[:2048],
                    message=f"reset_password DB error for {email}: {str(e)[:1800]}",
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log alert (reset_password POST): {log_err}")
            flash('We could not update your password. Please try again.', 'danger')
            return redirect(url_for('auth_bp.forgot_password'))

    # --- GET: render form (guarded) ---
    try:
        return render_template('reset_password.html', token=token, email=email)
    except Exception as e:
        current_app.logger.exception("❌ reset_password template render failed")
        try:
            log_alert(
                email=(email or "")[:320],
                role="",
                entity_id=None,
                link=str(request.url)[:2048],
                message=f"reset_password template error: {str(e)[:1600]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (reset_password render): {log_err}")
        return abort(500)

