# app/routes/view_class.py

from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
from app.utils.database import get_db_engine
from sqlalchemy import text
import pandas as pd
from app.routes.auth import login_required
import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt
import io, base64
import sys
from app.utils.fundernationalplot import create_competency_report
from app.utils.competencyplot import load_competency_rates, make_figure
from app.utils.nationalplot import generate_national_report
class_bp = Blueprint("class_bp", __name__)

@class_bp.route('/view_class/<int:class_id>/<int:term>/<int:year>')
@login_required
def view_class(class_id, term, year):
    engine = get_db_engine()
    with engine.connect() as conn:
        scenario_result = conn.execute(text("SELECT ScenarioID, HTMLScenario FROM Scenario"))
        scenarios = [dict(row._mapping) for row in scenario_result]

        class_info = conn.execute(text("""
            SELECT ClassName, TeacherName, MOENumber
            FROM Class
            WHERE ClassID = :class_id
        """), {"class_id": class_id}).fetchone()

        school_result = conn.execute(text("""
            SELECT SchoolName
            FROM MOE_SchoolDirectory
            WHERE MOENumber = :moe
        """), {"moe": class_info.MOENumber}).fetchone()

        class_name = class_info.ClassName
        teacher_name = class_info.TeacherName
        school_name = school_result.SchoolName if school_result else "(Unknown)"
        title_string = f"Class Name: {class_name} | Teacher Name: {teacher_name} | School Name: {school_name}"

        result = conn.execute(text("""
            SELECT s.NSN, s.FirstName, s.LastName, s.PreferredName, s.DateOfBirth,
                   e.Description AS Ethnicity, sy.YearLevelID
            FROM StudentClass scm
            JOIN Student s ON s.NSN = scm.NSN
            JOIN Class c ON c.ClassID = scm.ClassID
            JOIN StudentYearLevel sy ON sy.NSN = scm.NSN AND sy.Term = c.Term AND sy.CalendarYear = c.CalendarYear
            JOIN Ethnicity e ON e.EthnicityID = s.EthnicityID
            WHERE scm.ClassID = :class_id AND c.Term = :term AND c.CalendarYear = :year
        """), {"class_id": class_id, "term": term, "year": year})

        students = pd.DataFrame(result.fetchall(), columns=result.keys())
        if students.empty:
            flash("No students found.", "warning")
            return redirect(url_for("class_bp.funder_classes"))

        comp_result = conn.execute(text("EXEC GetRelevantCompetencies :CalendarYear, :Term"), {"CalendarYear": year, "Term": term})
        comp_df = pd.DataFrame(comp_result.fetchall(), columns=comp_result.keys())
        comp_df["label"] = comp_df["CompetencyDesc"] + "<br> (" + comp_df["YearGroupDesc"] + ")"
        comp_df["col_order"] = comp_df["YearGroupID"].astype(str).str.zfill(2) + "-" + comp_df["CompetencyID"].astype(str).str.zfill(4)
        comp_df = comp_df.sort_values("col_order")
        labels = comp_df["label"].tolist()

        all_records = []
        for _, student in students.iterrows():
            nsn = student["NSN"]
            comp_data = pd.read_sql(text("EXEC GetStudentCompetencyStatus :NSN, :Term, :CalendarYear"), conn, params={"NSN": nsn, "Term": term, "CalendarYear": year})
            if not comp_data.empty:
                comp_data = comp_data.merge(comp_df[["CompetencyID", "YearGroupID", "label"]], on=["CompetencyID", "YearGroupID"], how="inner")
                comp_row = comp_data.set_index("label")["CompetencyStatusID"].reindex(labels).fillna(0).astype(int).map({1: 'Y', 0: ''}).to_dict()
            else:
                comp_row = {label: '' for label in labels}

            scenario_df = pd.read_sql(text("EXEC FlaskHelperFunctions :Request, :Number"), conn,
                                      params={"Request": "StudentScenario", "Number": nsn})
            scenario1 = scenario_df.iloc[0].get("Scenario1", "") if not scenario_df.empty else ""
            scenario2 = scenario_df.iloc[0].get("Scenario2", "") if not scenario_df.empty else ""

            merged_row = {
                "NSN": nsn,
                "FirstName": student["FirstName"],
                "LastName": student["LastName"],
                "PreferredName": student["PreferredName"],
                "DateOfBirth": student["DateOfBirth"],
                "Ethnicity": student["Ethnicity"],
                "YearLevelID": student["YearLevelID"],
                **comp_row,
                "Scenario One - Selected <br> (7-8)": scenario1,
                "Scenario Two - Selected <br> (7-8)": scenario2
            }
            all_records.append(merged_row)

        df_combined = pd.DataFrame(all_records).sort_values("LastName")

        if "Scenario One - Selected <br> (7-8)" in df_combined.columns and "Scenario Two - Selected <br> (7-8)" in df_combined.columns:
            cols = df_combined.columns.tolist()
            cols.insert(-3, cols.pop(cols.index("Scenario One - Selected <br> (7-8)")))
            cols.insert(-1, cols.pop(cols.index("Scenario Two - Selected <br> (7-8)")))
            df_combined = df_combined[cols]

        competency_id_map = comp_df.set_index("label")["CompetencyID"].to_dict()

        return render_template(
            "funder_class_detail.html",
            students=df_combined.to_dict(orient="records"),
            columns=[col for col in df_combined.columns if col not in ["DateOfBirth", "Ethnicity", "FirstName", "NSN"]],
            competency_id_map=competency_id_map,
            scenarios=scenarios,
            class_name=class_name,
            teacher_name=teacher_name,
            school_name=school_name,
            class_title=title_string
        )
