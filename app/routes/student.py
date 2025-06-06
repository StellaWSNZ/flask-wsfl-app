# app/routes/student.py
from flask import Blueprint, render_template, request, jsonify, session
from sqlalchemy import text
from app.utils.database import get_db_engine

students_bp = Blueprint("students_bp", __name__)

@students_bp.route("/students")
def student_search_page():
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            EXEC FlaskHelperFunctions @Request = 'EthnicityDropdown'
        """))
        ethnicities = [{"id": row.EthnicityID, "desc": row.Description} for row in result.fetchall()]

    return render_template("student_search.html", ethnicities=ethnicities)

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
    
@students_bp.route("/students/edit", methods=["POST"])
def edit_student():
    data = request.get_json()

    nsn = data.get("NSN")
    first = data.get("FirstName")
    last = data.get("LastName")
    preferred = data.get("PreferredName")
    ethnicity = data.get("EthnicityID")

    print("📥 Received student update:")
    print(f"  NSN: {nsn}")
    print(f"  FirstName: {first}")
    print(f"  LastName: {last}")
    print(f"  PreferredName: {preferred}")
    print(f"  EthnicityID: {ethnicity}")

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskUpdateStudent
                    @NSN = :NSN,
                    @FirstName = :FirstName,
                    @LastName = :LastName,
                    @PreferredName = :PreferredName,
                    @EthnicityID = :EthnicityID
            """), {
                "NSN": nsn,
                "FirstName": first,
                "LastName": last,
                "PreferredName": preferred,
                "EthnicityID": ethnicity
            })

        print("✅ EXEC executed successfully.")
        return jsonify({"success": True})
    except Exception as e:
        print("❌ Error editing student:", e)
        return jsonify({"success": False, "message": str(e)}), 500