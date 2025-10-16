# app/routes/admin.py

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response, jsonify, abort
from werkzeug.security import generate_password_hash
from app.utils.database import get_db_engine
from app.routes.auth import login_required
import pandas as pd
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
    user_id   = session.get("user_id")
    desc      = session.get("desc")

    # Defaults for template (lists stay empty; JS will populate selects)
    funder = None
    funders, providers, schools, groups = [], [], [], []
    only_own_staff_or_empty = False  # no longer computed; keep constant or remove from template

    # Keep only what you truly need for header/banners
    try:
        if user_role == "FUN":
            # Optional: only if your header needs specific funder fields not in session
            with engine.connect() as conn:
                funder = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :fid"),
                    {"Request": "FunderByID", "fid": user_id}
                ).fetchone()
    except Exception:
        traceback.print_exc()
        flash("An error occurred while loading data.", "danger")

    # Handle create on POST (unchanged except for selected_id handling)
    if request.method == "POST":
        email        = request.form.get("email")
        firstname    = request.form.get("firstname")
        surname      = request.form.get("surname")
        send_email   = request.form.get("send_email") == "on"
        admin_flag   = 1 if request.form.get("admin") == "1" else 0
        selected_role = request.form.get("selected_role")
        selected_id_raw = request.form.get("selected_id")

        def to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        selected_id = to_int(selected_id_raw)
        hashed_pw = None  # you’re creating invite-only users; keep None if SP supports it

        try:
            with engine.begin() as conn:
                existing = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                    {"Request": "CheckEmailExists", "Text": email}
                ).fetchone()

                if existing:
                    flash("⚠️ Email already exists.", "warning")
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
                            "id": selected_id,
                            "firstname": firstname,
                            "surname": surname,
                            "admin": admin_flag,
                            "active": 1
                        }
                    )
                    flash(f"✅ User {email} created.", "success")

                    if send_email:
                        # Ensure `mail` is imported from your app
                        send_account_setup_email(
                            mail=mail,
                            recipient_email=email,
                            first_name=firstname,
                            role=selected_role,
                            is_admin=admin_flag,
                            invited_by_name=f"{session.get('user_firstname')} {session.get('user_surname')}",
                            inviter_desc=desc
                        )
        except Exception:
            traceback.print_exc()
            flash("Failed to create user due to an internal error.", "danger")

    return render_template(
        "create_user.html",
        user_role=user_role,
        name=desc,
        funder=funder,                 # optional; remove if your template doesn’t use it
        funders=funders,               # now always empty; JS fills
        providers=providers,           # now always empty; JS fills
        schools=schools,               # now always empty; JS fills
        groups=groups,                 # now always empty; JS fills
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
        "email_alt":session.get("user_email_alt"),
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
        result = conn.execute(text("EXEC GetElearningStatus :Email"), {"Email": user_info["email_alt"] or user_info["email"]})
        eLearning_status = [dict(row._mapping) for row in result]

        # optionally pass a static last_updated date for now 
        last_self_review_row = conn.execute(text("EXEC SVY_LatestSelfReveiw :Email, :AltEmail"), {"Email": user_info["email"],"AltEmail": user_info["email_alt"]}).fetchone()
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
    new_email_alt = request.form.get("alt_email", "").strip()
    print(new_email_alt)
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
                @OriginalEmail = :original_email,
                @EmailAlt = :new_email_alt
        """), {
            "request": "UpdateUserInfo",
            "fname": new_firstname,
            "sname": new_surname,
            "new_email": new_email,
            "original_email": original_email,
            "new_email_alt": new_email_alt
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
        session["user_email_alt"] = getattr(updated_info, "AlternateEmail", None)

            

    flash("Profile updated successfully!", "success")
    return redirect(url_for("admin_bp.profile"))



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

    funders, schools, providers,staff_list = [], [], [],[]

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
            if selected_funder is not None:
                staff_result = conn.execute(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = 'FunderStaff', @FunderID = :fid"),
                    {"fid": selected_funder}
                )
                staff_list = [dict(row._mapping) for row in staff_result]
            else:
                staff_list = []
        print(selected_funder)
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

@admin_bp.route('/ManageProviders', methods=['GET', 'POST'])
@login_required
def manage_providers():
    if not session.get("user_admin"):
        abort(403)

    funder_id = request.args.get("funder_id")
    print(f"🔍 funder_id received: {funder_id}")

    if not funder_id:
        flash("No funder selected.", "warning")
        return redirect(url_for("admin_bp.provider_maintenance"))
    if session.get("user_role") == "ADM":
        pass  # Allow
    elif session.get("user_role") == "FUN" and str(session.get("user_id")) == funder_id:
        pass  # Allow
    else:
        abort(403)
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            print("🛠️ Connected to DB")

            # 🔹 Fetch providers
            rows = conn.execute(text("""
                EXEC FlaskGetManageableProvidersByFunder @FunderID = :fid
            """), {"fid": funder_id}).mappings().all()

            print(f"📦 Providers fetched: {len(rows)}")

            providers = []
            for row in rows:
                provider = dict(row)
                try:
                    provider["Latitude"] = float(provider["Latitude"]) if provider["Latitude"] is not None else None
                    provider["Longitude"] = float(provider["Longitude"]) if provider["Longitude"] is not None else None
                except (ValueError, TypeError):
                    provider["Latitude"] = None
                    provider["Longitude"] = None

                print(f"   📌 {provider['ProviderID']} - {provider['ProviderDesc']} - Deletable: {provider['Deletable']}")
                providers.append(provider)

            # 🔹 Fetch funder name
            result = conn.execute(text("""
                EXEC FlaskHelperFunctions @Request = 'FunderNameID', @Number = :fid
            """), {"fid": funder_id}).fetchone()
            funder_name = result[0] if result else "Unknown Funder"
        
        print("✅ Rendering manage_providers.html")
        return render_template(
            "manage_providers.html",
            providers=providers,
            funder_id=funder_id,
            funder_name=funder_name
        )

    except Exception as e:
        print("Error:", e)
        flash("An error occurred while loading providers.", "danger")
        return redirect(url_for("admin_bp.provider_maintenance"))

@admin_bp.route('/UpdateProvider', methods=['POST'])
@login_required
def update_provider():
    if not session.get("user_admin"):
        abort(403)

    pid = request.form.get("provider_id")
    new_name = request.form.get("new_name")
    new_address = request.form.get("new_address")
    new_lat = request.form.get("new_latitude")
    new_lon = request.form.get("new_longitude")

    print(f"📝 Updating Provider {pid} — Name: {new_name}, Address: {new_address}, Lat: {new_lat}, Lon: {new_lon}")

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskUpdateProviderDetails 
                    @ProviderID = :pid,
                    @NewName = :new_name,
                    @NewAddress = :new_address,
                    @NewLatitude = :new_lat,
                    @NewLongitude = :new_lon
            """), {
                "pid": pid,
                "new_name": new_name,
                "new_address": new_address,
                "new_lat": new_lat or None,
                "new_lon": new_lon or None
            })

        flash("Provider updated successfully.", "success")
    except Exception as e:
        flash(f"Failed to update provider: {e}", "danger")

    return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))
