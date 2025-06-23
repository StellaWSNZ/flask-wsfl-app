# app/utils/email.py
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

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
def verify_reset_token(token, max_age=3600):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        return serializer.loads(token, salt='password-reset-salt', max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None

def send_account_setup_email(mail, recipient_email, first_name, role, is_admin, invited_by_name, inviter_desc):
    role_names = {
        "MOE": "a School User",
        "FUN": "a Funder User",
        "PRO": "a Provider User",
        "ADM": "an Administrator"
    }
    role_display = role_names.get(role, role)
    admin_note = " with administrator privileges" if is_admin else ""

    # Add contextual tail if inviter_desc == role_display
    if inviter_desc == role_display:
        if role == "FUN":
            context_tail = " for their funded organisation"
        elif role == "MOE":
            context_tail = " for their school"
        elif role == "PRO":
            context_tail = " for their provider"
        elif role == "ADM":
            context_tail = " for their team"
        else:
            context_tail = ""
    else:
        context_tail = ""

    msg = Message(
        subject="You've Been Invited to WSFL",
        sender=(f"{invited_by_name} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[recipient_email]
    )

    msg.body = f"""\
    Kia ora {first_name},

    {invited_by_name}{f" from {inviter_desc}" if inviter_desc else ""} has invited you to join the Water Skills for Life (WSFL) platform as a {role_display}{admin_note}{context_tail}.

    To get started, go to the login page and click "Forgot password" to set your password:
    {url_for('auth_bp.login', _external=True)}

    Ngā mihi,
    The WSFL Team
    """

    msg.html = f"""\
    <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
    <p>Kia ora <strong>{first_name}</strong>,</p>
    <p><strong>{invited_by_name}</strong> from <strong>{inviter_desc}</strong> has invited you to join the <strong>Water Skills for Life</strong> platform as <strong>{role_display}{admin_note}{context_tail}</strong>.</p>
    <p>To get started, visit the <a href="{url_for('auth_bp.login', _external=True)}">login page</a> and click <strong>"Forgot password"</strong> to set your password.</p>
    <p>Ngā mihi nui,<br>The WSFL Team</p>
    <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
    </div>
    """

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach("WSFLLogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})

    mail.send(msg)
    
    
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

def send_survey_invite_email(mail, recipient_email, first_name, role, user_id, survey_id, invited_by_name):
    link = generate_survey_link(
        email=recipient_email,
        firstname=first_name,
        lastname="",  # Optional: update if needed
        role=role,
        user_id=user_id,
        survey_id=survey_id
    )

    msg = Message(
        subject="You're Invited to Complete a WSFL Survey",
        sender=(f"{invited_by_name} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"]),
        recipients=[recipient_email]
    )

    msg.body = f"""\
    Kia ora {first_name},

    You’ve been invited to complete a Water Skills for Life (WSFL) survey.

    Please click the link below to get started:
    {link}

    If you have any questions, feel free to reply to this email.

    Ngā mihi,
    The WSFL Team
    """

    msg.html = f"""\
    <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
      <p>Kia ora <strong>{first_name}</strong>,</p>
      <p>You’ve been invited to complete a <strong>Water Skills for Life</strong> survey.</p>
      <p><a href="{link}">Click here to begin the survey</a></p>
      <p>If you have any questions, feel free to reply to this email.</p>
      <p>Ngā mihi,<br>The WSFL Team</p>
      <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
    </div>
    """

    with current_app.open_resource("static/WSFLLogo.png") as fp:
        msg.attach("WSFLLogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})

    mail.send(msg)
    
    
def send_survey_reminder_email(mail, email, firstname, requested_by, from_org):
    msg = Message(
        subject="Reminder: Complete Your Self Review",
        recipients=[email],
        sender=(f"{requested_by} via WSFL", current_app.config["MAIL_DEFAULT_SENDER"])
    )
    msg.html = f"""
    <p>Kia ora {firstname},</p>
    <p>This is a friendly reminder to log into the WSFL site and complete your self review.</p>
    <p>This reminder was sent at the request of an administrator from <strong>{from_org}</strong>.</p>
    <p><a href="{url_for('auth_bp.login', _external=True)}">Click here to log in</a></p>
    <p>Ngā mihi,<br>WSFL Team</p>
    """
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