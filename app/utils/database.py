from sqlalchemy import create_engine
import os
from flask import current_app

import os
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

def log_alert(engine, email, role, entity_id, link, message):
    """Best-effort alert insert; never raises to caller."""
    try:
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
    except Exception as e:
        current_app.logger.error(f"⚠️ Failed to write AUD_Alerts_Insert: {e}")
