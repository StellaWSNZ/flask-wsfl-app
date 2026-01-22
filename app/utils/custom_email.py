# app/utils/email.py
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import os

def send_reset_email(mail, email, token):
    reset_url = url_for('auth_bp.reset_password', token=token, _external=True)
    msg = Message(
        subject='Reset Your WSFL Password',
        recipients=[email],
        sender=("WSFL Admin", current_app.config["MAIL_USERNAME"])
    )
    msg.html = f"""
        <p>Kia ora,</p>
        <p>We received a request to reset your WSFL account password.</p>
        <p><a href="{reset_url}">Click here to reset your password</a></p>
        <p>If you didn't request this, you can safely ignore this email.</p>
        <p>Ngā mihi,<br><strong>WSFL Admin Team</strong></p>
        <img src="cid:wsfl_logo" alt="WSFL Logo" style="height:60px;margin-top:10px;">
    """
    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach("WSFLLogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})
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
    mail, recipient_email, first_name, role, user_id, survey_id, invited_by_name
):
    link = generate_survey_link(
        email=recipient_email,
        firstname=first_name,
        lastname="",  # Optional: update if needed
        role=role,
        user_id=user_id,
        survey_id=survey_id,
    )

    # ------------------------------
    # CUSTOM CONTENT FOR SELF REVIEW
    # ------------------------------
    if survey_id == 1:
        subject = "WSFL Self Review – Invitation"
        plain_body = f"""Kia ora {first_name},

You’ve been invited to complete your **Water Skills for Life Self Review**.

Click the link below to begin:
{link}

If you have any questions, feel free to reply to this email.

Ngā mihi,
{invited_by_name}
(On behalf of Water Skills for Life)
"""

        html_body = f"""
        <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
          <p>Kia ora <strong>{first_name}</strong>,</p>

          <p>
            You’ve been invited to complete a 
            <strong>Water Skills for Life Self Review</strong>.
          </p>

          <p>
            <a href="{link}" style="font-size: 17px; color: #0057b7;">
              Click here to begin your self review
            </a>
          </p>

          <p>
            If you have any questions, feel free to reply to this email.
          </p>

          <p>
            Ngā mihi,<br>
            {invited_by_name}<br>
            <span style="color: #444;">(via the Water Skills for Life Platform)</span>
          </p>

          <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
        </div>
        """

    # --------------------------------------------
    # DEFAULT CONTENT FOR ALL OTHER SURVEY INVITES
    # --------------------------------------------
    else:
        subject = "You're Invited to Complete a WSFL Survey"
        plain_body = f"""Kia ora {first_name},

You’ve been invited to complete a Water Skills for Life (WSFL) survey.

Click the link below to begin:
{link}

If you have any questions, feel free to reply to this email.

Ngā mihi,
The WSFL Team
"""

        html_body = f"""
        <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
          <p>Kia ora <strong>{first_name}</strong>,</p>

          <p>You’ve been invited to complete a <strong>Water Skills for Life</strong> survey.</p>

          <p>
            <a href="{link}" style="font-size: 17px; color: #0057b7;">
              Click here to begin the survey
            </a>
          </p>

          <p>If you have any questions, feel free to reply to this email.</p>

          <p>Ngā mihi,<br>The WSFL Team</p>

          <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
        </div>
        """

    # -------------------------
    # Construct & send message
    # -------------------------
    msg = Message(
        subject=subject,
        sender=(f"{invited_by_name} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[recipient_email],
    )

    msg.body = plain_body
    msg.html = html_body

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach("WSFLLogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})

    mail.send(msg)
    
    
def send_survey_reminder_email(mail, email, firstname, requested_by, from_org):
    login_link = url_for('auth_bp.login', _external=True)

    msg = Message(
        subject="Reminder: Complete Your Self Review",
        sender=(f"{requested_by} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[email]
    )

    msg.body = f"""
    Kia ora {firstname},

    This is a friendly reminder to log into the WSFL site and complete your self review.

    This reminder was sent at the request of an administrator from {from_org or "WSFL"}.

    Please click the link below to log in:
    {login_link}

    Ngā mihi,
    The WSFL Team
    """

    msg.html = f"""
    <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
      <p>Kia ora <strong>{firstname}</strong>,</p>
      <p>This is a friendly reminder to log into the <strong>WSFL</strong> site and complete your self review.</p>
      <p>This reminder was sent at the request of an administrator from <strong>{from_org}</strong>.</p>
      <p><a href="{login_link}">Click here to log in</a></p>
      <p>Ngā mihi,<br>The WSFL Team</p>
      <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
    </div>
    """

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach("WSFLLogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})

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
    
    
def send_elearning_reminder_email(mail, email, firstname, requested_by, from_org, course_statuses):
    # Badge color map
    status_badges = {
        'Passed': 'green',
        'In Progress': 'orange',
        'Not Started': 'grey',
        'Cancelled': 'red',
        'Not enrolled': 'grey'
    }

    # Build the rows
    course_rows = ""
    for course_name, status in course_statuses:
        color = status_badges.get(status, 'grey')
        course_rows += f"""
        <div style="display: flex; justify-content: space-between; align-items: center; margin: 8px 0; font-size: 14px;">
            <div style="flex: 1; padding-right: 12px;">{course_name}</div>
            <div>
                <span style="display: inline-block; padding: 3px 10px; font-size: 13px; border-radius: 4px; background-color: {color}; color: white;">
                    {status}
                </span>
            </div>
        </div>
        """

    # Construct message
    msg = Message(
        subject="Reminder: Complete Your eLearning Courses",
        sender=(f"{requested_by} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[email]
    )

    # -----------------------
    # Plain-text version
    # -----------------------
    msg.body = f"""
Kia ora {firstname},

This is a reminder to complete your eLearning courses with WSFL via SportTutor.

You can either follow the instructions in the attached PDF or go directly to the SportTutor website to continue:

https://sporttutor.nz/ilp/pages/catalogsearch.jsf?catalogId=3712496&menuId=3712463&locale=en-GB&showbundlekeys=false&client=watersafetynz&sidebarExpanded=true&q=*:*&rows=9

Ngā mihi,
{requested_by}
(via Water Skills for Life)
"""

    # -----------------------
    # HTML version
    # -----------------------
    msg.html = f"""
<div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
  <p>Kia ora <strong>{firstname}</strong>,</p>
  
  <p>This is a reminder to complete your eLearning courses with WSFL via SportTutor.</p>
  
  <p>Below is your current course status at the time this email was sent:</p>

  <div style="background-color: #f5f5f5; border-radius: 8px; padding: 16px 20px; margin-top: 20px; display: inline-block; max-width: 500px;">
    {course_rows}
  </div>

  <p>You can follow the instructions in the attached PDF, or click the link below to access your courses:</p>
  <p>
    <a href="https://sporttutor.nz/ilp/pages/catalogsearch.jsf?catalogId=3712496&menuId=3712463&locale=en-GB&showbundlekeys=false&client=watersafetynz&sidebarExpanded=true&q=*:*&rows=9">
      View your eLearning courses on SportTutor
    </a>
  </p>

  <p>
    Ngā mihi,<br>
    <strong>{requested_by}</strong><br>
    <span style="color:#555;">(via Water Skills for Life)</span>
  </p>

  <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
</div>
"""

    # Attach logo
    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach(
            "WSFLLogo.png",
            "image/png",
            fp.read(),
            disposition='inline',
            headers={"Content-ID": "<wsfl_logo>"}
        )

    # Attach PDF
    with current_app.open_resource("static/eLearningGuide.pdf") as pdf_fp:
        msg.attach("eLearningGuide.pdf", "application/pdf", pdf_fp.read())

    mail.send(msg)
