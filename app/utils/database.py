import os
from flask import current_app
from sqlalchemy import create_engine, text
import os, traceback
from dotenv import load_dotenv

load_dotenv(override=True)

from sqlalchemy import create_engine
import os, urllib.parse
from dotenv import load_dotenv

def get_db_engine():
    load_dotenv()

    #env = os.getenv("FLASK_ENV", "local").lower()
    env = ""
    if env == "production":
        db_url = os.getenv("AZURE_DB_URL")
        if not db_url:
            raise RuntimeError("AZURE_DB_URL not set")
    else:
        db_url = os.getenv("LOCAL_DB_URL")
        if not db_url:
            raise RuntimeError("LOCAL_DB_URL not set")
    print("LOCAL_DB_URL =", os.getenv("LOCAL_DB_URL"))
    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        future=True,
    )

    # 🔐 Safety check — NEVER skip this
    with engine.connect() as conn:
        db = conn.execute(text("SELECT DB_NAME()")).scalar()
        print(f"✅ Connected to database: {db}")

    return engine
    
# --- helpers (you can move this to a shared utils module) ------------------
from sqlalchemy import text


def log_alert(email=None, role=None, entity_id=None, link=None, message=None):
    """
    Best-effort write to AUD_Alerts_Insert; never raises.
    Creates its own engine, truncates long fields, logs failures to app logs.
    """
    try:
        # Sanitise & truncate (SP has @Link NVARCHAR(2048))
        email   = (email or "")[:320]
        role    = (role or "")[:10]
        link    = (link or "")[:2048]
        message = message or ""

        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                  EXEC AUD_Alerts_Insert
                       @Email        = :Email,
                       @RoleCode     = :RoleCode,
                       @EntityID     = :EntityID,
                       @Link         = :Link,
                       @ErrorMessage = :ErrorMessage
                """),
                {
                    "Email": email,
                    "RoleCode": role,
                    "EntityID": entity_id,
                    "Link": link,
                    "ErrorMessage": message
                }
            )
        # Optional: mark success in debug logs
        try:
            current_app.logger.info("✅ AUD_Alerts_Insert wrote successfully")
        except Exception:
            pass
    except Exception as e:
        # Never throw from logger; just record why it failed
        try:
            current_app.logger.error(
                "⚠️ Failed AUD_Alerts_Insert: %s\n%s", e, traceback.format_exc()
            )
        except Exception:
            pass
        
def get_terms():
    return list(range(1, 5))

def get_years():
    return list(range(2024, 2027))