# app/routes/home.py
from flask import Blueprint, render_template, session, redirect, url_for, current_app
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



@home_bp.route('/')
@login_required
def home():
    try:
        # guard guest / no role
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

        # ---- role-specific content
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
                    {"title": "Overview", "text": "See provider performance and progress", "href": url_for("funder_bp.funder_dashboard"), "image": "Overview.png"},
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.funder_classes"), "image": "ViewClass.png"},
                    {"title": "Provider Maintenance", "text": "Manage providers funded by your org", "href": url_for("admin_bp.provider_maintenance"), "image": "ProviderMaintenance.png"},
                    {"title": "Reporting", "text": "Generate funder/provider reports", "href": url_for("report_bp.new_reports"), "image": "Reporting.png"},
                ]
            else:
                cards = [
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.funder_classes"), "image": "ViewClass.png"},
                    {"title": "Self Review", "text": "Complete your staff review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "SelfReview.png"},
                    {"title": "Reporting", "text": "View progress reports", "href": url_for("report_bp.new_reports"), "image": "Reporting.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "Profile.png"},
                ]
            if last_login_nzt:
                subtitle += ". Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "MOE":
            subtitle = f"You are logged in as {desc} (school) staff"
            if ad == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Class Upload", "text": "Upload, validate and submit a class list", "href": url_for("upload_bp.classlistupload"), "image": "UploadCSV.png"},
                    {"title": "Class Lookup", "text": "Search all your school’s classes", "href": url_for("class_bp.moe_classes"), "image": "ViewClass.png"},
                    {"title": "Staff Maintenance", "text": "Manage your school’s staff", "href": url_for("staff_bp.staff_maintenance"), "image": "StaffMaintenance.png"},
                    {"title": "Self Review", "text": "Send self review links to staff", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "SelfReview.png"},
                ]
            else:
                cards = [
                    {"title": "Class Upload", "text": "Upload, validate and submit a class list", "href": url_for("upload_bp.classlistupload"), "image": "UploadCSV.png"},
                    {"title": "Class Lookup", "text": "View your school’s classes", "href": url_for("class_bp.moe_classes"), "image": "ViewClass.png"},
                    {"title": "Self Review", "text": "Complete your staff review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "SelfReview.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "Profile.png"},
                ]
            if last_login_nzt:
                subtitle += ". Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "PRO":
            subtitle = f"You are logged in as {desc} (provider) staff"
            if ad == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Class Lookup", "text": "Search classes you’ve delivered", "href": url_for("class_bp.provider_classes"), "image": "ViewClass.png"},
                    {"title": "Staff Maintenance", "text": "Manage provider staff accounts", "href": url_for("staff_bp.staff_maintenance"), "image": "StaffMaintenance.png"},
                    {"title": "Reporting", "text": "Access provider reports", "href": url_for("report_bp.new_reports"), "image": "Reporting.png"},
                    {"title": "Overview", "text": "See your organisation's overview by term.", "href": url_for("funder_bp.funder_dashboard"), "image": "Overview.png"},
                ]
            else:
                cards = [
                    {"title": "Class Lookup", "text": "See your assigned classes", "href": url_for("class_bp.provider_classes"), "image": "ViewClass.png"},
                    {"title": "Reporting", "text": "Download your provider reports", "href": url_for("report_bp.new_reports"), "image": "Reporting.png"},
                    {"title": "Self Review", "text": "Complete self review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "SelfReview.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "Profile.png"},
                ]
            if last_login_nzt:
                subtitle += " Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "GRP":
            subtitle = f"You are logged in as a Provider Administrator ({desc})"
            if last_login_nzt:
                subtitle += " Last Logged in: " + datetime.fromisoformat(last_login_nzt).strftime('%A, %d %B %Y, %I:%M %p')
            cards = [
                {"title": "Overview", "text": "See provider performance and progress", "href": url_for("funder_bp.funder_dashboard"), "image": "Overview.png"},
                {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.funder_classes"), "image": "ViewClass.png"},
                {"title": "Staff Maintenance", "text": "Manage your school’s staff", "href": url_for("staff_bp.staff_maintenance"), "image": "StaffMaintenance.png"},
                {"title": "Reporting", "text": "Access provider reports", "href": url_for("report_bp.new_reports"), "image": "Reporting.png"},
            ]

        # ---- SPECIAL: only add error cards for Stella
        email = (session.get("user_email") or "").strip().lower()
        error_cards = []
        if email == "stella@watersafety.org.nz":
            alerts = fetch_new_alerts_for_user(email=email, limit=8, mark_shown=True)
            for a in alerts:
                created_utc = a.get("CreatedAtUtc")

                # If it's a string, parse to datetime
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

        # put error cards first
        cards = (error_cards or []) + (cards or [])

        return render_template(
            "index.html",
            display_name=display_name,
            subtitle=subtitle,
            cards=cards,
            user_email=email
        )

    except Exception as e:
        # best-effort DB alert + server log
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
