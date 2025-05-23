# app/routes/upload.py
import io
import json
import threading
import pandas as pd
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, send_file, jsonify
from app.utils.processing import process_uploaded_csv
from app.utils.database import get_db_engine
from werkzeug.utils import secure_filename
from app.routes.auth import login_required
from sqlalchemy import text
from io import StringIO, BytesIO

upload_bp = Blueprint("upload_bp", __name__)

# Store processing results in memory (global for demo)
last_valid_df = pd.DataFrame()
last_error_df = pd.DataFrame()
processing_status = {"current": 0, "total": 0, "done": False}

def sanitize_filename(s):
    return s.replace(" ", "_").replace("/", "_")  # remove problematic characters


@upload_bp.route('/classlistupload', methods=['GET', 'POST'])
@login_required
def classlistupload():
    validated=False

    engine = get_db_engine()
    preview_data = None
    original_columns = [] 
    funders, schools = [], []
    selected_csv = selected_funder = selected_school = selected_term = selected_year = selected_teacher = selected_class = None

    with engine.connect() as conn:
        result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})
        funders = [dict(row._mapping) for row in result]

    if request.method == 'POST':
        action = request.form.get('action')  # 'preview' or 'validate'
        selected_funder = request.form.get('funder')
        selected_school = request.form.get('school') or session.get("user_id")

        selected_term = request.form.get('term')
        selected_year = request.form.get('year')
        selected_teacher = request.form.get('teachername')
        selected_class = request.form.get('classname')
        session["selected_class"] = selected_class
        session["selected_teacher"] = selected_teacher
        session["selected_year"] = selected_year
        session["selected_term"] = selected_term
        selected_school_str = str(selected_school)
        moe_number = (
            int(selected_school_str)
            if selected_school_str.isdigit()
            else int(selected_school_str.split('(')[-1].rstrip(')'))
        )
        column_mappings_json = request.form.get('column_mappings')
        file = request.files.get('csv_file')
        #print(file)
        selected_csv = file if file and file.filename else None,

        if selected_funder and selected_funder.isdigit():
            selected_funder = int(selected_funder)
        if selected_year and selected_year.isdigit():
            selected_year = int(selected_year)

        # Populate school dropdown
        if selected_funder and session.get("user_role") != "MOE":
            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions :Request, @Number=:Number"),
                    {"Request": "FilterSchoolID", "Number": selected_funder}
                )
                schools = [row.School for row in result]

        if action == "preview" and file and file.filename.endswith('.csv'):
            df = pd.read_csv(file)

            session["raw_csv_json"] = df.to_json(orient="records")
            preview_data = df.head(10).to_dict(orient="records")
            # Store the preview data in the session for later use
            session["preview_data"] = preview_data
            original_columns = list(df.columns)

        elif action == "validate":
            validated = True
            if not session.get("raw_csv_json"):
                flash("No CSV file has been uploaded for validation.", "danger")
            else:
                try:
                    raw_df = pd.read_json(StringIO(session["raw_csv_json"]))
                    #print(raw_df)
                    column_mappings = json.loads(column_mappings_json)
                    valid_fields = ["NSN", "FirstName", "LastName", "PreferredName", "BirthDate", "Ethnicity", "YearLevel"]

                    # Build reverse mapping: selected column name → expected name
                    reverse_mapping = {
                        k: v for k, v in column_mappings.items()
                        if v.strip().lower() in [field.lower() for field in valid_fields]
                    }                    
                    #print(reverse_mapping)
                    # Keep only columns mapped to valid fields
                    usable_columns = [col for col in raw_df.columns if col in reverse_mapping]

                    # Rename to expected names
                    df = raw_df[usable_columns].rename(columns=reverse_mapping)

                    # Ensure all required columns are present
                    for col in valid_fields:
                        if col not in df.columns:
                            df[col] = None  # Or np.nan
                    if "Birthdate" in df.columns:
                        df["Birthdate"] = pd.to_datetime(df["Birthdate"], errors="coerce").dt.strftime("%Y-%m-%d")
                    if "BirthDate" in df.columns:
                        df["BirthDate"] = pd.to_datetime(df["BirthDate"], errors="coerce").dt.strftime("%Y-%m-%d")
                    if "YearLevel" in df.columns:
                        df["YearLevel"] = pd.to_numeric(df["YearLevel"], errors="coerce").astype("Int64")
                        df["YearLevel"] = df["YearLevel"].where(pd.notnull(df["YearLevel"]), None)
                        
                    if "NSN" in df.columns:
                        df["NSN"] = pd.to_numeric(df["NSN"], errors="coerce").astype("Int64")
                    #print(df["YearLevel"])
                    #print(df.dtypes)
                    #print(df.to_json(orient="records"))

                    df_json = df.to_json(orient="records")
                    # Save a copy for debugging/inspection
                    #with open("last_validated_input.json", "w", encoding="utf-8") as f:
                    #   f.write(df_json)

                    with engine.connect() as conn:
                        result = conn.execute(
                            text("EXEC FlaskCheckNSN_JSON :InputJSON, :Term, :CalendarYear, :MOENumber"),
                            {"InputJSON": df_json, "Term": selected_term, "CalendarYear": selected_year, "MOENumber": moe_number}
                        )
                        preview_data = [dict(row._mapping) for row in result]
                        # Store the validated preview data in the session
                        session["preview_data"] = preview_data

                except Exception as e:
                    flash(f"Error during validation: {str(e)}", "danger")

        elif action == "preview":
            flash("Please upload a valid CSV file.", "danger")

    return render_template(
        "classlistupload.html",
        funders=funders,
        schools=schools,
        selected_funder=selected_funder,
        selected_school=selected_school,
        selected_term=selected_term,
        selected_year=selected_year,
        selected_teacher = selected_teacher,
        selected_class = selected_class,
        selected_csv = selected_csv,
        preview_data=preview_data,
        validated = validated,
        original_columns = original_columns
    )


