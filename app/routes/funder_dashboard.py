from flask import Blueprint, render_template, request, session, redirect, jsonify, abort
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
import pandas as pd
import traceback

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
        print(search_list)
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
        school_df = school_df.rename(columns={"SchoolName": "School", "NumClasses": "Number of Classes"})
        print(entity_type)
        if "EditedClasses" in school_df.columns:
            school_df = school_df.drop(columns=["EditedClasses"], errors="ignore")

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
