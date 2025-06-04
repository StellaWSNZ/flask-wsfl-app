# app/routes/home.py
from flask import Blueprint, render_template, session
from app.routes.auth import login_required
from datetime import datetime
home_bp = Blueprint('home_bp', __name__)
@home_bp.route('/')
@login_required
def home():
    role = session.get("user_role")
    ad = session.get("user_admin")
    display_name = session.get("display_name")
    subtitle = ""

    if role == "ADM":
        subtitle = "You are logged in as Admin. Last Logged in: " + (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    elif role == "FUN":
        subtitle = f"You are logged in as {session['desc']} (funder) staff. Last Logged in: " + (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    elif role == "MOE":
        subtitle = f"You are logged in as {session['desc']} (school) staff. Last Logged in: " + (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')

    if role == "ADM":
        cards = [
            {"title": "Generate Reports", "text": "Build reports on funder and competency performance.", "href": "/reporting", "image": "placeholder.png"},
            {"title": "View Classes", "text": "View any class and its student competencies.", "href": "/funder_classes", "image": "placeholder.png"},
            {"title": "Create User", "text": "Add a new user. They could be an Admin, Funder, Provider, or School staff member.", "href": "/create_user", "image": "placeholder.png"},
        ]

    elif role == "MOE":
        cards = [
            {"title": "Create Class", "text": "Upload a class list for your school.", "href": "/ClassList", "image": "placeholder.png"},
            {"title": "View Classes", "text": "View your school's classes by term and see competency results.", "href": "/moe_classes", "image": "placeholder.png"},
        ]
        if ad == 0:
            cards.append({
                "title": "Manage Profile",
                "text": "Update your contact information and view your E-learning status.",
                "href": "/profile",
                "image": "placeholder.png"
            })
        else:
            cards.append({
                "title": "Create User",
                "text": "Create accounts for your school staff members.",
                "href": "/create_user",
                "image": "placeholder.png"
            })

    elif role == "FUN":
        cards = [
            {"title": "View Classes", "text": "View all school classes you oversee and their student results.", "href": "/funder_classes", "image": "placeholder.png"},
            {"title": "Generate Reports", "text": "Build reports on provider and school performance.", "href": "/reporting", "image": "placeholder.png"},
        ]
        if ad == 0:
            cards.append({
                "title": "Manage Profile",
                "text": "Update your contact information and view your E-learning status.",
                "href": "/profile",
                "image": "placeholder.png"
            })
        else:
            cards.append({
                "title": "Manage Providers",
                "text": "Create providers, assign them to schools, or update existing records.",
                "href": "/coming_soon",
                "image": "placeholder.png"
            })

    elif role == "PRO":
        cards = [
            {"title": "View Classes", "text": "View your assigned classes and student results.", "href": "/funder_classes", "image": "placeholder.png"},
            {"title": "Generate Reports", "text": "Track your performance and review school progress.", "href": "/reporting", "image": "placeholder.png"},
        ]
        if ad == 0:
            cards.append({
                "title": "Manage Profile",
                "text": "Update your contact information and view your E-learning status.",
                "href": "/profile",
                "image": "placeholder.png"
            })
        else:
            cards.append({
                "title": "Manage Providers",
                "text": "Manage schools assigned to your provider organisation.",
                "href": "/coming_soon",
                "image": "placeholder.png"
            })
    else:
        cards = []

    return render_template("index.html", display_name=display_name, subtitle=subtitle, cards=cards)