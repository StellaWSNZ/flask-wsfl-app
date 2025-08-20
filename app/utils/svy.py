from sqlalchemy import create_engine, text
from collections import defaultdict
import json

# =========================
# DB connection
# =========================
DB_URL = "mssql+pyodbc://Stella:Wai_Ora2002@heimatau.database.windows.net:1433/WSFL?driver=ODBC+Driver+18+for+SQL+Server"
engine = create_engine(DB_URL)

# =========================
# Output
# =========================
output_path = "survey_form.html"

# =========================
# Context (set from session in real app)
# =========================
SURVEY_ID     = 3
FUNDER_ID     = 6      # <- your logged-in funder
CALENDARYEAR  = 2025
TERM          = 2

# =========================
# Queries
# =========================
SECTIONS_Q = """
SELECT SectionID, SectionName, SectionOrder, SectionText
FROM SVY_Section
WHERE SurveyID = :sid
ORDER BY SectionOrder;
"""

# Likert questions + labels (ordered by Section + DisplayOrder if present)
LIKERT_Q = """
SELECT q.QuestionID, q.QuestionText, q.SectionID, q.DisplayOrder,
       l.Position, l.LabelText
FROM SVY_Question q
LEFT JOIN SVY_LikertLabel l ON q.LikertScaleID = l.LikertScaleID
WHERE q.SurveyID = :sid AND q.QuestionCode = 'LIK'
ORDER BY q.SectionID,
         COALESCE(q.DisplayOrder, q.QuestionID),
         l.Position;
"""

# Dropdown (static options, if any)
DDL_STATIC_Q = """
SELECT q.QuestionID, q.QuestionText, q.SectionID, q.DisplayOrder,
       o.OptionValue, o.OptionLabel, o.SortOrder
FROM SVY_Question q
JOIN SVY_DropdownOption o
  ON q.QuestionID = o.QuestionID AND q.SurveyID = o.SurveyID
WHERE q.SurveyID = :sid AND q.QuestionCode = 'DDL'
ORDER BY q.SectionID,
         COALESCE(q.DisplayOrder, q.QuestionID),
         o.SortOrder;
"""

# Dropdown (custom config) — tells us where to get dynamic data
DDL_CONFIG_Q = """
SELECT qc.QuestionID, qc.ConfigKey, qc.ConfigValue, q.SectionID, q.QuestionText, q.DisplayOrder
FROM SVY_QuestionConfig qc
JOIN SVY_Question q
  ON q.QuestionID = qc.QuestionID AND q.SurveyID = :sid
WHERE q.SurveyID = :sid
  AND q.QuestionCode = 'DDL'
ORDER BY q.SectionID, COALESCE(q.DisplayOrder, q.QuestionID), qc.ConfigKey;
"""

# Dynamic lists for School & Teacher (adjust names/filters as needed)
SCHOOLS_Q = """
SELECT DISTINCT s.MOENumber, s.SchoolName
FROM School s
JOIN SchoolFunder sf ON sf.MOENumber = s.MOENumber
WHERE sf.FunderID = :funder_id
  AND sf.CalendarYear = :year
  AND sf.Term = :term
  AND (s.IsActive = 1 OR s.IsActive IS NULL)
ORDER BY s.SchoolName;
"""

TEACHERS_Q = """
SELECT t.TeacherID, t.TeacherName, COALESCE(t.Email,'') as Email, t.MOENumber, COALESCE(t.TeacherType,'') as TeacherType
FROM Teacher t
WHERE (t.IsActive = 1 OR t.IsActive IS NULL)
  AND t.MOENumber IN (
      SELECT DISTINCT s.MOENumber
      FROM School s
      JOIN SchoolFunder sf ON sf.MOENumber = s.MOENumber
      WHERE sf.FunderID = :funder_id
        AND sf.CalendarYear = :year
        AND sf.Term = :term
  )
ORDER BY t.TeacherName;
"""

# =========================
# Fetch data
# =========================
with engine.connect() as conn:
    sections = conn.execute(text(SECTIONS_Q), {"sid": SURVEY_ID}).fetchall()
    likert_rows = conn.execute(text(LIKERT_Q), {"sid": SURVEY_ID}).fetchall()
    ddl_static_rows = conn.execute(text(DDL_STATIC_Q), {"sid": SURVEY_ID}).fetchall()
    ddl_config_rows = conn.execute(text(DDL_CONFIG_Q), {"sid": SURVEY_ID}).fetchall()
    # Preload dynamic sources once (cheap enough; can be deferred if not used)
    schools = conn.execute(
        text(SCHOOLS_Q),
        {"funder_id": FUNDER_ID, "year": CALENDARYEAR, "term": TERM}
    ).fetchall()
    teachers = conn.execute(
        text(TEACHERS_Q),
        {"funder_id": FUNDER_ID, "year": CALENDARYEAR, "term": TERM}
    ).fetchall()

