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
from flask import Flask, request, jsonify, render_template_string, send_file # Web framework & templating
import pyodbc         # For ODBC database connection to Azure SQL Server
import os             # For reading environment variables
from dotenv import load_dotenv  # Load .env file for credentials
import pandas as pd   # For reading CSVs and processing tabular data
from werkzeug.utils import secure_filename  # Safe handling of uploaded filenames
import threading      # Allows background processing (non-blocking upload handling)
import io 

processing_status = {
    "current": 0,
    "total": 0,
    "done": False
}

school_name = None
moe_number = None
teacher_name = None
class_name = None

load_dotenv()

app = Flask(__name__)


# Secure database connection (variables stored in .env and render setup)
def get_db_connection():
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=tcp:heimatau.database.windows.net,1433;"
        "Database=WSFL;"
        f"Uid={os.getenv('WSNZDBUSER')};"
        f"Pwd={os.getenv('WSNZDBPASS')};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)

# Render home page
# - CSV file uploader
# - Year and Term selectors
# - Provider dropdown populated from the database
# - JS validation and progress bar logic included 
@app.route('/')
def home():
    conn = get_db_connection()

    providers = pd.read_sql("EXEC FlaskHelperFunctions ?", conn, params=['ProviderDropdown'])
    provider_names = providers['Description'].dropna().tolist()
    schools = pd  .read_sql("EXEC FlaskHelperFunctions ?", conn, params=['SchoolDropdown'])
    school_names = schools['School'].dropna().tolist()
    conn.close()

    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Student Tools</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head>
        <body class="bg-light">
            <div class="container py-5">
                <h1 class="mb-4 text-center">Student Tools</h1>

                <div class="card">
                    <div class="card-header">📤 Upload CSV</div>
                    <div class="card-body">
                       <form action="/upload" method="post" enctype="multipart/form-data" class="row g-3 align-items-end" onsubmit="return checkFileSelected()">
                            <div class="col-md-auto">
                                <label for="csv_file_input" class="form-label">CSV File</label>
                                <input type="file" name="csv_file" class="form-control" accept=".csv" id="csv_file_input" required>
                            </div>
                            <div class="col-md-auto">
                                <label for="year_input" class="form-label">Year</label>
                                <select name="year" class="form-select" id="year_input" required>
                                    {% for y in range(2023, 2026) %}
                                        <option value="{{ y }}" {% if y == 2025 %}selected{% endif %}>{{ y }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="col-md-auto">
                                <label for="term_input" class="form-label">Term</label>
                                <select name="term" class="form-select" id="term_input" required>
                                    <option value="1" selected>Term 1</option>
                                    <option value="2">Term 2</option>
                                    <option value="3">Term 3</option>
                                    <option value="4">Term 4</option>
                                </select>
                            </div>
                            <div class="col-md-auto">
                                <label for="provider" class="form-label">Provider:</label required>
                                <select id="provider" class="form-select" name="provider" onchange="loadSchools()">
                                <option disabled selected value></option>

                                {% for name in providers %}
                                    <option value="{{ name }}">{{ name }}</option>
                                {% endfor %}
                                </select>
                            </div>
                             <div class="col-md-auto">
                                <label for="teacher_name" class="form-label">Teacher Name</label>
                                <input type="text" class="form-control" id="teacher_name" name="teacher_name" placeholder="Enter teacher name">
                            </div>

                            <div class="col-md-auto">
                                <label for="class_name" class="form-label">Class Name</label>
                                <input type="text" class="form-control" id="class_name" name="class_name" placeholder="Enter class name">
                            </div>     
                            <div class="col-md-auto">
                                
                                <label for="school" class="form-label">School:</label>
                                <select id="school" class="form-select" name="school" required>
                                <option value="">Select a school</option>
                                </select>

                            </div>
                            <div class="col-md-auto">
                                <button type="submit" class="btn btn-success">Upload</button>
                            </div>
                        </form>

                        <script>
                                  
                            function loadSchools() {
                                const provider = document.getElementById("provider").value;
                                const schoolDropdown = document.getElementById("school");

                                // Clear old schools
                                schoolDropdown.innerHTML = "";

                                if (!provider) return;

                                // Fetch new schools
                                fetch(`/get_schools?provider=${encodeURIComponent(provider)}`)
                                    .then(response => response.json())
                                    .then(data => {
                                    if (data.length > 0) {
                                        data.forEach(function(school) {
                                        const option = document.createElement("option");
                                        option.value = school;
                                        option.text = school;
                                        schoolDropdown.appendChild(option);
                                        });
                                    } else {
                                        const option = document.createElement("option");
                                        option.text = "No schools found";
                                        schoolDropdown.appendChild(option);
                                    }
                                    });
}
                            function checkFileSelected() {
                                const fileInput = document.getElementById("csv_file_input");
                                if (!fileInput.value) {
                                    alert("Please select a CSV file to upload.");
                                    return false;
                                }
                                return true;
                            }
                        </script>
                    </div>
                    <div class="progress w-100 mt-3" id="uploadProgress" style="display:none;">
                        <div id="progressBar" class="progress-bar" role="progressbar" 
                            style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">
                            0%
                    </div>
                    </div>
                </div>
            </div>
        </body>
                                  <script>
  function checkFileSelected() {
    const fileInput = document.getElementById("csv_file_input");
    if (!fileInput.value) {
      alert("Please select a CSV file to upload.");
      return false;
    }

    // Show progress bar
    document.getElementById("uploadProgress").style.display = "block";

    // Start polling progress
    const interval = setInterval(() => {
      fetch("/progress")
        .then(response => response.json())
        .then(data => {
          const percent = Math.floor((data.current / data.total) * 100);
          const bar = document.getElementById("progressBar");
          bar.style.width = percent + "%";
          bar.innerText = percent + "%";
          bar.setAttribute("aria-valuenow", percent);
          if (data.done) {
            clearInterval(interval);
          }
        });
    }, 500); // poll every 0.5 seconds

    return true;
  }
</script>

        </html>
    ''', providers=provider_names,  schools=school_names)

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
    conn = get_db_connection()
    cursor = conn.cursor()
    processing_status["current"] = 0
    processing_status["total"] = len(df)
    processing_status["done"] = False
    errors = []
    valid_data = []

    # Get all relevant competencies to build consistent column structure
    competencies = pd.read_sql("EXEC GetRelevantCompetencies ?, ?", conn, params=[calendaryear, term])
    label_map = (
        competencies.assign(
            label=lambda d: d['CompetencyDesc'].astype(str) + "<br> ("+ d['YearGroupDesc'].astype(str) + ")",
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
            cursor.execute(
                "EXEC CheckNSNMatch ?, ?, ?, ?, ?, ?,?,?",
                row['NSN'] or None,
                row['FirstName'] or None,
                row.get('PreferredName') or None,
                row['LastName'] or None,
                row['BirthDate'] if pd.notna(row['BirthDate']) else None,
                row.get('Ethnicity') or None, 
                calendaryear,
                term
            )


            columns = [desc[0] for desc in cursor.description]
            result = cursor.fetchall()  # This clears the cursor state

            if not result:
                errors.append({"NSN": row.get('NSN', None), "Error": "No result returned"})
                continue

            result_row = dict(zip(columns, result[0]))


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
                 with get_db_connection() as conn2:

                    # Fetch scenario selections
                    scenario_query = pd.read_sql(
                        "EXEC FlaskHelperFunctions ?,?", 
                        conn2, 
                        params=['StudentScenario',result_row['NSN']]
                    )

                    # Initialize empty scenario fields
                    scenario_data = {"Scenario One - Selected": "", "Scenario Two - Selected": ""}

                    if not scenario_query.empty:
                        for _, srow in scenario_query.iterrows():
                            if srow['ScenarioIndex'] == 1:
                                scenario_data["Scenario One - Selected"] = srow['ScenarioID']
                            elif srow['ScenarioIndex'] == 2:
                                scenario_data["Scenario Two - Selected"] = srow['ScenarioID']

                    comp = pd.read_sql("EXEC GetStudentCompetencyStatus ?, ?, ?", conn2, params=[result_row['NSN'], term, calendaryear])
                    comp = comp.merge(label_map, on=['CompetencyID', 'YearGroupID'], how='inner')
                    comp_row = comp.set_index('label')['CompetencyStatusID'].reindex(labels).fillna(0).astype(int).to_dict()
                    comp_row = {k: ('Y' if v == 1 else '') for k, v in comp_row.items()}


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

    conn.close()
   
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
        if row['YearLevel'] is not None:
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
        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
                <title>Processing CSV...</title>
            </head>
            <body class="bg-light">
                <div class="container py-5">
                    <h2>⏳ Processing your file...</h2>
                    <div class="progress w-100 mt-3" id="uploadProgress">
                        <div id="progressBar" class="progress-bar" role="progressbar" 
                            style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">0%</div>
                    </div>
                    <script>
                        const interval = setInterval(() => {
                            fetch("/progress")
                              .then(response => response.json())
                              .then(data => {
                                const percent = Math.floor((data.current / data.total) * 100);
                                const bar = document.getElementById("progressBar");
                                bar.style.width = percent + "%";
                                bar.innerText = percent + "%";
                                bar.setAttribute("aria-valuenow", percent);
                                if (data.done) {
                                  clearInterval(interval);
                                  window.location.href = "/results";  // redirect to results
                                }
                              });
                        }, 500);
                    </script>
                </div>
            </body>
            </html>
        ''')
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

    return render_template_string(f'''
        <!DOCTYPE html>
        <html>
        <head>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
            <title>Upload Results</title>
        </head>
        <body class="bg-light">
            <div class="container py-5">
                <h2 class="mb-4">✅ Valid Records</h2>
                {valid_html}

                <h2 class="mt-5 text-danger">⚠️ Errors</h2>
                {error_html}

                <a class="btn btn-secondary mt-4" href="/">← Back</a>
                <a class="btn btn-primary mt-4" href="/download_excel">⬇️ Download Excel</a>

            </div>
        </body>
        </html>
    ''')



@app.route('/get_schools')
def get_schools():
    provider = request.args.get('provider')  

    if not provider:
        return jsonify([])

    conn = get_db_connection()

    query = """
    EXEC [FlaskHelperFunctions] @Request = ?, @Text = ?
    """
    schools = pd.read_sql(query, conn, params=['FilterSchool',provider])
    conn.close()

    # Convert to a simple list
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
            worksheet.insert_image('A1', 'DarkLogo.png', {
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
            worksheet_errors.set_column(0,5,15)
            worksheet_errors.set_column(6,6,50)


    output.seek(0)
    return send_file(
        output,
        download_name = school_name + " Term " + str(term) + " " + str(calendaryear) + ".xlsx"
,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# Run app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)


