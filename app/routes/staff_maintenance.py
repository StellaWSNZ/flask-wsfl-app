# Standard library
import traceback
from datetime import datetime
from types import SimpleNamespace

# Third-party
import pandas as pd
import requests
from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    current_app,
)
from sqlalchemy import text

# Local
from app.extensions import mail
from app.routes.auth import login_required
from app.utils.custom_email import  send_elearning_reminder_email

from app.utils.database import get_db_engine, log_alert, get_years, get_terms
from app.utils.wsfl_email import send_account_invites

# Blueprint
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
            return render_template(
                "error.html",
                error="You are not authorised to view that page.",
                code=403
            ), 403

        # NEW: these are used by the new HTML/JS
        funder_id    = user_id if user_role == "FUN" else None
        
        ROLE_MAP = {"Funder": "FUN", "Provider": "PRO", "School": "MOE", "Group": "GRP"}

        selected_entity_type = (request.form.get("entity_type") or request.args.get("entity_type") or "").strip() or None
        selected_entity_id   = (request.form.get("entity_id")   or request.args.get("entity_id")   or "").strip() or None

        has_groups = False
        group_list = []

        # ---------- Group list / has_groups ----------
        try:
            with get_db_engine().begin() as conn:
                if user_role == "ADM":
                    result = conn.execute(text("EXEC FlaskGetAllGroups"))
                    group_list = [{"id": row.ID, "name": row.Name} for row in result]
                    has_groups = len(group_list) > 0
                elif user_role == "FUN":
                    result = conn.execute(text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"), {"fid": user_id})
                    group_list = [{
                        "id": getattr(row, "GroupID", getattr(row, "ID", None)),
                        "name": getattr(row, "Description", getattr(row, "Name", "")),
                    } for row in result]
                    has_groups = len(group_list) > 0
                elif user_role == "GRP":
                    group_list = [{"id": user_id, "name": desc}]
                    has_groups = True
        except Exception as e:
            # best-effort: log and keep going (page can still render)
            log_alert(
                email=user_email,
                role=user_role,
                entity_id=user_id,
                link=url_for("staff_bp.staff_maintenance", _external=True),
                message=f"Staff: load groups failed: {e}\n{traceback.format_exc()}"[:4000],
            )

        # ---------- Default entity type ----------
        if not selected_entity_type:
            if user_role == "PRO": selected_entity_type = "Provider"
            elif user_role == "MOE": selected_entity_type = "School"
            elif user_role == "GRP": selected_entity_type = "Group"
            elif user_role == "FUN": selected_entity_type = "Funder"
            else: selected_entity_type = "Provider"

        # ---------- Default entity id per role ----------
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
        
        # ---------- Load selected entity + staff + hidden staff ----------
        if selected_entity_type and selected_entity_id:
            try:
                with get_db_engine().connect() as conn:
                    # Name
                    result = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :id, @Text = :type"),
                        {"Request": "GetEntityDescription", "id": int(selected_entity_id), "type": selected_entity_type}
                    )
                    selected_entity_name = result.scalar()

                    role_type = ROLE_MAP.get(selected_entity_type, user_role)
                    target_id = int(selected_entity_id)

                    # Staff data
                    result = conn.execute(
                        text("EXEC FlaskGetStaffDetails @RoleType = :role, @ID = :id, @Email = :email"),
                        {"role": role_type, "id": target_id, "email": user_email}
                    )
                    rows = result.fetchall()
                    staff_data = pd.DataFrame(rows, columns=result.keys())
                    columns = result.keys()

                    # Hidden staff
                    hidden_result = conn.execute(
                        text("EXEC FlaskGetHiddenStaff @EntityType = :etype, @EntityID = :eid"),
                        {"etype": role_type, "eid": target_id}
                    )
                    hidden_staff = [dict(row._mapping) for row in hidden_result]
                    
                    allowed_popup = 0

                    if role_type == "ADM":
                        allowed_popup = 1

                    elif role_type == "FUN":
                        row = conn.execute(
                            text("EXEC dbo.FlaskHelperFunctions @Request = :Request, @Number = :Number"),
                            {"Request": "Popup", "Number": funder_id}
                        ).fetchone()

                        # row[0] is safest if you SELECT a single scalar
                        allowed_popup = int(row[0]) if row and row[0] is not None else 0


            except Exception as e:
                # Log DB failure for this section and still render page (with blanks)
                log_alert(
                    email=user_email,
                    role=user_role,
                    entity_id=target_id if target_id is not None else user_id,
                    link=url_for("staff_bp.staff_maintenance", _external=True),
                    message=f"Staff: data fetch failed for {selected_entity_type} {selected_entity_id}: {e}\n{traceback.format_exc()}"[:4000],
                )
                # Keep defaults: selected_entity_name may be None; staff_data/hidden_staff empty

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
            allowed_popup=allowed_popup,
            
            data=staff_data.to_dict(orient="records"),
            columns=columns,
            name=(desc or "") + "'s Staff eLearning",
            user_role=user_role,
            user_admin=(user_admin == 1),
            has_groups=has_groups,
            hidden_staff=hidden_staff,
            # NEW: required by the new HTML
            user_id=user_id,
            funder_id=funder_id,
            current_year = session.get("nearest_year"),
        current_term = session.get("nearest_term"),
        term = get_terms(),
        years = get_years()
        )

    except Exception as e:
        # Catch-all: log to DB and return 500
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.staff_maintenance", _external=True),
            message=f"Unhandled exception in /Staff: {e}\n{traceback.format_exc()}"[:4000],
        )
        current_app.logger.exception("❌ Exception in /Staff route:")
        return "500 Internal Server Error", 500


