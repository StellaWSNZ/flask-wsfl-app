from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify, abort
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine
from app.routes.auth import login_required
from app.utils.custom_email import send_account_setup_email
from app.extensions import mail
import traceback

staff_bp = Blueprint("staff_bp", __name__)


@staff_bp.route("/Staff", methods=["GET", "POST"])
@login_required
def staff_maintenance():
    try:
        print("📥 /Staff route called")
        school_list = []
        user_id = session.get("user_id")
        user_role = session.get("user_role")
        user_email = session.get("user_email")
        user_desc = session.get("desc")
        user_admin = session.get("user_admin")
        print(f"🧑 Session info — ID: {user_id}, Role: {user_role}, Admin: {user_admin}, Email: {user_email}, Desc: {user_desc}")

        if not user_role or user_admin != 1:
            abort(403)
        has_groups = False
        group_list = []

        with get_db_engine().begin() as conn:
            if user_role == "ADM":
                result = conn.execute(text("EXEC FlaskGetAllGroups"))
                group_list = [{"id": row.ID, "name": row.Name} for row in result]
                has_groups = len(group_list) > 0
            elif user_role == "FUN":
                result = conn.execute(
                    text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"),
                    {"fid": user_id}
                )
                group_list = [{"id": row.GroupID, "name": row.Description} for row in result]
                has_groups = len(group_list) > 0
            elif user_role == "GRP":
                group_list = [{"id": user_id, "name": user_desc}]
                has_groups = True
        selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
        selected_entity_id = request.form.get("entity_id") or request.args.get("entity_id")
        print(f"📌 Initial entity_type: {selected_entity_type}, entity_id: {selected_entity_id}")
        if request.method == "POST" or request.args.get("trigger_load") == "1":
            print("📩 POST received")
        if not selected_entity_type or not selected_entity_id:
            if user_role == "PRO":
                selected_entity_type = "Provider"  # "Provider" or "School"
                selected_entity_id = user_id
            elif user_role == "MOE":
                selected_entity_type = "School"  # "Provider" or "School"
                selected_entity_id = user_id
            elif user_role == "GRP":
                selected_entity_type = "Group"  # "Provider" or "School"
                selected_entity_id = user_id
            else:
                selected_entity_type = "Funder"
                selected_entity_id = user_id
        print(f"📌 Final entity_type: {selected_entity_type}, entity_id: {selected_entity_id}")

        selected_entity_name = None
        provider_options = []
        funder_list = []  # Add this if missing
        staff_data = pd.DataFrame()
        columns = []
        name = user_desc
        is_admin = user_admin == 1

        with get_db_engine().connect() as conn:
            if user_role == "ADM":
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
                    print("⚠️ ADM but missing entity_type/id")
                    return render_template("staff_maintenance.html",
                        data=[], columns=[],
                        user_role=user_role, name=user_desc,
                        selected_entity_type=None, selected_entity_id=None,
                        selected_entity_name=None, provider_options=[]
                    )

            elif user_role == "FUN":
                entity_type = selected_entity_type or "Funder"
                if entity_type == "Funder":
                    role_type = "FUN"
                    target_id = int(user_id)
                    selected_entity_name = user_desc
                elif entity_type == "Provider":
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
                        print("⚠️ FUN selected Provider but no provider selected")
                        return render_template("staff_maintenance.html",
                            data=[], columns=[],
                            user_role=user_role, name=user_desc,
                            selected_entity_type="Provider", selected_entity_id=None,
                            selected_entity_name=None, provider_options=provider_options
                        )
                else:
                    return "Invalid entity type", 400
            elif user_role == "GRP":
                entity_type = selected_entity_type or "Group"
                provider_options = []
                school_list = []
                target_id = int(selected_entity_id)
                target_id = int(selected_entity_id)
                if entity_type == "Provider":
                    role_type = "PRO"
                    providers = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'ProvidersByGroupID', @Number = :gid"),
                        {"gid": user_id}
                    ).fetchall()
                    provider_options = [{"id": row.id, "name": row.Description} for row in providers]
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'ProviderName', @Number = :fid"),
                        {"fid": target_id}
                    )
                    selected_entity_name = result.scalar()

                elif entity_type == "School":
                    role_type = "MOE"
                    schools = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'SchoolsByGroupID', @Number = :gid"),
                        {"gid": user_id}
                    ).fetchall()
                    school_list = [{"id": row.id, "name": row.Description} for row in schools]
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'SchoolName', @Number = :fid"),
                        {"fid": target_id}
                    )
                    selected_entity_name = result.scalar()

                elif entity_type == "Group":
                    role_type = "GRP"
                    selected_entity_name = next((g["name"] for g in group_list if g["id"] == target_id), None)
                else:
                    print(f"⚠️ GRP selected invalid entity type: {entity_type}")
                    return "Invalid entity type", 400

            else:
                role_type = user_role
                target_id = int(user_id)
                selected_entity_name = user_desc

            print(f"🧾 Fetching staff for role_type: {role_type}, ID: {target_id}")
            result = conn.execute(
                text("EXEC FlaskGetStaffDetails @RoleType = :role, @ID = :id, @Email = :email"),
                {"role": role_type, "id": target_id, "email": user_email}
            )
            rows = result.fetchall()
            staff_data = pd.DataFrame(rows, columns=result.keys())
            columns = result.keys()
            # Fetch hidden staff using same role_type and target_id
            hidden_result = conn.execute(
                text("EXEC FlaskGetHiddenStaff @EntityType = :etype, @EntityID = :eid"),
                {"etype": role_type, "eid": target_id}
            )
            hidden_staff = [dict(row._mapping) for row in hidden_result]
        return render_template(
            "staff_maintenance.html",
            entity_type=selected_entity_type,
            selected_funder_id=int(selected_entity_id) if selected_entity_id else None,
            selected_entity_type=selected_entity_type,
            selected_entity_id=selected_entity_id,
            selected_entity_name=selected_entity_name,
            provider_options=provider_options,
            school_list=school_list,
            group_list=group_list,
            funder_list=funder_list,
            data=staff_data.to_dict(orient="records"),
            columns=columns,
            name=name+"'s Staff eLearning",
            user_role=user_role,
            user_admin=is_admin,
            has_groups=has_groups,
            hidden_staff=hidden_staff
        )

    except Exception as e:
        print("❌ Exception in /Staff route:")
        print(traceback.format_exc())
        return "500 Internal Server Error", 500
   




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

    entity_type = request.form.get("entity_type")
    entity_id = request.form.get("entity_id")
    if not entity_type:
        entity_type = session.get("user_role")
        if entity_type == "FUN":
            entity_type = "Funder"
        elif entity_type == "PRO":
            entity_type = "Provider"
        elif entity_type == "MOE":
            entity_type = "School"
        elif entity_type == "GRP":
            entity_type = "Group"

    if not entity_id:
        entity_id = session.get("user_id")

    print(entity_type)
    print(entity_id)
    return redirect(url_for("staff_bp.staff_maintenance", entity_type=entity_type, entity_id=entity_id))

