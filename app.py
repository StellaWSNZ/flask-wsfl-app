"""
Flask App Overview:
This web app allows users to upload a CSV of student data. It validates each row using stored procedures,
retrieves matching competency and scenario data, and displays the results (valid/invalid) with a Bootstrap interface.

Key Features:
- Upload CSVs with student NSNs and metadata
- Async processing with threading to avoid UI delay
- Uses stored procedures for data integrity (CheckNSNMatch, GetStudentCompetencyStatus)
- Front-end shows real-time progress bar
- Displays results in a styled table (valid data and errors)
"""


# Loading required packages
from flask import Flask, render_template, request, Response, flash, session, redirect, url_for, jsonify, send_file
from sqlalchemy import create_engine, text       # For ODBC database connection to Azure SQL Server
import pyodbc
import os             # For reading environment variables
from dotenv import load_dotenv  # Load .env file for credentials
import pandas as pd   # For reading CSVs and processing tabular data
from werkzeug.utils import secure_filename  # Safe handling of uploaded filenames
import threading      # Allows background processing (non-blocking upload handling)
import io 
from io import StringIO, BytesIO
import base64
import matplotlib
matplotlib.use("Agg")  # Use non-GUI backend
import matplotlib.pyplot as plt
import textwrap
import bcrypt
from datetime import datetime
import json

from functools import wraps

from providernationalplot import create_competency_report
from competencyplot import get_db_engine, load_competency_rates, make_figure
from nationalplot import generate_national_report

processing_status = {
    "current": 0,
    "total": 0,
    "done": False
}
last_pdf_generated = None
last_pdf_filename = None
last_png_generated = None
last_png_filename = None

school_name = None
moe_number = None
teacher_name = None
class_name = None
REQUIRE_LOGIN = True

load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv("SECRET_KEY", "changeme123")  # Replace this in production!


# Secure database connection (variables stored in .env and render setup)

def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    # print(connection_string)
    return create_engine(connection_string, fast_executemany=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if REQUIRE_LOGIN and not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function
@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get("next")

    if request.method == 'POST':
        email = request.form.get('username')
        password = request.form.get('password').encode('utf-8')

        engine = get_db_engine()

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT HashPassword FROM flasklogin WHERE Email = :email"),
                {"email": email}
            ).fetchone()

        if result:
            stored_hash = result.HashPassword.encode('utf-8')

            if bcrypt.checkpw(password, stored_hash):
                with engine.connect() as conn:
                    user_info = conn.execute(
                        text("EXEC FlaskLoginValidation :Email"),
                        {"Email": email}
                    ).fetchone()

                session["logged_in"] = True
                session["user_role"] = user_info.Role
                session["user_id"] = user_info.ID
                session["display_name"] = user_info.DisplayName
                session["last_login_nzt"] = str(user_info.LastLogin_NZT)
                session["desc"] = str(user_info.Desc)

                if user_info.Role == "PRO":
                    with engine.connect() as conn:
                        prov = conn.execute(
                            text("SELECT Description FROM Provider WHERE ProviderID = :id"),
                            {"id": user_info.ID}
                        ).fetchone()
                        if prov:
                            session["provider_name"] = prov.Description

                return redirect(next_url or url_for("home"))

        # ⛔ If login fails
        flash("Invalid credentials", "danger")
        return render_template("login.html")  # ✅ safe fallback

    return render_template("login.html")



@app.route('/get_dropdown_options', methods=['GET'])
def get_dropdown_options():
    role = request.args.get('role')
    engine = get_db_engine()
    options = []

    if role == 'PRO':
        # Fetch providers from the database
        with engine.connect() as conn:
            result = conn.execute(text("SELECT ProviderID, Description FROM Provider"))
            options = [{"id": row.ProviderID, "name": row.Description} for row in result]

    elif role == 'MOE':
        # Fetch schools from the MOE_SchoolDirectory
        with engine.connect() as conn:
            result = conn.execute(text("SELECT MOENumber, SchoolName FROM MOE_SchoolDirectory"))
            options = [{"id": row.MOENumber, "name": row.SchoolName} for row in result]

    return jsonify(options)


@app.route('/reporting', methods=["GET", "POST"])
@login_required
def reporting():
    global last_pdf_generated, last_pdf_filename

    engine = get_db_engine()
    role = session.get("user_role")
    user_id = session.get("user_id")

    providers = []
    competencies = []
    img_data = None
    report_type = None
    term = None
    year = None
    provider_name = None
    dropdown_string = None
    with engine.connect() as conn:
        if role == "ADM":
            result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"})
            providers = [row.Description for row in result]

        result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "CompetencyDropdown"})
        competencies = [row.Competency for row in result]

    if request.method == "POST":
        report_type = request.form.get("report_type")
        term = int(request.form.get("term"))
        year = int(request.form.get("year"))
        provider_name = request.form.get("provider") or session.get("provider_name")
        global last_png_generated, last_png_filename, last_pdf_generated, last_pdf_filename

        if report_type == "Provider":
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT ProviderID FROM Provider WHERE Description = :Description"),
                    {"Description": provider_name}
                )
                row = result.fetchone()
            if not row:
                flash("Provider not found.", "danger")
                return redirect(url_for("provider"))

            provider_id = int(row.ProviderID)
            fig = create_competency_report(term, year, provider_id, provider_name)

            # Generate PNG
            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            img_data = base64.b64encode(png_buf.getvalue()).decode("utf-8")

            # Store PNG globally
            global last_png_generated
            last_png_generated = io.BytesIO(png_buf.getvalue())  # make a copy for download

            # Generate PDF
            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)

            global last_pdf_generated, last_pdf_filename
            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{term}_{year}.pdf"

            plt.close(fig)


            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{provider_name}_{term}_{year}.pdf"
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
                    return redirect(url_for("provider"))

                competency_id = row.CompetencyID
                year_group_id = row.YearGroupID

            # 🔍 Load data from competencyplot
            df = load_competency_rates(engine, year, term, competency_id, year_group_id)
            if df.empty:
                flash("No data found.", "warning")
                return redirect(url_for("provider"))

            title = f"{df['CompetencyDesc'].iloc[0]} ({df['YearGroupDesc'].iloc[0]})"
            
            # 📈 Generate figure using competencyplot.make_figure
            fig = make_figure(df, title)

            # 🖼️ PNG for display
            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            img_data = base64.b64encode(png_buf.read()).decode("utf-8")

            # 💾 Store PNG buffer
            last_png_generated = io.BytesIO(png_buf.getvalue())
            last_png_filename = f"{report_type}_Report_{dropdown_string.replace(' ', '_')}_{term}_{year}.png"

            # 🧾 PDF for download
            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)
            #global last_pdf_generated, last_pdf_filename
            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{dropdown_string.replace(' ', '_')}_{term}_{year}.pdf"

            plt.close(fig)


    return render_template("reporting.html",
                       providers=providers,
                       competencies=competencies,
                       user_role=role,
                       img_data=img_data,
                       selected_report_type=report_type,
                       selected_term=term,
                       selected_year=year,
                       selected_provider=provider_name,
                       selected_competency=dropdown_string if report_type == "Competency" else None)



