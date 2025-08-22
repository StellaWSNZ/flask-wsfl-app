import sys
from flask import Blueprint, render_template, request, session, redirect, jsonify, abort, url_for
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
import pandas as pd
import traceback
from collections import defaultdict
from datetime import datetime
import traceback
import sys
from collections import defaultdict
from datetime import datetime
funder_bp = Blueprint("funder_bp", __name__)

@funder_bp.route('/Overview', methods=["GET", "POST"])
@login_required
def funder_dashboard():
    if session.get("user_admin") != 1:
        abort(403)

    try:
        engine = get_db_engine()
        user_role = session.get("user_role")
        is_admin  = session.get("user_admin") == 1
        user_id   = session.get("user_id")  # same as funder_id for FUN, provider_id for PRO, etc.

        # Persisted selection
        entity_type = request.form.get("entity_type") or session.get("entity_type") or "Funder"
        session["entity_type"] = entity_type
        entity_id_val = request.form.get("entity_id")

        # ----- Special MOE view -----
        if user_role == "MOE":
            user_email    = session.get("user_email") or "unknown@example.com"
            school_id     = session.get("user_id")
            selected_year = int(request.form.get("year", session.get("nearest_year", 2024)))
            selected_term = int(request.form.get("term", session.get("nearest_term", 1)))

            with engine.begin() as conn:
                # Main class summary for the chosen year/term
                class_df = pd.read_sql(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = :Request, @MOENumber = :SchoolID, @Term = :Term, @Year = :Year"),
                    conn,
                    params={"Request": "SchoolSummary", "SchoolID": school_id,
                            "Term": selected_term, "Year": selected_year}
                )
                # All options so dropdowns are populated
                class_df_all = pd.read_sql(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = :Request, @MOENumber = :SchoolID, @Term = :Term, @Year = :Year"),
                    conn,
                    params={"Request": "SchoolSummary", "SchoolID": school_id,
                            "Term": None, "Year": None}
                )
                # Staff list
                staff_df = pd.read_sql(
                    text("EXEC [FlaskHelperFunctions] @Request = :r, @Number = :sid"),
                    conn,
                    params={"r": "SchoolStaff", "sid": school_id}
                )

            # Normalize columns we rely on (class_df + class_df_all)
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

            # Dropdown options (from all data)
            def uniq_sorted(df, col, reverse=False):
                if df.empty or col not in df.columns: return []
                vals = pd.Series(df[col]).dropna().unique().tolist()
                out = []
                for v in vals:
                    try: out.append(int(v))
                    except: continue
                return sorted(out, reverse=reverse)
            print(class_df_all)
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

        # ----- Build dropdown lists for current entity_type -----
        all_funders, all_providers, all_groups = [], [], []

        with engine.begin() as conn:
            if user_role == "ADM":
                # Funders
                funders_result = list(conn.execute(text("EXEC FlaskHelperFunctions 'AllFunders'")))
                all_funders = [{"id": r._mapping["id"], "name": r._mapping["Description"]} for r in funders_result]
                # Providers
                providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions 'AllProviders'")))
                all_providers = [{"id": r._mapping["id"], "name": r._mapping["Description"]} for r in providers_result]
                # Groups (if any)
                groups_result = list(conn.execute(text("EXEC FlaskGetAllGroups")))
                all_groups = [{"id": r._mapping["ID"], "name": r._mapping["Name"]} for r in groups_result]
            elif user_role == "FUN":
                # Only own funder
                funders_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"}))
                all_funders = [
                    {"id": r._mapping["FunderID"], "name": r._mapping["Description"]}
                    for r in funders_result if r._mapping["FunderID"] == user_id
                ]
                # Providers for that funder
                providers_result = list(conn.execute(
                    text("EXEC FlaskHelperFunctions :Request, :Number"),
                    {"Request": "ProvidersByFunder", "Number": user_id}
                ))
                all_providers = [{"id": r._mapping["ProviderID"], "name": r._mapping["Description"]} for r in providers_result]
                # Groups belonging to funder
                groups_result = list(conn.execute(
                    text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"), {"fid": user_id}
                ))
                all_groups = [{"id": r._mapping["GroupID"], "name": r._mapping["Description"]} for r in groups_result]
            elif user_role == "PRO":
                # Self only
                providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"}))
                all_providers = [
                    {"id": r._mapping["ProviderID"], "name": r._mapping["Description"]}
                    for r in providers_result if r._mapping["ProviderID"] == user_id
                ]
            elif user_role == "GRP":
                # From session cache (set at login)
                ge = session.get("group_entities", {})
                # Commonly stored as {"PRO":[{id,name},...], "GRP":[{id,name},...]}
                all_providers = [{"id": e["id"], "name": e.get("name") or e.get("desc")} for e in ge.get("PRO", [])]
                all_groups    = [{"id": e["id"], "name": e.get("name") or e.get("desc")} for e in ge.get("GRP", [])]

        has_groups = (len(all_groups) > 0)

        # Current list for the dropdown in the template
        if entity_type == "Provider":
            entity_list = all_providers
        elif entity_type == "Group":
            entity_list = all_groups
        else:
            entity_list = all_funders

        # ----- Persist selected entity_id -----
        selected_entity_id = None
        if entity_id_val and str(entity_id_val).isdigit():
            selected_entity_id = int(entity_id_val)
            session["selected_entity_id"] = selected_entity_id
        elif session.get("selected_entity_id"):
            selected_entity_id = session["selected_entity_id"]
        else:
            # Default to self where applicable
            if user_role == "PRO" and entity_type == "Provider":
                selected_entity_id = user_id

        # If nothing picked yet, just render the shell
        if not selected_entity_id:
            return render_template(
                "overview.html",
                eLearning=[], schools=[],
                selected_year=None, selected_term=None,
                available_years=[], available_terms=[],
                no_eLearning=True, no_schools=True,
                summary_string=None, user_role=user_role,
                entity_list=entity_list,
                selected_entity_id=None,
                entity_type=entity_type,
                title="Overview",
                has_groups=has_groups
            )

        # Resolve description for title/summary
        entity_desc = next((x["name"] for x in entity_list if int(x["id"]) == int(selected_entity_id)), session.get("desc"))

        # ----- Choose procs by entity_type -----
        # Adjust these names if your stored procedures differ.
        if entity_type == "Provider" or user_role == "PRO":
            proc_summary    = "FlaskGetSchoolSummaryByProvider"
            id_param_name   = "ProviderID"
            proc_elearning  = "FlaskGetProvidereLearningStatus"
        elif entity_type == "Group":
            # If you don't have these group procs yet, swap to your actual names.
            proc_summary    = "FlaskGetSchoolSummaryByGroup"
            id_param_name   = "GroupID"
            proc_elearning  = "FlaskGetGroupeLearningStatus"
        else:
            proc_summary    = "FlaskGetSchoolSummaryByFunder"
            id_param_name   = "FunderID"
            proc_elearning  = "FlaskGetFundereLearningStatus"

        # ----- Pull data -----
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

        available_years = sorted(school_df_all.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
        available_terms = sorted(school_df_all.get("Term", pd.Series(dtype=int)).dropna().unique())

        # Pick nearest if not posted
        selected_year = int(request.form.get("year", session.get("nearest_year", available_years[0] if available_years else 2025)))
        selected_term = int(request.form.get("term", session.get("nearest_term", available_terms[0] if available_terms else 1)))

        school_df = school_df_all[(school_df_all["CalendarYear"] == selected_year) &
                                  (school_df_all["Term"] == selected_term)]

        total_students = school_df.get("TotalStudents", pd.Series(dtype=int)).fillna(0).astype(int).sum() if not school_df.empty else 0
        total_schools  = school_df.get("SchoolName", pd.Series(dtype=object)).nunique() if not school_df.empty else 0

        # Clean columns for display
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
        print(session.get("desc"))
        page_title = f"{entity_desc or session.get('desc') or ''} Overview"

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
            entity_list=entity_list,
            selected_entity_id=selected_entity_id,
            entity_type=entity_type,
            title=page_title,
            has_groups=has_groups
        )

    except Exception as e:
        traceback.print_exc()
        return "Internal Server Error", 500

