from flask import Blueprint, render_template, request, session, redirect, flash
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
import pandas as pd

funder_bp = Blueprint("funder_bp", __name__)

@funder_bp.route('/Overview', methods=["GET", "POST"])
@login_required
def funder_dashboard():
    engine = get_db_engine()
    user_role = session.get("user_role")
    is_admin = session.get("user_admin") == 1
    funder_id = session.get("user_id") or session.get("funder_id")

   # print("🔍 Session values:", dict(session))
   # print(f"🧑 Role: {user_role}, Admin: {is_admin}, Funder ID: {funder_id}")

    funder_dropdown = []
    selected_funder_id = None

    if user_role == "FUN" and is_admin and funder_id:
        selected_funder_id = funder_id

    elif user_role == "ADM":
        with engine.begin() as conn:
            result = conn.execute(text("EXEC FlaskHelperFunctions 'AllFunders'"))
            funder_dropdown = [{"id": row._mapping["id"], "name": row._mapping["Description"]} for row in result]
        # print(result)
        # Capture from POST or session fallback
        funder_val = request.form.get("funder_id")
        if funder_val and funder_val.isdigit():
            selected_funder_id = int(funder_val)
            session["selected_funder_id"] = selected_funder_id
        elif session.get("selected_funder_id"):
            selected_funder_id = session.get("selected_funder_id") 

        if not selected_funder_id:
            return render_template(
                "funder_dashboard.html",
                elearning=[],
                schools=[],
                selected_year=None,
                selected_term=None,
                available_years=[],
                available_terms=[],
                no_elearning=True,
                no_schools=True,
                summary_string=None,
                user_role=user_role,
                funder_list=funder_dropdown,
                selected_funder_id=None
            )

    if not selected_funder_id:
        return redirect("/")

    # Get funder description
    funder_desc = session.get("desc") if user_role == "FUN" else None
    if user_role == "ADM":
        for funder in funder_dropdown:
            if int(funder["id"]) == int(selected_funder_id):
                funder_desc = funder["name"]
                break

    # Load data
    with engine.begin() as conn:
        try:
            elearning_df = pd.read_sql(
                text("EXEC FlaskGetProviderELearningStatus @FunderID = :fid"),
                conn,
                params={"fid": selected_funder_id}
            )
        except Exception as e:
            print(f"❌ eLearning load error: {e}")
            elearning_df = pd.DataFrame()

        try:
            school_df_all = pd.read_sql(
                text("EXEC GetSchoolSummaryByFunder @FunderID = :f, @CalendarYear = NULL, @Term = NULL"),
                conn,
                params={"f": selected_funder_id}
            )
        except Exception as e:
            print(f"❌ School summary load error: {e}")
            school_df_all = pd.DataFrame()

    available_years = sorted(school_df_all["CalendarYear"].dropna().unique(), reverse=True)
    available_terms = sorted(school_df_all["Term"].dropna().unique())

    selected_year = int(request.form.get("year", session.get("nearest_year", available_years[0] if available_years else 2025)))
    selected_term = int(request.form.get("term", session.get("nearest_term", available_terms[0] if available_terms else 1)))

    school_df = school_df_all[
        (school_df_all["CalendarYear"] == selected_year) &
        (school_df_all["Term"] == selected_term)
    ]

    total_students = school_df["TotalStudents"].fillna(0).astype(int).sum()
    total_schools = school_df["SchoolName"].nunique()

    school_df = school_df.drop(columns=["TotalStudents", "CalendarYear", "Term"], errors="ignore")
    school_df = school_df.rename(columns={
        "SchoolName": "School",
        "NumClasses": "Number of Classes"
    })

    summary_string = ""
    if funder_desc:
        summary_string = (
            f"{funder_desc if user_role == 'ADM' else 'You'} "
            f"is delivering to <strong>{total_students:,}</strong> students across "
            f"<strong>{total_schools}</strong> school{'s' if total_schools != 1 else ''} "
            f"in <strong>Term {selected_term}</strong>, <strong>{selected_year}</strong>."
        )

    return render_template(
        "funder_dashboard.html",
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
        selected_funder_id=selected_funder_id
    )
