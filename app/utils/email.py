# app/utils/email.py
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

def send_reset_email(mail, email, token):
    reset_url = url_for('auth_bp.reset_password', token=token, _external=True)
    msg = Message('Reset Your WSFL Password', recipients=[email])
    msg.html = f"""
        <p>Kia ora,</p>
        <p>We received a request to reset your WSFL account password.</p>
        <p><a href="{reset_url}">Click here to reset your password</a></p>
        <p>If you didn't request this, you can safely ignore this email.</p>
        <p>Ngā mihi,<br><strong>WSFL Admin Team</strong></p>
        <img src="cid:wsfl_logo" alt="WSFL Logo" style="height:60px;margin-top:10px;">
    """
    with current_app.open_resource("static/darklogo.png") as fp:
        msg.attach("DarkLogo.png", "image/png", fp.read(), disposition='inline',
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
        "MOE": "School User",
        "FUN": "Funder User",
        "ADM": "Administrator"
    }
    role_display = role_names.get(role, role)

    admin_note = " with administrator privileges" if is_admin else ""

    msg = Message(
        subject="You've Been Invited to WSFL",
        sender="stella@watersafety.org.nz",
        recipients=[recipient_email]
    )

    msg.body = f"""\
    Kia ora {first_name},

    {invited_by_name} from {inviter_desc} has invited you to join the Water Skills for Life (WSFL) platform as a {role_display}{admin_note}.

    To get started, go to the login page and click "Forgot password" to set your password:
    {url_for('auth_bp.login', _external=True)}

    Ngā mihi,
    The WSFL Team
    """

    msg.html = f"""\
    <div style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
    <p>Kia ora <strong>{first_name}</strong>,</p>
    <p><strong>{invited_by_name}</strong> from <strong>{inviter_desc}</strong> has invited you to join the <strong>Water Skills for Life</strong> platform as a <strong>{role_display}{admin_note}</strong> for their organisation.</p>
    <p>To get started, visit the <a href="{url_for('auth_bp.login', _external=True)}">login page</a> and click <strong>"Forgot password"</strong> to set your password.</p>
    <p>Ngā mihi nui,<br>The WSFL Team</p>
    <img src="cid:wsfl_logo" alt="WSFL Logo" style="margin-top: 20px; width: 200px;">
    </div>
    """

    with current_app.open_resource("static/darklogo.png") as fp:
        msg.attach("darklogo.png", "image/png", fp.read(), disposition='inline',
                   headers={"Content-ID": "<wsfl_logo>"})

    mail.send(msg)

    