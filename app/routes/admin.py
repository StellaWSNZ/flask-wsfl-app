# app/routes/admin.py

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response, jsonify, abort
from werkzeug.security import generate_password_hash
from app.utils.database import get_db_engine
from app.routes.auth import login_required
import bcrypt
from sqlalchemy import text
import sqlalchemy.sql
from datetime import datetime, timedelta
from app.utils.custom_email import send_account_setup_email
from app.extensions import mail
admin_bp = Blueprint("admin_bp", __name__)

import traceback
@admin_bp.route('/CreateUser', methods=['GET', 'POST'])
@login_required
def create_user():
    if not session.get("user_admin"):
        abort(403)

    engine = get_db_engine()
    user_role = session.get("user_role")
    user_id = session.get("user_id")
    user_desc = session.get("desc")

    print(f"📌 user_role: {user_role}, user_id: {user_id}")

    providers = []
    schools = []
    groups = []
    funders = None
    funder = None
    only_own_staff_or_empty = False

    try:
        with engine.connect() as conn:
            if user_role == "FUN":
                print("🔍 Loading providers and schools for FUNDER...")
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
                print(f"ℹ️ only_own_staff_or_empty: {only_own_staff_or_empty}")

            elif user_role == "ADM":
                print("🔍 Loading all data for ADMIN...")
                providers = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request"),
                    {"Request": "AllProviders"}
                ).fetchall()
                schools = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request"),
                    {"Request": "AllSchools"}
                ).fetchall()
                funders = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request"),
                    {"Request": "AllFunders"}
                ).fetchall()
                groups = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request"),
                    {"Request": "AllGroups"}
                ).fetchall()
            elif user_role == "MOE":
                print("🔍 Loading school for MOE...")
                schools = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :moe"),
                    {"Request": "SchoolByMOENumber", "moe": user_id}
                ).fetchall()

            elif user_role == "PRO":
                print("🔍 Loading schools and provider for PROVIDER...")
                schools = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :pid"),
                    {"Request": "SchoolsByProvider", "pid": user_id}
                ).fetchall()

                providers = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :pid"),
                    {"Request": "ProviderByID", "pid": user_id}
                ).fetchone()

            elif user_role == "GRP":
                groups = [{"id": user_id, "Description": user_desc}]
                print("🔍 Loading schools and providers for GRP...")
                group_entities = session.get("group_entities", {})
                provider_ids = [str(e["id"]) for e in group_entities.get("PRO", [])]
                funder_ids = [str(e["id"]) for e in group_entities.get("FUN", [])]
                print(f"🧮 group_entities: {group_entities}")
                print(f"🧾 provider_ids: {provider_ids}")
                print(f"🧾 funder_ids: {funder_ids}")

                schools = []
                providers = []

                if provider_ids:
                    csv_providers = ",".join(provider_ids)
                    print(f"📤 Fetching schools for providers: {csv_providers}")
                    schools_result = conn.execute(
                        text("EXEC FlaskSchoolsByGroupProviders :ProviderList"),
                        {"ProviderList": csv_providers}
                    )
                    schools = schools_result.fetchall()
                    print(f"✅ Got {len(schools)} schools")
                    print(f"📤 Fetching providers by ID list")
                    providers_result = conn.execute(
                        text("EXEC FlaskProvidersByIDList :ProviderList"),
                        {"ProviderList": csv_providers}
                    )
                    providers = providers_result.fetchall()
                    print(f"✅ Got {len(providers)} providers")
                    schools = [{"description": s[0], "id": s[1], "provider_id": s[2]} for s in schools]
                    providers = [{"id": p[0], "Description": p[1]} for p in providers]
                if funder_ids:
                    csv_funders = ",".join(funder_ids)
                    print(f"📤 Fetching schools for funders: {csv_funders}")
                    funder_schools = conn.execute(
                        text("EXEC FlaskSchoolsByGroupFunders :FunderList"),
                        {"FunderList": csv_funders}
                    ).fetchall()
                    print(f"✅ Got {len(funder_schools)} funder schools")

                    # Merge and deduplicate schools
                    schools += funder_schools
                    seen = set()
                    schools = [s for s in schools if not (s.id in seen or seen.add(s.id))]
                    print(f"🧼 Deduplicated to {len(schools)} total schools")

    except Exception as e:
        print("❌ Error during role-specific DB calls")
        traceback.print_exc()
        flash("An error occurred while loading data.", "danger")

    if request.method == "POST":
        print("📥 POST request received")
        email = request.form.get("email")
        firstname = request.form.get("firstname")
        surname = request.form.get("surname")
        send_email = request.form.get("send_email") == "on"
        admin = 1 if request.form.get("admin") == "1" else 0
        hashed_pw = None
        selected_role = request.form.get("selected_role")
        selected_id = request.form.get("selected_id")

        print(f"📄 Raw selected_id: {selected_id}")
        if selected_id == "" or selected_id is None:
            selected_id = None
        else:
            selected_id = int(selected_id)

        print(f"🧾 Creating user: {email}, Role: {selected_role}, ID: {selected_id}")

        try:
            with engine.begin() as conn:
                existing = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                    {"Request": "CheckEmailExists", "Text": email}
                ).fetchone()

                if existing:
                    flash("⚠️ Email already exists.", "warning")
                    print("⚠️ Email already exists")
                else:
                    conn.execute(
                        text("""
                            EXEC FlaskInsertUser 
                                @Email = :email,
                                @HashPassword = :hash,
                                @Role = :role,
                                @ID = :id,
                                @FirstName = :firstname,
                                @Surname = :surname,
                                @Admin = :admin,
                                @Active = :active
                        """),
                        {
                            "email": email,
                            "hash": hashed_pw,
                            "role": selected_role,
"id": selected_id ,
                            "firstname": firstname,
                            "surname": surname,
                            "admin": admin,
                            "active": 1
                        }
                    )
                    flash(f"✅ User {email} created.", "success")
                    print(f"✅ Created user: {email}")

                    if send_email:
                        print("📧 Sending setup email...")
                        send_account_setup_email(
                            mail=mail,
                            recipient_email=email,
                            first_name=firstname,
                            role=selected_role,
                            is_admin=admin,
                            invited_by_name=f"{session.get('user_firstname')} {session.get('user_surname')}",
                            inviter_desc=user_desc
                        )
        except Exception as e:
            print("❌ Error during user creation")
            traceback.print_exc()
            flash("Failed to create user due to an internal error.", "danger")
    print(len(schools))
    print(len(providers))
    print(len(groups))
    print(len(funders))
    return render_template(
        "create_user.html",
        user_role=user_role,
        name=user_desc,
        funder=funder,
        funders=funders,
        providers=providers,
        schools=schools,
                groups=groups,  # ← Add this

        only_own_staff_or_empty=only_own_staff_or_empty
    )
