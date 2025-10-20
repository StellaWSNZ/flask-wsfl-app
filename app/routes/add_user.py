# app/add_user.py
from flask import Blueprint, current_app, render_template, request, redirect, session, url_for, flash
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Reuse your helper that sets the password via SP and sends the email
# (create_user_and_send(first_name, surname, email, moe_number))
from app.utils.wsfl_email import create_user_and_send

load_dotenv()

# --- DB engine for dropdown queries (schools) ---
DB_URL = os.getenv("DB_URL")
engine = create_engine(DB_URL, connect_args={"TrustServerCertificate": "yes"})

# No URL prefix
user_bp = Blueprint("add_user", __name__)

def _clean(s: str) -> str:
    return (s or "").strip()


@user_bp.route("/add-user", methods=["GET", "POST"])
def add_user():
    try:
        # -----------------------------
        # GET or POST logic
        # -----------------------------
        if request.method == "POST":
            first = _clean(request.form.get("first_name"))
            sur   = _clean(request.form.get("surname"))
            email = _clean(request.form.get("email")).lower()
            moe   = _clean(request.form.get("moe_number"))
            raw_admin = (request.form.get("is_admin") or "").strip().lower()

            # Basic validation
            if not (first and sur and email and moe and moe.isdigit()):
                flash("Please provide first name, surname, a valid email, and select a school.", "danger")
                return redirect(url_for("add_user.add_user"))

            moe_id = int(moe)
            role = session.get("user_role") or "UNKNOWN"
            is_user_admin = int(session.get("user_admin") or 0)

            # Who can grant admin rights
            can_grant_admin = (
                role in ("ADM", "MOE") or
                (role == "FUN" and is_user_admin == 1)
            )

            requested_admin = 1 if raw_admin in ("on", "1", "true", "yes") else 0
            is_admin = 1 if (can_grant_admin and requested_admin) else 0

            if requested_admin and not can_grant_admin:
                current_app.logger.warning(
                    "Denied admin grant attempt: user=%s role=%s target_moe=%s",
                    session.get("email"), role, moe_id
                )

            # Main action
            temp_pw = create_user_and_send(first, sur, email, moe_id, is_admin=is_admin)
            flag = "Admin" if is_admin else "Standard"
            flash(f"{flag} user created and welcome email sent to {email}.", "success")

            return redirect(url_for("add_user.add_user"))

        # -----------------------------
        # GET: populate dropdown
        # -----------------------------
        with engine.begin() as conn:
            schools = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownAll'")
            ).fetchall()

        return render_template("add_user.html", schools=schools)

    # -----------------------------
    # Universal error catcher
    # -----------------------------
    except Exception as e:
        err_msg = str(e)
        current_app.logger.exception("❌ add_user() route failed")

        # Try to log the alert to your audit table
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        EXEC AUD_Alerts_Insert
                             @Email        = :Email,
                             @RoleCode     = :RoleCode,
                             @EntityID     = NULL,
                             @Link         = :Link,
                             @ErrorMessage = :ErrorMessage
                    """),
                    {
                        "Email": session.get("email"),
                        "RoleCode": session.get("user_role"),
                        "Link": request.url,
                        "ErrorMessage": err_msg
                    }
                )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert: {log_err}")

        flash("An unexpected error occurred. The issue has been logged.", "danger")
        return redirect(url_for("add_user.add_user"))
    