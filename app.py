from flask import Flask, request, jsonify, render_template_string
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv
import traceback

load_dotenv()

app = Flask(__name__)

# Connect to Azure SQL using SQLAlchemy + pytds
def get_db_connection():
    user = os.getenv("WSNZDBUSER")
    password = os.getenv("WSNZDBPASS")
    server = "heimatau.database.windows.net"
    database = "WSFL"
    engine = create_engine(f"mssql+pytds://{user}:{password}@{server}/{database}")
    return engine.raw_connection()

# Basic form and result box
@app.route('/')
def home():
    return render_template_string('''
<html>
  <body>
    <h2>Student Lookup</h2>
    <input id="nsn" placeholder="Enter NSN">
    <button onclick="lookup()">Search</button>
    <pre id="out"></pre>

    <script>
    function lookup() {
      const nsn = document.getElementById("nsn").value;
      fetch("/student?nsn=" + nsn)
        .then(r => r.json())
        .then(data => {
          document.getElementById("out").textContent = JSON.stringify(data, null, 2);
        })
        .catch(e => {
          document.getElementById("out").textContent = "Error: " + e;
        });
    }
    </script>
  </body>
</html>
''')

# API route
@app.route('/student')
def get_student():
    nsn = request.args.get('nsn')
    if not nsn:
        return jsonify({'error': 'Missing NSN'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM Student WHERE NSN = %s", (nsn,))
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        return jsonify([dict(zip(columns, row)) for row in rows])
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
