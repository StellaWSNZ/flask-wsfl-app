from flask import Blueprint, current_app, render_template, request, session, redirect, jsonify, abort, url_for
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine, log_alert
import pandas as pd
from collections import defaultdict

funder_bp = Blueprint("funder_bp", __name__)
def compute_has_groups(engine, user_role, user_id):
    """Return True if the current user should see 'Provider Group' in the type dropdown."""
    try:
        if user_role == "GRP":
            ge = session.get("group_entities", {})
            return bool(ge.get("GRP"))
        with engine.begin() as conn:
            if user_role == "FUN":
                row = conn.execute(
                    text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"),
                    {"fid": user_id}
                ).fetchone()
                return bool(row)
            if user_role == "ADM":
                row = conn.execute(text("EXEC FlaskGetAllGroups")).fetchone()
                return bool(row)
        return False
    except Exception:
        # Fail-safe: don't break page if proc is missing
        return False

@funder_bp.route('/Overview', methods=["GET", "POST"])
@login_required
def funder_dashboard():
    if session.get("user_admin") != 1:
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    try:
        engine    = get_db_engine()
        user_role = session.get("user_role")
        user_id   = session.get("user_id")  # funder_id for FUN, provider_id for PRO, etc.

        # Persisted selection (type only; entity list loads via AJAX)
        entity_type = request.form.get("entity_type") or session.get("entity_type") or "Funder"
        session["entity_type"] = entity_type
        entity_id_val = request.form.get("entity_id")

        # ---------- Special MOE view ----------
        if user_role == "MOE":
            school_id     = user_id
            selected_year = int(request.form.get("year", session.get("nearest_year", 2024)))
            selected_term = int(request.form.get("term", session.get("nearest_term", 1)))

            with engine.begin() as conn:
                class_df = pd.read_sql(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific
                             @Request   = :Request,
                             @MOENumber = :SchoolID,
                             @Term      = :Term,
                             @Year      = :Year
                    """),
                    conn,
                    params={"Request": "SchoolSummary", "SchoolID": school_id,
                            "Term": selected_term, "Year": selected_year}
                )
                class_df_all = pd.read_sql(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific
                             @Request   = :Request,
                             @MOENumber = :SchoolID,
                             @Term      = :Term,
                             @Year      = :Year
                    """),
                    conn,
                    params={"Request": "SchoolSummary", "SchoolID": school_id,
                            "Term": None, "Year": None}
                )
                staff_df = pd.read_sql(
                    text("EXEC [FlaskHelperFunctions] @Request = :r, @Number = :sid"),
                    conn,
                    params={"r": "SchoolStaff", "sid": school_id}
                )

            def normalize(df):
                if df is None or df.empty:
                    return pd.DataFrame(columns=[
                        "ClassID","Class Name","Teacher","ClassSize","DistinctYearLevels","CalendarYear","Term"
                    ])
                cols = {c.lower(): c for c in df.columns}
                cn = cols.get("classname") or cols.get("class name") or "ClassName"
                tn = cols.get("teachername") or cols.get("teacher") or "TeacherName"
                sz = cols.get("classsize") or cols.get("students") or cols.get("studentcount") or "ClassSize"
                yl = cols.get("distinctyearlevels") or cols.get("yearlevel") or "DistinctYearLevels"
                cid= cols.get("classid") or "ClassID"
                cy = cols.get("calendaryear") or "CalendarYear"
                tm = cols.get("term") or "Term"
                df = df.rename(columns={
                    cn:"Class Name", tn:"Teacher", sz:"ClassSize",
                    yl:"DistinctYearLevels", cid:"ClassID", cy:"CalendarYear", tm:"Term"
                })
                df["ClassSize"] = pd.to_numeric(df.get("ClassSize", 0), errors="coerce").fillna(0).astype(int)
                if "Teacher" not in df.columns:
                    df["Teacher"] = ""
                return df

            class_df     = normalize(class_df)
            class_df_all = normalize(class_df_all)

            def uniq_sorted(df, col, reverse=False):
                if df.empty or col not in df.columns: return []
                vals = pd.Series(df[col]).dropna().unique().tolist()
                out = []
                for v in vals:
                    try: out.append(int(v))
                    except: continue
                return sorted(out, reverse=reverse)

            available_years = uniq_sorted(class_df_all, "CalendarYear", reverse=True)
            available_terms = uniq_sorted(class_df_all, "Term", reverse=False)

            return render_template(
                "school_overview.html",
                title="School Overview",
                classes=class_df.to_dict(orient="records"),
                staff=staff_df.to_dict(orient="records"),
                available_years=available_years,
                available_terms=available_terms,
                selected_year=selected_year,
                selected_term=selected_term,
                no_classes=class_df.empty,
            )

        # ---------- has_groups (no server-side entity lists) ----------
        has_groups = compute_has_groups(engine, user_role, user_id)

        # ---------- Resolve selected entity ----------
        selected_entity_id = None
        if entity_id_val and str(entity_id_val).isdigit():
            selected_entity_id = int(entity_id_val)
            session["selected_entity_id"] = selected_entity_id
        elif session.get("selected_entity_id"):
            selected_entity_id = session["selected_entity_id"]
        else:
            if user_role == "PRO" and entity_type == "Provider":
                selected_entity_id = user_id

        # If nothing picked yet: render shell
        if not selected_entity_id:
            return render_template(
                "overview.html",
                eLearning=[], schools=[],
                selected_year=None, selected_term=None,
                available_years=[], available_terms=[],
                no_eLearning=True, no_schools=True,
                summary_string=None,
                user_role=user_role,
                selected_entity_id=None,
                entity_type=entity_type,
                title="Overview",
                has_groups=has_groups
            )

        # ---------- Choose procs by entity_type ----------
        if entity_type == "Provider" or user_role == "PRO":
            proc_summary   = "FlaskGetSchoolSummaryByProvider"
            id_param_name  = "ProviderID"
            proc_elearning = "FlaskGetProvidereLearningStatus"
            name_proc_sql  = "EXEC FlaskGetNameByProviderID @ProviderID = :id"
        elif entity_type == "Group":
            proc_summary   = "FlaskGetSchoolSummaryByGroup"
            id_param_name  = "GroupID"
            proc_elearning = "FlaskGetGroupeLearningStatus"
            name_proc_sql  = "EXEC FlaskGetNameByGroupID @GroupID = :id"
        else:
            proc_summary   = "FlaskGetSchoolSummaryByFunder"
            id_param_name  = "FunderID"
            proc_elearning = "FlaskGetFundereLearningStatus"
            name_proc_sql  = "EXEC FlaskGetNameByFunderID @FunderID = :id"

        # ---------- Pull data ----------
        with engine.begin() as conn:
            eLearning_df = pd.read_sql(
                text(f"EXEC {proc_elearning} @{id_param_name} = :id_val, @Email = :email"),
                conn,
                params={"id_val": selected_entity_id, "email": session.get("user_email") or "unknown@example.com"}
            )
            school_df_all = pd.read_sql(
                text(f"EXEC {proc_summary} @{id_param_name} = :id_val, @CalendarYear = :CalendarYear, @Term = :Term, @Email = :Email"),
                conn,
                params={
                    "id_val": selected_entity_id,
                    "CalendarYear": None,
                    "Term": None,
                    "Email": session.get("user_email") or "unknown@example.com"
                }
            )
            try:
                name_row = conn.execute(text(name_proc_sql), {"id": selected_entity_id}).fetchone()
                entity_desc = (name_row and list(name_row)[0]) or session.get("desc")
            except Exception:
                entity_desc = session.get("desc")

        available_years = sorted(school_df_all.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
        available_terms = sorted(school_df_all.get("Term", pd.Series(dtype=int)).dropna().unique())

        selected_year = int(request.form.get("year", session.get("nearest_year", available_years[0] if available_years else 2025)))
        selected_term = int(request.form.get("term", session.get("nearest_term", available_terms[0] if available_terms else 1)))

        school_df = school_df_all[(school_df_all["CalendarYear"] == selected_year) &
                                  (school_df_all["Term"] == selected_term)]

        total_students = school_df.get("TotalStudents", pd.Series(dtype=int)).fillna(0).astype(int).sum() if not school_df.empty else 0
        total_schools  = school_df.get("SchoolName", pd.Series(dtype=object)).nunique() if not school_df.empty else 0

        school_df = school_df.drop(columns=["TotalStudents", "CalendarYear", "Term"], errors="ignore")
        school_df = school_df.rename(columns={
            "SchoolName":   "School",
            "NumClasses":   "Number of Classes",
            "EditedClasses":"Classes Edited"
        })

        subject = f"{entity_desc} is" if user_role in ["ADM","FUN"] else "You are"
        summary_string = (
            f"{subject} delivering to <strong>{total_students:,}</strong> students across "
            f"<strong>{total_schools}</strong> school{'s' if total_schools != 1 else ''} "
            f"in <strong>Term {selected_term}</strong>, <strong>{selected_year}</strong>."
        )

        page_title = f"{(entity_desc or session.get('desc') or '').strip()} Overview".strip() or "Overview"

        return render_template(
            "overview.html",
            eLearning=eLearning_df.to_dict(orient="records"),
            schools=school_df.to_dict(orient="records"),
            selected_year=selected_year,
            selected_term=selected_term,
            available_years=available_years,
            available_terms=available_terms,
            no_eLearning=eLearning_df.empty,
            no_schools=school_df.empty,
            summary_string=summary_string,
            user_role=user_role,
            selected_entity_id=selected_entity_id,
            entity_type=entity_type,
            title=page_title,
            has_groups=has_groups
        )

    except Exception:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=locals().get("selected_entity_id"),  # safe if unset
            link=request.url,
            message=f"funder_dashboard unhandled error: {e}"
        )
        current_app.logger.exception("Unhandled error in funder_dashboard")
        return "Internal Server Error", 500

