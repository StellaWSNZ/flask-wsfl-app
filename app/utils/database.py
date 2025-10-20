import os
from flask import current_app
from sqlalchemy import create_engine, text
import os, traceback
from dotenv import load_dotenv

load_dotenv(override=True)

def get_db_engine():
    from dotenv import load_dotenv

    return create_engine(
        f"mssql+pyodbc://{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL?driver=ODBC+Driver+18+for+SQL+Server&MARS_Connection=Yes"
    )
    
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
        