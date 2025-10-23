from sqlalchemy import create_engine, text
from collections import defaultdict
import os
from dotenv import load_dotenv

load_dotenv(override=True)

DB_URL = os.getenv("DB_URL_CUSTOM")
engine = create_engine(DB_URL)

SURVEY_ID = 5
output_path = f"app/templates/survey_form_{SURVEY_ID}.html"

# 1) Load survey meta INCLUDING RouteName
with engine.connect() as conn:
    result = conn.execute(
        text("SELECT Title, Header, Footer, RouteName FROM SVY_Survey WHERE SurveyID = :id"),
        {"id": SURVEY_ID}
    ).mappings().first()

survey_title = (result["Title"] or "Survey")
survey_header = (result["Header"] or "")
survey_footer = (result["Footer"] or "")
survey_route  = (result["RouteName"] or "").strip() or "ExternalReview"

# 2) Fetch sections/questions/labels
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

sections = defaultdict(lambda: {"text": "", "questions": defaultdict(list)})
with engine.connect() as conn:
    rows = conn.execute(text(query), {"id": SURVEY_ID}).fetchall()

for secname, secorder, sectext, qid, qtext, qcode, pos, label in rows:
    key = (secorder or 0, secname or "No Section")
    sections[key]["text"] = sectext or ""
    if qcode in ['SHT', 'LNG']:
        sections[key]["questions"][(qid, qtext, qcode)] = []
    else:
        sections[key]["questions"][(qid, qtext, qcode)].append((pos, label))

# 3) Build Jinja2 HTML
html = [
    '{% extends "header.html" %}',
    '{% block title %}' + survey_title + '{% endblock %}',
    '{% block content %}',
    '<div class="container mt-5">',
    f'  <h1 class="mb-4 text-center text-primary">{survey_title}</h1>',
    # IMPORTANT: let Jinja compute the URL using the DB route
    # Use a normal Python string (not f-string) so braces don't need doubling
    '  <form action="{{ url_for(\'survey_bp.submit_survey\', routename=\'' + survey_route + '\') }}" method="post">'
]
if survey_header.strip():
    html.append(survey_header)

# Render sections/questions
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
            for pos, lbl in labels:
                html.append(
                    f'<div class="form-check">'
                    f'<input class="form-check-input" type="radio" name="q{qid}" value="{pos}">'
                    f'<label class="form-check-label">{lbl}</label>'
                    f'</div>'
                )
        elif qcode == 'T/F':
            for pos, lbl in [(1, 'Yes'), (2, 'No')]:
                html.append(
                    f'<div class="form-check">'
                    f'<input class="form-check-input" type="radio" name="q{qid}" value="{pos}">'
                    f'<label class="form-check-label">{lbl}</label>'
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

# Submit + footer
html.append('<button type="submit" class="btn btn-primary">Submit Survey</button>')
if survey_footer.strip():
    html.append(survey_footer)
html.append('</form></div>{% endblock %}')

# 4) Write template
with open(output_path, "w", encoding="utf-8") as f:
    f.write('\n'.join(html))

print(f"✅ Jinja2 form written to {output_path} (route: {survey_route})")