@staff_bp.get("/helper")
@login_required
def helper():
    req = request.args.get("request")           # e.g. "FilterSchoolID" or "ProvidersByFunderID"
    number = request.args.get("number")         # user_id for schools OR funder_id for providers
    if not req or not number:
        return jsonify([]), 400

    out = []
    try:
        with get_db_engine().connect() as conn:
            if req == "FilterSchoolID":
                # Returns schools this user can see
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :req, @Number = :num"),
                    {"req": "FilterSchoolID2", "num": int(number)}
                )
                for row in result:
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
                    # prefer strongly-typed column names if present
                    pid  = (
                        getattr(row, "ProviderID", None)
                        if hasattr(row, "ProviderID") else
                        getattr(row, "id", None)
                    )
                    desc = getattr(row, "Description", getattr(row, "Name", None))
                    if pid is not None and desc:
                        out.append({"id": int(pid), "name": desc})
                    
            else:
                return jsonify([]), 400

        return jsonify(out)

    except Exception as e:
        # Log and return generic error
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.helper", _external=True),
            message=f"/helper failed (request={req}, number={number}): {e}\n{traceback.format_exc()}"[:4000],
        )
        return jsonify([]), 500


@staff_bp.post("/add_school_to_funder")
@login_required
def add_school_to_funder():
    data = request.get_json(silent=True) or {}
    moe       = data.get("MoeNumber")
    term      = data.get("Term")
    year      = data.get("CalendarYear")
    funder    = data.get("FunderID")
    provider  = data.get("ProviderID")   # optional

    # Validate
    if not all([moe, term, year, funder]):
        return jsonify(ok=False, error="Missing required fields."), 400

    # Authorize (FUN only)
    if session.get("user_role") != "FUN":
        return jsonify(ok=False, error="Forbidden"), 403

    try:
        with get_db_engine().begin() as conn:
            # Call stored proc; include ProviderID if given
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
                    "req":  "AddSchoolFunder",
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
        # Specific duplicate key handling
        msg = str(e)
        if "2627" in msg or "2601" in msg:
            friendly = "This school/term/year is already linked to this funder."
            # Log duplicate as well (useful to track)
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("staff_bp.add_school_to_funder", _external=True),
                message=f"Duplicate link attempt in add_school_to_funder: {data}\n{msg}",
            )
            return jsonify(ok=False, error=friendly), 409

        # Log unexpected errors
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.add_school_to_funder", _external=True),
            message=f"add_school_to_funder failed payload={data}: {e}\n{traceback.format_exc()}"[:4000],
        )
        return jsonify(ok=False, error=msg), 500
    
    


