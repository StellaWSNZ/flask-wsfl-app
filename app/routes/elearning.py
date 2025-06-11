from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.utils.database import get_db_engine
import pandas as pd
import json
from sqlalchemy import text
from app.routes.auth import login_required

elearning_bp = Blueprint("elearning_bp", __name__)

@elearning_bp.route("/elearning", methods=["GET", "POST"])
@login_required
def admin_elearning_upload():
    #print("*")
    #print(session.get("user_role"))
    if session.get("user_role") != "ADM":
        # flash("Access denied.", "danger")
        return redirect(url_for("main_bp.home"))

    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file:
            flash("Please upload a CSV file.", "warning")
            return redirect(request.url)

        try:
            df = pd.read_csv(file, encoding="windows-1252")
            df = df[["Email", "Course number", "Course enrolment status", "First name", "Last name"]].dropna()
            df.columns = ["Email", "CourseNumber", "EnrolmentStatus", "FirstName", "LastName"]
            json_payload = df.to_json(orient="records")
           #print(df.head())
            #print(json_payload[:500])
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(text("EXEC FlaskElearningUpdate @json=:json"), {"json": json_payload})

            flash("E-learning records successfully updated.", "success")
        except Exception as e:
            flash(f"Error processing file: {e}", "danger")

        return redirect(request.url)

    return render_template("elearning_upload.html")


@elearning_bp.route("/my-ip")
def get_my_ip():
    import requests
    return requests.get("https://api.ipify.org").text
