from flask import Blueprint, render_template, request, session
from sqlalchemy import text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
import pandas as pd

funder_bp = Blueprint("funder_bp", __name__)


@funder_bp.route('/funder_dashboard', methods=["GET", "POST"])
@login_required
def funder_dashboard():
    engine = get_db_engine()
    funder_id = session.get("user_id")
    print(f"\n🔍 funder_id from session: {funder_id}")

    elearning_df = pd.DataFrame()
    school_df = pd.DataFrame()
    school_df_all = pd.DataFrame()

    with engine.begin() as conn:
        # eLearning status
        try:
            print("🛠️ Running stored procedure: FlaskGetProviderELearningStatus")
            elearning_df = pd.read_sql(
                text("EXEC FlaskGetProviderELearningStatus @FunderID = :fid"),
                conn,
                params={"fid": funder_id}
            )
            print(f"✅ Retrieved {len(elearning_df)} eLearning records")
        except Exception as e:
            print(f"❌ Failed to retrieve eLearning data: {e}")

        # School summary (all years/terms)
        try:
            print("🛠️ Running stored procedure: GetSchoolSummaryByFunder")
            school_df_all = pd.read_sql(
                text("EXEC GetSchoolSummaryByFunder @FunderID = :f, @CalendarYear = NULL, @Term = NULL"),
                conn,
                params={"f": funder_id}
            )
            print(f"✅ Retrieved {len(school_df_all)} total school records")
            print(f"📦 Sample rows:\n{school_df_all.head().to_string(index=False)}")
        except Exception as e:
            print(f"❌ Failed to retrieve school summary data: {e}")
            school_df_all = pd.DataFrame()

    # Extract unique years and terms
    available_years = sorted(school_df_all["CalendarYear"].dropna().unique(), reverse=True)
    available_terms = sorted(school_df_all["Term"].dropna().unique())
    print(f"📅 Available years: {available_years}")
    print(f"🗓️ Available terms: {available_terms}")

    # Determine selected values
    if request.method == "POST":
        selected_year = int(request.form.get("year", available_years[0] if available_years else 2025))
        selected_term = int(request.form.get("term", available_terms[0] if available_terms else 1))
        print(f"📥 POST selected: year={selected_year}, term={selected_term}")
    else:
        selected_year = session.get("nearest_year", available_years[0] if available_years else 2025)
        selected_term = session.get("nearest_term", available_terms[0] if available_terms else 1)
        print(f"🧭 Default selected: year={selected_year}, term={selected_term}")

    # Filter results
    school_df = school_df_all[
        (school_df_all["CalendarYear"] == selected_year) &
        (school_df_all["Term"] == selected_term)
    ]
    print(f"🔎 Filtered school_df rows: {len(school_df)}")

    return render_template(
        "funder_dashboard.html",
        elearning=elearning_df.to_dict(orient="records"),
        schools=school_df.to_dict(orient="records"),
        selected_year=selected_year,
        selected_term=selected_term,
        available_years=available_years,
        available_terms=available_terms,
        no_elearning=elearning_df.empty,
        no_schools=school_df.empty
    )
