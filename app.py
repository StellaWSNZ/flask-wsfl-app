from flask import Flask, request, jsonify, render_template_string
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# 🧩 Database connection
def get_db_connection():
    user = os.getenv("WSNZDBUSER")
    password = os.getenv("WSNZDBPASS")
    server = "heimatau.database.windows.net"
    database = "WSFL"
    connection_url = f"mssql+pytds://{user}:{password}@{server}/{database}"
    engine = create_engine(connection_url)
    conn = engine.raw_connection()
    return conn

# 🌐 Home page with search form
@app.route('/')
def home():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>WSFL Student Lookup</title>
        <style>
            body { font-family: sans-serif; padding: 2rem; max-width: 600px; }
            input[type="text"] { padding: 0.5rem; font-size: 1rem; width: 200px; }
            button { padding: 0.5rem 1rem; font-size: 1rem; }
            pre { background: #f0f0f0; padding: 1rem; margin-top: 1rem; white-space: pre-wrap; }
        </style>
    </head>
    <body>
        <h1>Search for a Student by NSN</h1>
        <input type="text" id="nsnInput" placeholder="Enter NSN" />
        <button onclick="searchNSN()">Search</button>
        <pre id="result">Results will appear here...</pre>

        <script>
        function searchNSN() {
            const nsn = document.getElementById("nsnInput").value;
            if (!nsn) return;

            fetch(`/student?nsn=${nsn}`)
                .then(async res => {
                    const contentType = res.headers.get("content-type");
                    if (!res.ok) {
                        const text = await res.text();
                        throw new Error(`Server Error (${res.status}): ${text}`);
                    }
                    if (!contentType || !contentType.includes("application/json")) {
                        const text = await res.text();
                        throw new Error("Expected JSON, got:\n\n" + text);
                    }
                    return res.json();
                })
                .then(data => {
                    if (data.length === 0) {
                        document.getElementById("result").textContent = "No student found.";
                    } else {
                        document.getElementById("result").textContent = JSON.stringify(data, null, 2);
                    }
                })
                .catch(err => {
                    document.getElementById("result").textContent = "Error: " + err.message;
                });
        }
        </script>
    </body>
    </html>
    """)

# 🔍 Student data lookup route
@app.route('/student')
def get_student():
    nsn = request.args.get('nsn')
    if not nsn:
        return jsonify({'error': 'Missing NSN parameter'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM Student WHERE NSN = %s", (nsn,))
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        conn.close()

        result = [dict(zip(columns, row)) for row in rows]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# For local testing
if __name__ == '__main__':
    app.run(debug=True)
