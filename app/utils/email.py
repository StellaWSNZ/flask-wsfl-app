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
