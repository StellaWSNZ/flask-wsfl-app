from flask import Blueprint, render_template, session
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine  # adjust path as needed

staff_bp = Blueprint("staff_bp", __name__)

import sys
from flask import Blueprint, render_template, session
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine

staff_bp = Blueprint("staff_bp", __name__)

@staff_bp.route("/staff_maintenance")
def staff_maintenance():
    user_id = session.get("user_id")
    user_role = session.get("user_role")

    if not user_id or not user_role:
        return "Unauthorized", 403

    engine = get_db_engine()
    with engine.connect() as conn:
        # print("Connected to DB")
        sys.stdout.flush()

        try:
            result = conn.execute(
                text("EXEC FlaskGetStaffDetails @RoleType = :role, @ID = :id"),
                {"role": user_role.upper(), "id": int(user_id)}
            )

            rows = result.fetchall()
            sys.stdout.flush()

            data = pd.DataFrame(rows, columns=result.keys())
            sys.stdout.flush()
            print(data)
            return render_template("staff_maintenance.html", data=data.to_dict(orient="records"), columns=data.columns, name = session.get('desc'))


        except Exception as e:
            print("❌ Exception occurred:", e)
            sys.stdout.flush()
            return f"Error: {e}", 500
