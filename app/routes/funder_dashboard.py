from flask import Blueprint, render_template, request, session, redirect, jsonify, abort, url_for
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
import pandas as pd
import traceback
from collections import defaultdict

funder_bp = Blueprint("funder_bp", __name__)


@funder_bp.route('/Overview', methods=["GET", "POST"])
@login_required
def funder_dashboard():
    if session.get("user_admin") != 1:
        abort(403)

    try:
        engine = get_db_engine()
        user_role = session.get("user_role")
        is_admin = session.get("user_admin") == 1
        funder_id = session.get("user_id") or session.get("funder_id")

        entity_type = request.form.get("entity_type") or session.get("entity_type") or "Funder"
        session["entity_type"] = entity_type

        # MOE view
        if user_role == "MOE":
            user_email = session.get("user_email") or "unknown@example.com"
            school_id = session.get("user_id")
            selected_year = int(request.form.get("year", session.get("nearest_year", 2024)))
            selected_term = int(request.form.get("term", session.get("nearest_term", 1)))

            with engine.begin() as conn:
                class_df = pd.read_sql(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = :Request, @MOENumber = :SchoolID, @Term = :Term, @Year = :Year"),
                    conn,
                    params={"Request": "SchoolSummary", "SchoolID": school_id, "Term": selected_term, "Year": selected_year}
                )

                staff_df = pd.read_sql(
                    text("EXEC [FlaskHelperFunctions] @Request = :r, @Number = :sid"),
                    conn,
                    params={"r": "SchoolStaff", "sid": school_id}
                )

            available_years = sorted(class_df.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
            available_terms = sorted(class_df.get("Term", pd.Series(dtype=int)).dropna().unique())

            class_df = class_df.rename(columns={"YearLevel": "Year Level", "StudentCount": "Students",
                                                "ClassName": "Class Name", "TeacherName": "Teacher"})

            return render_template("school_overview.html",
                                   classes=class_df.to_dict(orient="records"),
                                   staff=staff_df.to_dict(orient="records"),
                                   available_years=available_years,
                                   available_terms=available_terms,
                                   selected_year=selected_year,
                                   selected_term=selected_term,
                                   no_classes=class_df.empty,
                                   title="School Overview")

        all_funders, all_providers = [], []
        funder_dropdown, selected_funder_id = [], None

        if user_role == "ADM":
            with engine.begin() as conn:
                funders_result = list(conn.execute(text("EXEC FlaskHelperFunctions 'AllFunders'")))
                providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions 'AllProviders'")))
                all_funders = [{"id": row._mapping["id"], "name": row._mapping["Description"]} for row in funders_result]
                all_providers = [{"id": row._mapping["id"], "name": row._mapping["Description"]} for row in providers_result]
            funder_dropdown = all_providers if entity_type == "Provider" else all_funders

        elif user_role == "FUN" and is_admin:
            with engine.begin() as conn:
                funders_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"}))
                all_funders = [{"id": row._mapping["FunderID"], "name": row._mapping["Description"]}
                               for row in funders_result if row._mapping["FunderID"] == funder_id]

                providers_result = list(conn.execute(
                    text("EXEC FlaskHelperFunctions :Request, :Number"),
                    {"Request": "ProvidersByFunder", "Number": funder_id}
                ))
                all_providers = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]}
                                 for row in providers_result]
            funder_dropdown = all_providers if entity_type == "Provider" else all_funders

        elif user_role in ["PRO", "GRP"]:
            if user_role == "PRO":
                with engine.begin() as conn:
                    providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"}))
                    all_providers = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]}
                                     for row in providers_result if row._mapping["ProviderID"] == funder_id]
            elif user_role == "GRP":
                all_providers = [{"id": e["id"], "desc": e["name"]} for e in session.get("group_entities", {}).get("PRO", [])]
            funder_dropdown = all_providers

        has_groups = False
        if user_role == "ADM":
            with engine.begin() as conn:
                result = conn.execute(text("EXEC FlaskGetAllGroups"))
                has_groups = result.first() is not None
        elif user_role == "FUN":
            with engine.begin() as conn:
                result = conn.execute(text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"), {"fid": funder_id})
                has_groups = result.first() is not None
        elif user_role == "GRP":
            has_groups = len(session.get("group_entities", {}).get("PRO", [])) > 0

        if user_role in ["ADM", "FUN", "GRP"]:
            funder_val = request.form.get("funder_id")
            if funder_val and funder_val.isdigit():
                selected_funder_id = int(funder_val)
                session["selected_funder_id"] = selected_funder_id
            elif session.get("selected_funder_id"):
                selected_funder_id = session["selected_funder_id"]
        elif user_role == "PRO":
            selected_funder_id = funder_id

        if not selected_funder_id:
            return render_template("overview.html", eLearning=[], schools=[], selected_year=None, selected_term=None,
                                   available_years=[], available_terms=[], no_eLearning=True, no_schools=True,
                                   summary_string=None, user_role=user_role, funder_list=funder_dropdown,
                                   selected_funder_id=None, all_funders=all_funders, all_providers=all_providers,
                                   entity_type=entity_type, title="Overview", has_groups=has_groups)

        entity_desc = session.get("desc")
        search_list = all_providers if entity_type == "Provider" else all_funders
        print(entity_desc)
        for item in search_list:
            if int(item["id"]) == int(selected_funder_id):
                entity_desc = item["name"]
                break

        if user_role == "PRO" or (user_role in ["ADM", "FUN", "GRP"] and entity_type == "Provider"):
            proc = "FlaskGetSchoolSummaryByProvider"
            id_param_name = "ProviderID"
            eLearning_proc = "FlaskGetProvidereLearningStatus"
        else:
            proc = "FlaskGetSchoolSummaryByFunder"
            id_param_name = "FunderID"
            eLearning_proc = "FlaskGetFundereLearningStatus"

        with engine.begin() as conn:
            eLearning_df = pd.read_sql(
                text(f"EXEC {eLearning_proc} @{id_param_name} = :id_val, @Email = :email"),
                conn,
                params={"id_val": selected_funder_id, "email": session.get("user_email") or "unknown@example.com"}
            )
            school_df_all = pd.read_sql(
                text(f"EXEC {proc} @{id_param_name} = :id_val, @CalendarYear = :CalendarYear, @Term = :Term, @Email = :Email"),
                conn,
                params={"id_val": selected_funder_id, "CalendarYear": None, "Term": None,
                        "Email": session.get("user_email") or "unknown@example.com"}
            )

        available_years = sorted(school_df_all.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
        available_terms = sorted(school_df_all.get("Term", pd.Series(dtype=int)).dropna().unique())

        selected_year = int(request.form.get("year", session.get("nearest_year", available_years[0] if available_years else 2025)))
        selected_term = int(request.form.get("term", session.get("nearest_term", available_terms[0] if available_terms else 1)))

        school_df = school_df_all[(school_df_all["CalendarYear"] == selected_year) &
                                  (school_df_all["Term"] == selected_term)]

        total_students = school_df["TotalStudents"].fillna(0).astype(int).sum()
        total_schools = school_df["SchoolName"].nunique()

        school_df = school_df.drop(columns=["TotalStudents", "CalendarYear", "Term"], errors="ignore")
        school_df = school_df.rename(columns={"SchoolName": "School", "NumClasses": "Number of Classes","EditedClasses":"Classes Edited"})
        print(entity_type)
        #if "EditedClasses" in school_df.columns:
        #    school_df = school_df.drop(columns=["EditedClasses"], errors="ignore")

        subject = f"{entity_desc} is" if user_role in ["ADM", "FUN"] else "You are"
        summary_string = f"{subject} delivering to <strong>{total_students:,}</strong> students across <strong>{total_schools}</strong> school{'s' if total_schools != 1 else ''} in <strong>Term {selected_term}</strong>, <strong>{selected_year}</strong>."
        title = f"{entity_desc or session.get('user_desc')} Overview"

        return render_template("overview.html",
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
                               funder_list=funder_dropdown,
                               selected_funder_id=selected_funder_id,
                               all_funders=all_funders,
                               all_providers=all_providers,
                               entity_type=entity_type,
                               title=title,
                               has_groups=has_groups)

    except Exception as e:
        traceback.print_exc()
        return "Internal Server Error", 500

@funder_bp.route("/get_entities")
@login_required
def get_entities():
    entity_type = request.args.get("entity_type")
    user_id = session.get("user_id")
    user_role = session.get("user_role")
    user_desc = session.get("desc")
    user_admin = session.get("user_admin")

    print(f"\n📥 /get_entities called")
    print(f"🧑 role = {user_role}, id = {user_id}, admin = {user_admin}")
    print(f"📦 entity_type = {entity_type}")

    engine = get_db_engine()
    entities = []

    with engine.begin() as conn:
        if entity_type == "Provider":
            if user_role == "PRO":
                print("🎯 Handling PRO role")
                entities = [{"id": user_id, "name": user_desc}]

            elif user_role == "GRP":
                print("🎯 Handling GRP role for Provider")
                raw_providers = session.get("group_entities", {}).get("PRO", [])
                print(f"🗂 raw_providers from session = {raw_providers}")
                entities = [{"id": e["id"], "name": e["name"]} for e in raw_providers]

            elif user_role == "FUN":
                print("🎯 Handling FUN role for Provider")
                stmt = text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID")
                result = conn.execute(stmt, {
                    "Request": "ProvidersByFunder",
                    "FunderID": user_id
                })
                entities = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]} for row in result]

            else:  # ADM or fallback
                print("🎯 Handling ADM role or default for Provider")
                stmt = text("EXEC FlaskHelperFunctions @Request = 'ProviderDropdown'")
                result = conn.execute(stmt)
                entities = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]} for row in result]

        elif entity_type == "Funder":
            if user_role == "FUN":
                print("🎯 Handling FUN role for Funder")
                entities = [{"id": user_id, "name": user_desc}]
            else:  # ADM or fallback
                print("🎯 Handling ADM role for Funder")
                stmt = text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'")
                result = conn.execute(stmt)
                entities = [{"id": row._mapping["FunderID"], "name": row._mapping["Description"]} for row in result]

        elif entity_type == "Group":
            print("🎯 Handling Group entity type")

            if user_role == "GRP":
                print("🔹 GRP user: loading from session")
                raw_groups = session.get("group_entities", {}).get("PRO", [])
                print(f"🗂 raw_groups = {raw_groups}")
                entities = [{"id": user_id, "name": user_desc} ]

            elif user_role == "ADM":
                print("🔹 ADM user: loading all groups via stored procedure")
                stmt = text("EXEC FlaskGetAllGroups")
                result = conn.execute(stmt)
                entities = [{"id": row._mapping["ID"], "name": row._mapping["Name"]} for row in result]

            elif user_role == "FUN":
                print("🔹 FUN user: loading groups restricted to funder")
                stmt = text("EXEC FlaskGetGroupsByFunder @FunderID = :fid")
                result = conn.execute(stmt, {"fid": user_id})
                entities = [{"id": row._mapping["ID"], "name": row._mapping["Name"]} for row in result]

    print(f"✅ Final entities being returned = {entities}\n")
    return jsonify(entities)

