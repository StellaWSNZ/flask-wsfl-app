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
        #print("📍 Entered funder_dashboard route")
        engine = get_db_engine()
        user_role = session.get("user_role")
        is_admin = session.get("user_admin") == 1
        funder_id = session.get("user_id") or session.get("funder_id")

        entity_type = request.form.get("entity_type") or session.get("entity_type") or "Funder"
        session["entity_type"] = entity_type

        #print(f"🧠 user_role: {user_role}, is_admin: {is_admin}, funder_id: {funder_id}, entity_type: {entity_type}")

        # ✅ MOE SchoolOverview
        if user_role == "MOE":
            user_email = session.get("user_email") or "unknown@example.com"
            school_id = session.get("user_id")
            selected_year = int(request.form.get("year", session.get("nearest_year", 2024)))
            selected_term = int(request.form.get("term", session.get("nearest_term", 1)))

          #  print(f"📚 MOE School ID: {school_id}, Year: {selected_year}, Term: {selected_term}")

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

           # print(f"📊 class_df.columns: {class_df.columns.tolist()}")
           # print(f"👨‍🏫 staff_df.shape: {staff_df.shape}")

            available_years = sorted(class_df.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
            available_terms = sorted(class_df.get("Term", pd.Series(dtype=int)).dropna().unique())
          #  print(f"📅 available_years: {available_years}, available_terms: {available_terms}")

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

        # ✅ Other user roles (Funder/Provider/Admin)
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

        elif user_role == "PRO":
            with engine.begin() as conn:
                providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"}))
                all_providers = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]}
                                 for row in providers_result if row._mapping["ProviderID"] == funder_id]
            funder_dropdown = all_providers

        #print(f"📥 funder_dropdown: {funder_dropdown}")

        # Determine selected funder/provider ID
        if user_role in ["ADM", "FUN"]:
            funder_val = request.form.get("funder_id")
            if funder_val and funder_val.isdigit():
                selected_funder_id = int(funder_val)
                session["selected_funder_id"] = selected_funder_id
            elif session.get("selected_funder_id"):
                selected_funder_id = session["selected_funder_id"]
        elif user_role == "PRO":
            selected_funder_id = funder_id

        #print(f"🎯 selected_funder_id: {selected_funder_id}")

        if not selected_funder_id:
            return render_template("overview.html", elearning=[], schools=[], selected_year=None, selected_term=None,
                                   available_years=[], available_terms=[], no_elearning=True, no_schools=True,
                                   summary_string=None, user_role=user_role, funder_list=funder_dropdown,
                                   selected_funder_id=None, all_funders=all_funders, all_providers=all_providers,
                                   entity_type=entity_type, title="Overview")

        # Funder/Provider metadata
        funder_desc = session.get("desc")
        search_list = all_providers if entity_type == "Provider" else all_funders
        for item in search_list:
            if int(item["id"]) == int(selected_funder_id):
                funder_desc = item["name"]
                break

        # Choose procedure
        if user_role == "PRO" or (user_role in ["ADM", "FUN"] and entity_type == "Provider"):
            proc = "FlaskGetSchoolSummaryByProvider"
            id_param_name = "ProviderID"
            elearning_proc = "FlaskGetProviderELearningStatus"
        else:
            proc = "FlaskGetSchoolSummaryByFunder"
            id_param_name = "FunderID"
            elearning_proc = "FlaskGetFunderELearningStatus"

        print(f"🔄 proc: {proc}, elearning_proc: {elearning_proc}, id_param: {id_param_name}")

        with engine.begin() as conn:
            elearning_df = pd.read_sql(
                text(f"EXEC {elearning_proc} @{id_param_name} = :id_val, @Email = :email"),
                conn,
                params={"id_val": selected_funder_id, "email": session.get("user_email") or "unknown@example.com"}
            )
            school_df_all = pd.read_sql(
                text(f"EXEC {proc} @{id_param_name} = :id_val, @CalendarYear = :CalendarYear, @Term = :Term, @Email = :Email"),
                conn,
                params={"id_val": selected_funder_id, "CalendarYear": None, "Term": None,
                        "Email": session.get("user_email") or "unknown@example.com"}
            )

       #print(f"📚 school_df_all.shape: {school_df_all.shape}")
        #print(f"📗 elearning_df.columns: {elearning_df.columns.tolist()}")

        available_years = sorted(school_df_all.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
        available_terms = sorted(school_df_all.get("Term", pd.Series(dtype=int)).dropna().unique())
        #print(f"📆 available_years: {available_years}, available_terms: {available_terms}")

        selected_year = int(request.form.get("year", session.get("nearest_year", available_years[0] if available_years else 2025)))
        selected_term = int(request.form.get("term", session.get("nearest_term", available_terms[0] if available_terms else 1)))

        #print(f"📌 selected_year: {selected_year}, selected_term: {selected_term}")

        school_df = school_df_all[(school_df_all["CalendarYear"] == selected_year) &
                                  (school_df_all["Term"] == selected_term)]

        total_students = school_df["TotalStudents"].fillna(0).astype(int).sum()
        total_schools = school_df["SchoolName"].nunique()

        school_df = school_df.drop(columns=["TotalStudents", "CalendarYear", "Term"], errors="ignore")
        school_df = school_df.rename(columns={"SchoolName": "School", "NumClasses": "Number of Classes"})

        subject = f"{funder_desc} is" if user_role in ["ADM", "FUN"] else "You are"
        summary_string = f"{subject} delivering to <strong>{total_students:,}</strong> students across <strong>{total_schools}</strong> school{'s' if total_schools != 1 else ''} in <strong>Term {selected_term}</strong>, <strong>{selected_year}</strong>."

        title = f"{session.get('user_desc') or funder_desc} Overview" if user_role in ["PRO", "FUN"] else "Overview"

        return render_template("overview.html",
                               elearning=elearning_df.to_dict(orient="records"),
                               schools=school_df.to_dict(orient="records"),
                               selected_year=selected_year,
                               selected_term=selected_term,
                               available_years=available_years,
                               available_terms=available_terms,
                               no_elearning=elearning_df.empty,
                               no_schools=school_df.empty,
                               summary_string=summary_string,
                               user_role=user_role,
                               funder_list=funder_dropdown,
                               selected_funder_id=selected_funder_id,
                               all_funders=all_funders,
                               all_providers=all_providers,
                               entity_type=entity_type,
                               title=title)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Exception occurred: {e}")
        return "Internal Server Error", 500



@funder_bp.route("/get_entities")
@login_required
def get_entities():
    entity_type = request.args.get("entity_type")
    print("📌 entity_type =", entity_type)

    if not entity_type:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        if entity_type == "Funder":
            stmt = text("EXEC FlaskHelperFunctions @Request = :Request")
            result = conn.execute(stmt, {"Request": "FunderDropdown"})
            entities = [{"id": row._mapping["FunderID"], "name": row._mapping["Description"]} for row in result]

        elif entity_type == "Provider":
            if session.get("user_id"):
                stmt = text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID")
                result = conn.execute(stmt, {"Request": "ProvidersByFunder", "FunderID": session.get("user_id")})
            else:
                stmt = text("EXEC FlaskHelperFunctions @Request = :Request")
                result = conn.execute(stmt, {"Request": "ProviderDropdown"})
            entities = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]} for row in result]

        else:
            return jsonify([])

        #print(entities)
        return jsonify(entities)