@app.route('/reporting/download_pdf')
@login_required
def provider_download_pdf():
    if last_pdf_generated is None:
        flash("No PDF report has been generated yet.", "warning")
        return redirect(url_for("provider"))

    last_pdf_generated.seek(0)
    return send_file(
        last_pdf_generated,
        download_name=last_pdf_filename or "report.pdf",
        as_attachment=True,
        mimetype='application/pdf'
    )

@app.route('/reporting/download_png')
@login_required
def provider_download_png():
    if last_png_generated is None:
        flash("No PNG report has been generated yet.", "warning")
        return redirect(url_for("provider"))

    last_png_generated.seek(0)
    return send_file(
        last_png_generated,
        download_name=last_png_filename or last_pdf_filename.replace(".pdf", ".png") if last_pdf_filename else "report.png",
        as_attachment=True,
        mimetype="image/png"
    )


@app.route('/school')
@login_required
def school():
    if session.get("user_role") == "PRO":
        return redirect(url_for("home"))
    return render_template("school.html")

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route('/favicon.ico')
def favicon():
    return send_file(os.path.join(app.root_path, 'static', 'favicon.ico'), mimetype='image/vnd.microsoft.icon')



@app.route('/')
@login_required
def home():
    role = session.get("user_role")
    display_name = session.get("display_name")
    subtitle = ""
    if(session["user_role"]=="ADM"):
        subtitle = "You are logged in as Admin. Last Logged in: " +  (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    elif (session["user_role"]=="PRO"):
        subtitle = "You are logged in as "+session["desc"]+" (provider) staff. Last Logged in: " +  (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    elif (session["user_role"]=="MOE"):
        subtitle = "You are logged in as "+session["desc"]+" (school) staff. Last Logged in: " +  (datetime.fromisoformat(session["last_login_nzt"])).strftime('%A, %d %B %Y, %I:%M %p')
    

    if role == "ADM":
        cards = [
            {"title": "Generate Reports", "text": "Build reports on provider and competency performance.", "href": "/reporting", "image": "placeholder.png"},
            {"title": "Audit Activity", "text": "Review login history and recent activity.", "href": "/comingsoon.html", "image": "placeholder.png"},
            {"title": "Create User", "text": "Add a new admin, MOE, or provider account.", "href": "/create_user", "image": "placeholder.png"},

        ]
    elif role == "MOE":
        cards = [
            {"title": "Upload Class List", "text": "Submit a class list and view student progress.", "href": "/", "image": "placeholder.png"},
            {"title": "Generate Summary", "text": "Download summary reports for your schools.", "href": "/comingsoon.html", "image": "placeholder.png"},
            {"title": "Support & Help", "text": "Access help documentation and contact support.", "href": "/comingsoon.html", "image": "placeholder.png"},
        ]
    elif role == "PRO":
        cards = [
            {"title": "Student Competency Maintenence", "text": "Update competency achievements for your class.", "href": "/provider_classes", "image": "viewclass.png"},
            {"title": "Live Reporting", "text": "Generate reporting for your provider.", "href": "/reporting", "image": "placeholder.png"},
            {"title": "Maintenance", "text": "Any issues with school and classes recorded.", "href": "/comingsoon.html", "image": "placeholder.png"},
        ]
    else:
        cards = []

    return render_template("index.html", display_name=display_name, subtitle=subtitle, cards=cards)


# Main logic to process each row from the uploaded CSV:
# 1. Use `CheckNSNMatch` stored procedure to validate student info.
# 2. If student is valid:
#    - Fetch scenario info from StudentScenario
#    - Fetch competency status using `GetStudentCompetencyStatus`
#    - Reconstruct row with ordered competency and scenario data
# 3. If NSN not found, default all competencies to blank and log basic info
# 4. Catch and store any errors

# Adds scenario columns back in at 2nd-to-last and 4th-to-last positions
# This maintains a user-friendly column layout in the final table
def process_uploaded_csv(df, term, calendaryear):
    engine = get_db_engine()
    processing_status["current"] = 0
    processing_status["total"] = len(df)
    processing_status["done"] = False
    errors = []
    valid_data = []

    with engine.connect() as connection:
        # Get all competencies
        result = connection.execute(
            text("EXEC GetRelevantCompetencies :CalendarYear, :Term"),
            {"CalendarYear": calendaryear, "Term": term}
        )
        competencies = pd.DataFrame(result.fetchall(), columns=result.keys())

        label_map = (
            competencies.assign(
                label=lambda d: d['CompetencyDesc'].astype(str) + "<br> (" + d['YearGroupDesc'].astype(str) + ")",
                col_order=lambda d: d['YearGroupID'].astype(str).str.zfill(2) + "-" + d['CompetencyID'].astype(str).str.zfill(4)
            )
            [['CompetencyID', 'YearGroupID', 'label', 'col_order']]
            .drop_duplicates()
            .sort_values('col_order')
        )
        labels = label_map['label'].tolist()


    for _, row in df.iterrows():
        processing_status["current"] += 1

        try:
            # Fully fetch the result BEFORE doing another SQL call
            with engine.connect() as connection:
                # Call CheckNSNMatch
                result = connection.execute(
                    text("""EXEC CheckNSNMatch 
                        :NSN, :FirstName, :PreferredName, :LastName,
                        :BirthDate, :Ethnicity, :CalendarYear, :Term
                    """),
                    {
                        "NSN": row['NSN'] or None,
                        "FirstName": row['FirstName'] or None,
                        "PreferredName": row.get('PreferredName') or None,
                        "LastName": row['LastName'] or None,
                        "BirthDate": row['BirthDate'] if pd.notna(row['BirthDate']) else None,
                        "Ethnicity": row.get('Ethnicity') or None,
                        "CalendarYear": calendaryear,
                        "Term": term
                    }
                )
                result_row = dict(result.mappings().first())


        
            



            if 'Error' in result_row and result_row['Error']:
                
                errors.append(result_row)
                
            elif result_row.get('Message') == 'NSN not found in Student table':
                valid_data.append({
                    'NSN': result_row['NSN'],
                    'FirstName': result_row.get('FirstName'),
                    'LastName': result_row.get('LastName'),
                    'PreferredName': result_row.get('PreferredName'),
                    'BirthDate': result_row.get('BirthDate'),
                    'Ethnicity': result_row.get('Ethnicity'),
                    'YearLevel': result_row.get('YearLevel'),
                    **{label: '' for label in labels},
                    "Scenario One - Selected <br> (7-8)": "",
                    "Scenario Two - Selected <br> (7-8)": ""
                })
            else:
                # Now it's safe to query again
                 with engine.connect() as connection:
                    # Fetch Scenario
                    
                    scenario_result = connection.execute(
                        text("EXEC FlaskHelperFunctions :Request, :Number"),
                        {"Request": "StudentScenario", "Number": result_row['NSN']}
                    )

                    scenario_query = pd.DataFrame(scenario_result.fetchall(), columns=scenario_result.keys())

                    # Build dictionary
                    if scenario_query.shape[0] > 0:
                        scenario_data = {
                            "Scenario One - Selected <br> (7-8)": scenario_query.iloc[0].get("Scenario1", ""),
                            "Scenario Two - Selected <br> (7-8)": scenario_query.iloc[0].get("Scenario2", "")
                        }
                    else:
                        scenario_data = {
                            "Scenario One - Selected <br> (7-8)": "",
                            "Scenario Two - Selected <br> (7-8)": ""
                        }
                    # Fetch Competency Status
                    comp_result = connection.execute(
                        text("EXEC GetStudentCompetencyStatus :NSN, :Term, :CalendarYear"),
                        {"NSN": result_row['NSN'], "Term": term, "CalendarYear": calendaryear}
                    )
                    comp = pd.DataFrame(comp_result.fetchall(), columns=comp_result.keys())
                    comp = comp.merge(label_map, on=['CompetencyID', 'YearGroupID'], how='inner')
                    comp_row = comp.set_index('label')['CompetencyStatusID'].reindex(labels).fillna(0).astype(int).to_dict()
                    comp_row = {k: ('Y' if v == 1 else '') for k, v in comp_row.items()}
                    #print(f"NSN {result_row['NSN']} - Competencies columns: {comp.columns.tolist()}")
                    #print(f"Competencies fetched: {len(comp)} rows")

                    if 'label' not in comp.columns:
                        raise ValueError("Competencies not found for NSN")
                   

                    # Add personal info fields up front
                    full_row = {
                        'NSN': result_row.get('NSN'),
                        'FirstName': result_row.get('FirstName'),
                        'LastName': result_row.get('LastName'),
                        'PreferredName': result_row.get('PreferredName'),
                        'BirthDate': result_row.get('BirthDate'),
                        'Ethnicity': result_row.get('Ethnicity'),
                        'YearLevel': result_row.get('YearLevel'),
                        **comp_row,  # Add all competency columns
                        **scenario_data
                    }

                    valid_data.append(full_row)


        except Exception as e:
            errors.append({"NSN": row.get('NSN', None), "Error": str(e)})

   
    df_valid = pd.DataFrame(valid_data)
    if not df_valid.empty:
        cols = df_valid.columns.tolist()
        for col in ["Scenario One - Selected <br> (7-8)", "Scenario Two - Selected <br> (7-8)"]:
            if col in df_valid.columns:
                df_valid[col] = df_valid[col].replace(0, '')

        # Remove scenario columns temporarily
        s1 = cols.pop(cols.index("Scenario One - Selected <br> (7-8)"))
        s2 = cols.pop(cols.index("Scenario Two - Selected <br> (7-8)"))

        # Insert at 4th-to-last and 2nd-to-last
        cols.insert(-2, s1)
        cols.insert(-1, s2)

        df_valid = df_valid[cols]
    if 'YearLevel' in df_valid.columns:
        df_valid['YearLevel'] = df_valid['YearLevel'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    for row in errors:
        if 'YearLevel' in row and row['YearLevel'] is not None:
            row['YearLevel'] = int(row['YearLevel'])

    df_errors = pd.DataFrame(errors)
    processing_status["done"] = True

    return df_valid, df_errors

@app.route('/update_competency', methods=['POST'])
@login_required
def update_competency():
    data = request.json

    # Print received data
    print("Received data:", data)

    nsn = data.get("nsn")
    header_name = data.get("header_name")
    status = data.get("status") 

    # Print extracted values
    print(f"NSN: {nsn}, Header Name: {header_name}, Status: {status}")

    if not all([nsn, header_name, status]):
        return jsonify({"success": False, "message": "Missing data"}), 400

    # Just print, don't execute the query
    print(f"Would update competency for NSN {nsn} with status '{status}' and header name '{header_name}'")

    # UpdateAchievement NSN, Header, Value

    # Return a successful response without executing the SQL query
    return jsonify({"success": True, "message": "Data received and printed successfully."})

@app.route("/update_scenario", methods=["POST"])
def update_scenario():
    data = request.get_json()
    nsn = data.get("nsn")
    header = data.get("header")
    value = data.get("value")

    print(f"Would update scenario for NSN {nsn}, header '{header}', and value '{value}'")

    try:
        # Uncomment this block when ready to write to the database
        # UpdateAchievement NSN, Header, Value

        return jsonify(success=True)
    except Exception as e:
        print("❌ Scenario update failed:", e)
        return jsonify(success=False, error=str(e)), 500

# Handles file upload from the form:
# - Parses CSV and birthdates
# - Kicks off async processing
# - Displays dynamic progress page using JS polling to track upload progress
@app.route('/upload', methods=['POST'])
def upload():
    global processing_status, term, calendaryear
    global school_name, moe_number, teacher_name, class_name
    term = int(request.form.get("term", 1))
    calendaryear = int(request.form.get("year", 2025))
    school_name = request.form.get("school", "")
    teacher_name = request.form.get("teacher_name", "")
    class_name = request.form.get("class_name", "")

    # Extract MOENumber from school string if in format "SchoolName (MOENumber)"
    import re
    match = re.match(r"^(.*?)\s+\((\d+)\)$", school_name)
    if match:
        school_name, moe_number = match.groups()  

    try:
        file = request.files.get("csv_file")
        if not file:
            return "No file uploaded", 400

        df = pd.read_csv(file)
        df['BirthDate'] = pd.to_datetime(df['BirthDate'], errors='coerce', dayfirst=True).dt.date

        if "NSN" not in df.columns:
            return "CSV must contain 'NSN' column", 400

        # Start processing in a separate thread
        thread = threading.Thread(target=process_and_store_results, args=(df, term, calendaryear))
        thread.start()

        # Immediately show progress page
        return render_template("uploadclasslist.html")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Upload failed: {e}", 500

last_valid_df = pd.DataFrame()
last_error_df = pd.DataFrame()

def process_and_store_results(df, term, calendaryear):
    global last_valid_df, last_error_df

    try:
        processing_status["current"] = 0
        processing_status["total"] = len(df)
        processing_status["done"] = False    

        valid, errors = process_uploaded_csv(df, term, calendaryear)
        last_valid_df = valid
        last_error_df = errors

    except Exception as e:
        import traceback
        traceback.print_exc()
        last_valid_df = pd.DataFrame()
        last_error_df = pd.DataFrame([{"Error": str(e)}])

    finally:
        processing_status["done"] = True


@app.route('/progress')
def get_progress():
    return jsonify({
        "current": processing_status["current"],
        "total": processing_status["total"],
        "done": processing_status["done"]
    })

# Displays:
# - Valid records (as a Bootstrap-styled HTML table)
# - Errors (if any) in a separate table
# - Includes a back button to return to the homepage
@app.route('/results')
@login_required
def results():
    valid_html = (
        last_valid_df.to_html(classes="table table-bordered table-sm", index=False, escape=False)
        if not last_valid_df.empty else "<p class='text-muted'>No valid records found.</p>"
    )

    error_html = (
        last_error_df.to_html(classes="table table-bordered table-sm text-danger", index=False)
        if not last_error_df.empty else "<p class='text-success'>No errors found.</p>"
    )

    return render_template("displayresults.html", valid_html=valid_html, error_html=error_html)



@app.route('/get_schools')
def get_schools():
    provider = request.args.get('provider')  

    if not provider:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as connection:
        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request,  @Text=:Text"),
            {"Request": "FilterSchool", "Text": provider}
        )
        schools = pd.DataFrame(result.fetchall(), columns=result.keys())

    school_list = schools['School'].dropna().tolist()
    return jsonify(school_list)

@app.route('/get_provider_dropdown')
def get_provider_dropdown():
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT ProviderID, Description FROM Provider"))
        providers = [{"id": row.ProviderID, "description": row.Description} for row in result]
    return jsonify(providers)

@app.route('/get_school_dropdown')
def get_school_dropdown():
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT MOENumber, SchoolName FROM MOE_SchoolDirectory"))
        schools = [{"id": row.MOENumber, "description": row.SchoolName} for row in result]
    return jsonify(schools)

@app.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if session.get("user_role") != "ADM":
        flash("Unauthorized access", "danger")
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password").encode("utf-8")
        role = request.form.get("role")
        name = request.form.get("name")
        selected_id = request.form.get("selected_id")  # The value from the dropdown
        provider_or_school = request.form.get("role")  # To check if it's provider or school

        # Hash the password
        hashed_pw = bcrypt.hashpw(password, bcrypt.gensalt()).decode("utf-8")

        engine = get_db_engine()

        # Check if the email already exists
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT 1 FROM FlaskLogin WHERE Email = :email"),
                {"email": email}
            ).fetchone()

            if existing:
                flash("⚠️ Email already exists. Please use a different email.", "warning")
                return redirect(url_for("create_user"))

            # Set the user ID
            if role == "ADM":
                user_id = None  # Admin gets None for ID
            else:
                user_id = selected_id  # Set the ID based on the selected Provider or School

            # Insert new user into FlaskLogin with the generated ID
            conn.execute(
                text("""
                    INSERT INTO FlaskLogin (Email, HashPassword, Role, DisplayName, ID) 
                    VALUES (:email, :hash, :role, :name, :user_id)
                """),
                {"email": email, "hash": hashed_pw, "role": role, "name": name, "user_id": user_id}
            )
            flash(f"✅ User {email} created with role {role}.", "success")
            return redirect(url_for("create_user"))
    return render_template("create_user.html")


@app.route('/download_excel')
@login_required
def download_excel():
    if last_valid_df.empty and last_error_df.empty:
        return "No data to export", 400


    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:

        
        if not last_valid_df.empty:
            last_valid_df.to_excel(writer, index=False, header=True, sheet_name='Competency Report', startrow= 9, )
            worksheet = writer.sheets['Competency Report']
            workbook = writer.book
            header_format = workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'bottom',
                'border': 0
            })
            
            # Write column headers on row 9 (1-based Excel index)
            for col_num, col_name in enumerate(last_valid_df.columns):
                worksheet.write(9, col_num, col_name, header_format)


            for col_idx, col in enumerate(last_valid_df.columns):
                if "<br>" in col:
                    desc, year = col.split("<br>")
                    desc = desc.strip()
                    year = year.strip(" ()")
                    
                    worksheet.write(8, col_idx, desc, header_format)  # row 9
                    worksheet.write(9, col_idx, year, header_format)  # row 10
                else:
                    worksheet.write(8, col_idx, col, header_format)    # row 9
                    if "Scenario" in col:
                        worksheet.write(9, col_idx, "7-8", header_format)  # row 10
                    else:
                        worksheet.write(8, col_idx, "", header_format)  # row 10


            worksheet.freeze_panes(10, 0)
            downward_format = workbook.add_format({
                'text_wrap': True,
                'rotation': 90,  # rotate downward
                'align': 'center',
                'valign': 'middle',
                'bold': True,
                'border': 0
            })

            # Merge each column from row 1 to 10 (index 0 to 9)
            for col_idx in range(7, len(last_valid_df.columns) - 4):
                col_name = last_valid_df.columns[col_idx]
                header_val = col_name.split("<br>")[0].strip()

                # Merge from row 0 to 9 in this column
                worksheet.merge_range(0, col_idx, 8, col_idx, header_val, downward_format)

            for col_idx in range(len(last_valid_df.columns) - 4, len(last_valid_df.columns)):
                col_name = last_valid_df.columns[col_idx]
                header_val = col_name.split("<br>")[0].strip()

                # Merge from row 0 to 9 in this column
                worksheet.merge_range(3, col_idx, 8, col_idx, header_val, downward_format)
            col_start = len(last_valid_df.columns) - 4
            col_end = len(last_valid_df.columns) - 1

            worksheet.merge_range(0, col_start, 2, col_end,
                                'Demonstrate use of multiple skills to respond to two different scenarios',
                                workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'bottom',
                'text_wrap': True,
                'border': 0
            }))
            worksheet.set_row(0, 70) 
            worksheet.set_column(0,5,15)
            worksheet.insert_image('A1', 'static/DarkLogo.png', {
                'x_scale': 0.2,  # Scale width to 50%
                'y_scale': 0.2   # Scale height to 50%
            })

            # Define formats
            label_bold = workbook.add_format({'bold': True})
            label_normal = workbook.add_format({})  # fallback if needed

            # Define data
            school_info = [
                (5, 0, 'School Name:', school_name),
                (6, 0, 'MOE Number:', int(moe_number)),
                (7, 0, 'Calendar Year:', calendaryear)
            ]

            teacher_info = [
                (5, 3, 'Teacher Name:', teacher_name),
                (6, 3, 'Class Name:', class_name),
                (7, 3, 'School Term:', term)
            ]

            # Write cells
            for row, col, label, value in school_info + teacher_info:
                fmt = label_bold if label.endswith(':') else label_normal
                worksheet.write(row, col, label, fmt)
                worksheet.write(row, col + 1, value)
        if not last_error_df.empty:
            last_error_df.to_excel(writer, sheet_name='Errors', startrow=1, index=False, header=False)
            worksheet_errors = writer.sheets['Errors']

            # Define a custom format for headers
            header_format_errors = workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'bottom',
                'text_wrap': True,
                'border': 0 
            })

            # Manually write headers in row 0
            for col_num, col_name in enumerate(last_error_df.columns):
                worksheet_errors.write(0, col_num, col_name, header_format_errors)
            worksheet_errors.set_column(0,6,15)
            worksheet_errors.set_column(7,7,50)


    output.seek(0)
    return send_file(
        output,
        download_name = school_name + " Term " + str(term) + " " + str(calendaryear) + ".xlsx"
,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/download_competency_pdf')
@login_required
def download_competency_pdf(): 
    year = int(request.args.get("year"))
    term = int(request.args.get("term"))
    dropdown_string = request.args.get("dropdown")

    engine = get_db_engine()
    with engine.connect() as connection:
        result = connection.execute(
            text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
            {"DropdownValue": dropdown_string}
        )
        row = result.fetchone()
        if row is None:
            return "Invalid dropdown selection", 400

        competency_id = row.CompetencyID
        year_group_id = row.YearGroupID

    df = load_competency_rates(engine, year, term, competency_id, year_group_id)

    if df.empty:
        return "No data found", 400

    comp_desc = df['CompetencyDesc'].unique()[0].replace(" ", "_").replace("/", "_")
    year_group = df['YearGroupDesc'].unique()[0].replace(" ", "")
    title = f"{df['CompetencyDesc'].unique()[0]} ({year_group})"

    fig = make_figure(df, title)

    output = io.BytesIO()
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(output, format="pdf")
    plt.close(fig)
    output.seek(0)

    filename = f"{year}_{term}_{comp_desc}.pdf"
    return send_file(output, download_name=filename, as_attachment=True, mimetype="application/pdf")


@app.route('/generate_report', methods=['POST'])
def generate_report():
    global last_pdf_generated, last_pdf_filename 
    report_type = request.form['report_type']
    year = int(request.form['year'])
    term = int(request.form['term'])

    if report_type == "Provider":
        provider_name = request.form.get("provider") or session.get("provider_name")

        
        engine = get_db_engine()
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT ProviderID FROM Provider WHERE Description = :Description"),
                {"Description": provider_name}
            )
            row = result.fetchone()

        if row is None:
            return "Provider not found", 400

        provider_id = int(row.ProviderID)

        # ⬇️ Replace long logic with call to graphing function
        fig = create_competency_report(term, year, provider_id, provider_name)

        buf = io.BytesIO()
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.savefig(buf, format="png")
        buf.seek(0)
        plt.close(fig)

        last_pdf_generated = buf
        last_pdf_filename = f"Provider_Report_{provider_name}_{term}_{year}.pdf"

        return render_template("generatereports.html", img_data=base64.b64encode(buf.getvalue()).decode("utf-8"))

    elif report_type == "National":
        fig = generate_national_report(term, year)

        png_buf = io.BytesIO()
        pdf_buf = io.BytesIO()

        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.savefig(png_buf, format="png")
        fig.savefig(pdf_buf, format="pdf")
        plt.close(fig)

        png_buf.seek(0)
        pdf_buf.seek(0)

        last_pdf_generated = pdf_buf
        last_pdf_filename = f"National_Report_{term}_{year}.pdf"

        img_data = base64.b64encode(png_buf.read()).decode("utf-8")
        return render_template("generatereports.html", img_data=img_data)

    elif report_type == "Competency":
        
        year = int(request.form['year'])
        term = int(request.form['term'])
        dropdown_string = request.form.get("competency")
        #print(dropdown_string)
        engine = get_db_engine()

        with engine.connect() as connection:
            result = connection.execute(
                text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
                {"DropdownValue": dropdown_string}
            )
            #print(result)
            row = result.fetchone()
            competency_id = row.CompetencyID
            year_group_id = row.YearGroupID

        # Load data
        con = get_db_engine()
        df = load_competency_rates(con, year, term, competency_id, year_group_id)

        if df.empty:
            return "No data available for the selected inputs", 400
        
        comp_desc = df['CompetencyDesc'].unique()[0]
        year_group = df['YearGroupDesc'].unique()[0]
        title = f"{comp_desc} ({year_group})"

        # Create figure and return base64
        fig = make_figure(df, title)
        buf = io.BytesIO()
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        last_pdf_generated = buf
        last_pdf_filename = f"Competency_Report_{term}_{year}.pdf"
        img_data = base64.b64encode(buf.read()).decode("utf-8")
        return render_template("competencyreport.html", img_data=img_data,year=year, term=term, dropdown=dropdown_string)
    else:
        return "Invalid report type selected", 400

