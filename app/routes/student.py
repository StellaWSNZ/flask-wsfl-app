# app/routes/student.py
from flask import Blueprint, render_template, request, jsonify, session, abort, current_app
from sqlalchemy import text
from app.utils.database import get_db_engine, log_alert
import traceback

students_bp = Blueprint("students_bp", __name__)

@students_bp.route("/Students")
def student_search_page():
    try:
        user_role = session.get("user_role")
        user_admin = session.get("user_admin")

        # Only allow ADM or MOE with admin rights
        if not ((user_role == "ADM") or (user_role == "MOE" and user_admin == 1)):
            log_alert(
                email=session.get("user_email"),
                role=user_role,
                entity_id=session.get("user_id"),
                link=request.url,
                message="403 Forbidden: attempted access to /Students"
            )
            return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("""
                EXEC FlaskHelperFunctions @Request = 'EthnicityDropdown'
            """))
            ethnicities = [{"id": row.EthnicityID, "desc": row.Description} for row in result.fetchall()]

        return render_template("student_search.html", ethnicities=ethnicities)

    except Exception as e:
        # single global safety net
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message=f"/Students route failure: {e}"
            )
        except Exception:
            pass
        try:
            current_app.logger.error("❌ /Students failed: %s\n%s", e, traceback.format_exc())
        except Exception:
            pass
        return jsonify({"error": "Unexpected error occurred"}), 500


@students_bp.route("/Students/search")
def live_student_search():
    try:
        query = request.args.get("q", "")

        if not query or len(query.strip()) < 2:
            return jsonify([])

        role = session.get("user_role")
        moe_number = session.get("user_id") if role == "MOE" else None

        engine = get_db_engine()
        params = {
            "FirstName": query,
            "LastName": query,
            "PreferredName": query,
            "DateOfBirth": None,
            "MOENumber": moe_number
        }

        with engine.connect() as conn:
            result = conn.execute(text("""
                EXEC FlaskStudentSearch 
                    @FirstName = :FirstName,
                    @LastName = :LastName,
                    @PreferredName = :PreferredName,
                    @DateOfBirth = :DateOfBirth,
                    @MOENumber = :MOENumber
            """), params)
            rows = result.fetchall()

        students = [dict(row._mapping) for row in rows]
        return jsonify(students)

    except Exception as e:
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message=f"/Students/search failed: {e}"
            )
        except Exception:
            pass
        try:
            current_app.logger.error("❌ live_student_search failed: %s\n%s", e, traceback.format_exc())
        except Exception:
            pass
        return jsonify({"error": "Unexpected error occurred"}), 500


@students_bp.route("/Students/edit", methods=["POST"])
def edit_student():
    try:
        data = request.get_json()

        nsn = data.get("NSN")
        first = data.get("FirstName")
        last = data.get("LastName")
        preferred = data.get("PreferredName")
        ethnicity = data.get("EthnicityID")

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

        return jsonify({"success": True})

    except Exception as e:
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message=f"/Students/edit failed for NSN={data.get('NSN')}: {e}"
            )
        except Exception:
            pass
        try:
            current_app.logger.error("❌ edit_student failed: %s\n%s", e, traceback.format_exc())
        except Exception:
            pass
        return jsonify({"success": False, "message": "Unexpected error occurred"}), 500