@funder_bp.route("/get_entities")
@login_required
def get_entities():
    entity_type = request.args.get("entity_type")
    user_id     = session.get("user_id")
    user_role   = session.get("user_role")
    desc        = session.get("desc")
    user_admin  = session.get("user_admin")

    entities = []

    # quick guard for missing/unknown type
    if not entity_type:
        log_alert(
            email=session.get("user_email"),
            role=user_role,
            entity_id=user_id,
            link=request.url,
            message="get_entities: missing entity_type"
        )
        return jsonify([])

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            if entity_type == "Provider":
                try:
                    if user_role == "PRO":
                        entities = [{"id": user_id, "name": desc}]
                    elif user_role == "GRP":
                        raw_providers = session.get("group_entities", {}).get("PRO", [])
                        entities = [{"id": e["id"], "name": e["name"]} for e in raw_providers]
                    elif user_role == "FUN":
                        stmt = text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID")
                        result = conn.execute(stmt, {"Request": "ProvidersByFunder", "FunderID": user_id})
                        entities = [{"id": r._mapping["ProviderID"], "name": r._mapping["Description"]} for r in result]
                    else:  # ADM or fallback
                        stmt = text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'")
                        result = conn.execute(stmt)
                        entities = [{"id": r._mapping["ProviderID"], "name": r._mapping["Description"]} for r in result]
                except Exception as e:
                    log_alert(
                        email=session.get("user_email"),
                        role=user_role,
                        entity_id=user_id,
                        link=request.url,
                        message=f"get_entities Provider error: {e}"
                    )
                    entities = []

            elif entity_type == "Funder":
                try:
                    if user_role == "FUN":
                        entities = [{"id": user_id, "name": desc}]
                    else:  # ADM or fallback
                        stmt = text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'")
                        result = conn.execute(stmt)
                        entities = [{"id": r._mapping["FunderID"], "name": r._mapping["Description"]} for r in result]
                except Exception as e:
                    log_alert(
                        email=session.get("user_email"),
                        role=user_role,
                        entity_id=user_id,
                        link=request.url,
                        message=f"get_entities Funder error: {e}"
                    )
                    entities = []

            elif entity_type == "Group":
                try:
                    if user_role == "GRP":
                        entities = [{"id": user_id, "name": desc}]
                    elif user_role == "ADM":
                        stmt = text("EXEC FlaskGetAllGroups")
                        result = conn.execute(stmt)
                        entities = [{"id": r._mapping["ID"], "name": r._mapping["Name"]} for r in result]
                    elif user_role == "FUN":
                        stmt = text("EXEC FlaskGetGroupsByFunder @FunderID = :fid")
                        result = conn.execute(stmt, {"fid": user_id})
                        entities = [{"id": r._mapping["ID"], "name": r._mapping["Name"]} for r in result]
                    else:
                        entities = []
                except Exception as e:
                    log_alert(
                        email=session.get("user_email"),
                        role=user_role,
                        entity_id=user_id,
                        link=request.url,
                        message=f"get_entities Group error: {e}"
                    )
                    entities = []

            elif entity_type == "School":
                try:
                    if user_role == "ADM":
                        stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdown'")
                        result = conn.execute(stmt)
                        rows = result.fetchall()
                        entities = [{"id": r._mapping["MOENumber"], "name": r._mapping["SchoolName"]} for r in rows]

                    elif user_role == "PRO":
                        stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownProvider', @Number = :fid")
                        result = conn.execute(stmt, {"fid": user_id})
                        rows = result.fetchall()
                        entities = [{"id": r._mapping["MOENumber"], "name": r._mapping["SchoolName"]} for r in rows]

                    elif user_role == "FUN":
                        stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownFunder', @Number = :fid")
                        result = conn.execute(stmt, {"fid": user_id})
                        rows = result.fetchall()
                        entities = [{"id": r._mapping["MOENumber"], "name": r._mapping["SchoolName"]} for r in rows]

                    elif user_role == "GRP":
                        stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownGroup', @Number = :fid")
                        result = conn.execute(stmt, {"fid": user_id})
                        rows = result.fetchall()
                        entities = [{"id": r._mapping["MOENumber"], "name": r._mapping["SchoolName"]} for r in rows]

                    elif user_role == "MOE":
                        entities = [{"id": user_id, "name": desc}]
                    else:
                        entities = []
                except Exception as e:
                    log_alert(
                        email=session.get("user_email"),
                        role=user_role,
                        entity_id=user_id,
                        link=request.url,
                        message=f"get_entities School error: {e}"
                    )
                    entities = []

            else:
                # Unknown entity_type
                log_alert(
                    email=session.get("user_email"),
                    role=user_role,
                    entity_id=user_id,
                    link=request.url,
                    message=f"get_entities: unknown entity_type '{entity_type}'"
                )
                entities = []

        return jsonify(entities)

    except Exception as e:
        # top-level safety net
        log_alert(
            email=session.get("user_email"),
            role=user_role,
            entity_id=user_id,
            link=request.url,
            message=f"get_entities unhandled error: {e}"
        )
        return jsonify([]), 500