@staff_bp.route('/update_staff', methods=['POST'])
@login_required
def update_staff():
    """
    Updates a staff member via stored proc FlaskUpdateStaffDetails.
    On any failure, logs to AUD_Alerts with full traceback (truncated).
    """
    # Defaults for redirect
    entity_type = request.form.get("entity_type")
    entity_id   = request.form.get("entity_id")

    try:
        user_id    = request.form.get("user_id")
        firstname  = request.form.get("firstname")
        lastname   = request.form.get("lastname")
        email      = request.form.get("email")
        old_email  = request.form.get("old_email")
        admin      = 1 if request.form.get("admin") == "1" else 0

        # call proc
        with get_db_engine().begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskUpdateStaffDetails 
                         @OldEmail         = :old_email,
                         @NewEmail         = :new_email,
                         @FirstName        = :firstname,
                         @LastName         = :lastname,
                         @Admin            = :admin,
                         @PerformedByEmail = :performed_by
                """),
                {
                    "old_email":   old_email,
                    "new_email":   email,
                    "firstname":   firstname,
                    "lastname":    lastname,
                    "admin":       admin,
                    "performed_by": session.get("user_email"),
                }
            )

        flash("Staff member updated successfully.", "success")

    except Exception as e:
        # Log to AUD_Alerts
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.update_staff", _external=True),
            message=f"update_staff failed for old_email={old_email}, new_email={email}: {e}\n{traceback.format_exc()}"[:4000],
        )
        flash("An error occurred while updating staff details.", "danger")

    # Work out where to return to (keeps your existing mapping)
    if not entity_type:
        entity_type = {
            "FUN": "Funder",
            "PRO": "Provider",
            "MOE": "School",
            "GRP": "Group",
        }.get(session.get("user_role"), "Provider")

    if not entity_id:
        entity_id = session.get("user_id")

    return redirect(url_for("staff_bp.staff_maintenance", entity_type=entity_type, entity_id=entity_id))


@staff_bp.route('/invite_user', methods=['POST']) 
@login_required
def invite_user():
    """
    Invites a user:
      1) Inserts via FlaskInviteUser
      2) Sends the invitation email(s) via wsfl_email.send_account_invites
    All exceptions are logged to AUD_Alerts and surfaced with a friendly flash.
    """
    # Defaults for redirect
    entity_type = request.form.get("entity_type")
    entity_id   = request.form.get("entity_id")
    try:
        # Read + normalize inputs
        email      = (request.form.get("email") or "").strip().lower()
        firstname  = (request.form.get("firstname") or "").strip() or "Staff"
        admin_raw  = request.form.get("admin", "0")
        admin      = 1 if admin_raw in ("1", "true", "True", True) else 0

        if not entity_type:
            entity_type = {
                "FUN": "Funder",
                "PRO": "Provider",
                "MOE": "School",
                "GRP": "Group",
            }.get(session.get("user_role"), None)

        if not entity_id:
            entity_id = session.get("user_id")

        if not email:
            raise ValueError("Missing email")

        user_role    = session.get("user_role") or "UNKNOWN"
        invited_by   = f"{session.get('user_firstname','')} {session.get('user_surname','')}".strip()
        inviter_desc = session.get("desc", "")  # e.g. "Aquatic Survival Skills" / school name

        # 1) DB insert
        with get_db_engine().begin() as conn:
            conn.execute(
                text(
                    "EXEC FlaskInviteUser "
                    "@Email = :email, "
                    "@Admin = :admin, "
                    "@PerformedByEmail = :performed_by"
                ),
                {
                    "email": email,
                    "admin": admin,
                    "performed_by": session.get("user_email"),
                },
            )

        # 2) Email via wsfl_email.send_account_invites
        recipients = [
            {
                "email": email,
                "firstname": firstname,
                "role": user_role,
            }
        ]

        send_account_invites(
            recipients=recipients,
            make_admin=(admin == 1),
            invited_by_name=invited_by or "Water Skills for Life",
            invited_by_org=inviter_desc or None,
        )

        flash(f"✅ Invitation sent to {email}.", "success")

    except Exception as e:
        # Log to AUD_Alerts (best effort)
        detail = f"invite_user failed for {request.form.to_dict(flat=True)}: {e}\n{traceback.format_exc()}"
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.invite_user", _external=True),
            message=detail[:4000],
        )
        flash("⚠️ Failed to invite user. Please check the logs.", "danger")

    # Always return to staff maintenance
    return redirect(
        url_for(
            'staff_bp.staff_maintenance',
            entity_type=entity_type,
            entity_id=entity_id,
            trigger_load=1,
        )
    )

@staff_bp.route('/add_staff', methods=['POST'])
@login_required
def add_staff():
    try:
        email         = request.form['email'].strip().lower()
        firstname     = request.form['first_name'].strip()
        lastname      = request.form['last_name'].strip()
        selected_role = session.get("user_role")
        selected_id   = request.form.get("entity_id") or session.get("user_id")
        account_status= request.form['account_status']   # "enable" / "disable"
        entity_type   = request.form.get("entity_type")
        entity_id     = request.form.get("entity_id") or selected_id
        admin_raw     = request.form.get("admin")        # "1" if ticked, else None
        active        = 1 if account_status == "enable" else 0

        # Map UI entity_type → role code used in SP
        if entity_type == "Provider":
            selected_role = "PRO"
        elif entity_type == "Funder":
            selected_role = "FUN"
        elif entity_type == "School":
            selected_role = "MOE"
        elif entity_type == "Group":
            selected_role = "GRP"
        else:
            selected_role = session.get("user_role")

        # --- admin rules: non-admin staff can't grant admin ----------------
        current_user_admin = session.get("user_admin") or 0
        if current_user_admin == 1:
            admin = 1 if admin_raw == "1" else 0
        else:
            admin = 0

        hashed_pw  = None   # invite-only; SP handles NULL
        send_email = (account_status == "enable")
        desc       = session.get("desc") or "Water Safety New Zealand"

        engine = get_db_engine()

        # Uniqueness check
        with engine.begin() as conn:
            existing = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                {"Request": "CheckEmailExists", "Text": email}
            ).fetchone()

            if existing:
                flash("⚠️ Email already exists.", "warning")
                return redirect(url_for(
                    'staff_bp.staff_maintenance',
                    entity_type=entity_type,
                    entity_id=entity_id,
                    trigger_load=1
                ))

        # Insert
        with engine.begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskInsertUser 
                         @Email        = :email,
                         @HashPassword = :hash,
                         @Role         = :role,
                         @ID           = :id,
                         @FirstName    = :firstname,
                         @Surname      = :surname,
                         @Admin        = :admin,
                         @Active       = :active
                """),
                {
                    "email":     email,
                    "hash":      hashed_pw,
                    "role":      selected_role,
                    "id":        selected_id,
                    "firstname": firstname,
                    "surname":   lastname,
                    "admin":     admin,
                    "active":    active
                }
            )

        flash(f"✅ User {email} created.", "success")

        # Optional welcome email via NEW helper
        if send_email and active == 1:
            try:
                recipients = [{
                    "email": email,
                    "first_name": firstname,
                    "last_name": lastname,
                    "role": selected_role,
                }]

                invited_by_name = f"{session.get('user_firstname')} {session.get('user_surname')}"
                invited_by_org  = desc 

                send_account_invites(
                    recipients=recipients,
                    make_admin=bool(admin),
                    invited_by_name=invited_by_name,
                    invited_by_org=invited_by_org,
                )
            except Exception as mail_e:
                # Log email failure but keep success UX
                log_alert(
                    email=session.get("user_email"),
                    role=session.get("user_role"),
                    entity_id=session.get("user_id"),
                    link=url_for("staff_bp.add_staff", _external=True),
                    message=f"add_staff: email send failed for {email}: {mail_e}\n{traceback.format_exc()}"[:4000],
                )
                flash("User created, but the invite email could not be sent.", "warning")

        return redirect(url_for(
            'staff_bp.staff_maintenance',
            entity_type=entity_type,
            entity_id=entity_id,
            trigger_load=1
        ))

    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.add_staff", _external=True),
            message=f"add_staff failed for {request.form.to_dict(flat=True)}: {e}\n{traceback.format_exc()}"[:4000],
        )
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
        current_app.logger.exception(f"❌ Error in /disable_user: {e}")

    return redirect(url_for('staff_bp.staff_maintenance', entity_type=entity_type, entity_id=entity_id, trigger_load=1))
