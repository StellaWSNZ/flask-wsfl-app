from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify, abort,request
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine
from app.routes.auth import login_required
from app.utils.custom_email import send_account_setup_email, send_elearning_reminder_email
from app.extensions import mail
import requests
import traceback
from datetime import datetime
staff_bp = Blueprint("staff_bp", __name__)


@staff_bp.route("/Staff", methods=["GET", "POST"])
@login_required
def staff_maintenance():
    try:
        user_id    = session.get("user_id")
        user_role  = session.get("user_role")
        user_email = session.get("user_email")
        desc       = session.get("desc")
        user_admin = session.get("user_admin")

        if not user_role or user_admin != 1:
            abort(403)

        # NEW: these are used by the new HTML/JS
        funder_id    = user_id if user_role == "FUN" else None
        current_year = datetime.now().year

        ROLE_MAP = {"Funder": "FUN", "Provider": "PRO", "School": "MOE", "Group": "GRP"}

        selected_entity_type = (request.form.get("entity_type") or request.args.get("entity_type") or "").strip() or None
        selected_entity_id   = (request.form.get("entity_id")   or request.args.get("entity_id")   or "").strip() or None

        has_groups = False
        group_list = []

        with get_db_engine().begin() as conn:
            if user_role == "ADM":
                result = conn.execute(text("EXEC FlaskGetAllGroups"))
                group_list = [{"id": row.ID, "name": row.Name} for row in result]
                has_groups = len(group_list) > 0
            elif user_role == "FUN":
                result = conn.execute(text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"), {"fid": user_id})
                group_list = [{"id": getattr(row, "GroupID", getattr(row, "ID", None)),
                               "name": getattr(row, "Description", getattr(row, "Name", ""))}
                              for row in result]
                has_groups = len(group_list) > 0
            elif user_role == "GRP":
                group_list = [{"id": user_id, "name": desc}]
                has_groups = True

        if not selected_entity_type:
            if user_role == "PRO": selected_entity_type = "Provider"
            elif user_role == "MOE": selected_entity_type = "School"
            elif user_role == "GRP": selected_entity_type = "Group"
            elif user_role == "FUN": selected_entity_type = "Funder"
            else: selected_entity_type = "Provider"

        if user_role == "MOE" and selected_entity_type == "School" and not selected_entity_id:
            selected_entity_id = str(user_id)
        if user_role == "PRO" and selected_entity_type == "Provider" and not selected_entity_id:
            selected_entity_id = str(user_id)
        if user_role == "GRP" and selected_entity_type == "Group" and not selected_entity_id:
            selected_entity_id = str(user_id)
        if user_role == "FUN" and selected_entity_type == "Funder" and not selected_entity_id:
            selected_entity_id = str(user_id)

        selected_entity_name = None
        role_type = None
        target_id = None
        staff_data = pd.DataFrame()
        columns = []
        hidden_staff = []

        with get_db_engine().connect() as conn:
            if selected_entity_type and selected_entity_id:
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :id, @Text = :type"),
                    {"Request": "GetEntityDescription", "id": int(selected_entity_id), "type": selected_entity_type}
                )
                selected_entity_name = result.scalar()
                role_type = ROLE_MAP.get(selected_entity_type, user_role)
                target_id = int(selected_entity_id)

                result = conn.execute(
                    text("EXEC FlaskGetStaffDetails @RoleType = :role, @ID = :id, @Email = :email"),
                    {"role": role_type, "id": target_id, "email": user_email}
                )
                rows = result.fetchall()
                staff_data = pd.DataFrame(rows, columns=result.keys())
                columns = result.keys()

                hidden_result = conn.execute(
                    text("EXEC FlaskGetHiddenStaff @EntityType = :etype, @EntityID = :eid"),
                    {"etype": role_type, "eid": target_id}
                )
                hidden_staff = [dict(row._mapping) for row in hidden_result]

        return render_template(
            "staff_maintenance.html",
            entity_type=selected_entity_type,
            selected_entity_type=selected_entity_type,
            selected_entity_id=selected_entity_id,
            selected_entity_name=selected_entity_name,
            provider_options=[],
            school_list=[],
            group_list=group_list,
            funder_list=[],
            data=staff_data.to_dict(orient="records"),
            columns=columns,
            name=desc + "'s Staff eLearning",
            user_role=user_role,
            user_admin=(user_admin == 1),
            has_groups=has_groups,
            hidden_staff=hidden_staff,
            # NEW: required by the new HTML
            user_id=user_id,
            funder_id=funder_id,
            current_year=current_year
        )

    except Exception:
        print("❌ Exception in /Staff route:")
        print(traceback.format_exc())
        return "500 Internal Server Error", 500