from datetime import datetime
from flask import session, render_template
from sqlalchemy import text

@admin_bp.route("/Profile")
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
        "funder_address": session.get("funder_address"),
        "provider_lat": session.get("provider_lat"),
        "provider_lon": session.get("provider_lon"),
        "provider_address": session.get("provider_address")
    }

    # Load dropdown options
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("EXEC FlaskHelperFunctions 'SchoolTypeDropdown'"))
        school_type_options = [dict(row._mapping) for row in result]
        result = conn.execute(text("EXEC GetElearningStatus :Email"), {"Email": user_info["email"]})
        eLearning_status = [dict(row._mapping) for row in result]

        # optionally pass a static last_updated date for now 
        last_self_review_row = conn.execute(text("EXEC SVY_LatestSelfReveiw :Email"), {"Email": user_info["email"]}).fetchone()
        user_info["last_self_review"] = (
            last_self_review_row[0].strftime('%A, %d %B %Y')
            if last_self_review_row and last_self_review_row[0]
            else None
        )
        last_review_date = last_self_review_row[0] if last_self_review_row else None

        overdue = False
        if last_review_date and isinstance(last_review_date, datetime):
            overdue = datetime.now() - last_review_date > timedelta(days=90)
        user_info["last_self_review_overdue"] = overdue

    return render_template("profile.html",
                       user=user_info,
                       school_type_options=school_type_options,
                       eLearning_status=eLearning_status)

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
    with engine.begin() as conn:
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

