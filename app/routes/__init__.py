# app/routes/__init__.py
from .auth import auth_bp
from .home import home_bp
from .upload import upload_bp
from .report import report_bp
from .admin import admin_bp
from .view_class import class_bp

def register_routes(app):
    app.register_blueprint(auth_bp, url_prefix="/auth")  # So /auth/login is the login page
    app.register_blueprint(home_bp)                      # Leave this without a prefix
    app.register_blueprint(upload_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(class_bp)