@class_bp.route('/funder_classes', methods=['GET', 'POST'])
@login_required
def funder_classes():
    if session.get("user_role") not in ["FUN", "ADM"]:
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))

    engine = get_db_engine()
    classes = []
    students = []
    suggestions = []
    schools = []
    selected_class_id = None

    user_role = session.get("user_role")
    user_id = session.get("user_id")

    with engine.connect() as conn:
        if request.method == "POST":
            term = request.form.get("term", "").strip()
            year = request.form.get("calendaryear", "").strip()
            moe_number = request.form.get("moe_number", "").strip()

            if term.isdigit() and year.isdigit():
                term = int(term)
                year = int(year)

                # Get school list
                if user_role == "FUN":
                    result = conn.execute(
                        text("""
                            SELECT sf.MOENumber, sd.SchoolName AS School
                            FROM SchoolFunder sf
                            JOIN MOE_SchoolDirectory sd ON sf.MOENumber = sd.MOENumber
                            WHERE sf.Term = :term AND sf.CalendarYear = :year AND sf.FunderID = :funder_id
                        """),
                        {"term": term, "year": year, "funder_id": user_id}
                    )
                else:
                    result = conn.execute(
                        text("""
                            SELECT sf.MOENumber, sd.SchoolName AS School
                            FROM SchoolFunder sf
                            JOIN MOE_SchoolDirectory sd ON sf.MOENumber = sd.MOENumber
                            WHERE sf.Term = :term AND sf.CalendarYear = :year
                        """),
                        {"term": term, "year": year}
                    )
                schools = [dict(row._mapping) for row in result]

                # Get classes
                if moe_number:
                    result = conn.execute(
                        text("""
                            SELECT ClassID, ClassName, TeacherName
                            FROM Class
                            WHERE MOENumber = :moe AND Term = :term AND CalendarYear = :year
                            ORDER BY TeacherName, ClassName
                        """),
                        {"moe": moe_number, "term": term, "year": year}
                    )
                    classes = [row._mapping for row in result.fetchall()]

                    if not classes:
                        suggestion_result = conn.execute(
                            text("""
                                SELECT DISTINCT CONCAT('Term ', Term, ' - ', CalendarYear) AS Label
                                FROM Class
                                WHERE MOENumber = :moe
                                ORDER BY CalendarYear DESC, Term DESC
                            """),
                            {"moe": moe_number}
                        )
                        suggestions = [row.Label for row in suggestion_result]

    return render_template(
        "funder_classes.html",
        schools=schools,
        classes=classes,
        students=students,
        suggestions=suggestions,
        selected_class_id=selected_class_id
    )


@class_bp.route('/update_competency', methods=['POST'])
@login_required
def update_competency():
    data = request.json
    print("Received data:", data)

    nsn = data.get("nsn")
    header_name = data.get("header_name")
    status = data.get("status")

    print(f"NSN: {nsn}, Header Name: {header_name}, Status: {status}")

    if nsn is None or header_name is None or status is None:
        return jsonify({"success": False, "message": "Missing data"}), 400

    print(f"Would update competency for NSN {nsn} with status '{status}' and header name '{header_name}'")
    return jsonify({"success": True, "message": "Data received and printed successfully."})

@class_bp.route("/update_scenario", methods=["POST"])
@login_required
def update_scenario():
    data = request.get_json()
    nsn = data.get("nsn")
    header = data.get("header")
    value = data.get("value")

    print(f"Would update scenario for NSN {nsn}, header '{header}', and value '{value}'")

    try:
        return jsonify(success=True)
    except Exception as e:
        print("❌ Scenario update failed:", e)
        return jsonify(success=False, error=str(e)), 500



