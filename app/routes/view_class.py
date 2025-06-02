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
    filter_type = request.args.get("filter", "all")  # ← add this
    engine = get_db_engine()
    with engine.connect() as conn:
        scenario_result = conn.execute(text("""
            EXEC FlaskHelperFunctions @Request = :request
        """), {"request": "ScenarioList"})
        scenarios = [dict(row._mapping) for row in scenario_result]

        class_info = conn.execute(
            text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :class_id"),
            {"Request": "ClassInfoByID", "class_id": class_id}
        ).fetchone()

        school_result = conn.execute(
            text("EXEC FlaskHelperFunctionsSpecific @Request = :Request, @MOENumber = :moe"),
            {"Request": "SchoolNameByMOE", "moe": class_info.MOENumber}
        ).fetchone()


        class_name = class_info.ClassName
        teacher_name = class_info.TeacherName
        school_name = school_result.SchoolName if school_result else "(Unknown)"
        title_string = f"Class Name: {class_name} | Teacher Name: {teacher_name} | School Name: {school_name}"

        result = conn.execute(
            text("""
                EXEC FlaskHelperFunctionsSpecific
                @Request = :Request,
                @ClassID = :class_id,
                @Term = :term,
                @Year = :year
            """),
            {
                "Request": "StudentsByClassTermYear",
                "class_id": class_id,
                "term": term,
                "year": year
            }
        )

        students = pd.DataFrame(result.fetchall(), columns=result.keys())
        if students.empty:
            flash("No students found.", "warning")
            return redirect(url_for("class_bp.funder_classes"))

        comp_result = conn.execute(text("EXEC GetRelevantCompetencies :CalendarYear, :Term"), {"CalendarYear": year, "Term": term})
        
        comp_df = pd.DataFrame(comp_result.fetchall(), columns=comp_result.keys())
        if filter_type == "water":
            comp_df = comp_df[comp_df["WaterBased"] == 1]
        comp_df["label"] = comp_df["CompetencyDesc"] + " <br>(" + comp_df["YearGroupDesc"] + ")"
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

        auto_result = conn.execute(
            text("""
                EXEC FlaskHelperFunctions 
                    @Request = :request
            """),
            {"request": "AutoMappedCompetencies"}
        )


        from collections import defaultdict
        header_map = defaultdict(list)

        for row in auto_result:
            header_map[row.HeaderPre].append(row.HeaderPost)
            
        #print(header_map)
        return render_template(
            "student_achievement.html",
            students=df_combined.to_dict(orient="records"),
            columns=[col for col in df_combined.columns if col not in ["DateOfBirth", "Ethnicity", "FirstName", "NSN"]],
            competency_id_map=competency_id_map,
            scenarios=scenarios,
            class_id = class_id,
            class_name=class_name,
            teacher_name=teacher_name,
            school_name=school_name,
            class_title=title_string,
            edit = session.get("user_admin"),
            autofill_map=header_map
        )
        
     