@funder_bp.route('/AdminDashboard', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    if session.get("user_role") != "ADM":
        print("❌ Access denied: user is not ADM")
        return redirect(url_for("home_bp.home"))

    print("✅ Access granted: ADM user")
    term = int(request.args.get("term", 2))
    year = int(request.args.get("year", 2025))
    threshold_raw = request.args.get("threshold", "0.5")

    try:
        threshold = float(threshold_raw)
    except ValueError:
        threshold = 0.5
        print(f"⚠️ Invalid threshold '{threshold_raw}' provided, defaulting to {threshold}")

    print(f"🔍 Filters: Term={term}, Year={year}, Threshold={threshold}")

    engine = get_db_engine()
    funder_data = []

    with engine.begin() as conn:
        result = conn.execute(text("EXEC FlaskHelperFunctions @Request = 'FunderDropdown'"))
        funders = [dict(row._mapping) for row in result]
        print(f"🧾 Found {len(funders)} funders")

        for funder in funders:
            funder_id = funder["FunderID"]
            funder_name = funder["Description"]
            print(f"\n➡️ Processing funder: {funder_name} (ID: {funder_id})")

            # Get school summary
            school_result = conn.execute(text("""
                EXEC FlaskGetSchoolSummaryByFunder 
                @FunderID = :fid, 
                @CalendarYear = :year, 
                @Term = :term, 
                @Email = :email,
                @Threshold = :threshold
            """), {
                "fid": funder_id,
                "year": year,
                "term": term,
                "email": session.get("user_email"),
                "threshold": threshold
            })

            raw_schools = [dict(row._mapping) for row in school_result]
            print(f"🏫 Raw schools returned: {len(raw_schools)}")

            filtered_schools = [
                {k: v for k, v in row.items() if k not in ["CalendarYear", "Term"]}
                for row in raw_schools
            ]
            print(f"✅ Filtered schools retained: {len(filtered_schools)}")

            try:
                elearning_result = conn.execute(text("""
                    EXEC flaskgetstaffelearning 
                    @RoleType = 'FUN', 
                    @ID = :fid, 
                    @Email = :email
                """), {
                    "fid": funder_id,
                    "email": session.get("user_email")
                })

                row_count = 0
                elearning_summary = defaultdict(lambda: {
                    "FirstName": "",
                    "Surname": "",
                    "Not Started": 0,
                    "In Progress": 0,
                    "Passed": 0,
                    "Completed": 0,
                    "Cancelled": 0
                })

                for row in elearning_result:
                    r = dict(row._mapping)
                    if r.get("Active", 0) != 1:
                        continue 
                    row_count += 1
                    email = r["Email"]
                    status = r["Status"]
                    course_name = r.get("CourseName", "Unnamed Course")  # ensure this column exists

                    elearning_summary[email]["FirstName"] = r["FirstName"]
                    elearning_summary[email]["Surname"] = r["Surname"]

                    # increment status count
                    if status in elearning_summary[email]:
                        elearning_summary[email][status] += 1
                    else:
                        elearning_summary[email][status] = 1

                    # append course name to *_Courses list
                    course_key = f"{status}_Courses"
                    if course_key not in elearning_summary[email]:
                        elearning_summary[email][course_key] = []
                    elearning_summary[email][course_key].append(course_name)


                print(f"👩‍🏫 eLearning records processed: {row_count}")
            except Exception as e:
                print(f"❌ Error fetching eLearning data for funder {funder_name}: {e}")
                import traceback
                traceback.print_exc()
                elearning_summary = {}

            print(f"👩‍🏫 eLearning records processed: {row_count}")
            print(f"🧑‍💻 Unique staff found: {len(elearning_summary)}")

            funder_data.append({
                "id": funder_id,
                "name": funder_name,
                "schools": filtered_schools,
                "elearning_summary": elearning_summary
            })

    print("✅ Finished processing all funders")
    try:
        return render_template(
            "admindashboard.html",
            funder_data=funder_data,
            user=session.get("email"),
            term=term,
            year=year,
            threshold=threshold
        )
    except Exception as e:
        print("❌ Error rendering template:", e)
        import traceback
        traceback.print_exc()
        return "Template rendering error", 500