@admin_bp.route('/DeleteProvider', methods=['POST'])
@login_required
def delete_provider():
    if not session.get("user_admin"):
        abort(403)

    pid = request.form.get("provider_id")
    print(f"🗑️ Deleting provider ID: {pid}")

    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("EXEC FlaskDeleteProvider @ProviderID = :pid"), {"pid": pid})
        flash("Provider deleted.", "success")
    except Exception as e:
        print("❌ Deletion failed:", e)
        flash(f"Could not delete provider: {e}", "danger")

    return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))

@admin_bp.route('/AddProviderDetails', methods=['POST'])
@login_required
def add_provider_details():
    if not session.get("user_admin"):
        abort(403)

    funder_id = request.form.get("funder_id")
    name = request.form.get("provider_name")
    address = request.form.get("address") or ""
    latitude = request.form.get("latitude") or None
    longitude = request.form.get("longitude") or None

    try:
        latitude = float(latitude) if latitude else None
        longitude = float(longitude) if longitude else None
    except ValueError:
        flash("Latitude and longitude must be numeric.", "danger")
        return redirect(request.referrer)

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("EXEC FlaskAddProviderWithDetails @FunderID = :fid, @Description = :desc, @Address = :addr, @Latitude = :lat, @Longitude = :lon"),
                {
                    "fid": funder_id,
                    "desc": name.strip(),
                    "addr": address.strip(),
                    "lat": latitude,
                    "lon": longitude
                }
            )
        flash("Provider added successfully.", "success")
    except Exception as e:
        print("❌ Error adding provider:", e)
        flash(str(e), "danger")

    return redirect(request.referrer or url_for('admin_bp.provider_maintenance'))


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
    
    
@admin_bp.route("/SchoolType", methods=["GET", "POST"])
@login_required
def edit_school_type():
    if session.get("user_role") != "ADM":
        abort(403)
    engine = get_db_engine()
    school_data, school_types = [], []

    with engine.begin() as conn:
        # Dropdown options
        school_types = conn.execute(
            text("EXEC FlaskSchoolTypeChanger @Request = 'GetSchoolTypeDropdown'")
        ).fetchall()

        # Handle update
        if request.method == "POST":
            moenumber = request.form.get("moenumber")
            new_type = request.form.get("schooltype")

            conn.execute(
                text("EXEC FlaskSchoolTypeChanger @Request = 'UpdateSchoolType', @MOENumber = :moe, @SchoolTypeID = :stype"),
                {"moe": moenumber, "stype": new_type}
            )
            flash("School type updated successfully.", "success")

        # Get schools
        school_data = conn.execute(
            text("EXEC FlaskSchoolTypeChanger @Request = 'GetSchoolDirectory'")
        ).fetchall()
        
        glossary = conn.execute(
            text("EXEC FlaskSchoolTypeChanger @Request = 'GetGlossary'")
        ).fetchall()
        search_term = request.form.get("search_term", "")
        sort_by = request.form.get("sort_by", "")
        sort_direction = request.form.get("sort_direction", "")
    return render_template(
        "edit_school_type.html",
        school_data=school_data,
        school_types=school_types,
        glossary=glossary,
        search_term=search_term,
        sort_by=sort_by,
        sort_direction=sort_direction
    )
    
    
