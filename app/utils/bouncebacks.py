"""
WSFL Bounce-back notifier (Flask-Mail)

- Takes a list of confirmed bounced emails (To/From/Subject)
- Groups by original sender
- Sends ONE debug email to stella@watersafety.org.nz when DEBUG_EMAIL=True
- When DEBUG_EMAIL=False, sends one email per original sender
- Supports mapping non-email "From" names (e.g., "CLM Otahuhu") to real email addresses

Requirements:
  pip install flask flask-mail python-dotenv

Env vars expected:
  EMAIL           (Office365 username, e.g. dbadmin@watersafety.org.nz)
  WSNZADMINPASS   (Office365 password / app password if applicable)
  MAIL_SERVER     (optional, default smtp.office365.com)
  MAIL_PORT       (optional, default 587)
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, List, Optional

from dotenv import load_dotenv
from flask import Flask
from flask_mail import Mail, Message

# =========================
# Load environment variables
# =========================
load_dotenv()

# =========================
# DEBUG settings
# =========================
DEBUG_EMAIL = False
DEBUG_ADDRESS = "stella@watersafety.org.nz"

# =========================
# Sender override map
# =========================
SENDER_EMAIL_MAP: Dict[str, str] = {
    "CLM Otahuhu": "roseanne.amataga@gmail.com",
}

# =========================
# Input: confirmed bounces
# =========================
BOUNCED_ROWS: List[Dict[str, str]] = [
    {"to": "nathaniel.smnford71@gmail.com", "from": "CLM Otahuhu", "subject": "Self Review"},
    {"to": "hopepsaris15@gmail.com", "from": "tracecy.lyon@ymcanorth.org.nz", "subject": "Account invitation, Self Review, eLearning"},
    {"to": "sarahb@dargavilleprimary.school.nz", "from": "phillips@dargavilleprimary.school.nz", "subject": "Account invitation, Self Review, eLearning"},
    {"to": "gill.thorn@naylandprimary.school.nz", "from": "leanne.jolly@naylandprimary.school.nz", "subject": "eLearning"},
    {"to": "dallas@riverveiw.school.nz", "from": "libbya@sportnorth.co.nz", "subject": "Account invitation"},
    {"to": "cathy.hadfield@kaurihohoreschool.co.nz", "from": "libbya@sportnorth.co.nz", "subject": "Account invitation"},
    {"to": "jessiet@dargavilleprimary.school.nz", "from": "libbya@sportnorth.co.nz", "subject": "Account invitation"},
    {"to": "alice.robbins@naylandprimary.school.nz", "from": "leanne.jolly@naylandprimary.school.nz", "subject": "Account invitation"},
    {"to": "krobinson@lowermoutere.school.nz", "from": "mlynch@lowermoutere.school.nz", "subject": "Account invitation"},
]

# =========================
# Flask-Mail app setup
# =========================
def create_app() -> Flask:
    app = Flask(__name__)

    app.config.update(
        MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.office365.com"),
        MAIL_PORT=int(os.getenv("MAIL_PORT", "587")),
        MAIL_USE_TLS=True,
        MAIL_USERNAME=os.getenv("EMAIL"),
        MAIL_PASSWORD=os.getenv("WSNZADMINPASS"),
        MAIL_DEFAULT_SENDER=("WSFL Web Application", os.getenv("EMAIL")),
    )

    return app


# =========================
# Helpers
# =========================
def resolve_sender_email(sender: str) -> Optional[str]:
    """
    Resolve the sender in the 'From' column to an actual email.
    - If it already looks like an email, return it.
    - Otherwise map it via SENDER_EMAIL_MAP.
    """
    if not sender:
        return None

    s = sender.strip()
    if "@" in s:
        return s

    return SENDER_EMAIL_MAP.get(s)


def validate_row(r: Dict[str, str]) -> bool:
    return bool(r.get("to")) and bool(r.get("from")) and bool(r.get("subject"))


def build_bounce_email_body(items: List[Dict[str, str]]) -> str:
    lines = "\n".join(
        f"- To  {r['to'].strip()} regarding {r['subject'].strip()}"
        for r in items
    )

    return f"""Kia ora,

Just letting you know that some emails you sent through the WSFL web app bounced back (delivery failed).

Details:
{lines}

Please check the recipient email address is correct and resend if needed.
You can update the email in the Manage my Staff Page.

Ngā mihi,
Stella
WSFL Web Application
Water Safety New Zealand
"""


# =========================
# Main sender
# =========================
def send_bounce_notifications(mail: Mail, rows: List[Dict[str, str]]) -> None:
    """
    Groups by sender and sends one email per sender.
    In DEBUG mode, sends ONE email only to DEBUG_ADDRESS.
    """
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)

    skipped = []
    for r in rows:
        if not validate_row(r):
            skipped.append(("missing required fields", r))
            continue

        sender_email = resolve_sender_email(r["from"])
        if not sender_email:
            skipped.append(("unknown sender email (needs mapping)", r))
            continue

        grouped[sender_email].append(r)

    if not grouped:
        print("No valid grouped items to send.")
        if skipped:
            print("\nSkipped rows:")
            for reason, r in skipped:
                print(f"- {reason}: {r}")
        return

    for sender_email, items in grouped.items():
        to_address = DEBUG_ADDRESS if DEBUG_EMAIL else sender_email

        msg = Message(
            subject="Delivery failure: emails bounced back",
            recipients=[to_address],
            body=build_bounce_email_body(items),
        )

        mail.send(msg)
        print(f"Sent bounce notification to {to_address} (original sender: {sender_email})")

        # DEBUG: send only one email
        if DEBUG_EMAIL:
            print("DEBUG_EMAIL=True -> sent one email only, stopping.")
            break

    if skipped:
        print("\nSkipped rows:")
        for reason, r in skipped:
            print(f"- {reason}: {r}")


# =========================
# Run
# =========================
if __name__ == "__main__":
    app = create_app()
    mail = Mail(app)

    with app.app_context():
        # Basic config sanity check
        if not os.getenv("EMAIL") or not os.getenv("WSNZADMINPASS"):
            raise RuntimeError("Missing EMAIL or WSNZADMINPASS environment variables.")

        send_bounce_notifications(mail, BOUNCED_ROWS)
