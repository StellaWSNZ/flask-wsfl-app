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
from flask import Flask, request, jsonify, render_template_string, render_template, send_file, session, redirect, url_for, flash # Web framework & templating
from sqlalchemy import create_engine, text       # For ODBC database connection to Azure SQL Server
import pyodbc
import os             # For reading environment variables
from dotenv import load_dotenv  # Load .env file for credentials
import pandas as pd   # For reading CSVs and processing tabular data
from werkzeug.utils import secure_filename  # Safe handling of uploaded filenames
import threading      # Allows background processing (non-blocking upload handling)
import io 
import base64
import matplotlib
matplotlib.use("Agg")  # Use non-GUI backend
import matplotlib.pyplot as plt
import textwrap
import bcrypt
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
school_name = None
moe_number = None
teacher_name = None
class_name = None
REQUIRE_LOGIN = True

load_dotenv()

app = Flask(__name__, static_folder='static')
# app.secret_key = os.getenv("SECRET_KEY", "changeme123")  # Replace this in production!


# Secure database connection (variables stored in .env and render setup)

def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, fast_executemany=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if REQUIRE_LOGIN and not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not REQUIRE_LOGIN:
        return redirect(url_for("home"))

    if request.method == 'POST':
        email = request.form.get('username')  # assuming this is the email
        password = request.form.get('password').encode('utf-8')  # encode to bytes
        engine = get_db_engine()
        # Query to get the hashed password
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT HashPassword, Role FROM FlaskLogin WHERE Email = :email"),
                {"email": email}
            ).fetchone()

        if result:
            stored_hash = result[0].encode('utf-8')  # bcrypt expects bytes
            role = result[1]

            # Check the password
            if bcrypt.checkpw(password, stored_hash):
                session["logged_in"] = True
                session["user_role"] = role
                return redirect(url_for("home"))

        flash("Invalid credentials", "danger")

    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route('/favicon.ico')
def favicon():
    return send_file(os.path.join(app.root_path, 'static', 'favicon.ico'), mimetype='image/vnd.microsoft.icon')

# Render home page
# - CSV file uploader
# - Year and Term selectors
# - Provider dropdown populated from the database
# - JS validation and progress bar logic included 
@app.route('/')
@login_required
def home():
    print("REQUIRE_LOGIN:", REQUIRE_LOGIN)
    print("Logged in session:", session.get("logged_in"))

    engine = get_db_engine()

    with engine.connect() as connection:
        # Get provider names
        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request"),
            {"Request": "ProviderDropdown"}
        )
        providers = pd.DataFrame(result.fetchall(), columns=result.keys())
        provider_names = providers['Description'].dropna().tolist()

        # Get school names
        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request"),
            {"Request": "SchoolDropdown"}
        )
        schools = pd.DataFrame(result.fetchall(), columns=result.keys())
        school_names = schools['School'].dropna().tolist()

        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request"),
            {"Request": "CompetencyDropdown"}
        )
        Competency = pd.DataFrame(result.fetchall(), columns=result.keys())
        Competency = Competency['Competency'].dropna().tolist()


    return render_template("index.html", providers=provider_names,  schools=school_names, competencies = Competency)

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
                    "Scenario One - Selected": "",
                    "Scenario Two - Selected": ""
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
                            "Scenario One - Selected": scenario_query.iloc[0].get("Scenario1", ""),
                            "Scenario Two - Selected": scenario_query.iloc[0].get("Scenario2", "")
                        }
                    else:
                        scenario_data = {
                            "Scenario One - Selected": "",
                            "Scenario Two - Selected": ""
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
        for col in ["Scenario One - Selected", "Scenario Two - Selected"]:
            if col in df_valid.columns:
                df_valid[col] = df_valid[col].replace(0, '')

        # Remove scenario columns temporarily
        s1 = cols.pop(cols.index("Scenario One - Selected"))
        s2 = cols.pop(cols.index("Scenario Two - Selected"))

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



@app.route('/download_excel')
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
        provider_name = request.form['provider']
        
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


@app.route('/download_pdf')
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

# Run app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
