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

# 🏠 Home page with both NSN search and file upload
@app.route('/')
def home():
    return render_template_string('''
        <h2>Student Lookup</h2>
        <form action="/student">
            <input name="nsn" placeholder="Enter NSN">
            <button type="submit">Search</button>
        </form>

        <h2>Upload CSV</h2>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="csv_file" accept=".csv">
            <button type="submit">Upload</button>
        </form>
    ''')

# 🔍 Look up student by NSN
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

# 📤 Upload CSV and render as HTML table
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
        html_table = df.to_html(classes="data", index=False)

        return render_template_string(f'''
            <h2>CSV Upload Preview</h2>
            {html_table}
            <br><a href="/">← Back</a>
            <style>
              .data {{
                border-collapse: collapse;
                width: 100%;
              }}
              .data th, .data td {{
                border: 1px solid #ccc;
                padding: 8px;
              }}
              .data th {{
                background-color: #f0f0f0;
              }}
            </style>
        ''')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Upload failed: {e}", 500

# 🏃 Run app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
