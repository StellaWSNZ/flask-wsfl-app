"""
WSFL help email sender

- Pulls recipients from FlaskLogin
- Sends a short “how to get help” email addressed to FirstName
- Does NOT set or change passwords
- Sends an HTML email via Office365 SMTP (Flask-Mail)
- Optional attachments
- Embeds a small WSFL logo below the signature (inline CID image)
- Uses DB_URL_CUSTOM from .env
- DEBUG mode sends only the first email to a debug address

Requires:
  pip install pandas sqlalchemy pyodbc flask flask-mail python-dotenv
"""

import mimetypes
import os
import traceback
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from flask import Flask
from flask_mail import Mail, Message
from dotenv import load_dotenv


# =========================
# Load environment variables
# =========================
load_dotenv()


# =========================
# Config
# =========================
DEBUG = False
debug_email = "stella@watersafety.org.nz"

EMAIL_SUBJECT = "Water Skills for Life – Getting help in the web app"

# Links
instructions_url = "https://wsfl.onrender.com/instructions"
faq_url = "https://wsfl.onrender.com/FAQ"
feedback_url = "https://wsfl.onrender.com/feedback"

# Optional file attachments (leave empty if not attaching)
ATTACHMENTS: list[str] = []

# Inline logo (below signature)
LOGO_PATH = "app/static/WSFLLogo.png"
LOGO_CID = "wsfl_logo"


# =========================
# Flask app + Mail config
# =========================
app = Flask(__name__)
mail = Mail()

app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.office365.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", "587")),
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.getenv("EMAIL"),
    MAIL_PASSWORD=os.getenv("WSNZADMINPASS"),
    MAIL_DEFAULT_SENDER=("WSFL Web Application", os.getenv("EMAIL")),
)

mail.init_app(app)


# =========================
# DB connection
# =========================
def get_engine():
    db_url = os.getenv("DB_URL_CUSTOM")
    if not db_url:
        raise RuntimeError("DB_URL_CUSTOM is not set in environment variables.")
    return create_engine(db_url, pool_pre_ping=True, future=True)


def normalize_email(e: str) -> str:
    return (e or "").strip().lower()


# =========================
# HTML helpers
# =========================
def _esc(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )


def build_help_email_html(*, firstname: str) -> str:
    firstname_safe = _esc((firstname or "").strip() or "there")

    return f"""
<p>Kia ora {firstname_safe},</p>

<p>
  Great to see you’ve been able to log in to the Water Skills for Life web application.
  We hope you’re finding it easy to navigate your way around.
</p>

<p>
  If you run into any issues, the quickest way to get help is:
</p>

<ol>
  <li>
    Click the <strong>Help</strong> button in the navigation bar and check:
    <ul>
      <li><strong>Instructions:</strong> <a href="{instructions_url}">{instructions_url}</a></li>
      <li><strong>FAQs:</strong> <a href="{faq_url}">{faq_url}</a></li>
    </ul>
  </li>
  <li>
    If you’re still stuck, please use the <strong>Feedback form</strong> so we can track and resolve it:
    <br />
    <a href="{feedback_url}">{feedback_url}</a>
  </li>
</ol>

<p>
  The feedback form is best for web app or database-related issues
  (for example: login problems, missing classes or students, or upload and validation errors).
</p>

<p>
  For questions about other parts of the wider programme (outside the web app),
  please contact your programme lead or Esther Hone <a href="mailto:Esther@watersafety.org.nz">Esther@watersafety.org.nz</a>.
</p>

<p>
  Ngā mihi,<br />
  Water Skills for Life Administration
</p>

<img src="cid:{LOGO_CID}"
     alt="Water Skills for Life"
     style="margin-top: 12px; width: 120px; height: auto;" />

<hr style="margin-top: 20px" />
<p style="font-size: 12px; color: #666">
  This email was sent by Stella McGann from Water Safety New Zealand via the WSFL Web Application.
</p>
""".strip()


# =========================
# Email send
# =========================
def send_email(*, to_addr: str, subject: str, html_body: str, attachments: list[str] | None = None):
    msg = Message(subject=subject, recipients=[to_addr])
    msg.html = html_body

    # Attach inline logo (CID)
    if LOGO_PATH and os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as f:
            msg.attach(
                filename=os.path.basename(LOGO_PATH),
                content_type="image/png",
                data=f.read(),
                disposition="inline",
                headers={"Content-ID": f"<{LOGO_CID}>"},
            )
    else:
        print(f"[WARN] Logo not found at {LOGO_PATH} (continuing without logo).")

    # Optional file attachments
    for fp in (attachments or []):
        path = Path(fp)
        if not path.exists():
            raise FileNotFoundError(f"Attachment not found: {path}")

        ctype, _ = mimetypes.guess_type(str(path))
        if not ctype:
            ctype = "application/octet-stream"

        with path.open("rb") as f:
            msg.attach(filename=path.name, content_type=ctype, data=f.read())

    mail.send(msg)


# =========================
# Main batch
# =========================
def run_batch():
    engine = get_engine()

    # Pull recipients from FlaskLogin.
    # NOTE: You currently have TOP(1) in your query — remove it for full sending.
    q = """
    SELECT 
        Email,
        FirstName,
        Role,
        Admin,
        Active
    FROM FlaskLogin f
    WHERE Email IS NOT NULL
      AND LTRIM(RTRIM(Email)) <> ''
      AND (Active = 1 OR Active IS NULL)
      AND HashPassword IS NOT NULL
      AND EXISTS (
            SELECT TOP(1) 1
            FROM FlaskLoginAttempts a
            WHERE a.Email = f.Email
              AND (a.Successful IS NULL OR a.Successful = 1)
      )
    ORDER BY Email
    """

    df = pd.read_sql(text(q), engine)

    sent = failed = skipped = 0

    with app.app_context():
        for i, row in df.iterrows():
            # DEBUG sends only first row, and redirects to debug_email
            if DEBUG and i != 0:
                break

            real_to = str(row.get("Email") or "").strip()
            if not real_to:
                skipped += 1
                continue

            firstname = str(row.get("FirstName") or "").strip()

            to_addr = debug_email if DEBUG else real_to
            if DEBUG:
                print(f"[DEBUG] Would send to {real_to} -> sending to {to_addr}")

            try:
                html = build_help_email_html(firstname=firstname)
                send_email(
                    to_addr=to_addr,
                    subject=EMAIL_SUBJECT,
                    html_body=html,
                    attachments=ATTACHMENTS,
                )
                sent += 1
            except Exception as e:
                failed += 1
                print(f"❌ Failed for recipient={real_to}: {e}")
                print(traceback.format_exc())

    print(f"Done. Sent={sent}, Failed={failed}, Skipped={skipped}, TotalRows={len(df)}")


if __name__ == "__main__":
    run_batch()
