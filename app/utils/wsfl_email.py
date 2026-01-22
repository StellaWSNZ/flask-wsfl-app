#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send WSFL welcome/login emails and set temp passwords.

- Loads DB/SMTP creds from .env
- Updates the user’s HashPassword with a generated temp password
- Sends rich HTML + plain-text email
"""

import os, re, ssl, smtplib, bcrypt, time
import traceback
from email.message import EmailMessage
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from html import escape as html_escape  # for safe HTML
from flask import current_app, render_template, url_for
from app.utils.database import get_db_engine
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
        # 👇 Add this line
        f"Full instructions and videos are available any time at https://wsfl.onrender.com/instructions/School\n\n"
        # f"{guides_line_text()}\n"
        f"Login details\n"
        f"    • Sign in: {LOGIN_URL}\n"
        f"    • Username: {actual_email}\n"
        f"    • Temporary password: your lowercase surname (keep punctuation such as apostrophes and hyphens) "
        f"followed immediately by your school's MOE number (no spaces).\n\n"
        f"Getting started\n"
        f"    • Check all classes are present and correct (Step 1).\n"
        f"    • Check staff access is up to date—add/remove as needed (Step 2).\n"
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

    smtp_user = (SMTP_USER or "").strip()
    smtp_pass = (SMTP_PASS or "").strip()

    # Safe debug (no password content)
    try:
        print(
            f"SMTP: user={smtp_user!r} pass_len={len(smtp_pass)} host={SMTP_HOST} port={SMTP_PORT}",
            flush=True
        )
    except Exception:
        # if current_app isn't available in some contexts
        print(f"SMTP: user={smtp_user!r} pass_len={len(smtp_pass)} host={SMTP_HOST} port={SMTP_PORT}")

    if not smtp_user or not smtp_pass:
        raise RuntimeError("SMTP_USER/SMTP_PASS missing or empty (check Render env vars)")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)
        time.sleep(SEND_DELAY_SEC)

# =====================================================
# Main function to create user + send email
# =====================================================
def create_user_and_send(first_name, surname, email, moe_number, is_admin=0):
    """Call stored proc to create/update user, set password, and send email."""
    temp_pw = temp_password(surname, moe_number)
    hashed  = bcrypt.hashpw(temp_pw.encode(), bcrypt.gensalt()).decode()

    with engine.begin() as conn:
        # Upsert via stored proc
        conn.execute(
            text("EXEC dbo.AddOrUpdateSchoolUser "
                 "@FirstName=:first, @Surname=:sur, "
                 "@Email=:email, @MOENumber=:moe, @Hash=:hash, @Admin=:admin"),
            {"first": first_name, "sur": surname,
             "email": email, "moe": moe_number, "hash": hashed, "admin": is_admin}
        )

        # School name via helper SP (you confirmed @Number is correct)
        school = conn.execute(
            text("EXEC FlaskHelperFunctions @Request='SchoolName', @Number=:m"),
            {"m": moe_number}
        ).scalar() or ""

    msg = build_message(email, first_name, school, temp_pw)
    send_email(msg)
    return temp_pw
from email.message import EmailMessage
from flask import current_app, url_for, render_template
from sqlalchemy import text
from .database import get_db_engine  # or whatever your helper is called

def send_account_invites(
    recipients,
    make_admin: bool,
    invited_by_name: str,
    invited_by_org: str | None = None,
):
    """
    recipients: iterable of dicts with at least:
      - email
      - firstname (optional)
      - role      (PRO/MOE/FUN/GRP/ADM)

    make_admin: if True, set user_admin = 1 (via stored proc).
    Returns: (sent_count, failed_count)
    """
    engine = get_db_engine()
    sent = 0
    failed = 0

    # Build URLs + load logo once
    with current_app.app_context():
        login_url = url_for("auth_bp.login", _external=True)
        forgot_url = url_for("auth_bp.forgot_password", _external=True)
        instructions_url = url_for("instructions_bp.instructions_me", _external=True)

        logo_path = os.path.join(current_app.static_folder, "WSFLLogo.png")
        try:
            with open(logo_path, "rb") as f:
                logo_bytes = f.read()
        except OSError:
            current_app.logger.exception("Could not read WSFLLogo.png at %s", logo_path)
            logo_bytes = None

        logo_cid = "wsfl-logo"  # used in src="cid:wsfl-logo"

    with engine.begin() as conn:
        for rec in recipients:
            email = (rec.get("email") or "").strip()
            if not email:
                current_app.logger.warning("Skipping invite with no email: %r", rec)
                failed += 1
                continue

            firstname = rec.get("firstname") or ""
            role = rec.get("role") or ""

            # 1) Activate + (optionally) give admin via stored procedure
            try:
                conn.execute(
                    text(
                        "EXEC FlaskActivateUserByEmail "
                        "@Email = :email, @MakeAdmin = :make_admin"
                    ),
                    {
                        "email": email,
                        "make_admin": 1 if make_admin else 0,
                    },
                )
            except Exception:
                current_app.logger.exception(
                    "Failed to activate user via FlaskActivateUserByEmail for %s", email
                )
                failed += 1
                continue

            # 2) Build + send email
            try:
                context = {
                    "firstname": firstname,
                    "role": role,
                    "login_url": login_url,
                    "forgot_url": forgot_url,
                    "instructions_url": instructions_url,
                    "make_admin": make_admin,
                    "logo_cid": logo_cid,
                    "invited_by_name": invited_by_name,
                    "invited_by_org": invited_by_org,
                }

                subject = "Water Skills for Life – account invitation"
                if make_admin:
                    subject += " (admin access)"

                text_body = render_template("emails/account_invite.txt", **context)
                html_body = render_template("emails/account_invite.html", **context)

                msg = EmailMessage()
                msg["Subject"] = subject
                msg["From"] = os.getenv("EMAIL")
                msg["To"] = email

                # Plain text part
                msg.set_content(text_body)

                # HTML part
                msg.add_alternative(html_body, subtype="html")

                # Embed logo into the HTML part with CID
                if logo_bytes:
                    # payload[0] = text/plain, payload[1] = text/html
                    html_part = msg.get_payload()[-1]
                    html_part.add_related(
                        logo_bytes,
                        maintype="image",
                        subtype="png",
                        cid=f"<{logo_cid}>",
                        filename="WSFLLogo.png",
                    )

                # Re-use existing SMTP helper
                send_email(msg)
                sent += 1

            except Exception:
                current_app.logger.exception("Failed to send invite to %s", email)
                failed += 1

    return sent, failed