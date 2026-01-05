"""
Batch invite sender (WSFL / Life Savings)

- Pulls recipients from TEMP_StuffDetails joined to MOE_SchoolDirectory + Provider
- Creates/updates users in FlaskLogin with a TEMP password:
    temp_password = <surname lowercase, stripped of spaces/punctuation> + <MOENumber>
- Stores BCRYPT hash in FlaskLogin.HashPassword
- Sends an HTML email via Office365 SMTP (Flask-Mail)
- Attaches PDF instructions
- Includes dropdown settings bullet (Term/Year/Provider/School/Funder)
- Uses DB_URL_CUSTOM from .env
- DEBUG mode sends only the first email to a debug address

Requires:
  pip install pandas sqlalchemy pyodbc flask flask-mail python-dotenv bcrypt
"""

import mimetypes
import os
import re
from pathlib import Path
import traceback

import bcrypt
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
DEBUG = True
DEBUG_WRITE_DB = False  # keep False unless you explicitly want DB writes during debug
debug_email = "stellajanemcgann@gmail.com"

# If an account already exists:
# - If True: ONLY set password if HashPassword is NULL/empty
# - If False: never touch existing users' passwords
SET_PASSWORD_IF_MISSING_FOR_EXISTING_USERS = True

ATTACHMENTS = [
    r"app/static/instructions/MOE/Upload Class List.PDF",
]

# Links
login_url = "https://wsfl.onrender.com/auth/login"
forgot_url = "https://wsfl.onrender.com/auth/forgot-password"
instructions_url = "https://wsfl.onrender.com/instructions"

# Dropdown defaults for this batch
TERM = 1
YEAR = 2026
FUNDER = "Life Savings Campaign"

EMAIL_SUBJECT = "Water Skills for Life – Account Invitation"


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
    MAIL_DEFAULT_SENDER= ("WSFL Web Application", os.getenv("EMAIL")),
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


# =========================
# Password helpers
# =========================
def normalize_surname_for_password(surname: str) -> str:
    """
    lastname in lowercase, no spaces, no punctuation.
    Keeps letters+numbers only.
    """
    s = (surname or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)  # remove spaces/punct/diacritics etc.
    return s


def make_temp_password(surname: str, moenumber: int) -> str:
    base = normalize_surname_for_password(surname)
    if not base:
        # fallback so we never generate a blank password
        base = "user"
    return f"{base}{int(moenumber)}"


def bcrypt_hash_password(plain: str) -> str:
    """
    Returns a bcrypt hash string suitable for storing in NVARCHAR.
    """
    pw_bytes = plain.encode("utf-8")
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


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


def build_invite_html(
    *,
    firstname: str,
    login_url: str,
    forgot_url: str,
    instructions_url: str,
    term: int,
    year: int,
    provider_name: str,
    school_name: str,
    funder_name: str,
    temp_password: str,
) -> str:
    firstname_safe = _esc((firstname or "").strip())
    provider_safe = _esc((provider_name or "").strip())
    school_safe = _esc((school_name or "").strip())
    funder_safe = _esc((funder_name or "").strip())
    pw_safe = _esc(temp_password)

    dropdown_line = f"""
      <li>
        When uploading class lists, set the dropdowns to:
        <strong>Term:</strong> Term {term},
        <strong>Year:</strong> {year},
        <strong>Provider:</strong> {provider_safe},
        <strong>School:</strong> {school_safe},
        <strong>Funder:</strong> {funder_safe}.
      </li>
    """

    return f"""
<p>Kia ora {firstname_safe},</p>

<p>
  Welcome to the Water Skills for Life web application. This is where you can
  upload class lists, record student achievements, and view class information for your school.
</p>

<p><strong>Your login details</strong></p>
<ul>
  <li><strong>Username:</strong> your email address</li>
  <li>
    <strong>Temporary password:</strong>
    your <em>surname (lowercase, no spaces or punctuation)</em>
    followed immediately by your school’s <em>MOE number</em>.
  </li>
</ul>

<p>
  You can log in here:
  <a href="{ login_url }">{ login_url }</a>
</p>

<p>
  Once logged in, you’re welcome to change your password at any time using
  <strong>Forgot password</strong>.
</p>


<p><strong>Getting started</strong></p>
<ul>
  {dropdown_line}
  <li>Navigate to the <strong>Class Lists</strong> page to upload your file, then <strong>validate and submit</strong>.</li>
  <li><strong>Reminder:</strong> To support Life Savings reporting, please upload your class lists by <strong>Tuesday, 30 January at 5:00 pm</strong>.</li>
  <li>Check that your classes are listed correctly for the current term.</li>
  <li>View class summaries, school reports and overall progress throughout the term.</li>
  <li>Open your Profile page to check your name and email details are correct.</li>
</ul>

<p>
  Step-by-step instructions and short videos are available here:
  <a href="{instructions_url}">{instructions_url}</a>.
</p>

<p>
  For convenience, PDF instructions for uploading
  class lists are attached to this email.
</p>

<p>
  If you have any trouble logging in, please reply to this email.
  If anything looks incorrect (for example, missing classes or students),
  use the in-app feedback form so we can track and resolve it quickly.
</p>

<p>Ngā mihi,<br />Water Skills for Life Administration Team</p>

<hr style="margin-top: 20px" />
<p style="font-size: 12px; color: #666">
  This invitation was sent by Water Safety New Zealand.
</p>
""".strip()


