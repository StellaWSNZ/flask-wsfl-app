from flask import Blueprint, render_template, request, session, redirect, jsonify
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
import pandas as pd
import traceback

funder_bp = Blueprint("funder_bp", __name__)

@funder_bp.route('/Overview', methods=["GET", "POST"])
@login_required
def funder_dashboard():
    try:
        engine = get_db_engine()
        user_role = session.get("user_role")
        is_admin = session.get("user_admin") == 1
        funder_id = session.get("user_id") or session.get("funder_id")

        entity_type = request.form.get("entity_type") or session.get("entity_type") or "Funder"
        session["entity_type"] = entity_type

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

                providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request, :Number"),
                                                     {"Request": "ProvidersByFunder", "Number": funder_id}))
                all_providers = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]}
                                 for row in providers_result]

            funder_dropdown = all_providers if entity_type == "Provider" else all_funders

        elif user_role == "PRO":
            with engine.begin() as conn:
                providers_result = list(conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"}))
                all_providers = [{"id": row._mapping["ProviderID"], "name": row._mapping["Description"]}
                                 for row in providers_result if row._mapping["ProviderID"] == funder_id]

            funder_dropdown = all_providers

        if user_role in ["ADM", "FUN"]:
            funder_val = request.form.get("funder_id")
            if funder_val and funder_val.isdigit():
                selected_funder_id = int(funder_val)
                session["selected_funder_id"] = selected_funder_id
            elif session.get("selected_funder_id"):
                selected_funder_id = session["selected_funder_id"]

        elif user_role == "PRO":
            selected_funder_id = funder_id

        if not selected_funder_id:
            return render_template("overview.html", elearning=[], schools=[], selected_year=None, selected_term=None,
                                   available_years=[], available_terms=[], no_elearning=True, no_schools=True,
                                   summary_string=None, user_role=user_role, funder_list=funder_dropdown,
                                   selected_funder_id=None, all_funders=all_funders, all_providers=all_providers,
                                   entity_type=entity_type, title="Overview")

        funder_desc = session.get("desc")
        search_list = all_providers if entity_type == "Provider" else all_funders
        for item in search_list:
            if int(item["id"]) == int(selected_funder_id):
                funder_desc = item["name"]
                break

        if user_role == "PRO" or (user_role in ["ADM", "FUN"] and entity_type == "Provider"):
            proc = "FlaskGetSchoolSummaryByProvider"
            id_param_name = "ProviderID"
            elearning_proc = "FlaskGetProviderELearningStatus"
        else:
            proc = "FlaskGetSchoolSummaryByFunder"
            id_param_name = "FunderID"
            elearning_proc = "FlaskGetFunderELearningStatus"

        with engine.begin() as conn:
            elearning_df = pd.read_sql(text(f"EXEC {elearning_proc} @{id_param_name} = :id_val, @Email = :email"), conn,
                                       params={"id_val": selected_funder_id, "email": session.get("user_email") or "unknown@example.com"})
            school_df_all = pd.read_sql(text(f"EXEC {proc} @{id_param_name} = :id_val, @CalendarYear = :CalendarYear, @Term = :Term, @Email = :Email"), conn,
                                        params={"id_val": selected_funder_id, "CalendarYear": None, "Term": None,
                                                "Email": session.get("user_email") or "unknown@example.com"})

        available_years = sorted(school_df_all.get("CalendarYear", pd.Series(dtype=int)).dropna().unique(), reverse=True)
        available_terms = sorted(school_df_all.get("Term", pd.Series(dtype=int)).dropna().unique())

        selected_year = int(request.form.get("year", session.get("nearest_year", available_years[0] if available_years else 2025)))
        selected_term = int(request.form.get("term", session.get("nearest_term", available_terms[0] if available_terms else 1)))

        school_df = school_df_all[(school_df_all["CalendarYear"] == selected_year) & (school_df_all["Term"] == selected_term)]
        total_students = school_df["TotalStudents"].fillna(0).astype(int).sum()
        total_schools = school_df["SchoolName"].nunique()

        school_df = school_df.drop(columns=["TotalStudents", "CalendarYear", "Term"], errors="ignore")
        school_df = school_df.rename(columns={"SchoolName": "School", "NumClasses": "Number of Classes"})

        subject = f"{funder_desc} is" if user_role in ["ADM", "FUN"] else "You are"
        summary_string = f"{subject} delivering to <strong>{total_students:,}</strong> students across <strong>{total_schools}</strong> school{'s' if total_schools != 1 else ''} in <strong>Term {selected_term}</strong>, <strong>{selected_year}</strong>."

        title = f"{session.get('user_desc')} Overview" if user_role in ["PRO", "FUN"] else "Overview"

        return render_template("overview.html", elearning=elearning_df.to_dict(orient="records"),
                               schools=school_df.to_dict(orient="records"),
                               selected_year=selected_year, selected_term=selected_term,
                               available_years=available_years, available_terms=available_terms,
                               no_elearning=elearning_df.empty, no_schools=school_df.empty,
                               summary_string=summary_string, user_role=user_role,
                               funder_list=funder_dropdown, selected_funder_id=selected_funder_id,
                               all_funders=all_funders, all_providers=all_providers,
                               entity_type=entity_type, title=title)

    except Exception as e:
        traceback.print_exc()
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
        elif entity_type == "Provider":
            funder_id = session.get("selected_funder_id") or session.get("user_id")  # fallback if needed
            stmt = text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :FunderID")
            result = conn.execute(stmt, {"Request": "ProvidersByFunder", "FunderID": funder_id})
        else:
            return jsonify([])

        entities = [{"id": row[0], "name": row[1]} for row in result]
        return jsonify(entities)
