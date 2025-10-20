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
    if request.method == "POST":
        first = _clean(request.form.get("first_name"))
        sur   = _clean(request.form.get("surname"))
        email = _clean(request.form.get("email")).lower()
        moe   = _clean(request.form.get("moe_number"))
        raw_admin = (request.form.get("is_admin") or "").strip().lower()

        # Basic validation
        if not (first and sur and email and moe and moe.isdigit()):
            flash("Please provide first name, surname, a valid email, and select a school.", "danger")
            return redirect(url_for("user_bp.add_user"))

        moe_id = int(moe)

        # ---- Authorisation: who can grant admin? ----
        role = session.get("user_role")  # e.g., ADM, FUN, MOE, PRO, SCH
        is_user_admin = int(session.get("user_admin") or 0)

        can_grant_admin = (
            role in ("ADM", "MOE") or
            (role == "FUN" and is_user_admin == 1)
        )

        # Normalise checkbox values: 'on'/'1'/'true' => 1 else 0
        requested_admin = 1 if raw_admin in ("on", "1", "true", "yes") else 0
        is_admin = 1 if (can_grant_admin and requested_admin) else 0

        # (Optional) If non-ADM tries to set admin, log it
        if requested_admin and not can_grant_admin:
            current_app.logger.warning("Denied admin grant attempt: user=%s role=%s target_moe=%s",
                                       session.get("email"), role, moe_id)

        try:
            # Pass is_admin through to your helper
            temp_pw = create_user_and_send(first, sur, email, moe_id, is_admin=is_admin)
            flag = "Admin" if is_admin else "Standard"
            flash(f"{flag} user created/updated and welcome email sent to {email}. Temp password: {temp_pw}", "success")
        except Exception as e:
            flash(f"Action failed: {e}", "warning")

        return redirect(url_for("user_bp.add_user"))

    # GET: populate school dropdown
    with engine.begin() as conn:
        schools = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownAll'")).fetchall()
    return render_template("add_user.html", schools=schools)