@funder_bp.route("/get_entities")
@login_required
def get_entities():
    entity_type = request.args.get("entity_type")
    user_id = session.get("user_id")
    user_role = session.get("user_role")
    desc = session.get("desc")
    user_admin = session.get("user_admin")

    #print(f"\n📥 /get_entities called")
    #print(f"🧑 role = {user_role}, id = {user_id}, admin = {user_admin}")
    #print(f"📦 entity_type = {entity_type}")

    engine = get_db_engine()
    entities = []

    with engine.begin() as conn:
        if entity_type == "Provider":
            if user_role == "PRO":
                #print("🎯 Handling PRO role")
                entities = [{"id": user_id, "name": desc}]

            elif user_role == "GRP":
                #print("🎯 Handling GRP role for Provider")
                raw_providers = session.get("group_entities", {}).get("PRO", [])
               # print(f"🗂 raw_providers from session = {raw_providers}")
               # print(raw_providers)
                entities = [{"id": e["id"], "name": e["name"]} for e in raw_providers]

            elif user_role == "FUN":
                #print("🎯 Handling FUN role for Provider")
                stmt = text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID")
                result = conn.execute(stmt, {
                    "Request": "ProvidersByFunder",
                    "FunderID": user_id
                })
                entities = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]} for row in result]

            else:  # ADM or fallback
                #print("🎯 Handling ADM role or default for Provider")
                stmt = text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'")
                result = conn.execute(stmt)
                entities = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]} for row in result]

        elif entity_type == "Funder":
            if user_role == "FUN":
                #print("🎯 Handling FUN role for Funder")
                entities = [{"id": user_id, "name": desc}]
            else:  # ADM or fallback
                #print("🎯 Handling ADM role for Funder")
                stmt = text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'")
                result = conn.execute(stmt)
                entities = [{"id": row._mapping["FunderID"], "name": row._mapping["Description"]} for row in result]

        elif entity_type == "Group":
            #print("🎯 Handling Group entity type")

            if user_role == "GRP":
                #print("🔹 GRP user: loading from session")
                raw_groups = session.get("group_entities", {}).get("PRO", [])
                #print(f"🗂 raw_groups = {raw_groups}")
                entities = [{"id": user_id, "name": desc} ]

            elif user_role == "ADM":
                #print("🔹 ADM user: loading all groups via stored procedure")
                stmt = text("EXEC FlaskGetAllGroups")
                result = conn.execute(stmt)
                entities = [{"id": row._mapping["ID"], "name": row._mapping["Name"]} for row in result]

            elif user_role == "FUN":
                #print("🔹 FUN user: loading groups restricted to funder")
                stmt = text("EXEC FlaskGetGroupsByFunder @FunderID = :fid")
                result = conn.execute(stmt, {"fid": user_id})
                entities = [{"id": row._mapping["ID"], "name": row._mapping["Name"]} for row in result]
        elif entity_type == "School":
           # print("*")
            if user_role == "ADM":
              #  print("*")
                # ✅ Example: adjust request string if needed
                stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdown'")
                result = conn.execute(stmt)

                # ✅ Safely consume just the first result set
                rows = result.fetchall()
                entities = [
                    {"id": row._mapping["MOENumber"], "name": row._mapping["SchoolName"]}
                    for row in rows
                ]
            elif user_role == "PRO":
                stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownProvider', @Number = :fid")
                result = conn.execute(stmt, {"fid": user_id})
                
                # ✅ Safely consume just the first result set
                rows = result.fetchall()
                print(rows)
                entities = [
                    {"id": row._mapping["MOENumber"], "name": row._mapping["SchoolName"]}
                    for row in rows
                ]
            elif user_role == "GRP":
                stmt = text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownGroup', @Number = :fid")
                result = conn.execute(stmt, {"fid": user_id})
                
                # ✅ Safely consume just the first result set
                rows = result.fetchall()
                print(rows)
                entities = [
                    {"id": row._mapping["MOENumber"], "name": row._mapping["SchoolName"]}
                    for row in rows
                ]
            elif user_role == "MOE":
                #print("🎯 Handling FUN role for Funder")
                entities = [{"id": user_id, "name": desc}]
            else:
                # Optional: restrict non-ADM access or return empty
                entities = []
    #print(f"✅ Final entities being returned = {entities}\n")
    return jsonify(entities)

    
