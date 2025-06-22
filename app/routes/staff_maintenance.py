from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify, abort
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine
from app.routes.auth import login_required
from app.utils.custom_email import send_account_setup_email
from app.extensions import mail
import traceback

staff_bp = Blueprint("staff_bp", __name__)


@staff_bp.route("/Staff")
@login_required
def staff_maintenance():
   
    user_id = session.get("user_id")
    user_role = session.get("user_role")
    user_email = session.get("user_email")
    user_desc = session.get("desc")
    user_admin = session.get("user_admin")
    if not user_role or user_admin != 1:
        abort(403)

    selected_entity_id = request.args.get("entity_id")
    selected_entity_type = request.args.get("entity_type")

    selected_entity_name = None
    data = pd.DataFrame()
    provider_options = []

    with get_db_engine().connect() as conn:
        if user_role == "ADM":
            # Full dropdown for ADM: Funder or Provider
            if selected_entity_id and selected_entity_type:
                id_column = f"{selected_entity_type}ID"
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :id, @Text = :type"),
                    {"Request": "GetEntityDescription", "id": int(selected_entity_id), "type": selected_entity_type}
                )
                selected_entity_name = result.scalar() 
                role_type = "FUN" if selected_entity_type == "Funder" else "PRO"
                target_id = int(selected_entity_id)
            else:
                return render_template("staff_maintenance.html",
                    data=[], columns=[],
                    user_role=user_role, name=user_desc,
                    selected_entity_type=None, selected_entity_id=None,
                    selected_entity_name=None, provider_options=[]
                )

        elif user_role == "FUN":
            # Funder: dropdown with "Funder" or "Provider"
            entity_type = selected_entity_type or "Funder"

            if entity_type == "Funder":
                # View own staff
                role_type = "FUN"
                target_id = int(user_id)
                selected_entity_name = user_desc

            elif entity_type == "Provider":
                # Provider dropdown for funded providers
                providers = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = 'ProvidersByFunderID', @Number = :fid"),
                    {"fid": user_id}
                ).fetchall()
                provider_options = [{"id": row.id, "name": row.Description} for row in providers]

                if selected_entity_id:
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'ProviderName', @Number = :fid"),
                        {"fid": selected_entity_id}
                    )
                    selected_entity_name = result.scalar()
                    role_type = "PRO"
                    target_id = int(selected_entity_id)
                else:
                    return render_template("staff_maintenance.html",
                        data=[], columns=[],
                        user_role=user_role, name=user_desc,
                        selected_entity_type="Provider", selected_entity_id=None,
                        selected_entity_name=None, provider_options=provider_options
                    )
            else:
                return "Invalid entity type", 400

        else:
            # MOE/PRO user views own staff
            role_type = user_role
            target_id = int(user_id)
            selected_entity_name = user_desc

        # Get staff details
        result = conn.execute(
            text("EXEC FlaskGetStaffDetails @RoleType = :role, @ID = :id, @Email = :email"),
            {"role": role_type, "id": target_id, "email": user_email}
        )
        rows = result.fetchall()
        data = pd.DataFrame(rows, columns=result.keys())

    return render_template("staff_maintenance.html",
        data=data.to_dict(orient="records"),
        columns=data.columns,
        user_role=user_role,
        name=user_desc,
        selected_entity_type=selected_entity_type,
        selected_entity_id=selected_entity_id,
        selected_entity_name=selected_entity_name,
        provider_options=provider_options
    )

    

@staff_bp.route("/get_entities")
@login_required
def get_entities():
    
    
    entity_type = request.args.get("entity_type")
    user_role = session.get("user_role")
    print(f"📥 /get_entities called with entity_type={entity_type}, user_role={user_role}")

    if not entity_type:
        return jsonify([])

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            if entity_type == "Provider":
                if user_role == "ADM":
                    # ADM should get all providers
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'")
                    )
                    return jsonify([{"id": row.ProviderID, "name": row.Description} for row in result])
                else:
                    # FUNDER gets providers by their own ID
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID"),
                        {"Request": "ProvidersByFunder", "FunderID": session.get("user_id")}
                    )
                    return jsonify([{"id": row.ProviderID, "name": row.Description} for row in result])

            elif entity_type == "Funder":
                if user_role == "ADM":
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'")
                    )
                    rows = result.fetchall()
                    print(rows)
                    return jsonify([
                        {"id": row.FunderID, "name": row.Description} for row in rows
                    ])
                else:
                    # FUNDER user should only see themselves
                    return jsonify([{
                        "id": session.get("user_id"),
                        "name": session.get("desc")
                    }])

        return jsonify([])
    except Exception as e:
        print("❌ Error in get_entities:", e)
        return jsonify([]), 500





