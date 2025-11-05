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

    odbc = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=heimatau.database.windows.net,1433;"
        "DATABASE=WSFL;"
        f"UID={os.getenv('WSNZDBUSER')};"
        f"PWD={os.getenv('WSNZDBPASS')};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "MARS_Connection=Yes;"
        "Login Timeout=30;"
        "Connection Timeout=30;"
        "ApplicationIntent=ReadWrite;"
        "MultiSubnetFailover=Yes;"
    )
    params = urllib.parse.quote_plus(odbc)

    # NOTE: avoid connect_args timeout here; rely on ODBC timeouts above
    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={params}",
        pool_pre_ping=True,
        pool_recycle=300,     # recycle idle conns before Azure drops them
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        future=True,
    )
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
        