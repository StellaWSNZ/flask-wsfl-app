# app/routes/student.py
from flask import Blueprint, render_template, request, jsonify
from sqlalchemy import text
from app.utils.database import get_db_engine

students_bp = Blueprint("students_bp", __name__)

@students_bp.route("/students")
def student_search_page():
    """Renders the student search page with search-as-you-type enabled."""
    return render_template("student_search.html")


@students_bp.route("/students/search")
def live_student_search():
    """Returns JSON list of students matching the input query."""
    query = request.args.get("q", "")

    if not query or len(query.strip()) < 2:
        return jsonify([])

    engine = get_db_engine()
    params = {
        "FirstName": query,
        "LastName": query,
        "PreferredName": query,
        "DateOfBirth": None
    }

    with engine.connect() as conn:
        result = conn.execute(text("""
            EXEC StudentSearch 
                @FirstName = :FirstName,
                @LastName = :LastName,
                @PreferredName = :PreferredName,
                @DateOfBirth = :DateOfBirth
        """), params)

        rows = result.fetchall()

    students = [
        {
            "NSN": row.NSN,
            "FirstName": row.FirstName,
            "LastName": row.LastName,
            "PreferredName": row.PreferredName,
            "DateOfBirth": str(row.DateOfBirth)
        }
        for row in rows
    ]

    return jsonify(students)