@staff_bp.route('/update_staff', methods=['POST'])
@login_required
def update_staff():
    try:
        user_id = request.form.get("user_id")
        firstname = request.form.get("firstname")
        lastname = request.form.get("lastname")
        email = request.form.get("email")
        old_email = request.form.get("old_email")
        admin = 1 if request.form.get("admin") == "1" else 0
        role = session.get("role")
        id_from_session = session.get("id")

        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskUpdateStaffDetails 
                        @OldEmail = :old_email,
                        @NewEmail = :new_email,
                        @FirstName = :firstname,
                        @LastName = :lastname,
                        @Admin = :admin,
                @PerformedByEmail = :performed_by
                """),
                {
                    "old_email": old_email,
                    "new_email": email,
                    "firstname": firstname,
                    "lastname": lastname,
                    "admin": admin,
            "performed_by": session["user_email"]
                }
            )

        flash("Staff member updated successfully.", "success")
    except Exception as e:
        print("❌ Error in update_staff:")
        print(traceback.format_exc())
        flash("An error occurred while updating staff details.", "danger")

    return redirect(url_for("staff_bp.staff_maintenance"))

@staff_bp.route('/invite_user', methods=['POST'])
@login_required
def invite_user():
    try:
        # print("📥 Received form data:", request.form)

        email = request.form['email'].strip().lower()
        admin = int(request.form['admin'])
        first_name = request.form['firstname']
        role = session.get("user_role")
        if not role:
            raise ValueError("Missing user_role in session")

        
        invited_by = session.get('user_firstname') + ' ' + session.get('user_surname')
        inviter_desc = session.get('desc')


        engine = get_db_engine()
        with engine.begin() as conn:
            print("🛠️ Inserting user into DB...")
            conn.execute(
                text("EXEC FlaskInviteUser @Email = :email, @Admin = :admin, @PerformedByEmail = :performed_by"),
                {
                    "email": email,
                    "admin": admin,
                    "performed_by": session["user_email"]
                }
            )

        # print("📨 Sending account setup email...")
        send_account_setup_email(
            mail = mail,
            recipient_email=email,
            first_name=first_name,
            role=role,
            invited_by_name=invited_by,
            inviter_desc=inviter_desc,
            is_admin=(admin == 1)
        )

        flash(f"✅ Invitation sent to {email}.", "success")
        return redirect(url_for('staff_bp.staff_maintenance'))

    except Exception as e:
        print("🚨 Error in /invite_user:", e)
        flash("⚠️ Failed to invite user. Please check the logs.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance'))


    
@staff_bp.route('/add_staff', methods=['POST'])
@login_required
def add_staff():
    try:
        email = request.form['email'].strip().lower()
        firstname = request.form['firstname'].strip()
        lastname = request.form['lastname'].strip()
        selected_role = session.get("user_role")
        selected_id = session.get("user_id")
        account_status = request.form['account_status']
        admin = 1 if request.form.get("admin") == "1" else 0
        active = 1 if account_status == "enable" else 0

        hashed_pw = None
        send_email = account_status == "enable"
        user_desc = session.get("desc")

        engine = get_db_engine()

        with engine.begin() as conn:
            existing = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                {"Request": "CheckEmailExists", "Text": email}
            ).fetchone()

            if existing:
                flash("⚠️ Email already exists.", "warning")
                return redirect(url_for('staff_bp.staff_maintenance'))

        with engine.begin() as conn:
            conn.execute(
                text("""EXEC FlaskInsertUser 
                        @Email = :email,
                        @HashPassword = :hash,
                        @Role = :role,
                        @ID = :id,
                        @FirstName = :firstname,
                        @Surname = :surname,
                        @Admin = :admin,
                        @Active = :active"""),
                {
                    "email": email,
                    "hash": hashed_pw,
                    "role": selected_role,
                    "id": selected_id,
                    "firstname": firstname,
                    "surname": lastname,
                    "admin": admin,
                    "active": active
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

        return redirect(request.referrer or url_for('staff_bp.staff_maintenance'))


    except Exception as e:
        print("❌ Exception in /add_staff:")
        print(traceback.format_exc())
        flash("❌ Failed to add user. Please check the logs.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance'))


@staff_bp.route('/disable_user', methods=['POST'])
def disable_user():
    email = request.form.get('email')
    if not email:
        flash("Missing email address.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance'))

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(
                text("EXEC FlaskDisableUser :Email, :PerformedBy"),
                {
                    "Email": email,
                    "PerformedBy": session.get("user_email")
                }
            )
            conn.commit()
        flash("User has been disabled successfully.", "success")
    except Exception as e:
        print(f"❌ Error in /disable_user: {e}")
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for('staff_bp.staff_maintenance'))
@staff_bp.route("/get_active_courses")
@login_required
def get_active_courses():
    try:
        print("📥 /get_active_courses route called")
        engine = get_db_engine()
        with engine.connect() as conn:
            print("🔌 Connected to DB")
            result = conn.execute(text("EXEC [FlaskHelperFunctionsSpecific] @Request = 'ActiveCourses'"))
            rows = [dict(row._mapping) for row in result]
            print(f"✅ Retrieved {len(rows)} active courses")
            return jsonify(rows)
    except Exception as e:
        print("❌ /get_active_courses error:", e)
        return jsonify([]), 500
from types import SimpleNamespace



@staff_bp.route("/StaffELearning", methods=["GET"])
@login_required
def staff_elearning():
    
    print("📥 Route '/StaffELearning' called")

    engine = get_db_engine()
    user_role = session.get("user_role")
    user_id = session.get("user_id")
    user_email = session.get("user_email")
    user_desc = session.get("desc")
    user_admin = session.get("user_admin")
    if session.get("user_admin") != 1:
        abort(403)
    if user_role == "PRO":
        selected_entity_type = "Provider"
        selected_entity_id = str(user_id)
    else:
        selected_entity_type = request.args.get("entity_type", "Funder")
        selected_entity_id = request.args.get("entity_id", user_id)

    print(f"🔍 user_role: {user_role}, user_id: {user_id}, email: {user_email}, desc: {user_desc}")
    print(f"🔽 selected_entity_type = {selected_entity_type}, selected_entity_id = {selected_entity_id}")

    # Normalize for string comparison
    selected_entity_id = str(selected_entity_id)

    with engine.connect() as conn:
        # 🔄 Load entities
        if selected_entity_type == "Provider":
            if user_role == "ADM":
                print("🔄 Getting ALL providers (ADM)")
                result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'"))
            else:
                print("🔄 Getting providers BY funder (FUN)")
                result = conn.execute(text(
                    "EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID"
                ), {"Request": "ProvidersByFunder", "FunderID": user_id})
            raw_entity_list = result.fetchall()

        elif selected_entity_type == "Funder":
            if user_role == "ADM":
                print("🔄 Getting ALL funders (ADM)")
                result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'"))
                raw_entity_list = result.fetchall()
            else:
                print("🔄 Returning self as Funder (FUN)")
                raw_entity_list = [SimpleNamespace(FunderID=user_id, Description=user_desc)]

        else:
            print("⚠️ Unknown entity_type passed, skipping list.")
            raw_entity_list = []

        # 🔄 Normalize list
        print("🧪 Normalizing entity list...")
        entity_list = []
        for row in raw_entity_list:
            if isinstance(row, dict):
                row = SimpleNamespace(**row)
            if selected_entity_type == "Funder":
                entity_id = getattr(row, "FunderID", None)
            elif selected_entity_type == "Provider":
                entity_id = getattr(row, "ProviderID", None)
            else:
                entity_id = None

            entity_list.append({
                "id": str(entity_id),
                "name": row.Description
            })

        print(f"✅ entity_list: {entity_list}")

        # 🧾 Get staff eLearning records
        print("📚 Fetching eLearning records...")
        el_rows = conn.execute(text("""
            EXEC FlaskGetStaffELearning :RoleType, :ID, :Email
        """), {
            "RoleType": selected_entity_type[:3].upper(),
            "ID": selected_entity_id,
            "Email": user_email
        }).fetchall()

        print(f"📦 Retrieved {len(el_rows)} rows from eLearning data.")

        active_courses = conn.execute(
            text("EXEC FlaskHelperFunctionsSpecific @Request = 'ActiveCourses'")
        ).fetchall()

    active_course_ids = [str(r.ELearningCourseID) for r in active_courses]
    print(f"🎓 Active course IDs: {active_course_ids}")

    # 📊 Group data by email
    grouped = {}
    for row in el_rows:
        email = row.Email
        if email not in grouped:
            grouped[email] = {
                "Email": email,
                "FirstName": row.FirstName,
                "Surname": row.Surname,
                "Courses": {}
            }
        grouped[email]["Courses"][str(row.CourseID)] = {
            "CourseName": row.CourseName,
            "Status": row.Status
        }

    # 🏷️ Get selected entity name
    selected_name = next((e["name"] for e in entity_list if e["id"] == selected_entity_id), "Selected")
    if(user_role == "PRO"):
        selected_name = user_desc
    print(f"🏷️ Selected entity name: {selected_name}")

    return render_template(
        "staff_elearning.html",
        staff_elearning_data=grouped,
        course_ids=active_course_ids,
        selected_entity_type=selected_entity_type,
        selected_entity_id=selected_entity_id,
        entity_list=entity_list,
        name=selected_name,
        role = user_role
    )

    