# app/routes/view_class.py
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import qrcode
import base64
from io import BytesIO
from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
from app.utils.database import get_db_engine
from sqlalchemy import text
import pandas as pd
from app.routes.auth import login_required
import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt
import io, base64
from datetime import datetime
import sys
from app.utils.fundernationalplot import create_competency_report
from app.utils.competencyplot import load_competency_rates, make_figure
from app.utils.nationalplot import generate_national_report
import traceback
from collections import defaultdict
from datetime import datetime, timedelta,timezone

class_bp = Blueprint("class_bp", __name__)

from datetime import datetime, timedelta
from dateutil.parser import isoparse
from collections import defaultdict



@class_bp.route('/Class/<int:class_id>/<int:term>/<int:year>')
@login_required
def view_class(class_id, term, year):
    try:
        filter_type = request.args.get("filter", "all")
        order_by = request.args.get("order_by", "last")
        cache_key = f"{class_id}_{term}_{year}_{filter_type}"
        cached = session.get("class_cache", {}).get(cache_key)
        cached = None
        # ⏳ Cache expiry check
        if cached:
            expires_str = cached.get("expires")
            if expires_str:
                try:
                    expires_at = isoparse(expires_str)
                    if datetime.now(timezone.utc) > expires_at:
                        print(f"🕒 Cache expired for {cache_key}")
                        session["class_cache"].pop(cache_key, None)
                        cached = None
                except Exception as e:
                    print("⚠️ Failed to parse cache expiry:", e)
                    session["class_cache"].pop(cache_key, None)
                    cached = None

        # ✅ Use cache if valid
        if cached and "student_competencies" in cached:
            try:
                print("using cached")
                df_combined = pd.DataFrame(cached["student_competencies"])
                df_combined = df_combined.sort_values("PreferredName" if order_by == "first" else "LastName")

                comp_df = pd.DataFrame(cached.get("competencies", []))
                competency_id_map = {}
                if not comp_df.empty and "label" in comp_df.columns:
                    competency_id_map = comp_df.set_index("label")["CompetencyID"].to_dict()
                print(cached.get("autofill_map", {}))
                return render_template(
                    "student_achievement.html",
                    students=df_combined.to_dict(orient="records"),
columns=[col for col in df_combined.columns if col not in ["DateOfBirth", "Ethnicity", "FirstName"]],
                    competency_id_map=competency_id_map,
                    scenarios=cached.get("scenarios", []),
                    class_id=class_id,
                    class_name=cached.get("class_name", "(Unknown)"),
                    teacher_name=cached.get("teacher_name", "(Unknown)"),
                    school_name=cached.get("school_name", "(Unknown)"),
                    class_title=f"Class Name: {cached.get('class_name', '')} | Teacher Name: {cached.get('teacher_name', '')} | School Name: {cached.get('school_name', '')}",
                    edit=session.get("user_admin"),
                    autofill_map=cached.get("autofill_map", {}),
                    term=term,
                    year=year,
                    order_by=order_by,
                    filter_type = request.args.get("filter", "all")

                )
            except Exception:
                print("⚠️ Error while rendering from cache:")
                traceback.print_exc()

        # ❌ If no cache or cache failed, load from DB
        engine = get_db_engine()
        with engine.begin() as conn:
            scenario_result = conn.execute(text("EXEC FlaskHelperFunctions @Request = :request"), {"request": "Scenario"})
            scenarios = [dict(row._mapping) for row in scenario_result]

            
            result = conn.execute(
                text("""
                    EXEC FlaskGetClassStudentAchievement 
                        @ClassID = :class_id, 
                        @Term = :term, 
                        @CalendarYear = :year, 
                        @Email = :email, 
                        @FilterType = :filter
                """),
                {
                    "class_id": class_id,
                    "term": term,
                    "year": year,
                    "email": session.get("user_email"),
                    "filter": filter_type
                }
            )
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
            comp_df = (
                df[["CompetencyLabel", "CompetencyID", "YearGroupID"]]
                .drop_duplicates()
                .rename(columns={"CompetencyLabel": "label"})
            )
            class_name = df["ClassName"].dropna().unique()[0] if "ClassName" in df.columns else "(Unknown)"
            teacher_name = df["TeacherName"].dropna().unique()[0] if "TeacherName" in df.columns else "(Unknown)"
            school_name = df["SchoolName"].dropna().unique()[0] if "SchoolName" in df.columns else "(Unknown)"
            title_string = f"Class Name: {class_name} | Teacher Name: {teacher_name} | School Name: {school_name}"
            df = df.drop(columns=["ClassName", "TeacherName", "SchoolName","CompetencyID","YearGroupID"])
            
            meta_cols = [
                "NSN",
                "FirstName",
                "LastName",
                "PreferredName",
                "Ethnicity",
                "YearLevelID",
                "Scenario1",
                "Scenario2"
            ]
            pivot_df = df.pivot_table(
                index=meta_cols,
                columns="CompetencyLabel",
                values="CompetencyStatus",
                aggfunc="first"  # In case of duplicates
            ).reset_index()
            comp_df_sorted = comp_df.sort_values(["YearGroupID", "CompetencyID"])
            competency_cols = comp_df_sorted["label"].tolist()

            pivot_df = pivot_df.sort_values("PreferredName" if order_by == "first" else "LastName")
            # Rename scenario columns
            pivot_df = pivot_df.rename(columns={
                "Scenario1": "Scenario One - Selected <br>(7-8)",
                "Scenario2": "Scenario Two - Selected <br>(7-8)"
            })

            fixed_cols = ["NSN","LastName", "PreferredName", "YearLevelID"]
            scenario_cols = [
                "Scenario One - Selected <br>(7-8)",
                "Scenario One - Completed <br>(7-8)",
                "Scenario Two - Selected <br>(7-8)",
                "Scenario Two - Completed <br>(7-8)"
            ]
            # Filter out any labels that aren't actually in pivot_df.columns (to avoid key errors)
            competency_cols = [col for col in competency_cols if col in pivot_df.columns]
            competency_cols = [col for col in competency_cols if col not in set(scenario_cols)]
            # Final column order
            ordered_cols = fixed_cols + competency_cols + scenario_cols
            pivot_df = pivot_df[ordered_cols]
            auto_result = conn.execute(text("EXEC FlaskHelperFunctions @Request = :request"), {"request": "AutoMappedCompetencies"})
            header_map = defaultdict(list)
            for row in auto_result:
                header_map[row.HeaderPre].append(row.HeaderPost)
            print(header_map)
            expiry_time = datetime.now(timezone.utc) + timedelta(minutes=15)
            session.setdefault("class_cache", {})[cache_key] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "expires": expiry_time.isoformat(),
                "students": pivot_df.to_dict(),
                "competencies": comp_df.to_dict(orient="records"),

                "filter": filter_type,
                "student_competencies": pivot_df.to_dict(orient="records"),
                "class_name": class_name,
                "teacher_name": teacher_name,
                "school_name": school_name,
                "scenarios": scenarios,
                "autofill_map": dict(header_map)
            }
            print(request.args.get("filter", "all")
)
            return render_template(
                "student_achievement.html",
                students=pivot_df.to_dict(orient="records"),
                columns=[col for col in pivot_df.columns if col not in ["DateOfBirth", "Ethnicity", "FirstName", "NSN"]],
                competency_id_map=comp_df.set_index("label")["CompetencyID"].to_dict(),
                scenarios=scenarios,
                class_id=class_id,
                class_name=class_name,
                teacher_name=teacher_name,
                school_name=school_name,
                class_title=title_string,
                edit=session.get("user_admin"),
                autofill_map=header_map,
                term=term,
                year=year,
                order_by=order_by,
                filter_type = request.args.get("filter", "all")
            )

    except Exception as e:
        print("❌ An error occurred in view_class:")
        traceback.print_exc()
        return "An internal error occurred. Check logs for details.", 500


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