@staff_bp.route('/invite_user', methods=['POST'])
@login_required
def invite_user():
    try:
        print("📥 Received form data:", request.form)

        email = request.form.get("email", "").strip().lower()
        firstname = request.form.get("firstname", "").strip()
        admin_raw = request.form.get("admin", "0")
        admin = 1 if admin_raw in ["1", "true", "True", True] else 0
        entity_type = request.form.get("entity_type")
        entity_id = request.form.get("entity_id")

        if not entity_type:
            role = session.get("user_role")
            entity_type = {
                "FUN": "Funder",
                "PRO": "Provider",
                "MOE": "School",
                "GRP": "Group"
            }.get(role, None)

        if not entity_id:
            entity_id = session.get("user_id")

        if not email:
            raise ValueError("Missing email")
        if not firstname:
            firstname = "Staff"

        role = session.get("user_role") or "UNKNOWN"
        invited_by = session.get("user_firstname", "") + ' ' + session.get("user_surname", "")
        inviter_desc = session.get("desc", "")

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

        print("📨 Sending invitation email...")
        send_account_setup_email(
            mail=mail,
            recipient_email=email,
            first_name=firstname,
            role=role,
            invited_by_name=invited_by,
            inviter_desc=inviter_desc,
            is_admin=(admin == 1)
        )

        flash(f"✅ Invitation sent to {email}.", "success")

    except Exception as e:
        print("🚨 Error in /invite_user:", e)
        flash("⚠️ Failed to invite user. Please check the logs.", "danger")

    return redirect(url_for('staff_bp.staff_maintenance', entity_type=entity_type, entity_id=entity_id, trigger_load=1))

    