@funder_bp.route('/FullOverview', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    if session.get("user_role") != "ADM":
        return redirect(url_for("home_bp.home"))

 

    try:
        print("📌 Admin Dashboard route hit")
        term = int(request.args.get("term", 2))
        year = int(request.args.get("year", 2025))
        threshold_raw = request.args.get("threshold", "50")
        entity_type = request.args.get("entity_type", None)  # "Funder" or "Provider"
        form_submitted = entity_type is not None

        try:
            threshold = float(threshold_raw) / 100  # Convert % to decimal
        except ValueError:
            threshold = 0.85
        engine = get_db_engine()
        funder_data = []

        with engine.begin() as conn:
            if entity_type == "Funder":
                result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'"))
            else:
                result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'"))
            entity_options = [dict(row._mapping) for row in result]

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

            if entity_type == "Funder":
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
                    if not r.get("Active"):  # Skip if course is not active (0 or None)
                        continue
                    fid = r["EntityID"]
                    email = r["Email"]
                    summary = elearning_grouped[fid][email]
                    summary["FirstName"] = r["FirstName"]
                    summary["Surname"] = r["Surname"]
                    status = r["Status"]
                    if status == "Enrolled":
                        status = "Not Started"

                    summary[status] += 1
                    summary.setdefault(f"{status}_Courses", []).append(r["CourseName"])
                    if r.get("SelfReviewSubmitted"):
                        summary["SelfReviewSubmitted"] = r["SelfReviewSubmitted"]
                        if r.get("RespondentID"):
                            summary["RespondentID"] = r["RespondentID"]
                schools_grouped = defaultdict(list)
                names = {}
                for row in schools:
                    fid = row["FunderID"]
                    names[fid] = row["FunderName"]
                    row["School Name"] = row.pop("SchoolName", None)
                    row["No. Classes"] = row.pop("NumClasses", None)
                    row["Edited Classes"] = row.pop("EditedClasses", None)
                    row["Total Students"] = row.pop("TotalStudents", None)
                    schools_grouped[fid].append({k: v for k, v in row.items() if k not in ["CalendarYear", "Term", "FunderID", "FunderName"]})

                for fid, schools in schools_grouped.items():
                    funder_data.append({
                        "id": fid,
                        "name": names.get(fid),
                        "schools": schools,
                        "elearning_summary": elearning_grouped.get(fid, {})
                    })

            elif entity_type == "Provider":
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
                    if not r.get("Active"):  # Skip if course is not active (0 or None)
                        continue
                    pid = r["EntityID"]
                    email = r["Email"]
                    summary = elearning_grouped[pid][email]
                    summary["FirstName"] = r["FirstName"]
                    summary["Surname"] = r["Surname"]
                    status = r["Status"]
                    if status == "Enrolled":
                        status = "Not Started"

                    summary[status] += 1
                    summary.setdefault(f"{status}_Courses", []).append(r["CourseName"])
                    if r.get("SelfReviewSubmitted"):
                        summary["SelfReviewSubmitted"] = r["SelfReviewSubmitted"]
                        if r.get("RespondentID"):
                            summary["RespondentID"] = r["RespondentID"]

                schools_grouped = defaultdict(list)
                names = {}
                for row in schools:
                    pid = row["ProviderID"]
                    names[pid] = row["ProviderName"]
                    row["School Name"] = row.pop("SchoolName", None)
                    row["No. Classes"] = row.pop("NumClasses", None)
                    row["Edited Classes"] = row.pop("EditedClasses", None)
                    row["Total Students"] = row.pop("TotalStudents", None)
                    schools_grouped[pid].append({k: v for k, v in row.items() if k not in ["CalendarYear", "Term", "ProviderID", "ProviderName"]})

                for pid, schools in schools_grouped.items():
                    funder_data.append({
                        "id": pid,
                        "name": names.get(pid),
                        "schools": schools,
                        "elearning_summary": elearning_grouped.get(pid, {})
                    })

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

    except Exception:
        traceback.print_exc(file=sys.stdout)
        print("🔴 AdminDashboard failed", flush=True)
        return "Internal Server Error", 500