@app.route('/provider_classes', methods=['GET', 'POST'])
@login_required
def provider_classes():
    if session.get("user_role") not in ["PRO", "ADM"]:
        flash("Unauthorized access", "danger")
        return redirect(url_for("home"))

    engine = get_db_engine()
    classes = []
    students = []
    selected_class_id = None

    user_role = session.get("user_role")
    user_id = session.get("user_id")  # stored during login

    with engine.connect() as conn:
        # ✅ CALL your stored procedure with optional Number param
        result = conn.execute(
            text("EXEC FlaskHelperFunctions :Request, :Number"),
            {"Request": "SchoolDropdown", "Number": None if user_role == "ADM" else user_id}
        )
        schools = pd.DataFrame(result.fetchall(), columns=result.keys())

        if request.method == "POST":
            moe_number = request.form.get("moe_number")
            term = request.form.get("term")
            year = request.form.get("calendaryear")
            selected_class_id = request.form.get("class_id")

            if not selected_class_id:
                result = conn.execute(
                    text("SELECT ClassID, ClassName, TeacherName FROM Class WHERE MOENumber = :moe AND Term = :term AND CalendarYear = :year"),
                    {"moe": moe_number, "term": term, "year": year}
                )
                classes = [row._mapping for row in result.fetchall()]
            # (you can keep the student fetch logic below here)

    # ✅ Pass schools to the template
    return render_template(
        "provider_classes.html",
        schools=schools.to_dict(orient="records"),
        classes=classes,
        students=students,
        selected_class_id=selected_class_id
    )


