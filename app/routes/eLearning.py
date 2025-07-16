from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.utils.database import get_db_engine
import pandas as pd
import json
from sqlalchemy import text
from app.routes.auth import login_required
import pprint
eLearning_bp = Blueprint("eLearning_bp", __name__)

@eLearning_bp.route("/eLearning", methods=["GET", "POST"])
@login_required
def admin_eLearning_upload():
    if session.get("user_role") != "ADM":
        return redirect(url_for("main_bp.home"))

    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file:
            flash("Please upload an Excel file.", "warning")
            return redirect(request.url)

        try:
            # Read Excel (instead of CSV)
            df = pd.read_excel(file, dtype=str)

            # Rename and select needed columns
            df = df[["Email", "Course name", "Course number", "Course enrolment status"]].dropna()
            df.columns = ["Email", "CourseName", "CourseNumber", "EnrolmentStatus"]

            # Convert CourseNumber to integer safely
            df["CourseNumber"] = pd.to_numeric(df["CourseNumber"], errors="coerce").fillna(0).astype(int)
            print(df[["CourseNumber", "CourseName"]].drop_duplicates())

            json_payload = df.to_json(orient="records")
            print(json_payload)

            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("EXEC FlaskElearningUpdate :json, :Email"),
                    {
                        "json": json_payload,
                        "Email": session.get("user_email")
                    }
                )
                conn.commit()

            flash("E-learning records successfully updated.", "success")
        except Exception as e:
            flash(f"Error processing file: {e}", "danger")

        return redirect(request.url)



    return render_template("elearning_upload.html")


@eLearning_bp.route("/eLearning-guide")
@login_required
def eLearning_guide():
    try:
        user_email = session.get("user_email")
        user_email_alt = session.get("user_email_alt")

        print("📧 user_email:", user_email," w alt ",user_email_alt, " used for elearning")

        engine = get_db_engine()
        with engine.begin() as conn:
            result = conn.execute(
                text("EXEC GetUserElearningResults :Email, :AltEmail"),
                {"Email": user_email, "AltEmail":user_email_alt}
            )
            courses = [dict(row._mapping) for row in result.fetchall()]  # 💡 use _mapping here
            print("✅ Courses:", courses)

        return render_template("elearning_guide.html", courses=courses)

    except Exception as e:
        import traceback
        print("❌ Error in /eLearning-guide:", e)
        traceback.print_exc()
        return "An error occurred", 500
    
@eLearning_bp.route("/my-ip")
def get_my_ip():
    import requests
    return requests.get("https://api.ipify.org").text