@staff_bp.route("/get_active_courses")
@login_required
def get_active_courses():
    try:
        with get_db_engine().connect() as conn:
            result = conn.execute(text("EXEC [FlaskHelperFunctionsSpecific] @Request = 'ActiveCourses'"))
            rows = [dict(row._mapping) for row in result]
            return jsonify(rows)
    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.get_active_courses", _external=True),
            message=f"/get_active_courses failed: {e}\n{traceback.format_exc()}"[:4000],
        )
        return jsonify([]), 500

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
        current_app.logger.exception("❌ /get_entities call failed:", e)
        return []

@staff_bp.route("/StaffeLearning", methods=["GET"])
@login_required
def staff_eLearning():
    try:
        engine      = get_db_engine()
        user_role   = session.get("user_role")
        user_id     = session.get("user_id")
        user_email  = session.get("user_email")
        desc        = session.get("desc")

        # What did the user actually send in the query string?
        selected_entity_type = request.args.get("entity_type")
        selected_entity_id   = request.args.get("entity_id")
        has_query_params     = bool(request.args)  # True only after user hits "View"

        # ---- Role → default entity_type / entity_id (but no auto for ADM on first load) ----
        if user_role == "PRO":
            # Provider always sees their own provider
            selected_entity_type = "Provider"
            selected_entity_id   = str(user_id)

        elif user_role == "FUN":
            # Default type = Funder; default ID = self, but only if nothing chosen yet
            if not selected_entity_type:
                selected_entity_type = "Funder"
            if not selected_entity_id and selected_entity_type == "Funder":
                selected_entity_id = str(user_id)

        elif user_role == "ADM":
            # Admin: default to "Funder" type, but DO NOT auto-pick an ID on first load
            selected_entity_type = selected_entity_type or "Funder"

        elif user_role == "GRP":
            # Group users always see their own group; they don't pick
            selected_entity_type = "Group"
            selected_entity_id = str(user_id)
        else:
            flash("Please select an entity to view staff eLearning.", "warning")
            return render_template(
                "staff_elearning.html",
                staff_eLearning_data={},
                course_ids=[],
                selected_entity_type="Funder",
                selected_entity_id=None,
                entity_list=[],
                name="Staff eLearning",
                user_role=user_role,
            )

        # ---- Load dropdown entities via existing helper ----
        entity_list = _call_get_entities(selected_entity_type) if selected_entity_type else []

        # ---- If there is still no selected_entity_id, decide what to do ----
        if not selected_entity_id:
            # Case 1: first page load, no query string → just show the filters, no data
            if not has_query_params and user_role == "ADM":
                return render_template(
                    "staff_elearning.html",
                    staff_eLearning_data={},
                    course_ids=[],
                    selected_entity_type=selected_entity_type,
                    selected_entity_id=None,
                    entity_list=entity_list,
                    name="Staff eLearning",
                    user_role=user_role,
                )

            # Case 2: group users (or others) might still have a single entity
            if user_role in ("GRP", "FUN") and entity_list:
                # for FUN/GRP we’re happy to pick the first entity if needed
                if user_role == "FUN" and selected_entity_type == "Funder":
                    self_row = next((e for e in entity_list if str(e.get("id")) == str(user_id)), None)
                    selected_entity_id = str((self_row or entity_list[0]).get("id"))
                else:
                    selected_entity_id = str(entity_list[0].get("id"))
            else:
                # No entity_id and nothing sensible to default to
                flash("No entities available for your selection.", "warning")
                return render_template(
                    "staff_elearning.html",
                    staff_eLearning_data={},
                    course_ids=[],
                    selected_entity_type=selected_entity_type,
                    selected_entity_id=None,
                    entity_list=entity_list,
                    name="Staff eLearning",
                    user_role=user_role,
                )

        # Normalise to str for comparisons
        selected_entity_id = str(selected_entity_id)
        if user_role == "ADM":
            # ADM must explicitly pick from dropdown; don't allow URL-tamper IDs
            if has_query_params and selected_entity_id and entity_list:
                allowed_ids = {str(e.get("id")) for e in entity_list if e.get("id") is not None}
                if selected_entity_id not in allowed_ids:
                    flash("Invalid selection.", "warning")
                    return redirect(url_for("staff_bp.staff_eLearning"))

        else:
            # For PRO/FUN/GRP/SCH (etc), still enforce allowed list if we have one
             if selected_entity_id and entity_list:
                allowed_ids = {str(e.get("id")) for e in entity_list if e.get("id") is not None}
                if selected_entity_id not in allowed_ids:
                    flash("You are no authorised to view eLearning records for this entity.", "warning")
                    # stable fallback: first visible entity from dropdown list
                    selected_entity_id = str(entity_list[0].get("id"))
                    return redirect(url_for("staff_bp.staff_eLearning", entity_type=selected_entity_type))

        try:
            ROLECODE_MAP = {
                "Funder":   "FUN",
                "Provider": "PRO",
                "Group":    "GRP",
                "School":   "MOE",
            }
            role_code = ROLECODE_MAP.get(selected_entity_type)
            if not role_code:
                raise ValueError(f"Unsupported entity type: {selected_entity_type}")

            with engine.connect().execution_options(timeout=150) as conn:
                el_rows = conn.execute(
                    text("EXEC FlaskGetStaffeLearning :RoleType, :ID, :Email"),
                    {"RoleType": role_code, "ID": selected_entity_id, "Email": user_email},
                ).fetchall()

                active_courses = conn.execute(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = 'ActiveCourses'")
                ).fetchall()

            course_ids = [str(r.ELearningCourseID) for r in active_courses]

            grouped = {}
            for r in el_rows:
                em = r.Email
                if em not in grouped:
                    grouped[em] = {
                        "Email": em,
                        "FirstName": r.FirstName,
                        "Surname": r.Surname,
                        "Courses": {},
                    }
                grouped[em]["Courses"][str(r.CourseID)] = {
                    "CourseName": r.CourseName,
                    "Status": r.Status,
                }

            selected_name = next(
                (e["name"] for e in entity_list if str(e["id"]) == selected_entity_id),
                desc if user_role == "PRO" else "Selected",
            )

            return render_template(
                "staff_elearning.html",
                staff_eLearning_data=grouped,
                course_ids=course_ids,
                selected_entity_type=selected_entity_type,
                selected_entity_id=selected_entity_id,
                entity_list=entity_list,
                name=selected_name,
                user_role=user_role,
            )

        except Exception as inner_e:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("staff_bp.staff_eLearning", _external=True),
                message=(
                    f"staff_eLearning inner failure for entity "
                    f"{selected_entity_type}/{selected_entity_id}: {inner_e}\n{traceback.format_exc()}"
                )[:4000],
            )
            return "500 Template Error", 500

    except Exception as outer_e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.staff_eLearning", _external=True),
            message=f"staff_eLearning outer failure: {outer_e}\n{traceback.format_exc()}"[:4000],
        )
        return "500 Template Error", 500
    