@funder_bp.get("/providers")
@login_required
def providers_by_funder():
    try:
        funder_id = request.args.get("funder_id", type=int)
        if not funder_id:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message="providers_by_funder: Missing or invalid funder_id"
            )
            return jsonify([]), 400

        engine = get_db_engine()

        with engine.begin() as conn:
            rows = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID"),
                {"Request": "ProvidersByFunder", "FunderID": funder_id}
            ).mappings().all()

        providers = [{"id": r["ProviderID"], "name": r["Description"]} for r in rows]
        return jsonify(providers)

    except Exception as e:
        # Log unexpected failure to DB and app logs
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=funder_id if 'funder_id' in locals() else None,
            link=request.url,
            message=f"providers_by_funder: {e}"
        )
        current_app.logger.exception("Unhandled error in providers_by_funder")
        return jsonify([]), 500
    
    

@funder_bp.route('/FullOverview', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    # Admins only
    if session.get("user_role") != "ADM":
        return redirect(url_for("home_bp.home"))

    # ---- Parse inputs with guards ----
    try:
        term = int(request.args.get("term", 2))
    except Exception:
        term = 2
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"admin_dashboard: invalid term query param; fell back to 2"
        )

    try:
        year = int(request.args.get("year", 2025))
    except Exception:
        year = 2025
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"admin_dashboard: invalid year query param; fell back to 2025"
        )

    threshold_raw = request.args.get("threshold", "50")
    entity_type = request.args.get("entity_type", None)  # "Funder" or "Provider"
    form_submitted = entity_type is not None

    try:
        threshold = float(threshold_raw) / 100  # Convert % to decimal
    except Exception:
        threshold = 0.85
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"admin_dashboard: invalid threshold '{threshold_raw}'; defaulted to 0.85"
        )

    try:
        current_app.logger.info("📌 Admin Dashboard route hit")

        engine = get_db_engine()
        funder_data = []

        with engine.begin() as conn:
            # Dropdown options for the form
            if entity_type == "Funder":
                result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'"))
            else:
                result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'"))

            entity_options = [dict(row._mapping) for row in result]

            # If the form hasn't been submitted yet, just render the shell
            if not form_submitted:
                return render_template(
                    "admindashboard.html",
                    funder_data=[],
                    entity_options=entity_options,
                    entity_type=entity_type,
                    term=term,
                    year=year,
                    threshold=threshold,
                    form_submitted=False
                )

            # -------- Pull data by entity_type --------
            if entity_type == "Funder":
                # School summary across all funders
                school_result = conn.execute(text("""
                    EXEC FlaskGetSchoolSummaryAllFunders 
                         @CalendarYear = :year, 
                         @Term = :term, 
                         @Email = :email,
                         @Threshold = :threshold
                """), {
                    "year": year,
                    "term": term,
                    "email": session.get("user_email"),
                    "threshold": threshold
                })
                schools = [dict(row._mapping) for row in school_result]

                # Staff eLearning for all funders
                elearning_result = conn.execute(text("""
                    EXEC FlaskGetStaffELearningAll 
                         @RoleType = 'FUN', 
                         @Email = :email, 
                         @SelfReview = 1
                """), {
                    "email": session.get("user_email")
                })
                elearning_rows = [dict(row._mapping) for row in elearning_result]

                elearning_grouped = defaultdict(lambda: defaultdict(lambda: {
                    "FirstName": "", "Surname": "",
                    "Not Started": 0, "In Progress": 0,
                    "Passed": 0, "Completed": 0,
                    "Cancelled": 0
                }))

                for r in elearning_rows:
                    if not r.get("Active"):  # skip inactive courses
                        continue
                    fid = r["EntityID"]
                    email = r["Email"]
                    summary = elearning_grouped[fid][email]
                    summary["FirstName"] = r.get("FirstName", "")
                    summary["Surname"] = r.get("Surname", "")
                    status = r.get("Status", "")
                    if status == "Enrolled":
                        status = "Not Started"
                    if status:
                        summary[status] += 1
                        summary.setdefault(f"{status}_Courses", []).append(r.get("CourseName", ""))
                    if r.get("SelfReviewSubmitted"):
                        summary["SelfReviewSubmitted"] = r["SelfReviewSubmitted"]
                        if r.get("RespondentID"):
                            summary["RespondentID"] = r["RespondentID"]

                schools_grouped = defaultdict(list)
                names = {}
                for row in schools:
                    fid = row["FunderID"]
                    names[fid] = row.get("FunderName")
                    # rename for display
                    row["School Name"] = row.pop("SchoolName", None)
                    row["No. Classes"] = row.pop("NumClasses", None)
                    row["Edited Classes"] = row.pop("EditedClasses", None)
                    row["Total Students"] = row.pop("TotalStudents", None)
                    # drop control cols
                    trimmed = {k: v for k, v in row.items()
                               if k not in ["CalendarYear", "Term", "FunderID", "FunderName"]}
                    schools_grouped[fid].append(trimmed)

                for fid, schools_list in schools_grouped.items():
                    funder_data.append({
                        "id": fid,
                        "name": names.get(fid),
                        "schools": schools_list,
                        "elearning_summary": elearning_grouped.get(fid, {})
                    })

            elif entity_type == "Provider":
                # School summary across all providers
                school_result = conn.execute(text("""
                    EXEC FlaskGetSchoolSummaryAllProviders 
                         @CalendarYear = :year, 
                         @Term = :term, 
                         @Email = :email,
                         @Threshold = :threshold
                """), {
                    "year": year,
                    "term": term,
                    "email": session.get("user_email"),
                    "threshold": threshold
                })
                schools = [dict(row._mapping) for row in school_result]

                # Staff eLearning for all providers
                elearning_result = conn.execute(text("""
                    EXEC FlaskGetStaffELearningAll 
                         @RoleType = 'PRO', 
                         @Email = :email, 
                         @SelfReview = 1
                """), {
                    "email": session.get("user_email")
                })
                elearning_rows = [dict(row._mapping) for row in elearning_result]

                elearning_grouped = defaultdict(lambda: defaultdict(lambda: {
                    "FirstName": "", "Surname": "",
                    "Not Started": 0, "In Progress": 0,
                    "Passed": 0, "Completed": 0,
                    "Cancelled": 0
                }))
                for r in elearning_rows:
                    if not r.get("Active"):
                        continue
                    pid = r["EntityID"]
                    email = r["Email"]
                    summary = elearning_grouped[pid][email]
                    summary["FirstName"] = r.get("FirstName", "")
                    summary["Surname"] = r.get("Surname", "")
                    status = r.get("Status", "")
                    if status == "Enrolled":
                        status = "Not Started"
                    if status:
                        summary[status] += 1
                        summary.setdefault(f"{status}_Courses", []).append(r.get("CourseName", ""))
                    if r.get("SelfReviewSubmitted"):
                        summary["SelfReviewSubmitted"] = r["SelfReviewSubmitted"]
                        if r.get("RespondentID"):
                            summary["RespondentID"] = r["RespondentID"]

                schools_grouped = defaultdict(list)
                names = {}
                for row in schools:
                    pid = row["ProviderID"]
                    names[pid] = row.get("ProviderName")
                    row["School Name"] = row.pop("SchoolName", None)
                    row["No. Classes"] = row.pop("NumClasses", None)
                    row["Edited Classes"] = row.pop("EditedClasses", None)
                    row["Total Students"] = row.pop("TotalStudents", None)
                    trimmed = {k: v for k, v in row.items()
                               if k not in ["CalendarYear", "Term", "ProviderID", "ProviderName"]}
                    schools_grouped[pid].append(trimmed)

                for pid, schools_list in schools_grouped.items():
                    funder_data.append({
                        "id": pid,
                        "name": names.get(pid),
                        "schools": schools_list,
                        "elearning_summary": elearning_grouped.get(pid, {})
                    })

            else:
                # Unknown / missing entity_type – render form again
                log_alert(
                    email=session.get("user_email"),
                    role=session.get("user_role"),
                    entity_id=None,
                    link=request.url,
                    message=f"admin_dashboard: missing/invalid entity_type '{entity_type}'"
                )
                return render_template(
                    "admindashboard.html",
                    funder_data=[],
                    entity_options=entity_options,
                    entity_type=entity_type,
                    term=term,
                    year=year,
                    threshold=threshold,
                    form_submitted=False
                )

        # Success render
        return render_template(
            "admindashboard.html",
            funder_data=funder_data,
            entity_options=entity_options,
            entity_type=entity_type,
            term=term,
            year=year,
            threshold=threshold,
            form_submitted=True
        )

    except Exception as e:
        # Log to DB + server logs, but never crash the request
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"admin_dashboard: {e}"
        )
        current_app.logger.exception("🔴 AdminDashboard failed")
        return "Internal Server Error", 500


