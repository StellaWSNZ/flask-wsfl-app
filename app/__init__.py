# app/__init__.py
from flask import Flask, session, redirect, url_for, request
from flask_mail import Mail
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from .routes import register_routes
import os

mail = Mail()
db = SQLAlchemy()

def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.secret_key = os.getenv("SECRET_KEY", "changeme123")

    # 🔐 Session config
    app.config["SESSION_TYPE"] = "sqlalchemy"
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DB_URL")  # Use your actual DB URL
    app.config["SESSION_SQLALCHEMY"] = db

    # 📧 Mail config
    app.config.update(
        MAIL_SERVER='smtp.office365.com',
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME='stella@watersafety.org.nz',
        MAIL_PASSWORD=os.getenv('WSNZPASS'),
        MAIL_DEFAULT_SENDER='stella@watersafety.org.nz'
    )

    # 🔧 Initialize extensions
    mail.init_app(app)
    db.init_app(app)
    Session(app)

    # 🔁 Create DB tables for session if needed
    with app.app_context():
        db.create_all()

    register_routes(app)

    # 🔒 Redirect unauthenticated users
    @app.before_request
    def require_login():
        allowed_routes = {
            'auth_bp.login', 
            'auth_bp.logout', 
            'auth_bp.forgot_password', 
            'auth_bp.reset_password',
            'static'
        }
        if not session.get("logged_in") and request.endpoint not in allowed_routes:
            return redirect(url_for("auth_bp.login", next=request.url))

    # ✅ Inject user roles in all templates
    @app.context_processor
    def inject_user_role():
        return {"user_role": session.get("user_role")}

    @app.context_processor
    def inject_user_admin():
        return {"user_admin": session.get("user_admin")}

    return app
