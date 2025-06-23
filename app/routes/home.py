# app/routes/home.py
from flask import Blueprint, render_template, session, redirect, url_for
from app.routes.auth import login_required
from datetime import datetime
home_bp = Blueprint('home_bp', __name__)
@home_bp.route('/')
@login_required
def home():
    try:
        if session.get("guest_user") or not session.get("user_role"):
            session.clear()
            return redirect(url_for("auth_bp.login"))



        role = session.get("user_role")
        ad = session.get("user_admin")
        display_name = session.get("display_name")
        subtitle = ""
        if role == "ADM":
            subtitle = "You are logged in as Admin. Last Logged in: " + (
                datetime.fromisoformat(session["last_login_nzt"])
            ).strftime('%A, %d %B %Y, %I:%M %p')

            cards = [
                {"title": "Dashboard", "text": "Go to admin dashboard home", "href": url_for("home_bp.home"), "image": "placeholder.png"},
                {"title": "Overview", "text": "View national performance summary", "href": url_for("funder_bp.funder_dashboard"), "image": "placeholder.png"},
                {"title": "Provider Maintenance", "text": "Add or update provider information", "href": url_for("admin_bp.provider_maintenance"), "image": "placeholder.png"},
                {"title": "Reporting", "text": "Access full national and provider reports", "href": url_for("report_bp.reporting"), "image": "placeholder.png"},
            ]

        elif role == "FUN":
            subtitle = f"You are logged in as {session['desc']} (funder) staff"
            if session.get("user_admin") == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Overview", "text": "See provider performance and progress", "href": url_for("funder_bp.funder_dashboard"), "image": "placeholder.png"},
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.funder_classes"), "image": "placeholder.png"},
                    {"title": "Provider Maintenance", "text": "Manage providers funded by your org", "href": url_for("admin_bp.provider_maintenance"), "image": "placeholder.png"},
                    {"title": "Reporting", "text": "Generate funder/provider reports", "href": url_for("report_bp.reporting"), "image": "placeholder.png"},
                ]
            else:
                cards = [
                    {"title": "Class Lookup", "text": "Search all relevant classes", "href": url_for("class_bp.funder_classes"), "image": "placeholder.png"},
                    {"title": "Self Review", "text": "Complete your staff review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "placeholder.png"},
                    {"title": "Reporting", "text": "View progress reports", "href": url_for("report_bp.reporting"), "image": "placeholder.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "placeholder.png"},

                ]
            subtitle += ". Last Logged in: " + (
                datetime.fromisoformat(session["last_login_nzt"])
            ).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "MOE":
            subtitle = f"You are logged in as {session['desc']} (school) staff"
            if session.get("user_admin") == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Class Upload", "text": "Upload, validate and submit a class list", "href": url_for("upload_bp.classlistupload"), "image": "placeholder.png"},
                    {"title": "Class Lookup", "text": "Search all your school’s classes", "href": url_for("class_bp.moe_classes"), "image": "placeholder.png"},
                    {"title": "Staff Maintenance", "text": "Manage your school’s staff", "href": url_for("staff_bp.staff_maintenance"), "image": "placeholder.png"},
                    {"title": "Self Review", "text": "Send self review links to staff", "href":  url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "placeholder.png"},
                ]
            else:
                cards = [
                    {"title": "Class Upload", "text": "Upload, validate and submit a class list", "href": url_for("upload_bp.classlistupload"), "image": "placeholder.png"},
                    {"title": "Class Lookup", "text": "View your school’s classes", "href": url_for("class_bp.moe_classes"), "image": "placeholder.png"},
                    {"title": "Self Review", "text": "Complete your staff review", "href":  url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "placeholder.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "placeholder.png"},
                ]
            subtitle += ". Last Logged in: " + (
                datetime.fromisoformat(session["last_login_nzt"])
            ).strftime('%A, %d %B %Y, %I:%M %p')

        elif role == "PRO":
            subtitle = f"You are logged in as {session['desc']} (provider) staff"
            if session.get("user_admin") == 1:
                subtitle += " with Administrator Permissions"
                cards = [
                    {"title": "Class Lookup", "text": "Search classes you’ve delivered", "href": url_for("class_bp.provider_classes"), "image": "placeholder.png"},
                    {"title": "Staff Maintenance", "text": "Manage provider staff accounts", "href": url_for("staff_bp.staff_maintenance"), "image": "placeholder.png"},
                    {"title": "Reporting", "text": "Access provider reports", "href": url_for("report_bp.reporting"), "image": "placeholder.png"},
                    {"title": "Overview", "text": "See your organisation's overview by term.=", "href": url_for("funder_bp.funder_dashboard"), "image": "placeholder.png"},
                ]
            else:
                cards = [
                    {"title": "Class Lookup", "text": "See your assigned classes", "href": url_for("class_bp.provider_classes"), "image": "placeholder.png"},
                    {"title": "Reporting", "text": "Download your provider reports", "href": url_for("report_bp.reporting"), "image": "placeholder.png"},
                    {"title": "Self Review", "text": "Complete self review", "href": url_for('survey_bp.survey_by_routename', routename='SelfReview'), "image": "placeholder.png"},
                    {"title": "Profile", "text": "View and update your details", "href": url_for("admin_bp.profile"), "image": "placeholder.png"},
                ]
            last_login = session.get("last_login_nzt")
            if last_login:
                subtitle += " Last Logged in: " + datetime.fromisoformat(last_login).strftime('%A, %d %B %Y, %I:%M %p')
        else:
            cards = []

        return render_template("index.html", display_name=display_name, subtitle=subtitle, cards=cards)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return "Internal Server Error", 500