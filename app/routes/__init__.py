# app/routes/__init__.py
from flask import Flask, render_template

from .api import api_bp
from .auth import auth_bp
from .home import home_bp
from .upload import upload_bp
from .report import report_bp
from .admin import admin_bp
from .view_class import class_bp
from .survey import survey_bp
from .student import students_bp
from .apr import apr_bp
from .staff_maintenance import staff_bp
from app.routes.overview import overview_bp
from app.routes.eLearning import eLearning_bp
from app.routes.feedback import feedback_bp
from app.routes.add_user import user_bp 
from app.routes.instructions import instructions_bp

def register_routes(app):
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/auth")  # So /auth/login is the login page
    app.register_blueprint(home_bp)                      # Leave this without a prefix
    app.register_blueprint(upload_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(class_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(survey_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(eLearning_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(instructions_bp)  
    app.register_blueprint(apr_bp)  
    


    
    @app.errorhandler(Exception)
    def handle_error(e):
        code = getattr(e, 'code', 500)
        return render_template("error.html", error=e, code=code), code