@upload_bp.route('/classlistdownload', methods=['POST'])
@login_required
def classlistdownload():
    if not session.get("preview_data"):
        flash("No data available to export.", "danger")
        return redirect(url_for("classlistupload"))

    desired_order = [
        "NSN",
        "FirstName",
        "PreferredName",
        "LastName",
        "Birthdate",
        "Ethnicity",
        "YearLevel",
        "ErrorMessage",
        "Match"
    ]

    # Reconstruct DataFrame
    df = pd.DataFrame(session["preview_data"])
    df["Birthdate"] = pd.to_datetime(df["Birthdate"], errors="coerce").dt.date

    df = df.fillna("")

    # Ensure only desired columns and in the correct order
    columns_to_write = [col for col in desired_order if col in df.columns]

    output = BytesIO()

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df[columns_to_write].to_excel(writer, sheet_name='Results', index=False, startrow=1, header=False)
        workbook = writer.book
        worksheet = writer.sheets['Results']

        for col_num, col_name in enumerate(columns_to_write):
            worksheet.write(0, col_num, col_name)

        # Excel column name helper
        def excel_col_letter(n):
            name = ''
            while n >= 0:
                name = chr(n % 26 + 65) + name
                n = n // 26 - 1
            return name

        max_row = len(df) + 1
        max_col = len(columns_to_write)
        last_col_letter = excel_col_letter(max_col - 1)

        worksheet.add_table(f"A1:{last_col_letter}{max_row}", {
            'columns': [{'header': col} for col in columns_to_write],
            'style': 'Table Style Light 8',
            'name': 'MyTable'
        })

        # Formats
        wrap_top_format = workbook.add_format({'text_wrap': True, 'valign': 'top'})
        red_format = workbook.add_format({'bg_color': '#D63A3A', 'font_color': '#FFFFFF', 'bold': True, 'valign': 'top'})
        orange_format = workbook.add_format({'bg_color': "#EF9D32", 'font_color': '#FFFFFF', 'bold': True, 'valign': 'top'})
        orange_date_format = workbook.add_format({
            'bg_color': "#EF9D32", 'font_color': '#FFFFFF', 'bold': True, 'valign': 'top',
            'num_format': 'yyyy-mm-dd'
        })
        red_date_format = workbook.add_format({
            'bg_color': '#D63A3A', 'font_color': '#FFFFFF', 'bold': True, 'valign': 'top',
            'num_format': 'yyyy-mm-dd'
        })

        badge_format_error = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'top', 'font_color': '#FFFFFF', 'bg_color': "#D63A3A", 'border': 1})
        badge_format_ready = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'top', 'font_color': '#FFFFFF', 'bg_color': "#49B00D", 'border': 1})
        date_format = workbook.add_format({'num_format': 'yyyy-mm-dd', 'valign': 'top'})

        # Write formatted cells
        for row in range(1, len(df) + 1):
            error_fields = df.loc[row - 1, 'ErrorFields']
            match_value = df.loc[row - 1, 'Match']
            error_columns = [field.strip() for field in error_fields.split(',')] if pd.notna(error_fields) else []

            for col in range(len(columns_to_write)):
                col_name = columns_to_write[col]
                value = df.iloc[row - 1][col_name]
                
                if col_name == 'ErrorMessage' and (value is True or str(value).strip().lower() == 'true'):
                    value = ""

                is_error_col = col_name.lower() in [e.lower() for e in error_columns]
                is_match = str(match_value).strip().lower() in ["1", "true", "yes"]

                if is_error_col:
                    fmt = orange_format if is_match else red_format
                else:
                    fmt = wrap_top_format  # fallback for normal cells

                if col_name == "Birthdate":
                    try:
                        dt = pd.to_datetime(value)
                        if pd.notnull(dt):
                            if is_error_col:
                                fmt = orange_date_format if is_match else red_date_format
                            else:
                                fmt = date_format
                            worksheet.write_datetime(row, col, dt, fmt)
                        else:
                            worksheet.write(row, col, "", fmt)
                    except Exception:
                        worksheet.write(row, col, "", fmt)


            # Badge
            badge_col = len(columns_to_write) - 1
            badge_value = 'Ready' if match_value == 1 else 'Fix required'
            badge_format = badge_format_ready if match_value == 1 else badge_format_error
            worksheet.write(row, badge_col, badge_value, badge_format)

        # Column widths
        column_widths = {
            'NSN': 14,
            'FirstName': 20,
            'PreferredName': 20,
            'LastName': 20,
            'Birthdate': 18,
            'Ethnicity': 14,
            'Match': 12,
            'ErrorMessage': 40
        }

        for col_num, col_name in enumerate(columns_to_write):
            width = column_widths.get(col_name, None)
            worksheet.set_column(col_num, col_num, width, wrap_top_format)

        worksheet.set_column(max_col, max_col, 15, wrap_top_format)
        for row in range(max_row):
            worksheet.set_row(row, None, wrap_top_format)

    output.seek(0)
    classname = sanitize_filename(session.get("selected_class"))
    teachername = sanitize_filename(session.get("selected_teacher"))
    year = sanitize_filename(session.get("selected_year"))
    term = sanitize_filename(session.get("selected_term"))

    filename = f"{classname or 'Class'}_{teachername or 'Teacher'}_{year or 'Year'}_T{term or 'Term'}.xlsx"
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
@upload_bp.route('/submitclass', methods=['POST'])
@login_required
def submitclass():
    print("✅ Class submitted successfully!")  # For now
    flash("Class submitted successfully!", "success")
    return redirect(url_for("classlistupload"))

