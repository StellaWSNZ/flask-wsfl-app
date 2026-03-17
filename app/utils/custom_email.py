# app/utils/email.py
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer
from flask import current_app, render_template, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import os


def send_reset_email(mail, email, token):
    reset_url = url_for(
        "auth_bp.reset_password",
        token=token,
        _external=True
    )

    msg = Message(
        subject="Water Skills for Life – Reset your password",
        recipients=[email],
        sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
    )

    # Plain text fallback
    msg.body = f"""
Kia ora,

We received a request to reset your Water Skills for Life password.

You can reset your password using the link below:
{reset_url}

If you didn’t request this, you can safely ignore this email.

Ngā mihi,
Water Safety New Zealand
""".strip()

    # HTML version
    msg.html = f"""
<p>Kia ora,</p>

<p>
We received a request to reset your Water Skills for Life password.
</p>

<p>
<a href="{reset_url}"
   style="
     display:inline-block;
     padding:10px 16px;
     background:#005ea5;
     color:#ffffff;
     text-decoration:none;
     border-radius:4px;
     font-weight:600;
   ">
   Click here to reset your password
</a>
</p>

<p>
If you didn’t request this, you can safely ignore this email.
</p>

<p>
Ngā mihi,<br>
Water Safety New Zealand
</p>
"""

    mail.send(msg)
    
    
def generate_reset_token(secret_key, email):
    serializer = URLSafeTimedSerializer(secret_key)
    return serializer.dumps(email, salt='reset-password')
