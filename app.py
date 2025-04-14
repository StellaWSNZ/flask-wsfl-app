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
                        <form action="/upload" method="post" enctype="multipart/form-data" class="row g-3">
                            <div class="col-auto">
                                <input type="file" name="csv_file" class="form-control" accept=".csv">
                            </div>
                            <div class="col-auto">
                                <button type="submit" class="btn btn-success">Upload</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </body>
        </html>
    ''')

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

# 📤 Upload CSV
@app.route('/upload', methods=['POST'])
def upload():
    try:
        file = request.files.get("csv_file")
        if not file:
            return "No file uploaded", 400

        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".csv"):
            return "Only CSV files are supported", 400

        df = pd.read_csv(file)
        html_table = df.to_html(classes="table table-striped", index=False)

        return render_template_string(f'''
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <title>CSV Preview</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
            </head>
            <body class="bg-light">
                <div class="container py-5">
                    <h2 class="mb-4">CSV Upload Preview</h2>
                    {html_table}
                    <a class="btn btn-secondary mt-3" href="/">← Back</a>
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
