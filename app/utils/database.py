from sqlalchemy import create_engine
import os

import os
from dotenv import load_dotenv

load_dotenv(override=True)

def get_db_engine():
    from dotenv import load_dotenv

    return create_engine(
        f"mssql+pyodbc://{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL?driver=ODBC+Driver+18+for+SQL+Server",
        fast_executemany=True
    )