# =========================
# Shape data
# =========================

# Likert mapping: per section: list of dicts {id, text, labels[]}
likert_by_section = defaultdict(list)
for qid, qtext, sec_id, dord, pos, label in likert_rows:
    # collect per question
    if not likert_by_section.get(sec_id) or not any(q["id"] == qid for q in likert_by_section[sec_id]):
        likert_by_section[sec_id].append({"id": qid, "text": qtext, "order": dord, "labels": []})
    # append label to the right question
    for q in likert_by_section[sec_id]:
        if q["id"] == qid:
            q["labels"].append((pos, label))

# DDL (static): {(sec_id, qid) -> {"text":..., "order":..., "options":[(val,label,sort),...]}}
ddl_static = {}
for qid, qtext, sec_id, dord, val, lab, sortorder in ddl_static_rows:
    key = (sec_id, qid)
    if key not in ddl_static:
        ddl_static[key] = {"text": qtext, "order": dord, "options": []}
    ddl_static[key]["options"].append((val, lab, sortorder))

# DDL (custom config): {(sec_id, qid) -> {"text":..., "order":..., "config": {k:v}}}
ddl_config = {}
for qid, ckey, cval, sec_id, qtext, dord in ddl_config_rows:
    key = (sec_id, qid)
    if key not in ddl_config:
        ddl_config[key] = {"text": qtext, "order": dord, "config": {}}
    ddl_config[key]["config"][ckey] = cval

# Dynamic data payloads for front-end
schools_json = [{"moe": r[0], "name": r[1]} for r in schools]
teachers_json = [{"id": r[0], "name": r[1], "email": r[2], "moe": r[3], "type": r[4]} for r in teachers]

# =========================
# HTML
# =========================
html = [
    '<!DOCTYPE html>',
    '<html lang="en"><head><meta charset="UTF-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
    '<title>WSFL Self Review</title>',
    '<style>',
    ' body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial,sans-serif;line-height:1.45;margin:24px;}',
    ' fieldset{border:1px solid #ddd;padding:14px;border-radius:10px;margin-bottom:18px;}',
    ' legend{font-weight:700;padding:0 6px;}',
    ' .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;}',
    ' select,input[type="text"],input[type="email"]{padding:8px 10px;border:1px solid #bbb;border-radius:8px;min-width:260px;}',
    ' .btn{display:inline-block;padding:10px 14px;border-radius:10px;border:0;background:#174b8a;color:white;font-weight:700;cursor:pointer;}',
    ' .muted{color:#666;}',
    '</style>',
    '</head><body>',
    '<h1>WSFL Self Review Survey</h1>',
    '<form id="surveyForm" action="/submit" method="post">'
]