@app.route('/view_class/<int:class_id>/<int:term>/<int:year>')
@login_required
def view_class(class_id, term, year):
    engine = get_db_engine()
    scenarios = []

    with engine.connect() as conn:
            # Fetch scenario information from the "Scenario" table
        result = conn.execute(text("SELECT ScenarioID, HTMLScenario FROM Scenario"))
        scenarios = [{"id": row.ScenarioID, "desc": row.HTMLScenario} for row in result]
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
        school_name = school_result.SchoolName if school_result else "(unknown)"
        title_string = f"Class Name: {class_name} | Teacher Name: {teacher_name} | School Name: {school_name}"

    engine = get_db_engine()

    with engine.connect() as conn:
        # Get all students for that class
        result = conn.execute(text(""" 
            SELECT 
                s.NSN, 
                s.FirstName, 
                s.LastName, 
                s.PreferredName, 
                s.DateOfBirth, 
                e.Description AS Ethnicity, 
                sy.YearLevelID
            FROM StudentClass scm
            JOIN Student s ON s.NSN = scm.NSN
            JOIN Class c ON c.ClassID = scm.ClassID
            JOIN StudentYearLevel sy ON sy.NSN = scm.NSN 
                                      AND sy.Term = c.Term 
                                      AND sy.CalendarYear = c.CalendarYear
            JOIN Ethnicity e ON e.EthnicityID = s.EthnicityID
            WHERE scm.ClassID = :class_id 
              AND c.Term = :term 
              AND c.CalendarYear = :year
        """), {"class_id": class_id, "term": term, "year": year})

        students = pd.DataFrame(result.fetchall(), columns=result.keys())

        if students.empty:
            flash("No students found.", "warning")
            return redirect(url_for("provider_classes"))

        # Get all relevant competency labels
        comp_result = conn.execute(text("EXEC GetRelevantCompetencies :CalendarYear, :Term"),
                                   {"CalendarYear": year, "Term": term})
        comp_df = pd.DataFrame(comp_result.fetchall(), columns=comp_result.keys())
        comp_df["label"] = comp_df["CompetencyDesc"] + "<br> (" + comp_df["YearGroupDesc"] + ")"
        comp_df["col_order"] = comp_df["YearGroupID"].astype(str).str.zfill(2) + "-" + comp_df["CompetencyID"].astype(str).str.zfill(4)
        comp_df = comp_df.sort_values("col_order")
        labels = comp_df["label"].tolist()

        all_records = []

        # Loop through each student and fetch their competency and scenario data
        for _, student in students.iterrows():
            nsn = student["NSN"]

            # Competency status
            comp_result = conn.execute(text(""" 
                EXEC GetStudentCompetencyStatus :NSN, :Term, :CalendarYear
            """), {"NSN": nsn, "Term": term, "CalendarYear": year})
            comp_data = pd.DataFrame(comp_result.fetchall(), columns=comp_result.keys())

            # If no competency data, create an empty record
            if comp_data.empty:
                comp_row = {label: '' for label in labels}
            else:
                comp_data = comp_data.merge(comp_df[["CompetencyID", "YearGroupID", "label"]],
                                            on=["CompetencyID", "YearGroupID"], how="inner")
                comp_row = comp_data.set_index("label")["CompetencyStatusID"].reindex(labels).fillna(0).astype(int)
                comp_row = {k: 'Y' if v == 1 else '' for k, v in comp_row.items()}

            # Scenario data (same as original logic)
            scenario_result = conn.execute(text(""" 
                EXEC FlaskHelperFunctions :Request, :Number
            """), {"Request": "StudentScenario", "Number": nsn})
            scenario_df = pd.DataFrame(scenario_result.fetchall(), columns=scenario_result.keys())

            # Default empty values if no scenarios are found
            if not scenario_df.empty:
                scenario1 = scenario_df.iloc[0].get("Scenario1", "")
                scenario2 = scenario_df.iloc[0].get("Scenario2", "")
            else:
                scenario1 = ""
                scenario2 = ""

            # Create the merged row with scenario and competency data
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

        # Final tidy up
        df_combined = pd.DataFrame(all_records)
        df_combined = df_combined.sort_values("LastName", ascending=True)

        # Reorder scenario columns to match the spreadsheet logic
        if "Scenario One - Selected <br> (7-8)" in df_combined.columns and "Scenario Two - Selected <br> (7-8)" in df_combined.columns:
            cols = df_combined.columns.tolist()
            s1 = cols.pop(cols.index("Scenario One - Selected <br> (7-8)"))
            s2 = cols.pop(cols.index("Scenario Two - Selected <br> (7-8)"))
            cols.insert(-2, s1)
            cols.insert(-1, s2)
            df_combined = df_combined[cols]

    # Competency ID map
    competency_id_map = comp_df.set_index("label")["CompetencyID"].to_dict()

    return render_template(
        "provider_class_detail.html",
        students=df_combined.to_dict(orient="records"),
        columns=[col for col in df_combined.columns if col not in ["DateOfBirth", "Ethnicity", "FirstName", "NSN"]],
        competency_id_map=competency_id_map, scenarios=scenarios,
    class_name=class_name,
    teacher_name=teacher_name,
    school_name=school_name, class_title=title_string 
    )


