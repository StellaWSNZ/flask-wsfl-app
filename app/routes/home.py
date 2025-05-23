# app/routes/home.py
from flask import Blueprint, render_template, session
from app.routes.auth import login_required
from datetime import datetime
home_bp = Blueprint('home_bp', __name__)

@home_bp.route('/')
@login_required
def home():
    role = session.get("user_role")
    display_name = session.get("display_name")
    subtitle = ""
    if(session["user_role"]=="ADM"):
        subtitle = "You are logged in as Admin. Last Logged in: " +  (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    elif (session["user_role"]=="FUN"):
        subtitle = "You are logged in as "+session["desc"]+" (funder) staff. Last Logged in: " +  (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    elif (session["user_role"]=="MOE"):
        subtitle = "You are logged in as "+session["desc"]+" (school) staff. Last Logged in: " +  (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    

    if role == "ADM":
        cards = [
            {"title": "Generate Reports", "text": "Build reports on funder and competency performance.", "href": "/reporting", "image": "placeholder.png"},
            {"title": "Audit Activity", "text": "Review login history and recent activity.", "href": "/comingsoon", "image": "placeholder.png"},
            {"title": "Create User", "text": "Add a new admin, MOE, or funder account.", "href": "/create_user", "image": "placeholder.png"},

        ]
    elif role == "MOE":
        cards = [
            {"title": "Upload Class List", "text": "Submit a class list and view student progress.", "href": "/", "image": "placeholder.png"},
            {"title": "Generate Summary", "text": "Download summary reports for your schools.", "href": "/comingsoon", "image": "placeholder.png"},
            {"title": "Support & Help", "text": "Access help documentation and contact support.", "href": "/comingsoon", "image": "placeholder.png"},
        ]
    elif role == "FUN":
        cards = [
            {"title": "Student Competency Maintenence", "text": "Update competency achievements for your class.", "href": "/funder", "image": "viewclass.png"},
            {"title": "Live Reporting", "text": "Generate reporting for your funder.", "href": "/reporting", "image": "placeholder.png"},
            {"title": "Maintenance", "text": "Any issues with school and classes recorded.", "href": "/comingsoon", "image": "placeholder.png"},
        ]
    else:
        cards = []

    return render_template("index.html", display_name=display_name, subtitle=subtitle, cards=cards)
