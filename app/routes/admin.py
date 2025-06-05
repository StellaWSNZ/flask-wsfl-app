# app/routes/admin.py

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response, jsonify
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
from app.utils.email import send_account_setup_email  # if not already
from app.utils.database import get_db_engine
from sqlalchemy import text
import traceback
@admin_bp.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if not session.get("user_admin"):
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))

    engine = get_db_engine()
    user_role = session.get("user_role")
    user_id = session.get("user_id")
    user_desc = session.get("desc")

    providers = []
    schools = []
    funder = None
    only_own_staff_or_empty = False

    with engine.connect() as conn:
        if user_role == "FUN":
            providers = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :fid"),
                {"Request": "ProvidersByFunderID", "fid": user_id}
            ).fetchall()

            schools = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :fid"),
                {"Request": "SchoolsByFunderID", "fid": user_id}
            ).fetchall()

            funder = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :fid"),
                {"Request": "FunderByID", "fid": user_id}
            ).fetchone()



            only_own_staff_or_empty = (
                len(providers) == 0 or
                (len(providers) == 1 and providers[0].Description.strip().lower() == "own staff")
            )
        elif user_role=="ADM":
            providers = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request"),
                {"Request": "AllProviders"}
            ).fetchall()

            schools = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request"),
                {"Request": "AllSchools"}
            ).fetchall()

            funder = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request"),
                {"Request": "AllFunders"}
            ).fetchall()
        elif user_role == "MOE":
        # Just one school, their own
            schools = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :moe"),
                {"Request": "SchoolByMOENumber", "moe": user_id}
            ).fetchall()
        elif user_role=="PRO":
            schools = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :pid"),
                {"Request": "SchoolsByProvider", "pid": user_id}
            ).fetchall()

            providers = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :pid"),
                {"Request": "ProviderByID", "pid": user_id}
            ).fetchone()


    if request.method == "POST":
        email = request.form.get("email")
        firstname = request.form.get("firstname")
        surname = request.form.get("surname")
        send_email = request.form.get("send_email") == "on"
        admin = 1 if request.form.get("admin") == "1" else 0
        hashed_pw = None  # User will set their password later
        selected_role = request.form.get("selected_role")
        selected_id = request.form.get("selected_id")

        with engine.begin() as conn:
            existing = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                {"Request": "CheckEmailExists", "Text": email}
            ).fetchone()


            if existing:
                flash("⚠️ Email already exists.", "warning")
            else:
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            EXEC FlaskInsertUser 
                                @Email = :email,
                                @HashPassword = :hash,
                                @Role = :role,
                                @ID = :id,
                                @FirstName = :firstname,
                                @Surname = :surname,
                                @Admin = :admin
                        """),
                        {
                            "email": email,
                            "hash": hashed_pw,
                            "role": selected_role,
                            "id": selected_id,
                            "firstname": firstname,
                            "surname": surname,
                            "admin": admin
                        }
                    )
                flash(f"✅ User {email} created.", "success")

                if send_email:
                    send_account_setup_email(
                        mail=mail,
                        recipient_email=email,
                        first_name=firstname,
                        role=selected_role,
                        is_admin=admin,
                        invited_by_name=f"{session.get('user_firstname')} {session.get('user_surname')}",
                        inviter_desc=user_desc
                    )
    
   # print(user_role)
   # print(providers)
    return render_template("create_user.html",
        user_role=user_role,
        name=session.get("desc"),
        funder=funder,
        provider=providers,
        schools=schools,
        only_own_staff_or_empty=only_own_staff_or_empty
    )

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
        conn.execute(text("""
            EXEC FlaskHelperFunctionsSpecific 
                @Request = :request,
                @FirstName = :fname,
                @Surname = :sname,
                @NewEmail = :new_email,
                @OriginalEmail = :original_email
        """), {
            "request": "UpdateUserInfo",
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
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request,
                        @MOENumber = :school_id,
                        @StreetAddress = :addr,
                        @TownCity = :town,
                        @SchoolTypeID = :stype
                """), {
                    "request": "UpdateSchoolInfo",
                    "school_id": school_id,
                    "addr": school_address,
                    "town": school_town,
                    "stype": school_type
                })

        # Update funder info if FUN admin
        if all([funder_address, funder_lat, funder_lon]) and session.get("user_admin") == 1 and session.get("user_role") == "FUN":
            funder_id = session.get("user_id")
            if funder_id:
                conn.execute(text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request,
                        @FunderID = :funder_id,
                        @FunderAddress = :address,
                        @FunderLatitude = :lat,
                        @FunderLongitude = :lon
                """), {
                    "request": "UpdateFunderInfo",
                    "funder_id": funder_id,
                    "address": funder_address,
                    "lat": funder_lat,
                    "lon": funder_lon
                })

    # Refresh session
    with engine.connect() as conn:
        updated_info = conn.execute(
            text("EXEC FlaskLoginValidation :Email"),
            {"Email": new_email}
        ).fetchone()
       # print(updated_info)
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
        session["nearest_term"] = getattr(updated_info,"CurrentTerm", None)
        session["nearest_year"] = getattr(updated_info, "CurrentCalendarYear", None)

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
        result = conn.execute(
            text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text, @Number = :Number"),
            {"Request": "GetLogo", "Text": logo_type.upper(), "Number": logo_id}
        ).fetchone()

    if result:
        return Response(result[0], mimetype=result[1])
    return '', 404

@admin_bp.route('/provider_maintenance', methods=['GET', 'POST'])
@login_required
def provider_maintenance():
    engine = get_db_engine()
    user_role = session.get("user_role")
    user_id = session.get("user_id")

    selected_funder = request.form.get("funder")
    selected_term = request.form.get("term") or session.get("nearest_term")
    selected_year = request.form.get("year") or session.get("nearest_year")

    funders, schools, providers = [], [], []

    with engine.connect() as conn:
        if user_role == "ADM":
            funders_result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request"),
                {"Request": "AllFunders"}
            )
            funders = [dict(row._mapping) for row in funders_result]
            

        if user_role == "FUN":
            selected_funder = str(user_id)

        if selected_funder:
            schools_result = conn.execute(text("""
                EXEC FlaskHelperFunctionsSpecific
                    @Request = 'GetSchoolsForProviderAssignment',
                    @Term = :term,
                    @Year = :year,
                    @FunderID = :funder_id
            """), {
                "term": selected_term,
                "year": selected_year,
                "funder_id": selected_funder
            })
            schools = [dict(row._mapping) for row in schools_result]

            providers_result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :funder_id"),
                {"Request": "GetProviderByFunder", "funder_id": selected_funder}
            )
            providers = [dict(row._mapping) for row in providers_result]
    try:
        return render_template(
            "provider_maintenance.html",
            schools=schools,
            providers=providers,
            funders=funders,
        selected_funder=int(selected_funder) if selected_funder else None,
            selected_term=int(selected_term),
            selected_year=int(selected_year),
            user_role=user_role
        )
    except Exception as e:
        traceback.print_exc()
        return str(e), 500
    
    
@admin_bp.route("/assign_provider", methods=["POST"])
def assign_provider():
    data = request.get_json()
    moe_number = data.get("moe_number")
    term = data.get("term")
    year = data.get("year")
    provider_id = data.get("provider_id")

    try:
        if provider_id == "" or provider_id is None:
            provider_id = None

        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskHelperFunctionsSpecific
                    @Request = 'AssignProviderToSchool',
                    @MOENumber = :moe,
                    @Year = :year,
                    @Term  = :term,
                    @ProviderID = :pid
            """), {
                "moe": moe_number,
                "term": term,
                "year": year,
                "pid": provider_id
            })

        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@admin_bp.route("/add_provider", methods=["POST"])
@login_required
def add_provider():
    provider_name = request.form.get("provider_name")
    funder_id = request.form.get("funder_id")

    if not provider_name or not funder_id:
        return jsonify({"success": False, "message": "Missing data"}), 400
    #print(provider_name)
    # print(funder_id)
    engine = get_db_engine()
    with engine.begin() as conn:
        try:
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctions 
                        @Request = :Request,
                        @Number = :Number,
                        @Text = :Text
                """),
                {
                    "Request": "AddProvider",
                    "Number": int(funder_id),
                    "Text": provider_name
                }
            )
            row = result.mappings().fetchone()
            new_id = row["NewID"]
            funder_name = row["FunderName"]

            return jsonify({
                "success": True,
                "new_id": new_id,
                "funder_name": funder_name,
                "provider_name": provider_name
            })        
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500