@app.route('/download_pdf')
@login_required
def download_pdf():
    if last_pdf_generated is None:
        return "No PDF generated yet.", 400

    last_pdf_generated.seek(0)
    return send_file(
        last_pdf_generated,
        download_name=last_pdf_filename or "Report.pdf",
        as_attachment=True,
        mimetype='application/pdf'
    )
@app.route('/get_schools_for_provider')
@login_required
def get_schools_for_provider():
    provider_id = request.args.get("provider_id")
    #print("🔍 Received provider_id:", provider_id)

    if not provider_id:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        sql = text("EXEC FlaskHelperFunctions :Request, @Number=:Number")
        params = {"Request": "FilterSchoolID", "Number": provider_id}
        #print("🔍 Executing SQL:", sql.text)
        #print("📦 With parameters:", params)

        result = conn.execute(sql, params)

        schools = [row.School for row in result]

    #print("✅ Returning schools:", schools)
    return jsonify(schools)


@app.route('/classlistupload', methods=['GET', 'POST'])
@login_required
def classlistupload():
    validated=False

    engine = get_db_engine()
    preview_data = None
    original_columns = [] 
    providers, schools = [], []
    selected_csv = selected_provider = selected_school = selected_term = selected_year = selected_teacher = selected_class = None

    # Load providers dropdown
    with engine.connect() as conn:
        result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"})
        providers = [dict(row._mapping) for row in result]

    if request.method == 'POST':
        action = request.form.get('action')  # 'preview' or 'validate'
        selected_provider = request.form.get('provider')
        selected_school = request.form.get('school')
        selected_term = request.form.get('term')
        selected_year = request.form.get('year')
        selected_teacher = request.form.get('teachername')
        selected_class = request.form.get('classname')

        column_mappings_json = request.form.get('column_mappings')
        file = request.files.get('csv_file')
        #print(file)
        selected_csv = file if file and file.filename else None,

        if selected_provider and selected_provider.isdigit():
            selected_provider = int(selected_provider)
        if selected_year and selected_year.isdigit():
            selected_year = int(selected_year)

        # Populate school dropdown
        if selected_provider:
            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions :Request, @Number=:Number"),
                    {"Request": "FilterSchoolID", "Number": selected_provider}
                )
                schools = [row.School for row in result]

        if action == "preview" and file and file.filename.endswith('.csv'):
            df = pd.read_csv(file)

            session["raw_csv_json"] = df.to_json(orient="records")
            preview_data = df.head(10).to_dict(orient="records")
            # Store the preview data in the session for later use
            session["preview_data"] = preview_data
            original_columns = list(df.columns)

        elif action == "validate":
            validated = True
            if not session.get("raw_csv_json"):
                flash("No CSV file has been uploaded for validation.", "danger")
            else:
                try:
                    raw_df = pd.read_json(StringIO(session["raw_csv_json"]))
                    
                    column_mappings = json.loads(column_mappings_json)
                    valid_fields = ["NSN", "FirstName", "LastName", "PreferredName", "BirthDate", "Ethnicity", "YearLevel"]

                    # Build reverse mapping: selected column name → expected name
                    reverse_mapping = {v: k for k, v in column_mappings.items() if v in valid_fields}

                    # Keep only columns mapped to valid fields
                    usable_columns = [col for col in raw_df.columns if col in reverse_mapping]

                    # Rename to expected names
                    df = raw_df[usable_columns].rename(columns={col: reverse_mapping[col] for col in usable_columns})

                    # Ensure all required columns are present
                    for col in valid_fields:
                        if col not in df.columns:
                            df[col] = None  # Or np.nan
                    if "BirthDate" in df.columns:
                        df["BirthDate"] = pd.to_datetime(df["BirthDate"], errors="coerce").dt.strftime("%Y-%m-%d")

                    df_json = df.to_json(orient="records")
                    # Save a copy for debugging/inspection
                    with open("last_validated_input.json", "w", encoding="utf-8") as f:
                        f.write(df_json)

                    with engine.connect() as conn:
                        result = conn.execute(
                            text("EXEC FlaskCheckNSN_JSON :InputJSON, :Term, :CalendarYear, :MOENumber"),
                            {"InputJSON": df_json, "Term": selected_term, "CalendarYear": selected_year, "MOENumber": int(selected_school.split('(')[-1].rstrip(')'))}
                        )
                        preview_data = [dict(row._mapping) for row in result]
                        # Store the validated preview data in the session
                        session["preview_data"] = preview_data

                except Exception as e:
                    flash(f"Error during validation: {str(e)}", "danger")

        elif action == "preview":
            flash("Please upload a valid CSV file.", "danger")

    return render_template(
        "classlistupload.html",
        providers=providers,
        schools=schools,
        selected_provider=selected_provider,
        selected_school=selected_school,
        selected_term=selected_term,
        selected_year=selected_year,
        selected_teacher = selected_teacher,
        selected_class = selected_class,
        selected_csv = selected_csv,
        preview_data=preview_data,
        validated = validated,
        original_columns = original_columns
    )