@staff_bp.route('/add_staff', methods=['POST'])
@login_required
def add_staff():
    try:
        email = request.form['email'].strip().lower()
        firstname = request.form['first_name'].strip()
        lastname = request.form['last_name'].strip()
        selected_role = session.get("user_role")
        selected_id = request.form.get("entity_id") or session.get("user_id")
        account_status = request.form['account_status']
        entity_type = request.form.get("entity_type")        
        print(entity_type)
        entity_id = request.form.get("entity_id") or selected_id
        admin = 1 if request.form.get("admin") == "1" else 0
        active = 1 if account_status == "enable" else 0
    	
        if entity_type == "Provider":
            selected_role = "PRO"
        elif entity_type == "Funder":
            selected_role = "FUN"
        else:
            selected_role = session.get("user_role")
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
                return redirect(url_for('staff_bp.staff_maintenance', entity_type=entity_type, entity_id=entity_id, trigger_load=1))


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

        return redirect(url_for('staff_bp.staff_maintenance', entity_type=entity_type, entity_id=entity_id, trigger_load=1))


    except Exception as e:
        print("❌ Exception in /add_staff:")
        print(traceback.format_exc())
        flash("❌ Failed to add user. Please check the logs.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance'))


@staff_bp.route('/disable_user', methods=['POST'])
@login_required
def disable_user():
    email = request.form.get('email')
    entity_type = request.form.get('entity_type')
    entity_id = request.form.get('entity_id')

    if not entity_type:
        role = session.get("user_role")
        entity_type = {
            "FUN": "Funder",
            "PRO": "Provider",
            "MOE": "School",
            "GRP": "Group"
        }.get(role, None)

    if not entity_id:
        entity_id = session.get("user_id")
    if not email:
        flash("Missing email address.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance', entity_type=entity_type, entity_id=entity_id))


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

    return redirect(url_for('staff_bp.staff_maintenance', entity_type=entity_type, entity_id=entity_id, trigger_load=1))

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


@staff_bp.route("/StaffeLearning", methods=["GET", "POST"])
@login_required
def staff_eLearning():
    try:
        print("📥 Route '/StaffeLearning' called")

        engine = get_db_engine()
        user_role = session.get("user_role")
        user_id = session.get("user_id")
        user_email = session.get("user_email")
        user_desc = session.get("desc")
        user_admin = session.get("user_admin")

        if user_admin != 1:
            abort(403)

        # Handle entity selection logic based on role
        if user_role == "PRO":
            selected_entity_type = "Provider"
            selected_entity_id = str(user_id)
        elif user_role == "FUN":
            selected_entity_type = "Funder"
            selected_entity_id = request.args.get("entity_id") or str(user_id)
        elif user_role == "ADM":
            selected_entity_type = request.args.get("entity_type", "Funder")
            selected_entity_id = request.args.get("entity_id")
            if not selected_entity_id:
                flash("Please select an entity to view staff eLearning.", "warning")
                return render_template(
                    "staff_elearning.html",
                    staff_eLearning_data={},
                    course_ids=[],
                    selected_entity_type=selected_entity_type,
                    selected_entity_id=None,
                    entity_list=[],
                    name="Staff eLearning",
                    role=user_role
                )
        else:
            selected_entity_type = "School"
            flash("Please select an entity to view staff eLearning.", "warning")
            return render_template(
                "staff_elearning.html",
                staff_eLearning_data={},
                course_ids=[],
                selected_entity_type=selected_entity_type,
                selected_entity_id=None,
                entity_list=[],
                name="Staff eLearning",
                role=user_role
            )
        print(f"🔍 user_role: {user_role}, user_id: {user_id}, email: {user_email}, desc: {user_desc}")
        print(f"🔽 selected_entity_type = {selected_entity_type}, selected_entity_id = {selected_entity_id}")
        selected_entity_id = str(selected_entity_id)

        try:
            with engine.connect().execution_options(timeout=150) as conn:

                # Load list of entities
                if selected_entity_type == "Provider":
                    if user_role == "ADM":
                        print("🔄 Getting ALL providers (ADM)")
                        raw_entity_list = conn.execute(
                            text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'")
                        ).fetchall()
                    else:
                        print("🔄 Getting providers BY funder (FUN)")
                        raw_entity_list = conn.execute(
                            text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID"),
                            {"Request": "ProvidersByFunder", "FunderID": user_id}
                        ).fetchall()
                elif selected_entity_type == "Funder":
                    if user_role == "ADM":
                        print("🔄 Getting ALL funders (ADM)")
                        raw_entity_list = conn.execute(
                            text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'")
                        ).fetchall()
                    else:
                        print("🔄 Returning self as Funder (FUN)")
                        raw_entity_list = [SimpleNamespace(FunderID=user_id, Description=user_desc)]
                else:
                    print("⚠️ Unknown entity_type passed.")
                    raw_entity_list = []

                # Normalize list
                print("🧪 Normalizing entity list...")
                entity_list = []
                for row in raw_entity_list:
                    if isinstance(row, dict):
                        row = SimpleNamespace(**row)
                    entity_id = getattr(row, "FunderID", None) if selected_entity_type == "Funder" else getattr(row, "ProviderID", None)
                    entity_list.append({"id": str(entity_id), "name": row.Description})
                print(f"✅ entity_list: {entity_list}")

                # Fetch eLearning records
                print("📚 Fetching eLearning records...")
                el_rows = conn.execute(
                    text("EXEC FlaskGetStaffeLearning :RoleType, :ID, :Email"),
                    {
                        "RoleType": selected_entity_type[:3].upper(),
                        "ID": selected_entity_id,
                        "Email": user_email
                    }
                ).fetchall()
                print(f"📦 Retrieved {len(el_rows)} rows from eLearning data.")

                # Fetch active courses
                active_courses = conn.execute(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = 'ActiveCourses'")
                ).fetchall()

            active_course_ids = [str(r.ELearningCourseID) for r in active_courses]
            print(f"🎓 Active course IDs: {active_course_ids}")

            # Group by email
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

            selected_name = next((e["name"] for e in entity_list if e["id"] == selected_entity_id), user_desc if user_role == "PRO" else "Selected")
            print(f"🏷️ Selected entity name: {selected_name}")

            return render_template(
                "staff_elearning.html",
                staff_eLearning_data=grouped,
                course_ids=active_course_ids,
                selected_entity_type=selected_entity_type,
                selected_entity_id=selected_entity_id,
                entity_list=entity_list,
                name=selected_name,
                role=user_role
            )

        except Exception as e:
            print("❌ Error rendering staff_elearning.html:")
            print(traceback.format_exc())
            return "500 Template Error", 500
    except Exception as e:
        print("❌ Error rendering staff_elearning.html:")
        print(traceback.format_exc())
        return "500 Template Error", 500

@staff_bp.route("/hide_user", methods=["POST"])
@login_required
def hide_user():
    print("***")
    email = request.form.get("email")

    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("EXEC FlaskDeactivateUser @Email = :email"), {"email": email})
        flash(f"{email} has been hidden from all staff views.", "success")
    except Exception:
        import traceback
        traceback.print_exc()
        flash("Failed to hide the user. Please contact support.", "danger")
    selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
    selected_entity_id = request.form.get("entity_id") or request.args.get("entity_id")
    return redirect(url_for('staff_bp.staff_maintenance', entity_type=selected_entity_type, entity_id=selected_entity_id))


@staff_bp.route("/unhide_user", methods=["POST"])
@login_required
def unhide_user():
  
    email = request.form.get("email")
    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("EXEC FlaskUnhideUserByEmail @Email = :email"), {"email": email})

        flash(f"{email} has been unhidden.", "success")
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash("Something went wrong while unhiding the user.", "danger")
    selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
    selected_entity_id = request.form.get("entity_id") or request.args.get("entity_id")
    return redirect(url_for('staff_bp.staff_maintenance', entity_type=selected_entity_type, entity_id=selected_entity_id))