@class_bp.route('/ProviderClasses', methods=['GET', 'POST'])
@login_required
def provider_classes():
    if session.get("user_role") != "PRO":
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
                result = conn.execute(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific 
                            @Request = :request,
                            @Term = :term,
                            @Year = :year,
                            @ProviderID = :provider_id
                    """),
                    {
                        "request": "SchoolsByProviderTermYear",
                        "term": term,
                        "year": year,
                        "provider_id": user_id
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
        selected_class_id=selected_class_id,
        TERM=session.get("nearest_term"),
        YEAR=session.get("nearest_year"),
    user_role=session.get("user_role") 
    )
    
@class_bp.route('/Classes', methods=['GET', 'POST'])
@login_required
def funder_classes():
    if session.get("user_role") not in ["FUN", "ADM", "GRP"]:
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))
    session.pop("class_cache", None) 
    engine = get_db_engine()
    classes, students, suggestions, schools = [], [], [], []
    selected_class_id = None

    user_role = session.get("user_role")
    user_id = session.get("user_id")
    group_entities = session.get("group_entities", {})
    provider_ids = [str(e["id"]) for e in group_entities.get("PRO", [])]
    funder_ids = [str(e["id"]) for e in group_entities.get("FUN", [])]

    default_term = session.get("nearest_term")
    default_year = session.get("nearest_year")

    # ========================
    # Always Load Schools List
    # ========================
    with engine.connect() as conn:
        if user_role == "GRP":
            print(group_entities)
            print(provider_ids)
            print(funder_ids)
            if provider_ids:
                csv_providers = ",".join(provider_ids)
                result = conn.execute(
                    text("EXEC FlaskSchoolsByGroupProviders :ProviderList, :Term, :Year"),
                    {"ProviderList": csv_providers, "Term": default_term, "Year": default_year}
                )
            elif funder_ids:
                csv_funders = ",".join(funder_ids)
                result = conn.execute(
                    text("EXEC FlaskSchoolsByGroupFunders :FunderList, :Term, :Year"),
                    {"FunderList": csv_funders, "Term": default_term, "Year": default_year}
                )
            else:
                result = []
        elif user_role == "FUN":
            result = conn.execute(
                text("""EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request,
                        @Term = :term,
                        @Year = :year,
                        @FunderID = :funder_id"""),
                {"request": "SchoolsByFunderTermYear", "term": default_term, "year": default_year, "funder_id": user_id}
            )
        else:  # ADM
            result = conn.execute(
                text("""EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request, 
                        @Term = :term, 
                        @Year = :year"""),
                {"request": "SchoolsByTermYear", "term": default_term, "year": default_year}
            )
        schools = [dict(row._mapping) for row in result]

        # ======================================
        # Only Load Classes Table After Form POST
        # ======================================
        if request.method == "POST":
            term = request.form.get("term", "").strip()
            year = request.form.get("calendaryear", "").strip()
            moe_number = request.form.get("moe_number", "").strip()

            if term.isdigit() and year.isdigit() and moe_number:
                term, year = int(term), int(year)

                result = conn.execute(
                    text("""EXEC FlaskHelperFunctionsSpecific 
                            @Request = :request,
                            @MOENumber = :moe,
                            @Term = :term,
                            @Year = :year"""),
                    {"request": "ClassesBySchoolTermYear", "moe": moe_number, "term": term, "year": year}
                )
                classes = [row._mapping for row in result.fetchall()]

                if not classes:
                    suggestion_result = conn.execute(
                        text("""EXEC FlaskHelperFunctionsSpecific 
                                @Request = :request, 
                                @MOENumber = :moe"""),
                        {"request": "DistinctTermsForSchool", "moe": moe_number}
                    )
                    suggestions = [row.Label for row in suggestion_result]

    return render_template(
        "funder_classes.html",
        schools=schools,
        classes=classes,
        students=students,
        suggestions=suggestions,
        selected_class_id=selected_class_id,
        TERM=default_term,
        YEAR=default_year,
        user_role=user_role
    )
@class_bp.route('/update_competency', methods=['POST'])
@login_required
def update_competency():
    data = request.json
    nsn = data.get("nsn")
    header_name = data.get("header_name")
    status = data.get("status")
    class_id = data.get("class_id")
    term = data.get("term")
    year = data.get("year")
    debug = 0

    print("📥 Incoming update_competency call")
    print(f"➡️ NSN: {nsn}, Header: {header_name}, Status: {status}, Class ID: {class_id}, Term: {term}, Year: {year}")

    if None in (nsn, header_name, status, class_id, term, year):
        print("❌ Missing one or more required fields")
        return jsonify({"success": False, "message": "Missing data"}), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            print("🔄 Running stored procedure FlaskUpdateAchievement...")
            conn.execute(
                text("EXEC FlaskUpdateAchievement @NSN = :nsn, @Header = :header, @Value = :value, @Email = :email, @Debug = :debug"),
                {
                    "nsn": nsn,
                    "header": header_name,
                    "value": status,
                    "email": session.get("user_email"),
                    "debug": debug
                }
            )
            print("✅ Stored procedure executed")

        class_cache = session.get("class_cache", {})
        updated_keys = 0
        updated_students = 0

        for key, cache in class_cache.items():
            if key.startswith(f"{class_id}_{term}_{year}_"):
                students = cache.get("student_competencies", [])
                for student in students:
                    if str(student.get("NSN")) == str(nsn):
                        print(f"✏️ Updating cache for NSN {nsn}, header {header_name}")
                        student[header_name] = status
                        updated_students += 1
                updated_keys += 1

        session["class_cache"] = class_cache
        print(f"✅ Cache edited for {updated_keys} key(s), {updated_students} student(s)")

        return jsonify({"success": True})
    except Exception as e:
        print("❌ Exception occurred during update_competency:")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@class_bp.route("/update_scenario", methods=["POST"])
@login_required
def update_scenario():
    data = request.get_json()
    nsn = data.get("nsn")
    header = data.get("header")
    value = data.get("value")
    class_id = data.get("class_id")
    term = data.get("term")
    year = data.get("year")
    debug = 0

    print(f"📥 Incoming update_scenario call")
    print(f"➡️ NSN: {nsn}, Header: {header}, Value: {value}, Class ID: {class_id}, Term: {term}, Year: {year}")

    if None in (nsn, header, value, class_id, term, year):
        return jsonify(success=False, error="Missing parameters"), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            print("🔄 Running stored procedure FlaskUpdateAchievement...")
            conn.execute(
                text("EXEC FlaskUpdateAchievement @NSN = :nsn, @Header = :header, @Value = :value, @Email = :email, @Debug = :debug"),
                {
                    "nsn": nsn,
                    "header": header,
                    "value": value,
                    "email": session.get("user_email"),
                    "debug": debug
                }
            )
            print("✅ Stored procedure executed")

        # ✏️ Inline update of session cache
        class_cache = session.get("class_cache", {})
        prefix = f"{class_id}_{term}_{year}_"
        updates = 0

        for key in list(class_cache):
            if key.startswith(prefix):
                entry = class_cache[key]
                students = entry.get("student_competencies", [])
                for student in students:
                    if isinstance(student, dict) and str(student.get("NSN")) == str(nsn):
                        print(f"✏️ Updating scenario cache for NSN {nsn}, header {header} in key {key}")
                        student[header] = str(value)
                        updates += 1
                class_cache[key] = entry  # save updated version

        session["class_cache"] = class_cache
        print(f"✅ Scenario cache updated in {updates} cache keys")

        return jsonify(success=True)

    except Exception as e:
        print("❌ Scenario update failed:", e)
        import traceback
        traceback.print_exc()
        return jsonify(success=False, error=str(e)), 500


@class_bp.route('/Reporting', methods=["GET", "POST"])
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
        #print(request.form.get("funder"))
        #print(session.get("desc"))
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

@class_bp.route("/get_schools_by_group")
@login_required
def get_schools_by_group():
    user_role = session.get("user_role")
    if user_role != "GRP":
        return jsonify([])

    group_entities = session.get("group_entities", {})
    provider_ids = [str(e["id"]) for e in group_entities.get("PRO", [])]
    funder_ids = [str(e["id"]) for e in group_entities.get("FUN", [])]
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)

    engine = get_db_engine()
    with engine.connect() as conn:
        if provider_ids:
            csv_providers = ",".join(provider_ids)
            result = conn.execute(
                text("EXEC FlaskSchoolsByGroupProviders :ProviderList, :Term, :Year"),
                {"ProviderList": csv_providers, "Term": term, "Year": year}
            )
        elif funder_ids:
            csv_funders = ",".join(funder_ids)
            result = conn.execute(
                text("EXEC FlaskSchoolsByGroupFunders :FunderList, :Term, :Year"),
                {"FunderList": csv_funders, "Term": term, "Year": year}
            )
        else:
            return jsonify([])

        schools = [{"MOENumber": row.MOENumber, "School": row.School} for row in result]
        return jsonify(schools)

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

@class_bp.route('/get_schools_by_provider')
@login_required
def get_schools_by_provider():
    if session.get("user_role") != "PRO":
        return jsonify([])

    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)
    provider_id = session.get("user_id")

    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                EXEC FlaskHelperFunctionsSpecific
                    @Request = 'SchoolsByProviderTermYear',
                    @Term = :term,
                    @Year = :year,
                    @ProviderID = :provider_id
            """), {
                "term": term,
                "year": year,
                "provider_id": provider_id
            }
        )
        schools = [dict(row._mapping) for row in result]
    return jsonify(schools)


