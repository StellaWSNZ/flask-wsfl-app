# app/routes/student.py
from flask import Blueprint, render_template, request, jsonify, session
from sqlalchemy import text
from app.utils.database import get_db_engine

students_bp = Blueprint("students_bp", __name__)

@students_bp.route("/students")
def student_search_page():
    """Renders the student search page with search-as-you-type enabled."""
    return render_template("student_search.html")
@students_bp.route("/students/search")
def live_student_search():
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

    try:
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
        print("❌ Error in live_student_search:", e)
        return jsonify({"error": str(e)}), 500