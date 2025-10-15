from flask import Flask, session, redirect, url_for, request
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from app.stored_session import StoredProcSessionInterface
from app.utils.database import get_db_engine
from app.routes import register_routes
import os
import urllib.parse

# Global objects (used across blueprints)
db = SQLAlchemy()
mail = Mail()

def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # Secret key
    app.secret_key = os.getenv("SECRET_KEY", "changeme123")

    # -----------------------------
    # 🔌 Database Configuration
    # -----------------------------

    user = os.getenv("WSNZDBUSER")
    password = os.getenv("WSNZDBPASS")

    if not user or not password:
        raise RuntimeError("❌ WSNZDBUSER or WSNZDBPASS not set!")

    # Safely encode the password (in case it contains special characters)
    quoted_password = urllib.parse.quote_plus(password)

    # Build the connection string
    db_url = (
        f"mssql+pyodbc://{user}:{quoted_password}@heimatau.database.windows.net:1433"
        f"/WSFL?driver=ODBC+Driver+18+for+SQL+Server"
    )

    print("🧪 DB_URL =", db_url)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # -----------------------------
    # 💾 Stored Procedure Session Setup
    # -----------------------------
    engine = get_db_engine()
    app.session_interface = StoredProcSessionInterface(engine)

    # -----------------------------
    # 📧 Email Configuration
    # -----------------------------
    app.config.update(
        MAIL_SERVER="smtp.office365.com",
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=os.getenv("EMAIL"),
        MAIL_PASSWORD=os.getenv("WSNZADMINPASS"),
        MAIL_DEFAULT_SENDER=os.getenv("EMAIL"),
    )
    mail.init_app(app)

    # -----------------------------
    # 🔗 Register Blueprints
    # -----------------------------
    register_routes(app)
    print(app.url_map)
    # -----------------------------
    # 🔐 Redirect Unauthenticated Users
    # -----------------------------
    @app.before_request
    def require_login():
        # ✅ allow health-check without login
        if request.path == "/__instructions_ping":
            return  # allow

        allowed_routes = {
            "auth_bp.login",
            "auth_bp.logout",
            "auth_bp.forgot_password",
            "auth_bp.reset_password",
            "survey_bp.survey_invite_token",
            "survey_bp.guest_survey_by_id",
            "survey_bp.submit_survey",
            "static",
        }

        # ✅ robust guard: endpoint may be None for some 404/static cases
        if not session.get("logged_in") and not session.get("guest_user"):
            if request.endpoint is None or request.endpoint not in allowed_routes:
                return redirect(url_for("auth_bp.login", next=request.url))

    # -----------------------------
    # 📦 Inject User Info into Templates
    # -----------------------------
    @app.context_processor
    def inject_user_role():
        return {"user_role": session.get("user_role")}

    @app.context_processor
    def inject_user_admin():
        return {"user_admin": session.get("user_admin")}

    @app.context_processor
    def inject_user_email():
        return {"user_email": session.get("user_email")}

    return app