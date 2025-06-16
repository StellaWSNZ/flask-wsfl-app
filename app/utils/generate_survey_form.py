from sqlalchemy import create_engine, text
from collections import defaultdict
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# DB connection string
DB_URL = os.getenv("DB_URL_CUSTOM")
engine = create_engine(DB_URL)

# Output Jinja2 HTML template path
SURVEY_ID = 1
output_path = f"app/templates/survey_form_{SURVEY_ID}.html"

# Step 1: Get survey title
with engine.connect() as conn:
    survey_title = conn.execute(
        text("SELECT Title FROM SVY_Survey WHERE SurveyID = :id"), {"id": SURVEY_ID}
    ).scalar() or "Survey"

# Step 2: Fetch questions + labels
query = """
SELECT 
    s.SectionName,
    s.SectionOrder,
    q.QuestionID,
    q.QuestionText,
    q.QuestionCode,
    l.Position,
    l.LabelText
FROM SVY_Question q
LEFT JOIN SVY_LikertLabel l ON q.LikertScaleID = l.LikertScaleID
LEFT JOIN SVY_Section s ON q.SectionID = s.SectionID
WHERE q.QuestionCode IN ('LIK', 'T/F', 'SHT', 'LNG') AND q.SurveyID = :id
ORDER BY s.SectionOrder, q.QuestionID, l.Position
"""

# Group: section → { question → list of labels }
sections = defaultdict(lambda: defaultdict(list))

with engine.connect() as conn:
    rows = conn.execute(text(query), {"id": SURVEY_ID}).fetchall()

for secname, secorder, qid, qtext, qcode, pos, label in rows:
    key = (secorder or 0, secname or "No Section")
    if qcode in ['SHT', 'LNG']:
        sections[key][(qid, qtext, qcode)] = []  # no labels
    else:
        sections[key][(qid, qtext, qcode)].append((pos, label))

# Step 3: Build Jinja2 HTML
html = [
    '{% extends "header.html" %}',
    '{% block title %}' + survey_title + '{% endblock %}',
    '{% block content %}',
    '<div class="container mt-5">',
    f'  <h1 class="mb-4 text-center text-primary">{ survey_title }</h2>',
    '<form action="/submit" method="post">'
]

# Render all sections and questions
for (secorder, secname) in sorted(sections):
    if secname != "No Section":
        html.append(f'<h3 class="mt-4">{secname}</h3>')
    for (qid, qtext, qcode), labels in sections[(secorder, secname)].items():
        html.append('<fieldset class="mb-4 p-4 border rounded bg-white shadow-sm">')
        html.append(f'<legend class="h6"><strong>Q{qid}:</strong> {qtext}</legend>')

        if qcode == 'LIK':
            for pos, label in labels:
                html.append(
                    f'<div class="form-check">'
                    f'<input class="form-check-input" type="radio" name="q{qid}" value="{pos}">'
                    f'<label class="form-check-label">{label}</label>'
                    f'</div>'
                )
        elif qcode == 'T/F':
            for pos, label in [(1, 'Yes'), (2, 'No')]:
                html.append(
                    f'<div class="form-check">'
                    f'<input class="form-check-input" type="radio" name="q{qid}" value="{pos}">'
                    f'<label class="form-check-label">{label}</label>'
                    f'</div>'
                )
        elif qcode == 'SHT':
            html.append(
                f'<input type="text" name="q{qid}" class="form-control" placeholder="Your response...">'
            )
        elif qcode == 'LNG':
            html.append(
                f'<textarea name="q{qid}" class="form-control" rows="4" placeholder="Your response..."></textarea>'
            )

        html.append('</fieldset>')

html.append('<button type="submit" class="btn btn-primary">Submit Survey</button>')
html.append('</form></div>{% endblock %}')

# Step 4: Write to file
with open(output_path, "w", encoding="utf-8") as f:
    f.write('\n'.join(html))

print(f"✅ Jinja2 form written to {output_path} with title '{survey_title}'")
