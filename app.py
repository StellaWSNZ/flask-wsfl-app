from flask import Flask, request, jsonify, render_template_string
import pyodbc
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# 🔌 Connect to Azure SQL with encryption
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

# 🧪 Test UI
@app.route('/')
def home():
    return render_template_string('''
    <html><body>
    <h2>Student Lookup</h2>
    <form action="/student">
        <input name="nsn" placeholder="Enter NSN">
        <button type="submit">Search</button>
    </form>
    </body></html>
    ''')

# 🔍 Query by NSN
@app.route('/student')
def get_student():
    nsn = request.args.get('nsn')
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
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