@staff_bp.get("/helper")
@login_required
def helper():
    req = request.args.get("request")           # e.g. "FilterSchoolID" or "ProvidersByFunderID"
    number = request.args.get("number")         # user_id for schools OR funder_id for providers
    if not req or not number:
        return jsonify([]), 400

    out = []
    with get_db_engine().connect() as conn:
        if req == "FilterSchoolID":
            # Returns schools this user can see
            result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :req, @Number = :num"),
                {"req": "FilterSchoolID2", "num": int(number)}
            )
            for row in result:
                # Be tolerant to different column casings
                moe  = getattr(row, "MOENumber", None)
                name = getattr(row, "SchoolName", None)
                if moe is not None and name:
                    out.append({"id": int(moe), "name": name})

        elif req == "ProvidersByFunderID":
            # Returns providers for a given funder
            result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :req, @Number = :num"),
                {"req": "ProvidersByFunderID", "num": int(number)}
            )
            for row in result:
                pid  = getattr(row, "id", getattr(row, "ID", None))
                desc = getattr(row, "Description", getattr(row, "Name", None))
                if pid is not None and desc:
                    out.append({"id": int(pid), "name": desc})

        else:
            return jsonify([]), 400

    return jsonify(out)


@staff_bp.post("/add_school_to_funder")
@login_required
def add_school_to_funder():
    data = request.get_json(silent=True) or {}
    moe       = data.get("MoeNumber")
    term      = data.get("Term")
    year      = data.get("CalendarYear")
    funder    = data.get("FunderID")
    provider  = data.get("ProviderID")   # NEW

    # Validate
    if not all([moe, term, year, funder]):
        return jsonify(ok=False, error="Missing required fields."), 400
    # If your proc requires ProviderID, include it in the check:
    # if provider is None: return jsonify(ok=False, error="ProviderID is required."), 400

    # Authorize (FUN only)
    if session.get("user_role") != "FUN":
        return jsonify(ok=False, error="Forbidden"), 403

    try:
        with get_db_engine().begin() as conn:
            # Call your stored proc; include @ProviderID
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific
                        @Term       = :term,
                        @Year       = :yr,
                        @MOENumber  = :moe,
                        @FunderID   = :fid,
                        @ProviderID = :pid,
                        @request    = :req
                """),
                {
                    "term": int(term),
                    "yr":   int(year),
                    "moe":  int(moe),
                    "fid":  int(funder),
                    "pid":  int(provider) if provider is not None else None,
                    # IMPORTANT: use the exact action string your proc expects
                    "req":  "AddSchoolFunder"
                }
            )

            # Optional: read a returned (ok, message)
            try:
                row = result.fetchone()
                if row is not None:
                    ok  = row[0] if len(row) > 0 else True
                    msg = row[1] if len(row) > 1 else None
                    if str(ok).lower() in ("0", "false", "none"):
                        return jsonify(ok=False, error=msg or "Stored procedure reported failure."), 400
            except Exception:
                # No result set returned; assume success if no exception thrown
                pass

        return jsonify(ok=True)

    except Exception as e:
        msg = str(e)
        if "2627" in msg or "2601" in msg:
            return jsonify(ok=False, error="This school/term/year is already linked to this funder."), 409
        return jsonify(ok=False, error=msg), 500

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
        desc = session.get("desc")

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
                inviter_desc=desc
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
ROLECODE_MAP = {
    "Funder":   "FUN",
    "Provider": "PRO",
    "Group":    "GRP",
    "School":   "SCH",   # only if your proc supports it
}

def _call_get_entities(entity_type: str):
    """
    Call the /get_entities route to retrieve a list of {id, name}.
    Reuses all your role-based logic already implemented there.
    """
    try:
        base = request.host_url.rstrip("/")
        resp = requests.get(
            f"{base}/get_entities",
            params={"entity_type": entity_type},
            # forward cookies so the endpoint sees the same session/role
            cookies=request.cookies,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()  # [{id, name}, ...]
    except Exception as e:
        print("❌ /get_entities call failed:", e)
        return []


@staff_bp.route("/StaffeLearning", methods=["GET", "POST"])
@login_required
def staff_eLearning():
    try:
        print("📥 Route '/StaffeLearning' called")

        engine = get_db_engine()
        user_role  = session.get("user_role")   # "ADM","FUN","PRO","GRP"
        user_id    = session.get("user_id")
        user_email = session.get("user_email")
        desc  = session.get("desc")
        user_admin = session.get("user_admin")

        # If you want GRP users to access even if not admin, allow GRP here:
        

        # --- Determine selection intent per role ---
        selected_entity_type = request.args.get("entity_type")
        selected_entity_id   = request.args.get("entity_id")

        if user_role == "PRO":
            # Provider users are locked to their provider
            selected_entity_type = "Provider"
            selected_entity_id   = str(user_id)

        elif user_role == "FUN":
            # Funder users can view Funder or Provider (your template shows both)
            if not selected_entity_type:
                selected_entity_type = "Funder"
            if not selected_entity_id and selected_entity_type == "Funder":
                selected_entity_id = str(user_id)

        elif user_role == "ADM":
            # Admins default to Funder; must choose an entity before rendering results
            selected_entity_type = selected_entity_type or "Funder"
            # selected_entity_id may be blank until user clicks View

        elif user_role == "GRP":
            # Group users are locked to Group
            selected_entity_type = "Group"
            # selected_entity_id may be blank; we’ll populate from /get_entities

        else:
            # Unknown/unsupported role — show empty state
            flash("Please select an entity to view staff eLearning.", "warning")
            return render_template(
                "staff_elearning.html",
                staff_eLearning_data={},
                course_ids=[],
                selected_entity_type="Funder",
                selected_entity_id=None,
                entity_list=[],
                name="Staff eLearning",
                user_role=user_role
            )

        print(f"🔍 user_role: {user_role}, user_id: {user_id}, email: {user_email}, desc: {desc}")
        print(f"🔽 selected_entity_type = {selected_entity_type}, selected_entity_id = {selected_entity_id}")

        # --- Load entities for the dropdown via /get_entities ---
        entity_list = _call_get_entities(selected_entity_type) if selected_entity_type else []

        # If no entity chosen yet, pick a sensible default (esp. for GRP)
        if not selected_entity_id:
            if user_role in ("GRP", "FUN", "ADM"):
                if entity_list:
                    # For FUN+Funder-type, prefer “self” if present; otherwise first
                    if user_role == "FUN" and selected_entity_type == "Funder":
                        # try to find self
                        self_row = next((e for e in entity_list if str(e.get("id")) == str(user_id)), None)
                        selected_entity_id = str((self_row or entity_list[0]).get("id"))
                    else:
                        selected_entity_id = str(entity_list[0].get("id"))
                else:
                    # No entities available -> show empty state
                    flash("No entities available for your selection.", "warning")
                    return render_template(
                        "staff_elearning.html",
                        staff_eLearning_data={},
                        course_ids=[],
                        selected_entity_type=selected_entity_type,
                        selected_entity_id=None,
                        entity_list=entity_list,
                        name="Staff eLearning",
                        user_role=user_role
                    )

        # Ensure string ID downstream
        selected_entity_id = str(selected_entity_id)

        try:
            with engine.connect().execution_options(timeout=150) as conn:
                # --- Fetch eLearning records via your proc ---
                role_code = ROLECODE_MAP.get(selected_entity_type)
                if not role_code:
                    raise ValueError(f"Unsupported entity type: {selected_entity_type}")

                print(f"📚 Fetching eLearning for {selected_entity_type} ({role_code}) ID={selected_entity_id}")
                el_rows = conn.execute(
                    text("EXEC FlaskGetStaffeLearning :RoleType, :ID, :Email"),
                    {"RoleType": role_code, "ID": selected_entity_id, "Email": user_email}
                ).fetchall()
                print(f"📦 Retrieved {len(el_rows)} eLearning rows.")
                if el_rows:
                    print("🧪 First row keys:", list(el_rows[0]._mapping.keys()))
                # --- Active courses ---
                active_courses = conn.execute(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = 'ActiveCourses'")
                ).fetchall()

            # Flatten active course IDs
            course_ids = [str(r.ELearningCourseID) for r in active_courses]
            print(f"🎓 Active course IDs: {course_ids}")

            # Group results by staff email
            grouped = {}
            for r in el_rows:
                email = r.Email
                if email not in grouped:
                    grouped[email] = {
                        "Email": email,
                        "FirstName": r.FirstName,
                        "Surname": r.Surname,
                        "Courses": {}
                    }
                grouped[email]["Courses"][str(r.CourseID)] = {
                    "CourseName": r.CourseName,
                    "Status": r.Status
                }

            # Pick label for selected entity
            selected_name = next((e["name"] for e in entity_list if str(e["id"]) == selected_entity_id),
                                 desc if user_role == "PRO" else "Selected")
            if grouped:
                # peek a couple entries
                import itertools
                sample = dict(itertools.islice(grouped.items(), 0, 2))
                print("🔎 Sample staff_eLearning_data keys:", list(sample.keys()))
            return render_template(
                "staff_elearning.html",
                staff_eLearning_data=grouped,
                course_ids=course_ids,
                selected_entity_type=selected_entity_type,
                selected_entity_id=selected_entity_id,
                entity_list=entity_list,
                name=selected_name,
                user_role=user_role
            )

        except Exception:
            print("❌ Error rendering staff_elearning.html:")
            print(traceback.format_exc())
            return "500 Template Error", 500

    except Exception:
        print("❌ Error in /StaffeLearning:")
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


@staff_bp.route("/send_elearning_reminder", methods=["POST"])
@login_required
def send_elearning_reminder():
    try:
        email = request.form["email"]
        firstname = request.form["firstname"]
        requested_by = request.form["requested_by"]
        from_org = request.form["from_org"]

        # Query course status
        engine = get_db_engine()
        with engine.begin() as conn:
            result = conn.execute(text("EXEC FlaskGetUserELearningStatus :email"), {"email": email})
            course_statuses = [(row.ELearningCourseName, row.ELearningStatus) for row in result]


        send_elearning_reminder_email(mail, email, firstname, requested_by, from_org, course_statuses)
        flash(f"📧 eLearning reminder sent to {firstname}.", "info")

    except Exception as e:
        import traceback
        traceback.print_exc()
       
        flash("❌ Failed to send eLearning reminder.", "danger")

    selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
    selected_entity_id = request.form.get("entity_id") or request.args.get("entity_id")
    return redirect(url_for('staff_bp.staff_maintenance', entity_type=selected_entity_type, entity_id=selected_entity_id))