@class_bp.route('/reporting', methods=["GET", "POST"])
@login_required
def reporting():
    global last_pdf_generated, last_pdf_filename, last_png_generated, last_png_filename

    engine = get_db_engine()
    role = session.get("user_role")
    user_id = session.get("user_id")

    funders = []
    competencies = []
    img_data = None
    report_type = None
    term = None
    year = None
    funder_name = None
    dropdown_string = None

    with engine.connect() as conn:
        if role == "ADM":
            result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})
            funders = [row.Description for row in result]

        result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "CompetencyDropdown"})
        competencies = [row.Competency for row in result]
    #print("Session dump:", dict(session), file=sys.stderr)

    if request.method == "POST":
        report_type = request.form.get("report_type")
        term = int(request.form.get("term"))
        year = int(request.form.get("year"))
        funder_name = request.form.get("funder") or session.get("desc")
        print(request.form.get("funder"))
        print(session.get("desc"))
        if report_type == "Funder":
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT FunderID FROM Funder WHERE Description = :Description"),
                    {"Description": funder_name}
                )
                row = result.fetchone()

            if not row:
                flash("Funder not found.", "danger")
                return redirect(url_for("class_bp.reporting"))

            funder_id = int(row.FunderID)
            fig = create_competency_report(term, year, funder_id, funder_name)

            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            img_data = base64.b64encode(png_buf.getvalue()).decode("utf-8")

            last_png_generated = io.BytesIO(png_buf.getvalue())

            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)
            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{funder_name}_{term}_{year}.pdf"

            plt.close(fig)

        elif report_type == "Competency":
            dropdown_string = request.form.get("competency")

            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
                    {"DropdownValue": dropdown_string}
                )
                row = result.fetchone()

            if not row:
                flash("Invalid competency selected.", "danger")
                return redirect(url_for("class_bp.reporting"))

            competency_id = row.CompetencyID
            year_group_id = row.YearGroupID

            df = load_competency_rates(engine, year, term, competency_id, year_group_id)
            if df.empty:
                flash("No data found.", "warning")
                return redirect(url_for("class_bp.reporting"))

            title = f"{df['CompetencyDesc'].iloc[0]} ({df['YearGroupDesc'].iloc[0]})"
            fig = make_figure(df, title)

            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            img_data = base64.b64encode(png_buf.read()).decode("utf-8")

            last_png_generated = io.BytesIO(png_buf.getvalue())
            last_png_filename = f"{report_type}_Report_{dropdown_string.replace(' ', '_')}_{term}_{year}.png"

            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)
            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{dropdown_string.replace(' ', '_')}_{term}_{year}.pdf"

            plt.close(fig)

    return render_template(
        "reporting.html",
        funders=funders,
        competencies=competencies,
        selected_report_type=report_type,
        selected_term=term,
        selected_year=year,
        selected_funder=funder_name,
        selected_competency=dropdown_string,
        img_data=img_data,
        user_role=role
    )

@class_bp.route("/comingsoon")
@login_required
def comingsoon():
    return render_template("comingsoon.html")
@class_bp.route("/get_schools_for_term_year")
@login_required
def get_schools_for_term_year():
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)
    user_role = session.get("user_role")
    provider_id = session.get("user_id")

    if not term or not year:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        if user_role == "ADM":
            result = conn.execute(
                text("""
                    SELECT DISTINCT sf.MOENumber, sd.SchoolName
                    FROM SchoolFunder sf
                    JOIN MOE_SchoolDirectory sd ON sf.MOENumber = sd.MOENumber
                    WHERE sf.Term = :term AND sf.CalendarYear = :year
                """),
                {"term": term, "year": year}
            )
        else:
            result = conn.execute(
                text("""
                    SELECT DISTINCT sf.MOENumber, sd.SchoolName
                    FROM SchoolFunder sf
                    JOIN MOE_SchoolDirectory sd ON sf.MOENumber = sd.MOENumber
                    WHERE sf.Term = :term AND sf.CalendarYear = :year AND sf.FunderID = :pid
                """),
                {"term": term, "year": year, "pid": provider_id}
            )

        return jsonify([{"MOENumber": row.MOENumber, "School": row.SchoolName} for row in result.fetchall()])
@class_bp.route('/moe_classes', methods=['GET', 'POST'])
@login_required
def moe_classes():
    if session.get("user_role") != "MOE":
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))

    engine = get_db_engine()
    classes = []
    students = []
    suggestions = []

    moe_number = session.get("user_id")

    if request.method == "POST":
        term = request.form.get("term")
        year = request.form.get("calendaryear")

        with engine.connect() as conn:
            # Attempt to find classes
            result = conn.execute(
                text("SELECT ClassID, ClassName, TeacherName FROM Class WHERE MOENumber = :moe AND Term = :term AND CalendarYear = :year"),
                {"moe": moe_number, "term": term, "year": year}
            )
            classes = [row._mapping for row in result.fetchall()]

            if not classes:
                # If no classes found, fetch available terms/years for this school
                result = conn.execute(
                    text("SELECT DISTINCT Term, CalendarYear FROM Class WHERE MOENumber = :moe ORDER BY CalendarYear DESC, Term"),
                    {"moe": moe_number}
                )
                suggestions = [f"{row.CalendarYear} Term {row.Term}" for row in result.fetchall()]
                flash("No classes found for your school in this term and year.", "warning")

    return render_template(
        "moe_classes.html",
        classes=classes,
        students=students,
        suggestions=suggestions
    )
