#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send WSFL welcome/login emails and set temp passwords.

- Loads DB/SMTP creds from .env
- Updates the user’s HashPassword with a generated temp password
- Sends rich HTML + plain-text email
"""

import os, re, ssl, smtplib, bcrypt, time
from email.message import EmailMessage
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from html import escape as html_escape  # for safe HTML

# =====================================================
# .env and constants
# =====================================================
load_dotenv()

DB_URL       = os.getenv("DB_URL")
SMTP_HOST    = "smtp.office365.com"
SMTP_PORT    = 587
SMTP_USER    = os.getenv("EMAIL")
SMTP_PASS    = os.getenv("WSNZADMINPASS")
FROM_EMAIL   = os.getenv("EMAIL")
FROM_NAME    = "WSFL Team • Water Safety NZ"
REPLY_TO     = os.getenv("REPLY_TO", FROM_EMAIL)

DEBUG_SEND           = False
DEBUG_ROUTE_TO       = "stellajanemcgann@gmail.com"
DEBUG_SUBJECT_PREFIX = "[TEST] "
SEND_DELAY_SEC       = 0.7
CC_EMAILS            = ["Funding@watersafety.org.nz"]
CC_ON_DEBUG          = False

SUBJECT        = "WSFL database access - your login + instructions"
LOGIN_URL      = "https://wsfl.onrender.com/auth/login"
FORGOT_URL     = "https://wsfl.onrender.com/auth/forgot-password"
INTRO_LINE     = "Welcome to the Water Skills for Life web application – your place to view classes, record achievements, and access reports."
PROVIDER_NAME  = "Your provider"
HISTORY_YEARS  = 2
SECTION_HEADING= "Other notes"
SUPPORT_LINE   = "If you need help logging in, reply to this email. For other issues use our feedback form: https://wsfl.onrender.com/feedback"
PRIVACY_LINE   = "Student info is kept secure and used only for WSFL reporting; NSN/DOB are not shown alongside results."
NAV_PATH_PLAIN = 'School Tools > Class Lookup'
SIGN_OFF_NAME  = "Stella McGann"
SIGN_OFF_TITLE = "WSFL Administration Team"

engine = create_engine(DB_URL, connect_args={"TrustServerCertificate": "yes"})

# =====================================================
# Helpers
# =====================================================
def smart_title(name: str) -> str:
    if not name:
        return ""
    def cap_piece(p): return p[:1].upper() + p[1:].lower()
    parts = []
    for token in name.split(" "):
        sub = re.split(r"([\'\-])", token)
        sub = [cap_piece(s) if s and s not in {"'", "-"} else s for s in sub]
        parts.append("".join(sub))
    return " ".join(parts)

def guides_line_text() -> str:
    return (
        "You can access the user guides via Dropbox (no sign-in required): "
        "https://www.dropbox.com/scl/fo/zi08g6x7zolu9llggx1c4/AKEj806mYjR_tA71JSbq07Q?dl=0\n"
    )

def temp_password(surname: str, moe_id: int) -> str:
    clean = "".join(ch for ch in surname.lower().strip() if ch != " ")
    return f"{clean}{moe_id}"

def build_plain_body(actual_email, first_name, school_name,
                     debug=False, cc_list=None, temp_pw=""):
    fname  = smart_title((first_name or "").strip() or "team")
    school = (school_name or "").strip() or "your school"
    cc_display = (", ".join(cc_list) if cc_list else "-")
    debug_note = (
        f"(TEST routed to {DEBUG_ROUTE_TO}. Intended To: {actual_email}; Intended Cc: {cc_display})\n\n"
        if debug else ""
    )
    return (
        f"{debug_note}"
        f"Kia ora {fname},\n\n"
        f"{INTRO_LINE} {PROVIDER_NAME} may have uploaded {school}'s class lists to the WSFL database already. "
        f"If your lists have been uploaded, you'll see them under \"{NAV_PATH_PLAIN}\". "
        f"When you have a moment, please log in and check everything looks right.\n\n"
        f"{guides_line_text()}\n"
        f"Login details\n"
        f"    • Sign in: {LOGIN_URL}\n"
        f"    • Username: {actual_email}\n"
        f"    • Temporary password: your lowercase surname (keep punctuation such as apostrophes and hyphens)\n\n"
        f"Getting started\n"
        f"    • Check all classes are present and correct (Step 1).\n"
        f"    • Check staff access is up to date-add/remove as needed (Step 2).\n"
        f"    • Optional: View and record class achievements (Step 3).\n"
        f"    • Optional: View performance reports (e.g., \"YTD Performance vs National Target\") (Step 4).\n"
        f"    • Optional: View previously submitted data for your classes from the past {HISTORY_YEARS} years (Step 4).\n"
        f"    • If anything is missing or incorrect, report an issue (Step 5).\n\n"
        f"{SECTION_HEADING}\n"
        f"    • To set or change your password, use \"Forgot password\": {FORGOT_URL}\n"
        f"    • {PRIVACY_LINE}\n\n"
        f"If someone else manages uploads or reporting at {school} and you've received this email in error, "
        f"please reply with their name and email and we'll set them up accordingly.\n\n"
        f"{SUPPORT_LINE}\n\n"
        f"Ngā mihi,\n"
        f"{SIGN_OFF_NAME}\n"
        f"{SIGN_OFF_TITLE}\n"
    )

def build_message(email, first_name, school_name, temp_pw):
    to_addr = DEBUG_ROUTE_TO if DEBUG_SEND else email
    cc_list = CC_EMAILS if (not DEBUG_SEND or CC_ON_DEBUG) else []
    subject = (DEBUG_SUBJECT_PREFIX if DEBUG_SEND else "") + SUBJECT

    # ----- Plain text (unchanged) -----
    plain = build_plain_body(
        email, first_name, school_name,
        debug=DEBUG_SEND, cc_list=cc_list, temp_pw=temp_pw
    )

    # ----- Prepare HTML safely (no complex expressions inside braces) -----
    debug_banner_html = (
        f"<p style='color:#b00;font-weight:600;margin:0 0 12px;'>(TEST to {DEBUG_ROUTE_TO}; intended To: {email})</p>"
        if DEBUG_SEND else ""
    )
    safe_plain = html_escape(plain).replace("\r\n", "\n")
    html_core = safe_plain.replace("\n\n", "</p><p>").replace("\n", "<br>")

    html_parts = [
        "<html><body style='margin:0;padding:0;background:#fff;'>",
        "<div style=\"font-family:Segoe UI, Arial, Helvetica, sans-serif; font-size:14px; "
        "line-height:1.5; color:#222; padding:16px;\">",
        debug_banner_html,
        "<p>", html_core, "</p>",
        "</div></body></html>"
    ]
    html = "".join(html_parts)

    # ----- Build message -----
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"]      = to_addr
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if REPLY_TO:
        msg["Reply-To"] = REPLY_TO
    if DEBUG_SEND:
        msg["X-Intended-To"] = email

    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    return msg

def send_email(msg: EmailMessage):
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
        time.sleep(SEND_DELAY_SEC)

# =====================================================
# Main function to create user + send email
# =====================================================
def create_user_and_send(first_name, surname, email, moe_number):
    """Call stored proc to create/update user, set password, and send email."""
    temp_pw = temp_password(surname, moe_number)
    hashed  = bcrypt.hashpw(temp_pw.encode(), bcrypt.gensalt()).decode()

    with engine.begin() as conn:
        # Upsert via stored proc
        conn.execute(
            text("EXEC dbo.AddOrUpdateSchoolUser "
                 "@FirstName=:first, @Surname=:sur, "
                 "@Email=:email, @MOENumber=:moe, @Hash=:hash"),
            {"first": first_name, "sur": surname,
             "email": email, "moe": moe_number, "hash": hashed}
        )

        # School name via helper SP (you confirmed @Number is correct)
        school = conn.execute(
            text("EXEC FlaskHelperFunctions @Request='SchoolName', @Number=:m"),
            {"m": moe_number}
        ).scalar() or ""

    msg = build_message(email, first_name, school, temp_pw)
    send_email(msg)
    return temp_pw