import uuid
import traceback
import time
from flask import current_app, request, render_template, redirect, url_for, flash, abort, session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from werkzeug.exceptions import HTTPException
from app.utils.database import get_db_engine, log_alert  # if you have log_alert, great; else comment out
from app.routes.auth import login_required
# add this import near the top of the file
import json


def _parse_json_maybe(val):
    """Return a Python list from a SQL NVARCHAR/VARBINARY JSON array, or [] on failure."""
    if isinstance(val, list):
        return val
    if val is None:
        return []
    # handle bytes/memoryview
    if isinstance(val, (bytes, bytearray, memoryview)):
        try:
            val = bytes(val).decode("utf-8")
        except Exception:
            return []
    else:
        val = str(val)
    s = val.strip()
    if not s or s.lower() == "null":
        return []
    try:
        return json.loads(s)
    except Exception:
        current_app.logger.warning("JSON parse failed: %r", s, exc_info=True)
        return []


@funder_bp.route("/Schools", methods=["GET", "POST"])
@login_required
def funder_schools():
    """
    School Overview for funders/admins.
    - Loads schools for the selected funder
    - Builds per-school aggregates
    - Picks a default (year, term) if none supplied
    - Computes SelectedClasses per row for the chosen (year, term)
    """
    user_role = session.get("user_role")
    if user_role not in ("ADM", "FUN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("home_bp.home"))

    engine  = get_db_engine()
    user_id = session.get("user_id")

    funders = []
    selected_funder = None
    rows = []

    # --- 1) Pull selected filters from GET or POST; blank = choose latest below
    sel_term_raw = (request.values.get("term") or "").strip()
    sel_year_raw = (request.values.get("year") or "").strip()
    sel_term = int(sel_term_raw) if sel_term_raw.isdigit() else None
    sel_year = int(sel_year_raw) if sel_year_raw.isdigit() else None

    # Helper: safe JSON parser handling None/bytes
    def parse_list(val):
        try:
            import json as _json
            if val is None:
                return []
            if isinstance(val, (bytes, bytearray, memoryview)):
                val = bytes(val).decode("utf-8", errors="ignore")
            return _json.loads(str(val))
        except Exception:
            return []

    try:
        with engine.connect() as conn:
            # --- 2) Resolve selected funder
            if user_role == "ADM":
                funders = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :r"),
                    {"r": "FunderDropdown"}
                ).mappings().all()

                fid_raw = (request.form.get("FunderID") or "").strip()
                if fid_raw.isdigit():
                    selected_funder = int(fid_raw)
                elif funders:
                    selected_funder = int(funders[0]["FunderID"])
            else:
                # FUN role uses their own ID
                selected_funder = int(user_id) if user_id is not None else None

            if selected_funder is None:
                # Nothing to show if no funder can be resolved
                return render_template(
                    "funder_schools.html",
                    rows=[],
                    funders=funders,
                    selected_funder=None,
                    is_admin=(user_role == "ADM"),
                    selected_year=sel_year,
                    selected_term=sel_term,
                )

            # --- 3) Fetch data
            raw = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :r, @Number = :fid"),
                {"r": "FunderSchoolOverview", "fid": selected_funder}
            ).mappings().all()

            # --- 4) Build pre_rows + collect all (year, term) pairs to pick defaults
            all_year_terms = set()
            pre_rows = []

            for r in raw:
                class_counts = parse_list(r.get("ClassCountsJson"))

                # collect all available (year, term) pairs
                for c in class_counts:
                    y = c.get("CalendarYear")
                    t = c.get("Term")
                    if isinstance(y, int) and isinstance(t, int):
                        all_year_terms.add((y, t))

                pre_rows.append({
                    "MOENumber":        r.get("MOENumber"),
                    "SchoolName":       r.get("SchoolName"),
                    "ActiveUserCount":  int(r.get("ActiveUserCount", 0) or 0),
                    "HasActiveUsers":   int(r.get("HasActiveUsers", 0) or 0),
                    "Contacts":         parse_list(r.get("ContactsJson")),
                    "InactiveContacts": parse_list(r.get("InactiveContactsJson")),
                    "TotalClasses":     int(r.get("TotalClasses", 0) or 0),
                    "ClassCounts":      class_counts,
                })

            # --- 5) If no explicit selection, choose latest available (max year, then max term)
            if (sel_year is None or sel_term is None) and all_year_terms:
                latest_y, latest_t = sorted(
                    all_year_terms, key=lambda p: (p[0], p[1]), reverse=True
                )[0]
                if sel_year is None:
                    sel_year = latest_y
                if sel_term is None:
                    sel_term = latest_t

            # --- 6) Compute SelectedClasses for each row for chosen (sel_year, sel_term)
            # If no (year,term) was resolvable, this stays 0 and the UI will show "all time" badge.
            rows = []
            for r in pre_rows:
                selected_count = 0
                if isinstance(sel_year, int) and isinstance(sel_term, int):
                    for c in (r["ClassCounts"] or []):
                        if c.get("CalendarYear") == sel_year and c.get("Term") == sel_term:
                            selected_count += int(c.get("Count", 0) or 0)

                r["SelectedYear"] = sel_year
                r["SelectedTerm"] = sel_term
                r["SelectedClasses"] = selected_count
                rows.append(r)

    except Exception:
        err_id = uuid.uuid4().hex[:8]
        current_app.logger.error(f"[FunderSchools ERROR {err_id}]\n{traceback.format_exc()}")
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=None,
                link=request.url,
                message=f"FunderSchools ERROR {err_id}"
            )
        except Exception:
            pass
        flash(f"Unexpected error (ID {err_id}). Please try again.", "danger")
        return redirect(url_for("home_bp.home"))

    # --- 7) Render
    return render_template(
        "funder_schools.html",
        rows=rows,
        funders=funders,
        selected_funder=selected_funder,
        is_admin=(user_role == "ADM"),
        selected_year=sel_year,
        selected_term=sel_term,
    )