@upload_bp.route("/progress")
@login_required
def get_progress():
    return jsonify(processing_status)

@upload_bp.route("/results")
@login_required
def results():
    global last_valid_df, last_error_df
    valid_html = (
        last_valid_df.to_html(classes="table table-bordered", index=False)
        if not last_valid_df.empty else "<p>No valid records found.</p>"
    )
    error_html = (
        last_error_df.to_html(classes="table table-danger", index=False)
        if not last_error_df.empty else "<p>No errors found.</p>"
    )
    return render_template("displayresults.html", valid_html=valid_html, error_html=error_html)

@upload_bp.route("/download_excel")
@login_required
def download_excel():
    global last_valid_df
    if last_valid_df.empty:
        flash("No data available for export.", "warning")
        return redirect(url_for("upload_bp.results"))

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        last_valid_df.to_excel(writer, index=False, sheet_name='Competency Report')
    output.seek(0)
    return send_file(
        output,
        download_name="competency_report.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def process_and_store_results(df, term, year):
    global last_valid_df, last_error_df, processing_status
    try:
        processing_status = {"current": 0, "total": len(df), "done": False}
        valid, errors = process_uploaded_csv(df, term, year)
        last_valid_df = valid
        last_error_df = errors
    except Exception as e:
        last_valid_df = pd.DataFrame()
        last_error_df = pd.DataFrame([{"Error": str(e)}])
    finally:
        processing_status["done"] = True