@app.route('/classlistdownload', methods=['POST'])
@login_required
def classlistdownload():
    if not session.get("preview_data"):
        flash("No data available to export.", "danger")
        return redirect(url_for("classlistupload"))

    desired_order = [
        "NSN",
        "FirstName",
        "PreferredName",
        "LastName",
        "Birthdate",
        "Ethnicity",
        "YearLevel",
        "ErrorMessage",
        "Match"
    ]

    # Reconstruct DataFrame
    df = pd.DataFrame(session["preview_data"])
    df = df.fillna("")

    # Ensure only desired columns and in the correct order
    columns_to_write = [col for col in desired_order if col in df.columns]

    output = BytesIO()

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df[columns_to_write].to_excel(writer, sheet_name='Results', index=False, startrow=1, header=False)
        workbook = writer.book
        worksheet = writer.sheets['Results']

        for col_num, col_name in enumerate(columns_to_write):
            worksheet.write(0, col_num, col_name)

        # Excel column name helper
        def excel_col_letter(n):
            name = ''
            while n >= 0:
                name = chr(n % 26 + 65) + name
                n = n // 26 - 1
            return name

        max_row = len(df) + 1
        max_col = len(columns_to_write)
        last_col_letter = excel_col_letter(max_col - 1)

        worksheet.add_table(f"A1:{last_col_letter}{max_row}", {
            'columns': [{'header': col} for col in columns_to_write],
            'style': 'Table Style Light 8',
            'name': 'MyTable'
        })

        # Formats
        wrap_top_format = workbook.add_format({'text_wrap': True, 'valign': 'top'})
        red_format = workbook.add_format({'bg_color': '#D63A3A', 'font_color': '#FFFFFF', 'bold': True, 'valign': 'top'})
        orange_format = workbook.add_format({'bg_color': "#EF9D32", 'font_color': '#FFFFFF', 'bold': True, 'valign': 'top'})
        badge_format_error = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'top', 'font_color': '#FFFFFF', 'bg_color': "#D63A3A", 'border': 1})
        badge_format_ready = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'top', 'font_color': '#FFFFFF', 'bg_color': "#49B00D", 'border': 1})

        # Write formatted cells
        for row in range(1, len(df) + 1):
            error_fields = df.loc[row - 1, 'ErrorFields']
            match_value = df.loc[row - 1, 'Match']
            error_columns = [field.strip() for field in error_fields.split(',')] if pd.notna(error_fields) else []

            for col in range(len(columns_to_write)):
                col_name = columns_to_write[col]
                value = df.iloc[row - 1][col_name]
                if col_name == 'ErrorMessage' and (value is True or str(value).strip().lower() == 'true'):
                    value = ""

                if col_name in error_columns:
                    fmt = orange_format if match_value == 1 else red_format
                    worksheet.write(row, col, value, fmt)
                else:
                    worksheet.write(row, col, value)

            # Badge
            badge_col = len(columns_to_write) - 1
            badge_value = 'Ready' if match_value == 1 else 'Fix required'
            badge_format = badge_format_ready if match_value == 1 else badge_format_error
            worksheet.write(row, badge_col, badge_value, badge_format)

        # Column widths
        column_widths = {
            'NSN': 14,
            'FirstName': 20,
            'PreferredName': 20,
            'LastName': 20,
            'DateOfBirth': 18,
            'Ethnicity': 14,
            'Match': 12,
            'ErrorMessage': 40
        }

        for col_num, col_name in enumerate(columns_to_write):
            width = column_widths.get(col_name, None)
            worksheet.set_column(col_num, col_num, width, wrap_top_format)

        worksheet.set_column(max_col, max_col, 15, wrap_top_format)
        for row in range(max_row):
            worksheet.set_row(row, None, wrap_top_format)

    output.seek(0)
    return send_file(output, download_name="Fixes.xlsx", as_attachment=True)