def verify_reset_token(token, max_age=86400):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        return serializer.loads(token, salt='password-reset-salt', max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None

    
    
def generate_survey_link(email, firstname, lastname, role, user_id, survey_id):
    s = URLSafeTimedSerializer(current_app.secret_key)
    token = s.dumps({
        "email": email,
        "firstname": firstname,
        "lastname": lastname,
        "role": role,
        "user_id": user_id,
        "survey_id": survey_id
    })
    # Link goes to the token route, which then redirects to guest view
    return url_for("survey_bp.survey_invite_token", token=token, _external=True)

def send_survey_invite_email(
    mail,
    recipient_email,
    first_name,
    role,
    user_id,
    survey_id,
    invited_by_name,
    requester_email,
    invited_by_org=None,
):
    link = generate_survey_link(
        email=recipient_email,
        firstname=first_name,
        lastname="",
        role=role,
        user_id=user_id,
        survey_id=survey_id,
    )

    invited_by_org = invited_by_org or "Water Skills for Life"

    if survey_id == 1:
        subject = "WSFL Self Review – Invitation"
        body_template = "emails/survey_invite_selfreview.txt"
        html_template = "emails/survey_invite_selfreview.html"
    else:
        subject = "You're Invited to Complete a WSFL Survey"
        body_template = "emails/survey_invite_generic.txt"
        html_template = "emails/survey_invite_generic.html"

    msg = Message(
        subject=subject,
        sender=("WSFL Administration Team", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[recipient_email],
    )
    msg.reply_to = requester_email

    msg.body = render_template(
        body_template,
        first_name=first_name,
        invited_by_name=invited_by_name,
        invited_by_org=invited_by_org,
        link=link,
    )

    msg.html = render_template(
        html_template,
        first_name=first_name,
        invited_by_name=invited_by_name,
        invited_by_org=invited_by_org,
        link=link,
    )

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach(
            "WSFLLogo.png",
            "image/png",
            fp.read(),
            disposition="inline",
            headers={"Content-ID": "<wsfl_logo>"}
        )

    mail.send(msg)
    
    
def send_survey_reminder_email(
    mail,
    email,
    firstname,
    requested_by,
    requester_email,
    from_org
):
    login_link = url_for('auth_bp.login', _external=True)

    msg = Message(
        subject="Reminder: Complete Your Self Review",
        sender=("WSFL Administration Team", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[email]
    )

    # reply-to = person who triggered it
    msg.reply_to = requester_email

    msg.body = render_template(
        "emails/survey_reminder.txt",
        firstname=firstname,
        requested_by=requested_by,
        from_org=from_org,
        login_link=login_link,
    )

    msg.html = render_template(
        "emails/survey_reminder.html",
        firstname=firstname,
        requested_by=requested_by,
        from_org=from_org,
        login_link=login_link,
    )

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach(
            "WSFLLogo.png",
            "image/png",
            fp.read(),
            disposition="inline",
            headers={"Content-ID": "<wsfl_logo>"}
        )

    mail.send(msg)


def send_survey_invitation_email(mail, email, firstname, lastname, role, user_id, survey_id, requested_by, from_org):
    # Generate tokenized one-time link
    survey_url = generate_survey_link(email, firstname, lastname, role, user_id, survey_id)

    msg = Message(
        subject="Your Self Review Survey Link",
        recipients=[email],
        sender=(f"{requested_by} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"])
    )
    
    msg.html = f"""
    <p>Kia ora {firstname},</p>
    <p>Please complete your self review by clicking the secure link below:</p>
    <p><a href="{survey_url}">Start Self Review</a></p>
    <p>This invitation was sent at the request of <strong>{requested_by}</strong> from <strong>{from_org}</strong>.</p>
    <p>Ngā mihi,<br>WSFL Team</p>
    """
    mail.send(msg)



def send_feedback_email(mail, user_email, issue_text, display_name, role, is_admin, desc, screenshot_file=None):
    admin_text = "Yes" if is_admin else "No"

    msg = Message(
        subject="WSFL Feedback Submitted",
        recipients=["stella@watersafety.org.nz","dbadmin@watersafety.org.nz"],
        sender=(f"{display_name} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"])
    )

    msg.body = f"""\
    Feedback submitted in WSFL

    Name: {display_name}
    Email: {user_email}
    Role: {role}
    Admin: {admin_text}
    Entity: {desc}

    Issue:
    {issue_text}
    """

    msg.html = f"""\
    <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
      <p><strong>New feedback submitted in WSFL</strong></p>
      <p><strong>Name:</strong> {display_name}<br>
         <strong>Email:</strong> {user_email}<br>
         <strong>Role:</strong> {role}<br>
         <strong>Admin:</strong> {admin_text}<br>
         <strong>Entity:</strong> {desc}</p>
      <p><strong>Issue:</strong></p>
      <p style="white-space: pre-line;">{issue_text}</p>
      <p>Ngā mihi,<br>WSFL Auto Notification System</p>
      <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
    </div>
    """
    if screenshot_file and screenshot_file.filename:
        screenshot_file.seek(0)  # Ensure it's at the start
        msg.attach(
            filename="feedback_screenshot.png",  # or screenshot_file.filename if you prefer
            content_type="image/png",
            data=screenshot_file.read()
        )
    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach("WSFLLogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})
    
    
    mail.send(msg)
    
    
def send_elearning_reminder_email(
    mail,
    email,
    firstname,
    requested_by,
    requester_email,
    from_org,
    course_statuses
):
    msg = Message(
        subject="Reminder: Complete Your eLearning Courses",
        sender=("WSFL Administration Team", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[email]
    )

    # If your mail setup/version supports it:
    msg.reply_to = requester_email

    msg.body = render_template(
        "emails/elearning_reminder.txt",
        firstname=firstname,
        requested_by=requested_by,
        from_org=from_org or "Water Safety New Zealand",
        requester_email=requester_email,
        course_statuses=course_statuses,
        sporttutor_url="https://sporttutor.nz/ilp/pages/catalogsearch.jsf?catalogId=3712496&menuId=3712463&locale=en-GB&showbundlekeys=false&client=watersafetynz&sidebarExpanded=true&q=*:*&rows=9",
    )

    msg.html = render_template(
        "emails/elearning_reminder.html",
        firstname=firstname,
        requested_by=requested_by,
        from_org=from_org or "Water Safety New Zealand",
        requester_email=requester_email,
        course_statuses=course_statuses,
        status_badges={
            "Passed": "green",
            "In Progress": "orange",
            "Not Started": "grey",
            "Cancelled": "red",
            "Not enrolled": "grey",
        },
        sporttutor_url="https://sporttutor.nz/ilp/pages/catalogsearch.jsf?catalogId=3712496&menuId=3712463&locale=en-GB&showbundlekeys=false&client=watersafetynz&sidebarExpanded=true&q=*:*&rows=9",
    )

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach(
            "WSFLLogo.png",
            "image/png",
            fp.read(),
            disposition="inline",
            headers={"Content-ID": "<wsfl_logo>"}
        )

    with current_app.open_resource("static/eLearningGuide.pdf") as pdf_fp:
        msg.attach("eLearningGuide.pdf", "application/pdf", pdf_fp.read())

    mail.send(msg)