@staff_bp.route("/hide_user", methods=["POST"])
@login_required
def hide_user():
    email = request.form.get("email")
    selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
    selected_entity_id   = request.form.get("entity_id")   or request.args.get("entity_id")

    try:
        with get_db_engine().begin() as conn:
            conn.execute(text("EXEC FlaskDeactivateUser @Email = :email"), {"email": email})
        flash(f"{email} has been hidden from all staff views.", "success")
    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.hide_user", _external=True),
            message=f"hide_user failed for {email}: {e}\n{traceback.format_exc()}"[:4000],
        )
        flash("Failed to hide the user. Please contact support.", "danger")

    return redirect(url_for('staff_bp.staff_maintenance',
                            entity_type=selected_entity_type, entity_id=selected_entity_id))
@staff_bp.route("/unhide_user", methods=["POST"])
@login_required
def unhide_user():
    email = request.form.get("email")
    selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
    selected_entity_id   = request.form.get("entity_id")   or request.args.get("entity_id")

    try:
        with get_db_engine().begin() as conn:
            conn.execute(text("EXEC FlaskUnhideUserByEmail @Email = :email"), {"email": email})
        flash(f"{email} has been unhidden.", "success")
    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.unhide_user", _external=True),
            message=f"unhide_user failed for {email}: {e}\n{traceback.format_exc()}"[:4000],
        )
        flash("Something went wrong while unhiding the user.", "danger")

    return redirect(url_for('staff_bp.staff_maintenance',
                            entity_type=selected_entity_type, entity_id=selected_entity_id))

