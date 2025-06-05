from sqlalchemy import create_engine, text
from collections import defaultdict

# DB connection string
DB_URL = "mssql+pyodbc://Stella:Wai_Ora2002@heimatau.database.windows.net:1433/WSFL?driver=ODBC+Driver+18+for+SQL+Server"
engine = create_engine(DB_URL)

# Output HTML path
output_path = "survey_form.html"

# Fetch Likert questions + labels
query = """
SELECT 
    q.QuestionID,
    q.QuestionText,
    l.Position,
    l.LabelText
FROM SVY_Question q
LEFT JOIN SVY_LikertLabel l ON q.LikertScaleID = l.LikertScaleID
WHERE q.QuestionCode = 'LIK'
ORDER BY q.QuestionID, l.Position
"""

# Build question: [(pos, label)] mapping
questions = defaultdict(list)

with engine.connect() as conn:
    rows = conn.execute(text(query)).fetchall()

for qid, qtext, pos, label in rows:
    questions[(qid, qtext)].append((pos, label))

# Start HTML form
html = [
    '<!DOCTYPE html>',
    '<html><head><meta charset="UTF-8"><title>WSFL Self Review</title></head><body>',
    '<h1>WSFL Self Review Survey</h1>',
    '<form action="/submit" method="post">'
]

# Add each question
for (qid, qtext), labels in questions.items():
    html.append(f'<fieldset>')
    html.append(f'<legend><strong>Q{qid}:</strong> {qtext}</legend>')
    for pos, label in labels:
        html.append(f'<label><input type="radio" name="q{qid}" value="{pos}"> {label}</label><br>')
    html.append('</fieldset><br>')

# Submit button and close form
html.append('<input type="submit" value="Submit Survey">')
html.append('</form></body></html>')

# Write to file
with open(output_path, "w", encoding="utf-8") as f:
    f.write('\n'.join(html))

print(f"✅ Form written to {output_path}")