@class_bp.route('/get_schools_by_funder')
@login_required
def get_schools_by_funder():
    user_role = session.get("user_role")
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)

    if not term or not year:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        if user_role == "FUN":
            funder_id = session.get("user_id")
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific
                        @Request = 'SchoolsByFunderTermYear',
                        @Term = :term,
                        @Year = :year,
                        @FunderID = :funder_id
                """), {
                    "term": term,
                    "year": year,
                    "funder_id": funder_id
                }
            )
        else:
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific
                        @Request = 'SchoolsByTermYear',
                        @Term = :term,
                        @Year = :year
                """), {
                    "term": term,
                    "year": year
                }
            )
        schools = [dict(row._mapping) for row in result]
    return jsonify(schools)

@class_bp.route('/SchoolClasses', methods=['GET', 'POST'])
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
        suggestions=suggestions,
        TERM=session.get("nearest_term"),
        YEAR=session.get("nearest_year")
    )
    
    
def generate_qr_code_png(data, box_size=2):
    qr = qrcode.QRCode(box_size=box_size, border=1)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"
@class_bp.route("/Class/print/<int:class_id>/<int:term>/<int:year>")
@login_required
def print_class_view(class_id, term, year):
    try:
        

        filter_type = request.args.get("filter") or session.get("last_filter_used", "all")
        order_by = request.args.get("order_by", "last")
        cache_key = f"{class_id}_{term}_{year}_{filter_type}"

        print(f"🖨️ [print_class_view] Requested print for cache key: {cache_key}")
        print(f"📦 Available cache keys: {list(session.get('class_cache', {}).keys())}")

        if request.args.get("refresh") == "1":
            print("🔁 Refresh requested — clearing cache key if present")
            session.get("class_cache", {}).pop(cache_key, None)

        cache = session.get("class_cache", {}).get(cache_key)

        if not cache or "student_competencies" not in cache:
            print("🔄 Cache miss or incomplete — regenerating from FlaskGetClassStudentAchievement")
            engine = get_db_engine()
            with engine.begin() as conn:
                result = conn.execute(
                    text("""EXEC FlaskGetClassStudentAchievement 
                            @ClassID = :class_id, 
                            @Term = :term, 
                            @CalendarYear = :year, 
                            @Email = :email, 
                            @FilterType = :filter"""),
                    {
                        "class_id": class_id,
                        "term": term,
                        "year": year,
                        "email": session.get("user_email"),
                        "filter": filter_type
                    }
                )
                df = pd.DataFrame(result.fetchall(), columns=result.keys())

                if df.empty:
                    flash("No class data found for printing.", "warning")
                    return redirect(url_for("class_bp.funder_classes"))

                comp_df = (
                    df[["CompetencyLabel", "CompetencyID", "YearGroupID"]]
                    .drop_duplicates()
                    .rename(columns={"CompetencyLabel": "label"})
                )
                comp_df["col_order"] = comp_df["YearGroupID"].astype(str).str.zfill(2) + "-" + comp_df["CompetencyID"].astype(str).str.zfill(4)
                comp_df = comp_df.sort_values("col_order")
                labels = comp_df["label"].tolist()

                meta_cols = [
                    "NSN", "FirstName", "LastName", "PreferredName",
                    "DateOfBirth", "Ethnicity", "YearLevelID"
                ]
                df_combined = df.pivot_table(
                    index=meta_cols,
                    columns="label",
                    values="CompetencyStatus",
                    aggfunc="first"
                ).fillna(0).astype(int).replace({1: "✓", 0: ""}).reset_index()

                expiry_time = datetime.now(timezone.utc) + timedelta(minutes=15)
                session.setdefault("class_cache", {})[cache_key] = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "expires": expiry_time.isoformat(),
                    "students": df_combined.to_dict(),
                    "competencies": comp_df.to_dict(orient="records"),
                    "filter": filter_type,
                    "student_competencies": df_combined.to_dict(orient="records")
                }

        else:
            print("✅ Cache hit — loading from session")
            df_combined = pd.DataFrame(cache["student_competencies"])
            df_combined = df_combined.replace({1: "✓", 0: ""})
            comp_df = pd.DataFrame(cache["competencies"])

        labels = comp_df["label"].tolist()
        grouped = {"0–2": [], "3–4": [], "5–6": [], "7–8": []}
        for row in df_combined.to_dict(orient="records"):
            for group in grouped:
                grouped[group].append(row)

        def get_range_labels(labels, yr_range):
            return [label for label in labels if f"({yr_range})" in label]

        columns_by_range = {
            "0–2": get_range_labels(labels, "0-2"),
            "3–4": get_range_labels(labels, "3-4"),
            "5–6": get_range_labels(labels, "5-6"),
            "7–8": get_range_labels(labels, "7-8")
        }

        engine = get_db_engine()
        with engine.connect() as conn:
            class_info = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :class_id"),
                {"Request": "ClassInfoByID", "class_id": class_id}
            ).fetchone()

        class_name = class_info.ClassName if class_info else "Unknown Class"
        teacher_name = class_info.TeacherName if class_info else "Unknown Teacher"

        target_path = url_for("class_bp.view_class", class_id=class_id, term=term, year=year)
        login_url = url_for("auth_bp.login", next=target_path, _external=True)
        qr_data_uri = generate_qr_code_png(login_url)

        return render_template(
            "print_view.html",
            grouped=grouped,
            columns_by_range=columns_by_range,
            class_name=class_name,
            teacher_name=teacher_name,
            filter_type=filter_type,
            now=datetime.now,
            qr_data_uri=qr_data_uri
        )

    except Exception as e:
        import traceback
        print("❌ Unhandled error in print_class_view:", e)
        traceback.print_exc()
        return "Internal Server Error (print view)", 500
