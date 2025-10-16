# app/add_user.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
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

        # Basic validation
        if not (first and sur and email and moe.isdigit()):
            flash("Please provide first name, surname, a valid email, and select a school.", "danger")
            return redirect(url_for("add_user.add_user"))

        moe_id = int(moe)

        try:
            # This calls your stored proc to upsert the user and set HashPassword,
            # then sends the welcome email (HTML + plain), all in one go.
            temp_pw = create_user_and_send(first, sur, email, moe_id)
            flash(f"User created/updated and welcome email sent to {email}. Temp password: {temp_pw}", "success")
        except Exception as e:
            # If email fails but DB was updated, your helper already raised; we surface it here.
            flash(f"Action failed: {e}", "warning")

        return redirect(url_for("add_user.add_user"))

    # GET: populate school dropdown
    with engine.begin() as conn:
        schools = conn.execute(
            text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownAll'")
        ).fetchall()

    # Expect a template at templates/add_user.html
    return render_template("add_user.html", schools=schools)