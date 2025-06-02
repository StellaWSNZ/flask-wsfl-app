from flask.sessions import SessionInterface, SessionMixin
from uuid import uuid4
import pickle
from datetime import datetime, timedelta
from sqlalchemy import text

class StoredProcSession(dict, SessionMixin):
    def __init__(self, initial=None, sid=None, expiry=None):
        self.sid = sid
        self.expiry = expiry
        dict.__init__(self, initial or {})

class StoredProcSessionInterface(SessionInterface):
    def __init__(self, db_engine, default_timeout=timedelta(days=1)):
        self.db_engine = db_engine
        self.default_timeout = default_timeout

    def generate_sid(self):
        return str(uuid4())

    def get_expiration_time(self, app, session):
        return datetime.utcnow() + self.default_timeout

    def open_session(self, app, request):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        sid = request.cookies.get(cookie_name)
        #print(f"🔍 open_session: session ID from cookie = {sid}")

        if not sid:
            sid = self.generate_sid()
            #print(f"🆕 open_session: no session ID, generated new = {sid}")
            return StoredProcSession(sid=sid)

        try:
            with self.db_engine.connect() as conn:
                #print(f"📡 open_session: calling FlaskSessionGet with SID = {sid}")
                result = conn.execute(
                    text("EXEC FlaskSessionGet @session_id = :sid"),
                    {"sid": sid}
                ).fetchone()

                if result and result[1]:  # Use tuple access if row is not a dict
                    expiry = result[2]
                    try:
                        data = pickle.loads(result[1])
                        #print(f"✅ open_session: session loaded with expiry {expiry}")
                        return StoredProcSession(data, sid=sid, expiry=expiry)
                    except Exception as e:
                        print(f"⚠️ open_session: error unpickling session data — {e}")
                else:
                    print("❌ open_session: no session found in DB")

        except Exception as e:
            print(f"❌ open_session: database error — {e}")

        return StoredProcSession(sid=sid)

    def save_session(self, app, session, response):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        domain = self.get_cookie_domain(app)

        if not session:
            #print(f"🧼 save_session: deleting cookie for SID {session.sid}")
            response.delete_cookie(cookie_name, domain=domain)
            return

        expiry = self.get_expiration_time(app, session)

        try:
            val = pickle.dumps(dict(session))
            #print(f"💾 save_session: saving session ID {session.sid} with expiry {expiry}")
            #print(f"💬 save_session: session keys = {list(session.keys())}")
            #print(f"📦 save_session: pickled data size = {len(val)} bytes")

            with self.db_engine.begin() as conn:
                conn.execute(
                    text("EXEC FlaskSessionSave @session_id = :sid, @data = :data, @expiry = :expiry"),
                    {"sid": session.sid, "data": val, "expiry": expiry}
                )
        except Exception as e:
            print(f"❌ save_session: error saving session — {e}")
            return

        response.set_cookie(
            cookie_name,
            session.sid,
            expires=expiry,
            httponly=True,
            domain=domain
        )
        #print(f"🍪 save_session: cookie set for SID {session.sid}")
