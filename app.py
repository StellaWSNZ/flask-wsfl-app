from flask import Flask, request, jsonify, render_template_string
import pyodbc
import os
from dotenv import load_dotenv
import pandas as pd
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)

# 🔌 Connect using ODBC
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

# 🏠 Home page with NSN search and file upload
@app.route('/')
def home():
    conn = get_db_connection()
    providers = pd.read_sql("SELECT DISTINCT Description from Provider", conn)
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

                <div class="card mb-4">
                    <div class="card-header">🔍 Student Lookup</div>
                    <div class="card-body">
                        <form action="/student" class="row g-3">
                            <div class="col-auto">
                                <input name="nsn" class="form-control" placeholder="Enter NSN">
                            </div>
                            <div class="col-auto">
                                <button type="submit" class="btn btn-primary">Search</button>
                            </div>
                        </form>
                    </div>
                </div>

                <div class="card">
                    <div class="card-header">📤 Upload CSV</div>
                    <div class="card-body">
                       <form action="/upload" method="post" enctype="multipart/form-data" class="row g-3 align-items-end" onsubmit="return checkFileSelected()">
                            <div class="col-md-auto">
                                <label for="csv_file_input" class="form-label">CSV File</label>
                                <input type="file" name="csv_file" class="form-control" accept=".csv" id="csv_file_input">
                            </div>
                            <div class="col-md-auto">
                                <label for="year_input" class="form-label">Year</label>
                                <select name="year" class="form-select" id="year_input">
                                    {% for y in range(2023, 2026) %}
                                        <option value="{{ y }}" {% if y == 2025 %}selected{% endif %}>{{ y }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="col-md-auto">
                                <label for="term_input" class="form-label">Term</label>
                                <select name="term" class="form-select" id="term_input">
                                    <option value="1" selected>Term 1</option>
                                    <option value="2">Term 2</option>
                                    <option value="3">Term 3</option>
                                    <option value="4">Term 4</option>
                                </select>
                            </div>
                            <div class="col-md-auto">
                                 <label for="provider">Search Provider:</label>
                                <input list="provider-list" id="provider" name="provider" class="form-control">
                                <datalist id="provider-list">
                                    {% for name in providers %}
                                        <option value="{{ name }}">
                                    {% endfor %}
                                </datalist>
                            </div>
                            <div class="col-md-auto">
                                <button type="submit" class="btn btn-success">Upload</button>
                            </div>
                        </form>

                        <script>
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
                </div>
            </div>
        </body>
        </html>
    ''', providers=providers)

# 🔍 Student lookup
@app.route('/student')
def get_student():
    nsn = request.args.get("nsn")
    if not nsn:
        return jsonify({"error": "Missing NSN"}), 400
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM Student WHERE NSN = ?", (nsn,))
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        return jsonify([dict(zip(columns, row)) for row in rows])
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def process_uploaded_csv(df, term, calendaryear):
    conn = get_db_connection()
    cursor = conn.cursor()

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
        try:
            # Fully fetch the result BEFORE doing another SQL call
            cursor.execute(
                "EXEC CheckNSNMatch ?, ?, ?, ?, ?, ?,?,?",
                row['NSN'] or None,
                row['FirstName'] or None,
                row.get('PreferredName') or None,
                row['LastName'] or None,
                row['BirthDate'] if pd.notna(row['BirthDate']) else None,
                row.get('Ethnicity') or None,  # if this is a float column, use float(row.get('Ethnicity', 0)) or None
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
                    **{label: 0 for label in labels}
                })
            else:
                # Now it's safe to query again
                 with get_db_connection() as conn2:
                    comp = pd.read_sql("EXEC GetStudentCompetencyStatus ?, ?, ?", conn2, params=[result_row['NSN'], term, calendaryear])
                    comp = comp.merge(label_map, on=['CompetencyID', 'YearGroupID'], how='inner')
                    comp_row = comp.set_index('label')['CompetencyStatusID'].reindex(labels).fillna(0).astype(int).to_dict()

                    # Add personal info fields up front
                    full_row = {
                        'NSN': result_row.get('NSN'),
                        'FirstName': result_row.get('FirstName'),
                        'LastName': result_row.get('LastName'),
                        'PreferredName': result_row.get('PreferredName'),
                        'BirthDate': result_row.get('BirthDate'),
                        'Ethnicity': result_row.get('Ethnicity'),
                        'YearLevel': result_row.get('YearLevel'),
                        **comp_row  # Add all competency columns
                    }

                    valid_data.append(full_row)


        except Exception as e:
            errors.append({"NSN": row.get('NSN', None), "Error": str(e)})

    conn.close()
   
    df_valid = pd.DataFrame(valid_data)
    if 'YearLevel' in df_valid.columns:
        df_valid['YearLevel'] = df_valid['YearLevel'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    df_errors = pd.DataFrame(errors)

    return df_valid, df_errors

    conn = get_db_connection()

    # Fetch all StudentCompetency records for NSNs
    query_comp = "SELECT * FROM StudentCompetency WHERE NSN IN ({})".format(
        ",".join(["?"] * len(nsn_list))
    )
    df = pd.read_sql(query_comp, conn, params=nsn_list)

    # Fetch relevant competencies
    relevant = pd.read_sql(
        "EXEC GetRelevantCompetencies ?, ?", conn, params=[calendaryear, term]
    )

    # Merge to filter only relevant combinations
    merged = df.merge(
        relevant[['CompetencyID', 'YearGroupID', 'CompetencyDesc', 'YearGroupDesc']],
        on=['CompetencyID', 'YearGroupID'],
        how='right'
    )

    # Add missing NSNs with outer merge
    nsn_df = pd.DataFrame({'NSN': nsn_list})
    nsn_df['NSN'] = pd.to_numeric(nsn_df['NSN'], errors='coerce')

    if 'NSN' in merged.columns:
        merged['NSN'] = pd.to_numeric(merged['NSN'], errors='coerce')

    merged = pd.merge(nsn_df, merged, on='NSN', how='left')
    merged = merged[merged['NSN'].notna()]

    # Create label column for pivot
    merged['label'] = merged['CompetencyDesc'].astype(str) + " (" + merged['YearGroupDesc'].astype(str) + ")"

    # Create column sort key
    merged['col_order'] = merged['YearGroupID'].astype(str).str.zfill(2) + "-" + merged['CompetencyID'].astype(str).str.zfill(4)

    label_order = merged[['label', 'col_order']].drop_duplicates().sort_values('col_order')['label'].tolist()

    # Pivot to wide format
    wide = merged.pivot(index="NSN", columns="label", values="CompetencyStatusID").fillna(0).astype(int)

    # Reorder columns
    wide = wide[label_order]

    conn.close()
    return wide.reset_index()
    



@app.route('/upload', methods=['POST'])
def upload():
    term = int(request.form.get("term", 1))
    calendaryear = int(request.form.get("year", 2025))

    try:
        file = request.files.get("csv_file")
        if not file:
            return "No file uploaded", 400
        term = int(request.form.get("term", 1))
        calendaryear = int(request.form.get("year", 2025))  
        df = pd.read_csv(file)
        df['BirthDate'] = pd.to_datetime(df['BirthDate'], errors='coerce', dayfirst=True).dt.date

        if "NSN" not in df.columns:
            return "CSV must contain 'NSN' column", 400

        term = 1
        calendaryear = 2025

        df_valid, df_errors = process_uploaded_csv(df, term, calendaryear)

        valid_html = (
            df_valid.to_html(classes="table table-bordered table-sm", index=False, escape =False)
            if not df_valid.empty else "<p class='text-muted'>No valid records found.</p>"
        )

        error_html = (
            df_errors.to_html(classes="table table-bordered table-sm text-danger", index=False)
            if not df_errors.empty else "<p class='text-success'>No errors found.</p>"
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
                </div>
            </body>
            </html>
        ''')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Upload failed: {e}", 500


# 🏃 Run app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)


