# WSFL Flask App (Uploading and filling class lists & Downloading Reports)

This Flask web application allows education providers to upload student data, validate it against a SQL Server database, and generate formatted reports showing student competencies and scenarios.

---

## Features

- **CSV Upload**  
  Upload a CSV of student details including NSNs, names, dates of birth, and ethnicity.

- **Row-by-row Validation**  
  Each record is checked via stored procedure `CheckNSNMatch` to ensure it matches existing student records.

- **Competency Status Fetching**  
  For valid students, the app pulls all relevant competencies and their statuses using `GetStudentCompetencyStatus`.

- **Scenario Information**  
  If available, student scenarios are retrieved and inserted at specific positions in the output table.

- **Excel Report Generator**  
  Creates a downloadable `.xlsx` with:

  - Vertical-rotated competency labels
  - Merged headers with scenario grouping
  - Provider/school/teacher metadata filled in
  - Embedded logo in the top-left

- **PDF Summary Report**  
  Renders a PNG + downloadable PDF graph summary showing competency coverage by provider.

---

## Tech Stack

- **Backend:** Flask (Python 3.10), SQLAlchemy, PyODBC
- **Database:** Azure SQL Server
- **Frontend:** Bootstrap 5.3
- **Exporting:** Pandas, XlsxWriter, Matplotlib
- **Deployment:** Local for now — Render/WSGI plan in future

---

## Stored Procedures Used

- `CheckNSNMatch` – validates each student row
- `GetStudentCompetencyStatus` – retrieves student competency info
- `FlaskHelperFunctions` – multipurpose SP for:
  - Provider dropdown
  - Filtered school list
  - Student scenario lookup

---
