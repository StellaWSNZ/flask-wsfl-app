# app/routes/home.py
from flask import Blueprint, render_template, request, session, redirect, url_for, current_app
from app.routes.auth import login_required
from app.utils.database import get_db_engine, log_alert
from sqlalchemy import text
from datetime import datetime
import pytz
home_bp = Blueprint('home_bp', __name__)

# ---- helper: pull newest unshown alerts for a user and mark as shown
def fetch_new_alerts_for_user(email: str, limit: int = 12, mark_shown: bool = True):
    if not email:
        return []
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            rows = conn.execute(
                text("""
                    EXEC dbo.AUD_Alerts_FetchForDashboard
                         @Email=:email, @Limit=:limit, @MarkShown=:mark
                """),
                {"email": email, "limit": limit, "mark": 1 if mark_shown else 0}
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        # Best-effort log of the failure to your alerts table
        try:
            log_alert(
                email=email,
                role=session.get("user_role"),
                entity_id=None,
                link=url_for("home_bp.home", _external=True),
                message=f"Fetch dashboard alerts failed: {e}"
            )
        except Exception:
            pass
        # Also app log for visibility
        current_app.logger.exception("Failed to fetch alerts for dashboard")
        return []


from flask import Blueprint, render_template, session, redirect, url_for, current_app, request
from app.routes.auth import login_required
from app.utils.database import get_db_engine, log_alert
from sqlalchemy import text
from datetime import datetime
import pytz

home_bp = Blueprint('home_bp', __name__)

# ---- helper: pull newest unshown alerts for a user and mark as shown
def fetch_new_alerts_for_user(email: str, limit: int = 12, mark_shown: bool = True):
    if not email:
        return []
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            rows = conn.execute(
                text("""
                    EXEC dbo.AUD_Alerts_FetchForDashboard
                         @Email=:email, @Limit=:limit, @MarkShown=:mark
                """),
                {"email": email, "limit": limit, "mark": 1 if mark_shown else 0}
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        try:
            log_alert(
                email=email,
                role=session.get("user_role"),
                entity_id=None,
                link=url_for("home_bp.home", _external=True),
                message=f"Fetch dashboard alerts failed: {e}"
            )
        except Exception:
            pass
        current_app.logger.exception("Failed to fetch alerts for dashboard")
        return []
def _normalise_cards(cards):
    normalised = []

    for c in cards or []:
        image = c.get("image")
        icon = c.get("icon")

        if image and not icon:
            icon = url_for("static", filename=image)

        normalised.append({
            "title": c.get("title"),
            "subtitle": c.get("subtitle") or c.get("text"),
            "href": c.get("href"),
            "icon": icon,
            "type": c.get("type"),
            "text": c.get("text"),
            "image": c.get("image"),
            "entity": c.get("entity"),
            "user_full_name": c.get("user_full_name"),
            "email": c.get("email"),
            "created_at": c.get("created_at"),
        })

    return normalised

@home_bp.route('/')
@login_required
def home():
    try:
        if session.get("guest_user") or not session.get("user_role"):
            session.clear()
            return redirect(url_for("auth_bp.login"))

        role = session.get("user_role")
        ad = session.get("user_admin")
        display_name = session.get("display_name") or "User"
        last_login_nzt = session.get("last_login_nzt")
        desc = session.get("desc", "")

        subtitle = ""
        cards = []
        review_summary = None
        sportnz_table = None
        kaiako_summary = None
        student_summary = None

        if role == "ADM":
            if last_login_nzt:
                subtitle = "You are logged in as Admin. Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')
            else:
                subtitle = "You are logged in as Admin."
            cards = []

        elif role == "FUN":
            subtitle = f"You are logged in as {desc} (funder) staff"
            if ad == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Overview", "text": "See provider performance and progress", "href": url_for("overview_bp.funder_dashboard"), "image": "cardicons/Overview.png"},
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Provider Maintenance", "text": "Manage providers funded by your organisation", "href": url_for("admin_bp.provider_maintenance"), "image": "cardicons/ProviderMaintenance.png"},
                    {"title": "Reporting", "text": "Generate funder/provider reports", "href": url_for("report_bp.new_reports"), "image": "cardicons/Reporting.png"},
                ]
            else:
                cards = [
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Self Review", "text": "Complete your staff review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "cardicons/SelfReview.png"},
                    {"title": "Past Forms", "text": "View past forms", "href": url_for('survey_bp.list_my_surveys'), "image": "cardicons/PastForms.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "cardicons/Profile.png"},
                ]
            if last_login_nzt:
                subtitle += ". Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "MOE":
            subtitle = f"You are logged in as {desc} (school) staff"
            if ad == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Class Upload", "text": "Upload, validate and submit a class list", "href": url_for("upload_bp.classlistupload"), "image": "cardicons/UploadCSV.png"},
                    {"title": "Class Lookup", "text": "Search all your school’s classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Staff Maintenance", "text": "Manage your school’s staff", "href": url_for("staff_bp.staff_maintenance"), "image": "cardicons/StaffMaintenance.png"},
                    {"title": "Self Review", "text": "Send self review links to staff", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "cardicons/SelfReview.png"},
                ]
            else:
                cards = [
                    {"title": "Class Upload", "text": "Upload, validate and submit a class list", "href": url_for("upload_bp.classlistupload"), "image": "cardicons/UploadCSV.png"},
                    {"title": "Class Lookup", "text": "View your school’s classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Self Review", "text": "Complete your staff review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "cardicons/SelfReview.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "cardicons/Profile.png"},
                ]
            if last_login_nzt:
                subtitle += ". Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "PRO":
            subtitle = f"You are logged in as {desc} (provider) staff"
            if ad == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Class Lookup", "text": "Search classes you’ve delivered", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Staff Maintenance", "text": "Manage provider staff accounts", "href": url_for("staff_bp.staff_maintenance"), "image": "cardicons/StaffMaintenance.png"},
                    {"title": "Reporting", "text": "Access provider reports", "href": url_for("report_bp.new_reports"), "image": "cardicons/Reporting.png"},
                    {"title": "Overview", "text": "See your organisation's overview by term.", "href": url_for("overview_bp.funder_dashboard"), "image": "cardicons/Overview.png"},
                ]
            else:
                cards = [
                    {"title": "Class Lookup", "text": "See your assigned classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Past Forms", "text": "View past forms", "href": url_for('survey_bp.list_my_surveys'), "image": "cardicons/PastForms.png"},
                    {"title": "Self Review", "text": "Complete self review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "cardicons/SelfReview.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "cardicons/Profile.png"},
                ]
            if last_login_nzt:
                subtitle += " Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "GRP":
            subtitle = f"You are logged in as a Provider Administrator ({desc})"
            if last_login_nzt:
                subtitle += " Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')
            if ad == 1:
                cards = [
                    {"title": "Overview", "text": "See provider performance and progress", "href": url_for("overview_bp.funder_dashboard"), "image": "cardicons/Overview.png"},
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Staff Maintenance", "text": "Manage your school’s staff", "href": url_for("staff_bp.staff_maintenance"), "image": "cardicons/StaffMaintenance.png"},
                    {"title": "Reporting", "text": "Access provider reports", "href": url_for("report_bp.new_reports"), "image": "cardicons/Reporting.png"},
                ]
            else:
                cards = [
                    {"title": "Overview", "text": "See provider performance and progress", "href": url_for("overview_bp.funder_dashboard"), "image": "cardicons/Overview.png"},
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.filter_classes"), "image": "cardicons/ViewClass.png"},
                    {"title": "Staff Maintenance", "text": "Manage your school’s staff", "href": url_for("staff_bp.staff_maintenance"), "image": "cardicons/StaffMaintenance.png"},
                    {"title": "Past Forms", "text": "View past forms", "href": url_for('survey_bp.list_my_surveys'), "image": "cardicons/PastForms.png"},
                ]

        email = (session.get("user_email") or "").strip().lower()

        dashboard_mode = "default"
        if email == "stella@watersafety.org.nz":
            requested_view = (request.args.get("view") or "").strip().lower()
            if requested_view in {"errors", "esther"}:
                session["stella_dashboard_view"] = requested_view
            dashboard_mode = session.get("stella_dashboard_view", "errors")
        elif email == "esther@watersafety.org.nz":
            dashboard_mode = "esther"

        error_cards = []
        if email == "stella@watersafety.org.nz":
            alerts = fetch_new_alerts_for_user(email=email, limit=6, mark_shown=True)
            for a in alerts:
                created_utc = a.get("CreatedAtUtc")
                if isinstance(created_utc, str):
                    try:
                        created_utc = datetime.fromisoformat(created_utc.replace("Z", "+00:00"))
                    except Exception:
                        created_utc = None

                local_tz = pytz.timezone(session.get("user_timezone", "Pacific/Auckland"))
                if created_utc:
                    created_local = created_utc.astimezone(local_tz)
                    created_str = created_local.strftime("%A, %d %B %Y, %I:%M %p")
                else:
                    created_str = None

                error_cards.append({
                    "type": "error",
                    "title": "New Error",
                    "entity": a.get("EntityName") or "Unknown",
                    "user_full_name": (a.get("UserFullName") or "").strip() or None,
                    "email": a.get("Email") or "(no email)",
                    "text": a.get("ErrorMessage") or "An error was logged.",
                    "href": a.get("Link") or url_for("home_bp.home"),
                    "created_at": created_str
                })

        if email in ["stella@watersafety.org.nz", "esther@watersafety.org.nz"]:
            try:
                engine = get_db_engine()

                with engine.connect() as conn:
                    result = conn.execute(
                        text("EXEC dbo.GetExternalReviewSummary @SurveyID = :sid"),
                        {"sid": 4}
                    )
                    review_summary = result.mappings().first()

                    nearest_term = session.get("nearest_term") or session.get("term") or 2
                    nearest_year = session.get("nearest_year") or session.get("calendar_year") or 2026

                    kaiako_rows = conn.execute(
                        text("EXEC dbo.GetKaiakoSurveySummary @CalendarYear = :year, @Term = :term"),
                        {"year": nearest_year, "term": nearest_term}
                    ).mappings().all()
                    kaiako_summary = dict(kaiako_rows[0]) if kaiako_rows else None

                    student_rows = conn.execute(
                        text("EXEC dbo.GetStudentCounts @CalendarYear = :year, @Term = :term"),
                        {"year": nearest_year, "term": nearest_term}
                    ).mappings().all()
                    student_summary = dict(student_rows[0]) if student_rows else None

                    sport_rows = conn.execute(
                        text("EXEC dbo.GetSportNZStatsByTerm @CalendarYear = :year, @Term = :term"),
                        {"year": nearest_year, "term": nearest_term}
                    ).mappings().all()

                    if sport_rows:
                        term_groups = []
                        seen_groups = set()
                        for r in sport_rows:
                            key = (r["CalendarYear"], r["Term"])
                            if key not in seen_groups:
                                seen_groups.add(key)
                                term_groups.append({
                                    "calendar_year": r["CalendarYear"],
                                    "term": r["Term"],
                                    "label": f'T{r["Term"]} {r["CalendarYear"]}'
                                })

                        table_rows = []
                        seen_rows = set()
                        lookup = {
                            (r["CompetencyID"], r["YearLevelID"], r["CalendarYear"], r["Term"]): r
                            for r in sport_rows
                        }

                        for r in sport_rows:
                            row_key = (r["CompetencyID"], r["YearLevelID"])
                            if row_key in seen_rows:
                                continue
                            seen_rows.add(row_key)

                            row = {
                                "label": f'{r["CompetencyName"]} ({r["YearLevel"]})',
                                "cells": []
                            }

                            for g in term_groups:
                                item = lookup.get((
                                    r["CompetencyID"],
                                    r["YearLevelID"],
                                    g["calendar_year"],
                                    g["term"]
                                ))

                                if item:
                                    row["cells"].append({
                                        "student_count": item["StudentCount"],
                                        "rate": f'{item["Rate"]:.2f}%'
                                    })
                                else:
                                    row["cells"].append({
                                        "student_count": "-",
                                        "rate": "-"
                                    })

                            table_rows.append(row)

                        sportnz_table = {
                            "term_groups": term_groups,
                            "rows": table_rows
                        }

            except Exception as e:
                current_app.logger.error(f"Error loading review summary / sport NZ stats: {e}")
                review_summary = None
                sportnz_table = None
                kaiako_summary = None
                student_summary = None

        cards = _normalise_cards((error_cards or []) + (cards or []))

        return render_template(
            "index.html",
            display_name=display_name,
            subtitle=subtitle,
            cards=cards,
            user_email=email,
            video_url="https://www.youtube.com/embed/59CXUrI328Y?si=MzpSy_bZ8_lKj5_E" ,
            review_summary=review_summary,
            sportnz_table=sportnz_table,
            kaiako_summary=kaiako_summary,
            student_summary=student_summary,
            dashboard_mode=dashboard_mode,
        )

    except Exception as e:
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=None,
                link=url_for("home_bp.home", _external=True),
                message=f"/ home failed: {e}"
            )
        except Exception:
            pass
        current_app.logger.exception("Home route failed")
        return "Internal Server Error", 500