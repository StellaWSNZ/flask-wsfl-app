# utils/database.py
import os
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

load_dotenv()

_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    """Create (or return) a module-level SQLAlchemy engine using DB_URL from .env."""
    global _ENGINE
    #print("con")
    if _ENGINE is None:
        
        db_url = os.getenv("DB_URL")
        #print(db_url)
        if not db_url:
            raise ValueError("DB_URL is not set in your .env file")
        _ENGINE = create_engine(db_url, pool_pre_ping=True, pool_recycle=1800)
    return _ENGINE


def read_sql_df(sql: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """
    Execute a SQL query and return a pandas DataFrame.
    Supports named parameters, e.g.:
        SELECT * FROM MyTable WHERE CalendarYear = :year
    """
    eng = get_engine()
    with eng.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or {})