@staff_bp.route("/send_elearning_reminder", methods=["POST"])
@login_required
def send_elearning_reminder():
    selected_entity_type = request.form.get("entity_type") or request.args.get("entity_type")
    selected_entity_id   = request.form.get("entity_id")   or request.args.get("entity_id")

    try:
        email        = request.form["email"]
        firstname    = request.form["firstname"]
        requested_by = request.form["requested_by"]
        from_org     = request.form.get("from_org") or "WSNZ"

        with get_db_engine().begin() as conn:
            result = conn.execute(text("EXEC FlaskGetUserELearningStatus :email"), {"email": email})
            course_statuses = [(row.ELearningCourseName, row.ELearningStatus) for row in result]

        send_elearning_reminder_email(mail, email, firstname, requested_by, from_org, course_statuses)
        flash(f"📧 eLearning reminder sent to {firstname}.", "info")

    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=url_for("staff_bp.send_elearning_reminder", _external=True),
            message=f"send_elearning_reminder failed for {request.form.to_dict(flat=True)}: {e}\n{traceback.format_exc()}"[:4000],
        )
        flash("❌ Failed to send eLearning reminder.", "danger")

    return redirect(url_for('staff_bp.staff_maintenance',
                            entity_type=selected_entity_type, entity_id=selected_entity_id))