# =========================
# Email send
# =========================
def send_invite_email(*, to_addr: str, subject: str, html_body: str, attachments: list[str] | None = None):
    msg = Message(subject=subject, recipients=[to_addr])
    msg.html = html_body

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
# DB insert/update helpers
# =========================
def upsert_login_with_temp_password(
    conn,
    *,
    email: str,
    moenumber: int,
    firstname: str,
    surname: str,
    admin: int = 1,
) -> tuple[str, str]:
    """
    Ensures a user exists in FlaskLogin and has a password hash.

    Returns: (action, temp_password)
      action in {"inserted", "updated_password", "already_exists", "skipped"}
    """
    email_norm = normalize_email(email)
    if not email_norm:
        return ("skipped", "")

    moenumber_i = int(moenumber)

    # Compute temp password + hash
    temp_pw = make_temp_password(surname=surname, moenumber=moenumber_i)
    temp_hash = bcrypt_hash_password(temp_pw)

    row = conn.execute(
        text("""
            SELECT Email, HashPassword
            FROM FlaskLogin
            WHERE LOWER(LTRIM(RTRIM(Email))) = :e
        """),
        {"e": email_norm},
    ).fetchone()

    if row is None:
        conn.execute(
            text("""
                INSERT INTO FlaskLogin
                    (Email, HashPassword, Role, ID, FirstName, Admin, Surname, Active, AlternateEmail, Hidden)
                VALUES
                    (:Email, :HashPassword, 'MOE', :ID, :FirstName, :Admin, :Surname, 1, NULL, 0)
            """),
            {
                "Email": email_norm,
                "HashPassword": temp_hash,
                "ID": moenumber_i,
                "FirstName": (firstname or "").strip(),
                "Admin": int(admin),
                "Surname": (surname or "").strip(),
            },
        )
        return ("inserted", temp_pw)

    # Exists already
    existing_hash = row._mapping.get("HashPassword") if hasattr(row, "_mapping") else row[1]

    if SET_PASSWORD_IF_MISSING_FOR_EXISTING_USERS:
        if existing_hash is None or str(existing_hash).strip() == "":
            conn.execute(
                text("""
                    UPDATE FlaskLogin
                    SET HashPassword = :HashPassword
                    WHERE LOWER(LTRIM(RTRIM(Email))) = :e
                """),
                {"HashPassword": temp_hash, "e": email_norm},
            )
            return ("updated_password", temp_pw)

    return ("already_exists", temp_pw)


# =========================
# Main batch
# =========================
def run_batch():
    engine = get_engine()

    q = """
    SELECT
        t.firstname,
        t.surname,
        t.[Contact email] AS ContactEmail,
        t.MOENumber,
        sd.SchoolName,
        t.ProviderID,
        p.Description AS ProviderName
    FROM TEMP_StuffDetails t
    JOIN MOE_SchoolDirectory sd ON sd.MOENumber = t.MOENumber
    JOIN Provider p ON p.ProviderID = t.ProviderID
    """

    df = pd.read_sql(text(q), engine)

    sent = failed = skipped = 0
    inserted = updated_pw = existed = 0

    with app.app_context():
        with engine.begin() as conn:
            for i, row in df.iterrows():
                if DEBUG and i != 0:
                    break

                real_to = str(row.get("ContactEmail") or "").strip()
                if not real_to:
                    print(f"Row {i}: missing ContactEmail, skipping.")
                    skipped += 1
                    continue

                provider_name = str(row.get("ProviderName") or "").strip()
                school_name = str(row.get("SchoolName") or "").strip()
                moenumber = row.get("MOENumber")
                firstname = str(row.get("firstname") or "").strip()
                surname = str(row.get("surname") or "").strip()

                if not provider_name or not school_name or pd.isna(moenumber):
                    print(f"Row {i}: missing ProviderName/SchoolName/MOENumber; skipping.")
                    skipped += 1
                    continue

                to_addr = debug_email if DEBUG else real_to
                if DEBUG:
                    print(f"[DEBUG] Would send to {real_to} -> sending to {to_addr}")

                try:
                    # 1) Upsert user (or simulate)
                    if DEBUG and not DEBUG_WRITE_DB:
                        action = "debug_no_db_write"
                        temp_pw = make_temp_password(surname=surname, moenumber=int(moenumber))
                        print(f"[DEBUG] Would upsert FlaskLogin for {normalize_email(real_to)} (action={action})")
                    else:
                        action, temp_pw = upsert_login_with_temp_password(
                            conn,
                            email=real_to,
                            moenumber=int(moenumber),
                            firstname=firstname,
                            surname=surname,
                            admin=1,
                        )

                    if action == "inserted":
                        inserted += 1
                    elif action == "updated_password":
                        updated_pw += 1
                    elif action == "already_exists":
                        existed += 1

                    # 2) Decide whether to email
                    # If you ONLY want to email new users, change this to: if action == "inserted":
                    if action in ("inserted", "updated_password", "already_exists", "debug_no_db_write"):
                        html = build_invite_html(
                            firstname=firstname,
                            login_url=login_url,
                            forgot_url=forgot_url,
                            instructions_url=instructions_url,
                            term=TERM,
                            year=YEAR,
                            provider_name=provider_name,
                            school_name=school_name,
                            funder_name=FUNDER,
                            temp_password=temp_pw,
                        )
                        send_invite_email(
                            to_addr=to_addr,
                            subject=EMAIL_SUBJECT,
                            html_body=html,
                            attachments=ATTACHMENTS,
                        )
                        sent += 1
                    else:
                        print(f"Row {i}: not sending email because action={action}")
                        skipped += 1

                except Exception as e:
                    failed += 1
                    print(f"❌ Failed for recipient={real_to}: {e}")
                    print(traceback.format_exc())

    print(
        f"Done. Sent={sent}, Failed={failed}, Skipped={skipped}, "
        f"Inserted={inserted}, UpdatedPassword={updated_pw}, AlreadyExisted={existed}, TotalRows={len(df)}"
    )


if __name__ == "__main__":
    run_batch()
