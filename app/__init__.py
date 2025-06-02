from flask import Flask, session, redirect, url_for, request
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from app.stored_session import StoredProcSessionInterface
from app.utils.database import get_db_engine
from app.routes import register_routes
import os

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
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "mssql+pyodbc://FlaskUser:Wai_Ora2025@heimatau.database.windows.net:1433/WSFL?driver=ODBC+Driver+18+for+SQL+Server"
    )
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
        MAIL_USERNAME="stella@watersafety.org.nz",
        MAIL_PASSWORD=os.getenv("WSNZPASS"),
        MAIL_DEFAULT_SENDER="stella@watersafety.org.nz",
    )
    mail.init_app(app)

    # -----------------------------
    # 🔗 Register Blueprints
    # -----------------------------
    register_routes(app)

    # -----------------------------
    # 🔐 Redirect Unauthenticated Users
    # -----------------------------
    @app.before_request
    def require_login():
        allowed_routes = {
            "auth_bp.login",
            "auth_bp.logout",
            "auth_bp.forgot_password",
            "auth_bp.reset_password",
            "static"
        }
        if not session.get("logged_in") and request.endpoint not in allowed_routes:
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

    return app
