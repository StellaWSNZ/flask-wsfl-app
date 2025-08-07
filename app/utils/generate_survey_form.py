from sqlalchemy import create_engine, text
from collections import defaultdict
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# DB connection string
DB_URL = os.getenv("DB_URL_CUSTOM")
engine = create_engine(DB_URL)

# Output Jinja2 HTML template path
SURVEY_ID = 3
output_path = f"app/templates/survey_form_{SURVEY_ID}.html"
with engine.connect() as conn:
    result = conn.execute(
        text("SELECT Title, Header, Footer FROM SVY_Survey WHERE SurveyID = :id"),
        {"id": SURVEY_ID}
    ).mappings().first()

survey_title = result["Title"] or "Survey"
survey_header = result["Header"] or ""
survey_footer = result["Footer"] or ""


# Step 2: Fetch sections, questions + labels
query = """
SELECT 
    s.SectionName,
    s.SectionOrder,
    s.SectionText,
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

# Group: section → {text + question → labels}
sections = defaultdict(lambda: {"text": "", "questions": defaultdict(list)})

with engine.connect() as conn:
    rows = conn.execute(text(query), {"id": SURVEY_ID}).fetchall()

for secname, secorder, sectext, qid, qtext, qcode, pos, label in rows:
    key = (secorder or 0, secname or "No Section")
    sections[key]["text"] = sectext or ""

    if qcode in ['SHT', 'LNG']:
        sections[key]["questions"][(qid, qtext, qcode)] = []  # No labels needed
    else:
        sections[key]["questions"][(qid, qtext, qcode)].append((pos, label))

# Step 3: Build Jinja2 HTML
html = [
    '{% extends "header.html" %}',
    '{% block title %}' + survey_title + '{% endblock %}',
    '{% block content %}',
    '<div class="container mt-5">',
    f'  <h1 class="mb-4 text-center text-primary">{ survey_title }</h1>',
    '<form action="/submit" method="post">'
]
if survey_header.strip():
    html.append(survey_header)

# Render all sections and questions
for (secorder, secname), secdata in sorted(sections.items()):
    if secname != "No Section":
        html.append('<div class="p-3 mb-3 bg-white rounded">')
        html.append(f'<h3 class="mt-2">{secname}</h3>')
        if secdata["text"].strip():
            sectext_html = secdata["text"].replace("\r\n", "<br>").replace("\n", "<br>")
            html.append(f'<p class="text-muted">{sectext_html}</p>')
        html.append('</div>')

    for (qid, qtext, qcode), labels in secdata["questions"].items():
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

# Final submit button
html.append('<button type="submit" class="btn btn-primary">Submit Survey</button>')
html.append('</form></div>{% endblock %}')

# Step 4: Write to file
with open(output_path, "w", encoding="utf-8") as f:
    f.write('\n'.join(html))

print(f"✅ Jinja2 form written to {output_path} with title '{survey_title}'")