@admin_bp.route('/ProviderMaintenance', methods=['GET', 'POST'])
@login_required
def provider_maintenance():
    engine = get_db_engine()
    user_role = session.get("user_role")
    user_id = session.get("user_id")
    print(f"🔐 User Role: {user_role}, User ID: {user_id}")

    if not (user_role == "ADM" or (user_role == "FUN" and session.get("user_admin") == 1)):
        abort(403)

    selected_funder = request.form.get("funder")
    selected_term = request.form.get("term") or session.get("nearest_term")
    selected_year = request.form.get("year") or session.get("nearest_year")
    print(f"📥 Selected funder: {selected_funder}, Term: {selected_term}, Year: {selected_year}")

    funders, schools, providers = [], [], []

    with engine.connect() as conn:
        if user_role == "ADM":
            print("🔍 Loading all funders for admin...")
            funders_result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request"),
                {"Request": "AllFunders"}
            )
            funders = [dict(row._mapping) for row in funders_result]
            print(f"📊 Funders loaded: {len(funders)}")

        if user_role == "FUN":
            selected_funder = str(user_id)
            print(f"🔍 Funder user - using own ID as funder: {selected_funder}")

        if selected_funder:
            print(f"🔎 Querying schools and providers for funder: {selected_funder}")
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
            print(f"🏫 Schools loaded: {len(schools)}")

            providers_result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :funder_id"),
                {"Request": "GetProviderByFunder", "funder_id": selected_funder}
            )
            providers = [dict(row._mapping) for row in providers_result]
            print(f"🏢 Providers loaded: {len(providers)}")
            if(selected_funder is not null):
                staff_result = conn.execute(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = 'FunderStaff', @FunderID = :fid"),
                    {"fid": selected_funder}
                )
                staff_list = [dict(row._mapping) for row in staff_result]
            else:
                staff_list = []
    try:
        print("✅ Rendering provider_maintenance.html")
        return render_template(
            "provider_maintenance.html",
            schools=schools,
            providers=providers,
            funders=funders,
            selected_funder=int(selected_funder) if selected_funder else None,
            selected_term=int(selected_term),
            selected_year=int(selected_year),
            user_role=user_role,
            staff_list=staff_list
        )
    except Exception as e:
        print("❌ Error rendering provider_maintenance.html")
        traceback.print_exc()
        return str(e), 500
    
    
@admin_bp.route("/assign_provider", methods=["POST"])
def assign_provider():
    data = request.get_json()
    print(data)
    moe_number = data.get("MOENumber")
    term = data.get("Term")
    year = data.get("Year")
    provider_id = data.get("ProviderID")

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
    data = request.get_json()
    provider_name = data.get("provider_name")
    funder_id = data.get("funder_id")

    if not provider_name or not funder_id:
        return jsonify({"success": False, "message": "Missing data"}), 400

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
            error_message = str(e)

            if "Provider name already exists" in error_message:
                return jsonify({"success": False, "message": "A provider with this name already exists for the selected funder."}), 400

            return jsonify({"success": False, "message": error_message}), 500



@admin_bp.route("/get_funder_staff/<int:funder_id>")
@login_required
def get_funder_staff(funder_id):
    engine = get_db_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("EXEC FlaskHelperFunctionsSpecific @Request = 'FunderStaff', @FunderID = :fid"),
            {"fid": funder_id}
        )
        staff = [dict(row._mapping) for row in result]
        return jsonify(staff)


@admin_bp.route("/assign_kaiako_staff", methods=["POST"])
@login_required
def assign_kaiako_staff():
    data = request.get_json()
    moe = data.get("MOENumber")
    term = data.get("Term")
    year = data.get("Year")
    email = data.get("Email")

    print(f"📥 Incoming assign_kaiako_staff request")
    print(f"   ➤ MOE Number: {moe}")
    print(f"   ➤ Term: {term}")
    print(f"   ➤ Year: {year}")
    print(f"   ➤ Staff Email: {email}")

    if not all([moe, term, year]):
        print("❌ Missing MOE, Term, or Year.")
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            if email == "":
                print("🗑️ Deleting staff assignment...")
                conn.execute(
                    text("EXEC FlaskDeleteEntityStaff @MOENumber = :moe, @Term = :term, @CalendarYear = :year"),
                    {"moe": moe, "term": term, "year": year}
                )
                print("✅ Staff assignment deleted.")
            else:
                print("🔄 Assigning/Updating staff...")
                conn.execute(
                    text("EXEC FlaskAssignEntityStaff @MOENumber = :moe, @Term = :term, @CalendarYear = :year, @Email = :email"),
                    {"moe": moe, "term": term, "year": year, "email": email}
                )
                print("✅ Staff assigned/updated in EntityStaff.")

        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Error during DB execution: {e}")
        return jsonify({"success": False, "message": str(e)}), 500