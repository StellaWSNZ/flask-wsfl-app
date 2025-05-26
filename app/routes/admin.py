# app/routes/admin.py

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash
from app.utils.database import get_db_engine
from app.routes.auth import login_required
import bcrypt
from sqlalchemy import text
from datetime import datetime
from app.utils.email import send_account_setup_email
from app.extensions import mail
admin_bp = Blueprint("admin_bp", __name__)
# --- In your Flask route file ---
@admin_bp.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if not session.get("user_admin"):
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))

    engine = get_db_engine()
    user_created = False
    user_role = session.get("user_role")
    user_id = session.get("user_id")

    if request.method == "POST":
        email = request.form.get("email")
        # password = request.form.get("password")
        firstname = request.form.get("firstname")
        surname = request.form.get("surname")
        send_email = request.form.get("send_email") == "on"
        admin = 1 if request.form.get("admin") == "1" else 0
        hashed_pw = None

        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT 1 FROM FlaskLogin WHERE Email = :email"),
                {"email": email}
            ).fetchone()

            if existing:
                flash("⚠️ Email already exists.", "warning")
            else:
                if user_role == "MOE":
                    role = "MOE"
                    conn.execute(
                        text("""
                            INSERT INTO FlaskLogin (Email, HashPassword, Role, ID, FirstName, Surname, Admin)
                            VALUES (:email, :hash, :role, :user_id, :firstname, :surname, :admin)
                        """),
                        {
                            "email": email,
                            "hash": hashed_pw,
                            "role": role,
                            "user_id": user_id,
                            "firstname": firstname,
                            "surname": surname,
                            "admin": admin
                        }
                    )
                    user_created = True
                    flash(f"✅ User {email} created.", "success")
                    if send_email:
                        invited_by_name = f"{session.get('user_firstname')} {session.get('user_surname')}"
                        inviter_desc = session.get("desc")

                        send_account_setup_email(
                            mail=mail,
                            recipient_email=email,
                            first_name=firstname,
                            role=role,
                            is_admin=admin,
                            invited_by_name=invited_by_name,
                            inviter_desc=inviter_desc
                        )

    return render_template("create_user.html", user_role=user_role, school_name = session.get("desc"))

from datetime import datetime
from flask import session, render_template
from sqlalchemy import text

@admin_bp.route("/profile")
@login_required
def profile():
    try:
        formatted_login = datetime.fromisoformat(session["last_login_nzt"]).strftime('%A, %d %B %Y, %I:%M %p')
    except (KeyError, ValueError, TypeError):
        formatted_login = "Unknown"

    user_info = {
        "email": session.get("user_email"),
        "role": session.get("user_role"),
        "admin": session.get("user_admin"),
        "firstname": session.get("user_firstname"),
        "surname": session.get("user_surname"),
        "desc": session.get("desc"),
        "last_login": formatted_login,
        "school_address": session.get("school_address"),
        "school_town": session.get("school_town"),
        "school_lat": session.get("school_lat"),
        "school_lon": session.get("school_lon"),
        "school_type": session.get("school_type"),
        "user_id": session.get("user_id"),
        "user_type_desc": session.get("school_type_desc"),
        "funder_lat": session.get("funder_lat"),
        "funder_lon": session.get("funder_lon"),
        "funder_address": session.get("funder_address")
    }

    # Load dropdown options
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("EXEC FlaskHelperFunctions 'SchoolTypeDropdown'"))
        school_type_options = [dict(row._mapping) for row in result]

    return render_template("profile.html", user=user_info, school_type_options=school_type_options)

from flask import request, redirect, url_for, flash

@admin_bp.route("/update_profile", methods=["POST"])
@login_required
def update_profile():
    original_email = request.form.get("original_email")
    new_firstname = request.form.get("firstname", "").strip()
    new_surname = request.form.get("surname", "").strip()
    new_email = request.form.get("email", "").strip()
    funder_address = request.form.get("funder_address", "").strip()
    funder_lat = request.form.get("funder_lat", "").strip()
    funder_lon = request.form.get("funder_lon", "").strip()
    school_address = request.form.get("school_address", "").strip()
    school_town = request.form.get("school_town", "").strip()
    school_type = request.form.get("school_type", "").strip()

    engine = get_db_engine()
    with engine.begin() as conn:
        # Update user info
        conn.execute(text("""
            UPDATE FlaskLogin
            SET FirstName = :fname, Surname = :sname, Email = :new_email
            WHERE Email = :original_email
        """), {
            "fname": new_firstname,
            "sname": new_surname,
            "new_email": new_email,
            "original_email": original_email
        })

        # Update school info if MOE admin
        if all([school_address, school_town, school_type]) and session.get("user_admin") == 1 and session.get("user_role") == "MOE":
            school_id = session.get("user_id")
            if school_id:
                conn.execute(text("""
                    UPDATE MOE_SchoolDirectory
                    SET StreetAddress = :addr, TownCity = :town, SchoolTypeID = :stype
                    WHERE MOENumber = :school_id
                """), {
                    "addr": school_address,
                    "town": school_town,
                    "stype": school_type,
                    "school_id": school_id
                })

        # Update funder info if FUN admin
        if all([funder_address, funder_lat, funder_lon]) and session.get("user_admin") == 1 and session.get("user_role") == "FUN":
            funder_id = session.get("user_id")
            if funder_id:
                conn.execute(text("""
                    UPDATE FunderDetails
                    SET Address = :address, Latitude = :lat, Longitude = :lon
                    WHERE FunderID = :funder_id
                """), {
                    "address": funder_address,
                    "lat": funder_lat,
                    "lon": funder_lon,
                    "funder_id": funder_id
                })

    # Refresh session
    with engine.connect() as conn:
        updated_info = conn.execute(
            text("EXEC FlaskLoginValidation :Email"),
            {"Email": new_email}
        ).fetchone()

        session["user_role"] = updated_info.Role
        session["user_id"] = updated_info.ID
        session["user_admin"] = updated_info.Admin
        session["user_email"] = updated_info.Email
        session["display_name"] = updated_info.FirstName
        session["user_firstname"] = updated_info.FirstName
        session["user_surname"] = updated_info.Surname
        session["last_login_nzt"] = str(updated_info.LastLogin_NZT)
        session["desc"] = str(updated_info.Desc)
        session["school_address"] = getattr(updated_info, "StreetAddress", None)
        session["school_town"] = getattr(updated_info, "TownCity", None)
        session["school_lat"] = getattr(updated_info, "Latitude", None)
        session["school_lon"] = getattr(updated_info, "Longitude", None)
        session["school_type"] = getattr(updated_info, "SchoolTypeID", None)
        session["funder_address"] = getattr(updated_info, "Funder_Address", None)
        session["funder_lat"] = getattr(updated_info, "Funder_Latitude", None)
        session["funder_lon"] = getattr(updated_info, "Funder_Longitude", None)

    flash("Profile updated successfully!", "success")
    return redirect(url_for("admin_bp.profile"))


@admin_bp.route('/school')
@login_required
def school():
    if session.get("user_role") == "FUN":
        return redirect(url_for("home"))
    return render_template("school.html")


@admin_bp.route('/logo/<logo_type>/<int:logo_id>')
def serve_logo(logo_type, logo_id):
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT ImageData, ContentType
            FROM Logo
            WHERE Type = :type AND ID = :id
        """), {"type": logo_type.upper(), "id": logo_id}).first()

    if result:
        return Response(result[0], mimetype=result[1])
    return '', 404