@class_bp.route("/update_class_info", methods=["POST"])
@login_required
def update_class_info():
    if session.get("user_admin") != 1:
        return jsonify(success=False, error="Unauthorized"), 403

    data = request.get_json()
    class_id = data.get("class_id")
    class_name = data.get("class_name", "").strip()
    teacher_name = data.get("teacher_name", "").strip()

    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                EXEC FlaskHelperFunctionsSpecific 
                    @Request = :request,
                    @ClassID = :class_id,
                    @ClassName = :class_name,
                    @TeacherName = :teacher_name
            """),
            {
                "request": "UpdateClassInfo",
                "class_id": class_id,
                "class_name": class_name,
                "teacher_name": teacher_name
            }
        )
    return jsonify(success=True)



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
                            EXEC FlaskHelperFunctionsSpecific 
                                @Request = :request,
                                @Term = :term,
                                @Year = :year,
                                @FunderID = :funder_id
                        """),
                        {
                            "request": "SchoolsByFunderTermYear",
                            "term": term,
                            "year": year,
                            "funder_id": user_id
                        }
                    )
                else:
                    result = conn.execute(
                        text("""
                            EXEC FlaskHelperFunctionsSpecific 
                                @Request = :request, 
                                @Term = :term, 
                                @Year = :year
                        """),
                        {
                            "request": "SchoolsByTermYear",
                            "term": term,
                            "year": year
                        }
                    )
                schools = [dict(row._mapping) for row in result]

                # Get classes
                if moe_number:
                    result = conn.execute(
                        text("""
                            EXEC FlaskHelperFunctionsSpecific 
                                @Request = :request,
                                @MOENumber = :moe,
                                @Term = :term,
                                @Year = :year
                        """),
                        {
                            "request": "ClassesBySchoolTermYear",
                            "moe": moe_number,
                            "term": term,
                            "year": year
                        }
                    )
                    classes = [row._mapping for row in result.fetchall()]


                    if not classes:
                        suggestion_result = conn.execute(
                             text("""
                                EXEC FlaskHelperFunctionsSpecific 
                                    @Request = :request, 
                                    @MOENumber = :moe
                            """),
                            {
                                "request": "DistinctTermsForSchool",
                                "moe": moe_number
                            }
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
    nsn = data.get("nsn")
    header_name = data.get("header_name")
    status = data.get("status")
    debug = 0
    print(f"📥 Incoming update request: NSN={nsn}, Header='{header_name}', Status={status}")

    if nsn is None or header_name is None or status is None:
        return jsonify({"success": False, "message": "Missing data"}), 400

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            with conn.begin():  # <-- this ensures commit happens

                result = conn.execute(
                    text("EXEC FlaskUpdateAchievement @NSN = :nsn, @Header = :header, @Value = :value, @Debug = :debug"),
                    {"nsn": nsn, "header": header_name, "value": status, "debug": debug}
                )

            # Fetch debug output from first result set (assuming it's a SELECT '...' AS Msg)

        return jsonify({
            "success": True
        })

    except Exception as e:
        print("❌ Competency update failed:", e)
        return jsonify({"success": False, "error": str(e)}), 500

@class_bp.route("/update_scenario", methods=["POST"])
@login_required
def update_scenario():
    data = request.get_json()
    nsn = data.get("nsn")
    header = data.get("header")
    value = data.get("value")
    debug = 0  # Optional: change to 1 if you want debug mode

    if not all([nsn, header, value]):
        return jsonify(success=False, error="Missing parameters"), 400

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(
                text("EXEC FlaskUpdateAchievement @NSN = :nsn, @Header = :header, @Value = :value, @Debug = :debug"),
                {"nsn": nsn, "header": header, "value": value, "debug": debug}
            )
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
                    text("EXEC FlaskHelperFunction @Request =:Request, Text = :Description"),
                    {"Request":"FunderIDDescription","Description": funder_name}
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
    funder_id = session.get("user_id")

    if not term or not year:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        if user_role == "ADM":
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :Request,
                        @Term = :Term,
                        @Year = :Year
                """),
                {
                    "Request": "DistinctSchoolsByTermYear",
                    "Term": term,
                    "Year": year
                }
            )
        else:
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :Request,
                        @Term = :Term,
                        @Year = :Year,
                        @FunderID = :FunderID
                """),
                {
                    "Request": "DistinctSchoolsByTermYear",
                    "Term": term,
                    "Year": year,
                    "FunderID": funder_id
                }
            )

        rows = result.fetchall()

        return jsonify([
            {"MOENumber": row.MOENumber, "School": row.SchoolName}
            for row in rows
        ])

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
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :Request,
                        @MOENumber = :moe,
                        @Term = :term,
                        @Year = :year
                """),
                {"Request": "ClassesBySchoolTermYear", "moe": moe_number, "term": term, "year": year}
            )
            classes = [row._mapping for row in result.fetchall()]

            if not classes:
                result = conn.execute(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific 
                            @Request = :Request,
                            @MOENumber = :moe
                    """),
                    {"Request": "DistinctTermsForSchool", "moe": moe_number}
                )
                suggestions = [f"{row.CalendarYear} Term {row.Term}" for row in result.fetchall()]

                flash("No classes found for your school in this term and year.", "warning")

    return render_template(
        "moe_classes.html",
        classes=classes,
        students=students,
        suggestions=suggestions
    )



@class_bp.route('/mark_all_complete', methods=['POST'])
@login_required
def mark_all_complete():
    data = request.get_json()
    column = data.get('column')
    class_id = data.get('class_id')
    print(class_id)
    print(column)
    return jsonify(success=True)