# Render sections in order
for sec_id, sec_name, sec_order, sec_text in sections:
    html.append('<fieldset>')
    html.append(f'<legend>{sec_order}. {sec_name}</legend>')
    if sec_text:
        html.append(f'<p class="muted">{sec_text}</p>')

    # 1) Render DDL questions (static first, then custom), in DisplayOrder
    # Collect all DDL qids in this section
    ddl_qs = []

    for (s, qid), meta in ddl_static.items():
        if s == sec_id:
            ddl_qs.append((qid, meta["order"], "static", meta))

    for (s, qid), meta in ddl_config.items():
        if s == sec_id:
            # If same qid also exists as static, skip config (static wins)
            if not any(qid == q for (q, *_ignored) in [(q[0],) for q in ddl_qs]):
                ddl_qs.append((qid, meta["order"], "config", meta))

    # Order by DisplayOrder (or None last)
    ddl_qs.sort(key=lambda t: (t[1] is None, t[1], t[0]))

    for qid, _dord, kind, meta in ddl_qs:
        qtext = meta["text"]
        safe_text = (qtext or "").replace('"', "&quot;")
        if kind == "static":
            # render a static dropdown
            options = sorted(meta["options"], key=lambda x: (x[2] or 0))
            html.append('<div class="row" style="margin-top:8px;">')
            html.append(f'<label for="ddl_{qid}"><strong>{safe_text}</strong></label>')
            html.append(f'<select id="ddl_{qid}" name="ddl_{qid}" required>')
            html.append('<option value="" disabled selected>Select…</option>')
            for val, lab, _ in options:
                lab_safe = (lab or "").replace('"', "&quot;")
                val_safe = (val or "").replace('"', "&quot;")
                html.append(f'<option value="{val_safe}">{lab_safe}</option>')
            html.append('</select></div>')
        else:
            # render a dynamic dropdown based on config
            cfg = meta["config"]
            datasource = (cfg.get("DataSource") or "").lower()
            # We’ll support: datasource in {"school", "teacher"}
            if datasource == "school":
                html.append('<div class="row" style="margin-top:8px;">')
                html.append(f'<label for="school_{qid}"><strong>{safe_text}</strong></label>')
                html.append(f'<select id="school_{qid}" name="school_{qid}" required>')
                html.append('<option value="" disabled selected>Select a school…</option>')
                for s in schools_json:
                    html.append(f'<option value="{s["moe"]}">{s["name"]}</option>')
                html.append('</select></div>')
            elif datasource == "teacher":
                # Teacher list may depend on chosen school — we render empty and fill via JS.
                html.append('<div class="row" style="margin-top:8px;">')
                html.append(f'<label for="teacher_{qid}"><strong>{safe_text}</strong></label>')
                html.append(f'<select id="teacher_{qid}" name="teacher_{qid}" required disabled>')
                html.append('<option value="" disabled selected>Select a teacher…</option>')
                html.append('</select></div>')
                # Optional email text field if configured
                if cfg.get("WithEmail", "").lower() == "true":
                    html.append('<div class="row" style="margin-top:8px;">')
                    html.append(f'<label for="teacher_email_{qid}"><strong>Teacher Email</strong></label>')
                    html.append(f'<input id="teacher_email_{qid}" name="teacher_email_{qid}" type="email" placeholder="name@example.org" required>')
                    html.append('</div>')
            else:
                # Unknown datasource → render a plain text input to avoid blocking
                html.append('<div class="row" style="margin-top:8px;">')
                html.append(f'<label for="ddltext_{qid}"><strong>{safe_text}</strong></label>')
                html.append(f'<input id="ddltext_{qid}" name="ddltext_{qid}" type="text" placeholder="Enter value" required>')
                html.append('</div>')

    # 2) Render Likert questions for this section
    if sec_id in likert_by_section:
        # sort by DisplayOrder (None last), fallback to question id order preserved above
        likert_by_section[sec_id].sort(key=lambda q: (q["order"] is None, q["order"], q["id"]))
        for q in likert_by_section[sec_id]:
            html.append('<div style="margin-top:10px;">')
            html.append(f'<div><strong>Q{q["id"]}.</strong> {q["text"]}</div>')
            for pos, label in q["labels"]:
                lab = (label or "").replace('"', "&quot;")
                html.append(f'<div><label><input type="radio" name="q{q["id"]}" value="{pos}" required> {lab}</label></div>')
            html.append('</div>')

    html.append('</fieldset>')

# Submit
html.append('<button type="submit" class="btn">Submit Survey</button>')
html.append('</form>')

# =========================
# Front-end JS for dependent dropdowns
# =========================
html.append('<script>')
html.append(f'const TEACHERS = {json.dumps(teachers_json)};')
html.append(f'const SCHOOLS = {json.dumps(schools_json)};')

# Wire up every school_* to the next teacher_* in the DOM (simple heuristic)
html.append(r"""
(function(){
  // Find all school selects
  const schoolSelects = Array.from(document.querySelectorAll('select[id^="school_"]'));
  schoolSelects.forEach(schoolSel => {
    // find the next teacher select in the DOM (same section typically)
    const fieldset = schoolSel.closest('fieldset');
    if (!fieldset) return;
    const teacherSel = fieldset.querySelector('select[id^="teacher_"]');
    const emailInput = fieldset.querySelector('input[id^="teacher_email_"]');

    function clearTeachers() {
      if (!teacherSel) return;
      teacherSel.innerHTML = '<option value="" disabled selected>Select a teacher…</option>';
    }
    function populateTeachersForMOE(moe) {
      if (!teacherSel) return;
      clearTeachers();
      const list = TEACHERS.filter(t => String(t.moe) === String(moe));
      list.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.name + (t.type ? ` (${t.type})` : '');
        opt.dataset.email = t.email || '';
        teacherSel.appendChild(opt);
      });
      teacherSel.disabled = list.length === 0;
    }

    schoolSel.addEventListener('change', () => {
      const moe = schoolSel.value;
      populateTeachersForMOE(moe);
      if (emailInput) emailInput.value = '';
    });

    if (teacherSel) {
      teacherSel.addEventListener('change', () => {
        const opt = teacherSel.options[teacherSel.selectedIndex];
        if (!opt) return;
        const email = opt.dataset.email || '';
        if (emailInput) emailInput.value = email;
      });
    }
  });
})();
""")
html.append('</script>')

html.append('</body></html>')

# =========================
# Write file
# =========================
with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(html))

print(f"✅ Form written to {output_path}")
