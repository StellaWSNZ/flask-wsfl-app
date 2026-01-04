from flask import Blueprint, render_template, request, session, redirect, url_for, flash, current_app, abort
from sqlalchemy import text
from app.utils.database import get_db_engine, log_alert
from app.routes.auth import login_required
from app.utils.custom_email import send_feedback_email
from app.extensions import mail

feedback_bp = Blueprint("feedback_bp", __name__)

# Optional: simple file limits for screenshots
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB

def _log_alert_safe(message):
    """Best-effort DB alert logging with truncation."""
    try:
        log_alert(
            email=(session.get("user_email")   or "")[:320],
            role=(session.get("user_role") or "")[:10],
            entity_id=session.get("user_id"),
            link=str(request.url)[:2048],
            message=message[:2000],
        )
    except Exception as log_err:
        current_app.logger.exception(f"⚠️ Failed to log alert (feedback)")

@feedback_bp.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    # Build display strings safely
    raw_desc   = session.get("desc", "") or ""
    user_admin = int(session.get("user_admin") or 0)
    user_role  = session.get("user_role", "Unknown")
    role_label = {
        "MOE": "School",
        "PRO": "Provider",
        "FUN": "Funder",
        "ADM": "Administrator",
        "GRP": "Group",
    }.get(user_role, "User")

    if user_role == "ADM":
        header_desc = f"{role_label}"
    else:
        header_desc = f"{role_label} {'Administrator' if user_admin else 'Staff'} from {raw_desc}"

    display_name = session.get("display_name", "Unknown")

    if request.method == "POST":
        issue_text = (request.form.get("issue") or "").strip()
        if not issue_text:
            flash("Please describe the issue before submitting.", "warning")
            return redirect(url_for("feedback_bp.feedback"))

        email = session.get("user_email") or ""
        screenshot_file = request.files.get("screenshot")
        screenshot_data = None

        # Validate optional screenshot
        if screenshot_file and screenshot_file.filename:
            try:
                # MIME/type check (client-sent; not bulletproof, but helpful)
                if screenshot_file.mimetype not in ALLOWED_IMAGE_MIMES:
                    flash("Screenshot must be an image (png/jpg/gif/webp).", "warning")
                    return redirect(url_for("feedback_bp.feedback"))

                # Size cap
                screenshot_file.stream.seek(0, 2)  # move to end
                size = screenshot_file.stream.tell()
                screenshot_file.stream.seek(0)
                if size > MAX_IMAGE_BYTES:
                    flash("Screenshot is too large (max 5MB).", "warning")
                    return redirect(url_for("feedback_bp.feedback"))

                screenshot_data = screenshot_file.read()
            except Exception as e:
                current_app.logger.exception("❌ Screenshot processing failed")
                _log_alert_safe(f"Screenshot processing failed: {str(e)}")
                flash("We couldn't process your screenshot. You can try again without it.", "warning")
                # continue without screenshot

        # 1) Save feedback + (optional) image to DB
        saved_ok = False
        try:
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("EXEC SubmitFeedback :Email, :Issue, :Screenshot"),
                    {"Email": email, "Issue": issue_text, "Screenshot": screenshot_data}
                )
            saved_ok = True
        except Exception as e:
            current_app.logger.exception("❌ SubmitFeedback DB call failed")
            _log_alert_safe(f"SubmitFeedback DB error: {str(e)}")
            flash("We couldn’t save your feedback. The issue has been logged.", "danger")
            return redirect(url_for("feedback_bp.feedback"))

        # 2) Send email (non-fatal if this fails)
        if saved_ok:
            try:
                send_feedback_email(
                    mail,
                    user_email=email,
                    issue_text=issue_text,
                    display_name=display_name,
                    role=role_label,
                    is_admin=(user_admin == 1),
                    desc=raw_desc,
                    screenshot_file=screenshot_file if screenshot_file and screenshot_file.filename else None,
                )
            except Exception as e:
                current_app.logger.exception("⚠️ send_feedback_email failed (non-fatal)")
                _log_alert_safe(f"send_feedback_email error: {str(e)}")
                flash("Your feedback was saved, but we couldn’t send the notification email.", "warning")

        flash("Thank you for your feedback!", "success")
        return redirect(url_for("feedback_bp.feedback"))

    # GET — guard template render
    try:
        return render_template("feedback.html", display_name=display_name, desc=header_desc)
    except Exception as e:
        current_app.logger.exception("❌ feedback template render failed")
        _log_alert_safe(f"feedback template error: {str(e)}")
        return abort(500)