@admin_bp.route("/EditUser")
@login_required
def admin_user_entities():
    if(session.get("user_role")!="ADM"):
        flash("You don’t have permission to access this page", "danger")
        return redirect(url_for("home_bp.home"))
    return render_template("admin_user_entities.html")


@admin_bp.route("/get_users")
@login_required
def get_users():
    engine = get_db_engine()
    query = "EXEC FlaskGetAllUsers"
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return jsonify(df.to_dict(orient="records"))


@admin_bp.route("/update_user_role_entity", methods=["POST"])
@login_required
def update_user_role_entity():
    data = request.get_json()
    email = data.get("email")
    role = data.get("role")
    entity_id = data.get("entityId")
    full_name = data.get("fullName")
    entity_name = data.get("entityName")
    display_role = data.get("displayRole")  # ✅ Add this line

    print(f"🔁 Received update request for user: {email}")
    print(f"   → New Role: {role}, Entity ID: {entity_id}")

    if not all([email, role, entity_id]):
        flash("❌ Missing required fields.", "warning")
        return jsonify(success=False, message="Missing fields")

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            print("✅ Executing stored procedure FlaskUpdateUserRoleAndEntity...")
            stmt = text("""
                EXEC FlaskUpdateUserRoleAndEntity
                    @Email = :email,
                    @Role = :role,
                    @EntityID = :entity_id
            """)
            conn.execute(stmt, {
                "email": email,
                "role": role,
                "entity_id": entity_id
            })

        flash(f"✅ Updated {full_name} to {display_role} – {entity_name}.", "success")  # ✅ Use display_role
        return jsonify(success=True)

    except Exception as e:
        flash(f"🔥 Error during update: {e}", "danger")
        return jsonify(success=False, message="Internal server error")