@app.route('/submitclass', methods=['POST'])
@login_required
def submitclass():
    print("✅ Class submitted successfully!")  # For now
    flash("Class submitted successfully!", "success")
    return redirect(url_for("classlistupload"))

@app.route('/export_excel', methods=['POST'])
def export_excel():
    # Retrieve preview data from the session
    preview_data = session.get("preview_data", None)

    if preview_data is None:
        flash("No data available for export.", "danger")
        return redirect(url_for('classlistupload'))

    # Convert the data into a pandas DataFrame
    df = pd.DataFrame(preview_data)

    # Create a BytesIO buffer to store the Excel file
    output = BytesIO()

    # Create an Excel writer using XlsxWriter
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')

        # Access the XlsxWriter workbook and worksheet
        workbook = writer.book
        worksheet = writer.sheets['Sheet1']

        # Apply a simple border format for all columns (optional)
        border_format = workbook.add_format({'border': 1})

        # Set column widths to be auto-sized based on content
        for col_num, col_name in enumerate(df.columns.values):
            column_width = max(df[col_name].astype(str).map(len).max(), len(col_name))
            worksheet.set_column(col_num, col_num, column_width, border_format)

    # Rewind the buffer to the beginning
    output.seek(0)

    # Return the Excel file as a downloadable response
    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=preview_data.xlsx"}
    )

@app.context_processor
def inject_user_role():
    return dict(user_role=session.get("user_role"))

# Run app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)