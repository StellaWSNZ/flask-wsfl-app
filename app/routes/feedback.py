from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from sqlalchemy import text
from app.utils.database import get_db_engine
from app.routes.auth import login_required
from app.utils.custom_email import send_feedback_email
from app.extensions import mail
feedback_bp = Blueprint("feedback_bp", __name__)
from werkzeug.utils import secure_filename

@feedback_bp.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    user_desc = session.get("desc")
    user_admin = session.get("user_admin")
    user_role = session.get("user_role", "Unknown")
    user_role_desc = {
        "MOE": "School",
        "PRO": "Provider",
        "FUN": "Funder",
        "ADM": "Administrator"
    }.get(user_role, "User")
    if(user_role == "ADM"):
        user_desc = f"{user_role_desc}"
    else:
        user_desc = f"{user_role_desc} {'Administrator' if user_admin else 'Staff'} from {user_desc}"

    display_name = session.get("display_name", "Unknown")

    if request.method == "POST":
        issue_text = request.form.get("issue", "").strip()
        if not issue_text:
            flash("Please describe the issue before submitting.", "warning")
            return redirect(url_for("feedback_bp.feedback"))

        email = session.get("user_email")
        role = session.get("user_role", "Unknown")
        desc = session.get("user_desc", "Unknown")

        admin = "Yes" if session.get("user_admin") else "No"
        display_name = session.get("display_name", "Unknown")
        moe_number = session.get("moe_number", "Unknown")

        screenshot_file = request.files.get("screenshot")
        screenshot_data = None

        if screenshot_file and screenshot_file.filename != "":
            screenshot_data = screenshot_file.read()

        try:
            # Save feedback + image to the database
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("EXEC SubmitFeedback :Email, :Issue, :Screenshot"),
                    {"Email": email, "Issue": issue_text, "Screenshot": screenshot_data}
                )

            # Send email to Stella (optional: attach image)
            send_feedback_email(
                mail,
                user_email=email,
                issue_text=issue_text,
                display_name=display_name,
                role=user_role_desc,
                is_admin=session.get("user_admin") == 1,
                desc=desc,
                screenshot_file=screenshot_file  # ✅ Add this
            )

            flash("Thank you for your feedback!", "success")
            return redirect(url_for("feedback_bp.feedback"))
        except Exception as e:
            flash(f"Error submitting feedback: {e}", "danger")

    # Load info for template


    return render_template("feedback.html", display_name=display_name, user_desc=user_desc)