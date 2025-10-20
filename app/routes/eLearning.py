from flask import Blueprint, abort, current_app, render_template, request, redirect, url_for, flash, session
from app.utils.database import get_db_engine, log_alert
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
            # Read Excel (xlsx/xls)
            try:
                df = pd.read_excel(file, dtype=str, engine="openpyxl")
            except Exception:
                df = pd.read_excel(file, dtype=str)  # fallback if engine unavailable

            # Required columns
            required = {"Email", "Course name", "Course number", "Course enrolment status"}
            missing = required - set(df.columns)
            if missing:
                raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

            # Normalize
            df = df[["Email", "Course name", "Course number", "Course enrolment status"]].dropna(how="all")
            df.columns = ["Email", "CourseName", "CourseNumber", "EnrolmentStatus"]
            for c in ["Email", "CourseName", "EnrolmentStatus"]:
                df[c] = df[c].astype(str).str.strip()
            df["CourseNumber"] = pd.to_numeric(df["CourseNumber"], errors="coerce").fillna(0).astype(int)

            json_payload = df.to_json(orient="records")

            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("EXEC FlaskElearningUpdate :json, :Email"),
                    {"json": json_payload, "Email": session.get("user_email")}
                )
            flash("E-learning records successfully updated.", "success")

        except Exception as e:
            current_app.logger.exception("❌ eLearning upload failed")
            # ---- DB alert (guarded + truncated) ----
            try:
                log_alert(
                    email=(session.get("user_email") or session.get("email") or "")[:320],
                    role=(session.get("user_role") or "")[:10],
                    entity_id=session.get("user_id"),
                    link=str(request.url)[:2048],
                    message=f"eLearning upload error: {str(e)[:1800]}",
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log alert (eLearning upload): {log_err}")
            flash(f"Error processing file: {e}", "danger")

        return redirect(request.url)

    # GET
    try:
        return render_template("elearning_upload.html")
    except Exception as e:
        current_app.logger.exception("❌ elearning_upload template render failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"elearning_upload template error: {str(e)[:1500]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (elearning_upload render): {log_err}")
        return abort(500)


@eLearning_bp.route("/eLearning-guide")
@login_required
def eLearning_guide():
    try:
        user_email = session.get("user_email")
        user_email_alt = session.get("user_email_alt")

        engine = get_db_engine()
        with engine.begin() as conn:
            result = conn.execute(
                text("EXEC GetUserElearningResults :Email, :AltEmail"),
                {"Email": user_email, "AltEmail": user_email_alt}
            )
            courses = [dict(row._mapping) for row in result]

        return render_template("elearning_guide.html", courses=courses)

    except Exception as e:
        current_app.logger.exception("❌ /eLearning-guide failed")
        # ---- DB alert ----
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"eLearning_guide error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (eLearning_guide): {log_err}")
        flash("We couldn’t load your eLearning guide. The issue has been logged.", "warning")
        return abort(500)

@eLearning_bp.route("/my-ip")
def get_my_ip():
    import requests
    return requests.get("https://api.ipify.org").text
