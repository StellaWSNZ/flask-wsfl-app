# app/routes/student.py
import traceback
from flask import Blueprint, render_template, request, jsonify, session, current_app
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.routes.auth import login_required
from app.utils.database import get_db_engine, log_alert

students_bp = Blueprint("students_bp", __name__)


def _is_moe_admin_or_adm() -> bool:
    role = session.get("user_role")
    admin = int(session.get("user_admin") or 0)
    return (role == "ADM") or (role == "MOE" and admin == 1)


def _forbidden_page(message="You are not authorised to view that page.", code=403):
    # your error.html currently expects `error` not `message`
    return render_template("error.html", error=message, code=code), code


def _json_error(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


@students_bp.route("/Students")
@login_required
def student_search_page():
    try:
        if not _is_moe_admin_or_adm():
            try:
                log_alert(
                    email=session.get("user_email"),
                    role=session.get("user_role"),
                    entity_id=session.get("user_id"),
                    link=request.url,
                    message="403 Forbidden: attempted access to /Students"
                )
            except Exception:
                pass
            return _forbidden_page()

        engine = get_db_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text("EXEC FlaskHelperFunctions @Request='EthnicityDropdown'")
            ).mappings().all()

        ethnicities = [{"id": r.get("EthnicityID"), "desc": r.get("Description")} for r in rows]
        return render_template("student_search.html", ethnicities=ethnicities)

    except Exception as e:
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message=f"/Students route failure: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass

        current_app.logger.exception("/Students failed")
        return _json_error("Unexpected error occurred", 500)

@students_bp.route("/Students/search")
@login_required
def live_student_search():
    try:
        if not _is_moe_admin_or_adm():
            return jsonify([])

        query = (request.args.get("q") or "").strip()
        if len(query) < 2:
            return jsonify([])

        role = (session.get("user_role") or "").upper().strip()
        moe_number = session.get("user_id") if role == "MOE" else None

        engine = get_db_engine()

        # IMPORTANT: don't pass query into 3 params if the SQL uses AND logic
        params = {
            "FirstName": query,        # use this as the general "name/fragment" input
            "LastName": None,
            "PreferredName": None,
            "DateOfBirth": None,
            "MOENumber": moe_number
        }

        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    EXEC dbo.FlaskStudentSearch
                        @FirstName      = :FirstName,
                        @LastName       = :LastName,
                        @PreferredName  = :PreferredName,
                        @DateOfBirth    = :DateOfBirth,
                        @MOENumber      = :MOENumber
                """),
                params
            )
            rows = result.fetchall()

        return jsonify([dict(r._mapping) for r in rows])

    except Exception as e:
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message=f"/Students/search failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass

        current_app.logger.exception("live_student_search failed")
        return _json_error("Unexpected error occurred", 500)

@students_bp.route("/Students/edit", methods=["POST"])
def edit_student():
    data = None
    try:
        # --- auth (recommended) ---
        user_role = session.get("user_role")
        user_admin = session.get("user_admin")
        if not ((user_role == "ADM") or (user_role == "MOE" and user_admin == 1)):
            return jsonify({"success": False, "message": "Not authorised"}), 403

        data = request.get_json(silent=True) or {}

        nsn = data.get("NSN")
        first = data.get("FirstName")
        last = data.get("LastName")
        preferred = data.get("PreferredName")
        ethnicity = data.get("EthnicityID")

        # basic validation so you don’t get silent SP failures
        if not nsn:
            return jsonify({"success": False, "message": "Missing NSN"}), 400
        if not first or not last:
            return jsonify({"success": False, "message": "First and Last name are required"}), 400

        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskUpdateStudent
                    @NSN = :NSN,
                    @FirstName = :FirstName,
                    @LastName = :LastName,
                    @PreferredName = :PreferredName,
                    @EthnicityID = :EthnicityID,
                    @PerformedByEmail = :PerformedByEmail
            """), {
                "NSN": nsn,
                "FirstName": first,
                "LastName": last,
                "PreferredName": preferred,
                "EthnicityID": ethnicity,
                "PerformedByEmail": session.get("user_email")
            })

        return jsonify({"success": True, "message": "Student updated"})

    except Exception as e:
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message=f"/Students/edit failed for NSN={(data or {}).get('NSN')}: {e}"
            )
        except Exception:
            pass
        try:
            current_app.logger.exception("❌ edit_student failed")
        except Exception:
            pass

        # return the real error message (safe enough for internal tool; if public, hide it)
        return jsonify({"success": False, "message": str(e)}), 500
