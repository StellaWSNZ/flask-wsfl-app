# app/routes/upload.py
import io
import json
import threading
import warnings
import pandas as pd
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, send_file, jsonify, abort
from app.utils.database import get_db_engine
from werkzeug.utils import secure_filename
from app.routes.auth import login_required
from sqlalchemy import text
from io import StringIO, BytesIO
import os 
import tempfile
import unicodedata
import re
import datetime
import traceback
upload_bp = Blueprint("upload_bp", __name__)

# Store processing results in memory (global for demo)
last_valid_df = pd.DataFrame()
last_error_df = pd.DataFrame()
processing_status = {"current": 0, "total": 0, "done": False}

def sanitize_filename(s):
    return s.replace(" ", "_").replace("/", "_")  # remove problematic characters

def remove_macrons(s):
    if not isinstance(s, str):
        return s
    normalized = unicodedata.normalize("NFD", s)
    return ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')

def autodetect_date_column(series):
   

    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m-%d-%Y"
    ]

    best_parse = None
    max_valid = -1

    # print("📥 Raw input values:")
    # print(series.head(10).to_string(index=False))

    # Step 1: Clean up timestamp and spaces
    series = series.astype(str).str.replace(r"[ T]?00:00:00(?:\.0+)?", "", regex=True).str.strip()

    # Step 2: Replace punctuation with dash
    series = series.str.replace(r"[^\d\s]", "-", regex=True)

    # print("\n🧹 Cleaned birthdates (pre-parse):")
    # print(series.head(10).to_string(index=False))

    # Step 3: Try specific formats
    for fmt in formats:
        try:
            parsed = pd.to_datetime(series, format=fmt, errors='coerce')
            valid_count = parsed.notna().sum()
            # print(f"\n🧪 Tried format '{fmt}' — valid parsed count: {valid_count}")
            if valid_count > max_valid:
                best_parse = parsed
                max_valid = valid_count
        except Exception as e:
            print(f"❌ Error trying format {fmt}: {e}")
            continue

    # Step 4: Fallback
    if best_parse is None or max_valid == 0:
        # print("\n⚠️ Falling back to automatic date parsing...")
        best_parse = pd.to_datetime(series, errors='coerce')

    # Step 5: Final result
    final_result = best_parse.dt.strftime("%Y-%m-%d")
   # print("\n🧼 Final normalized birthdates:")
   # print(final_result.head(10).to_string(index=False))

    # Check for any parsing failures
    if final_result.isna().any():
        print("\n❌ Unparsed values found at indices:")
        print(final_result[final_result.isna()].index.tolist())

    return final_result
def is_iso_format(series):
    iso_regex = r"^\d{4}-\d{2}-\d{2}$"
    return series.astype(str).str.match(iso_regex).all()

def normalize_date_string(s):
    s = str(s).strip()
    # Replace any non-alphanumeric character (like em dash, slash, unicode junk) with "-"
    s = re.sub(r"[^\w]", "-", s)
    return s
@upload_bp.route('/ClassUpload', methods=['GET', 'POST'])
@login_required
def classlistupload():
    try:
        validated=False
        if session.get("user_role") not in ["ADM", "FUN", "MOE","PRO","GRP"]:
            flash("You don’t have permission to access the class upload page.", "danger")
            return redirect(url_for("home_bp.home"))  # or whatever landing page is suitable
        engine = get_db_engine()
        preview_data = None
        original_columns = [] 
        funders, schools = [], []
        selected_csv = selected_funder = selected_school = selected_school_str = selected_term = selected_year = selected_teacher = selected_class = None
        selected_school = session.get("desc") or ""
        selected_moe = None 
        with engine.connect() as conn:
            if session.get("user_role") == "FUN":
                funders = [{"Description": session.get("desc"), "FunderID": session.get("user_id")}]
            elif session.get("user_role") == "PRO":
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions :Request, @Number=:Number"),
                    {"Request": "GetFunderByProvider", "Number": session.get("user_id")}
                )
                funders = [dict(row._mapping) for row in result]
            elif session.get("user_role") == "GRP":
                stmt = text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = 'FunderIDsFromGroupEntities', 
                        @ProviderIDs = :ProviderIDs, 
                        @RawFUNIDs = :RawFUNIDs
                """)

                provider_ids = ",".join(str(e["id"]) for e in session.get("group_entities", {}).get("PRO", []))
                raw_fun_ids = ",".join(str(e["id"]) for e in session.get("group_entities", {}).get("FUN", []))

                result = conn.execute(stmt, {
                    "ProviderIDs": provider_ids,
                    "RawFUNIDs": raw_fun_ids
                })
                funders = [dict(row._mapping) for row in result]
            else:
                result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})
                funders = [dict(row._mapping) for row in result]

        if request.method == 'POST':
            action = request.form.get('action')  # 'preview' or 'validate'
            selected_funder = request.form.get('funder')
            
            selected_term = request.form.get('term')
            selected_year = request.form.get('year')
            selected_teacher = request.form.get('teachername')
            selected_class = request.form.get('classname')
            session["selected_class"] = selected_class
            session["selected_teacher"] = selected_teacher
            session["selected_year"] = selected_year
            session["selected_term"] = selected_term
            session["selected_funder"] = selected_funder
            session["selected_funder"] = selected_funder

            selected_school_str = (
                request.form.get("school")
                or f"{session.get('desc')} ({session.get('user_id')})"
            )
            selected_school = selected_school_str

            # Safely extract MOE number; fallback to session user_id if parsing fails
            try:
                if selected_school_str.isdigit():
                    moe_number = int(selected_school_str)
                else:
                    # Look for digits inside parentheses
                    inner = selected_school_str.split("(")[-1].rstrip(")")
                    moe_number = int(inner)
            except (ValueError, IndexError):
                # Fallback to the logged-in user’s id if we can’t parse a number
                moe_number = int(session.get("user_id"))

            session["selected_moe"] = moe_number
            selected_moe = moe_number
            
            column_mappings_json = request.form.get('column_mappings')
            
            file = request.files.get('csv_file')
            selected_csv = file if file and file.filename else None

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
            else:
                schools = []
            #print(selected_funder)
            #print(schools)
            if action == "preview" and file and file.filename:
                
                filename = file.filename.lower()
                file_ext = os.path.splitext(filename)[-1]
                has_headers = not bool(request.form.get("no_headers"))
                session["has_headers"] = has_headers

                try:
                    if file_ext == ".csv":
                        try:
                            df = pd.read_csv(file, header=0 if has_headers else None)
                        except UnicodeDecodeError:
                            file.seek(0)  # Reset pointer to start of file
                            df = pd.read_csv(file, header=0 if has_headers else None, encoding="latin1")               
                    elif file_ext in [".xls", ".xlsx",".xlsm"]:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                            file.save(tmp.name)

                            # Convert Excel to CSV using pandas
                            excel_df = pd.read_excel(tmp.name, header=0 if has_headers else None)
                            tmp_csv_path = tmp.name.replace(".xlsx", ".csv")
                            excel_df.to_csv(tmp_csv_path, index=False)

                        # Now load the CSV version for consistent handling
                            df = pd.read_csv(tmp_csv_path, header=0 if has_headers else None, encoding="latin1")               
                    else:
                        flash("Unsupported file format. Please upload a .csv, .xls, or .xlsx file.", "danger")
                        return redirect(url_for("upload_bp.classlistupload"))
                except Exception as e:
                    flash(f"Failed to read uploaded file: {str(e)}", "danger")
                    return redirect(url_for("upload_bp.classlistupload"))

                # Replace NaN and NaT with None
                if "Birthdate" in df.columns:
                    print("\n📅 Starting Birthdate normalization...")

                    try:
                        # print("👀 Raw Birthdate column before normalization:")
                        # print(df["Birthdate"].head(5).to_string(index=False))

                        # Use autodetect_date_column to handle format
                        df["Birthdate"] = autodetect_date_column(df["Birthdate"])

                        # print("🧼 Normalized Birthdate values:")
                        # print(df["Birthdate"].head(5).to_string(index=False))

                        session["birthdate_format"] = "autodetected"

                    except Exception as e:
                        print("❌ Error during Birthdate normalization:", str(e))
                        raise
                else:
                    print("⚠️ 'Birthdate' column not found in uploaded file.")




                df_cleaned = df.where(pd.notnull(df), None)

                # Convert datetime columns to string format
                
                if "BirthDate" in df_cleaned.columns:
                    df_cleaned.rename(columns={"BirthDate": "Birthdate"}, inplace=True)
                # Save raw JSON version for later validation (as string)
                for col in df_cleaned.columns:
                    df_cleaned[col] = df_cleaned[col].apply(lambda x: remove_macrons(x) if isinstance(x, str) else x)
                df_cleaned.rename(columns={"BirthDate": "Birthdate"}, inplace=True)

                session["raw_csv_json"] = df_cleaned.to_json(orient="records")
                # print("✅ raw_csv_json saved with", len(df_cleaned), "rows")

                # Save top 10 rows preview to session
                preview_data = df_cleaned.head(10).to_dict(orient="records")
                for row in preview_data:
                    yl = row.get("YearLevel")
                    if isinstance(yl, (int, float)) and not pd.isnull(yl):
                        row["YearLevel"] = str(int(yl))
                    elif yl is None or pd.isnull(yl):
                        row["YearLevel"] = ""
                session["preview_data"] = preview_data


                # Save original column headers
                original_columns = list(df.columns)


            elif action == "validate":
                validated = True
                if not session.get("raw_csv_json"):
                    flash("No CSV file has been uploaded for validation.", "danger")
                else:
                    try:
                        raw_df = pd.read_json(StringIO(session["raw_csv_json"]))
                        column_mappings = json.loads(column_mappings_json)
                        #print("📋 Column mappings received from frontend:", column_mappings_json)
                        #print("📊 Raw DataFrame columns:", raw_df.columns.tolist())
                        valid_fields = ["NSN", "FirstName", "LastName", "PreferredName", "BirthDate", "Ethnicity", "YearLevel"]

                        # Build reverse mapping: selected column name → expected name
                        reverse_mapping = {
                            k: v for k, v in column_mappings.items()
                            if v.strip().lower() in [field.lower() for field in valid_fields]
                        }

                        usable_columns = [col for col in raw_df.columns if str(col) in reverse_mapping]
                        df = raw_df[usable_columns].rename(columns={col: reverse_mapping[str(col)] for col in usable_columns})

                        
                        # Ensure all required columns are present
                        for col in valid_fields:
                            if col not in df.columns:
                                df[col] = None

                        # Reapply formatting in case new columns were added
                        if "Birthdate" in df.columns:
                            df.rename(columns={"Birthdate": "BirthDate"}, inplace=True)
                        if "BirthDate" in df.columns:
                            df.rename(columns={"BirthDate": "BirthDate"}, inplace=True)
                        if "YearLevel" in df.columns:
                            df["YearLevel"] = df["YearLevel"].astype(str).str.extract(r"(\d+)")[0]
                            df["YearLevel"] = pd.to_numeric(df["YearLevel"], errors='coerce').astype("Int64")
                        if "NSN" in df.columns:
                            df["NSN"] = pd.to_numeric(df["NSN"], errors="coerce").astype("Int64")
                        df.rename(columns={"BirthDate": "Birthdate"}, inplace=True)
                        if "Birthdate" in df.columns:
                            birthdate_format = session.get("birthdate_format", "dayfirst")  # fallback to dayfirst
                            use_dayfirst = birthdate_format == "dayfirst"
                            df["Birthdate"] = autodetect_date_column(df["Birthdate"])


                        df_json = df.to_json(orient="records")
                        #print("*")
                        try:
                            parsed_json = json.loads(df_json)
                            for i, row in enumerate(parsed_json[:5]):
                                print(f"📦 Row {i+1}: {row}")
                        except Exception as e:
                            print("❌ JSON error:", e)
                        #print("*")
                        try:
                            with engine.begin() as conn:
                                result = conn.execute(
                                    text("EXEC FlaskCheckNSN_JSON :InputJSON, :Term, :CalendarYear, :MOENumber, :Email"),
                                    {
                                        "InputJSON": df_json,
                                        "Term": selected_term,
                                        "CalendarYear": selected_year,
                                        "MOENumber": moe_number,
                                        "Email": session.get("user_email"),
                                    }
                                )
                                preview_data = [dict(row._mapping) for row in result]
                                session["preview_data"] = preview_data
                        except Exception as e:
                            print("❌ SQL execution error:", e)
                            traceback.print_exc()
                            
                        #print("🔍 Inspecting Birthdate values in preview_data:")
                        for i, row in enumerate(preview_data[:5]):
                            print(f"Row {i+1} Birthdate:", row.get("Birthdate"), "Type:", type(row.get("Birthdate")))
                            #print(row)
                        for row in preview_data:
                            if isinstance(row.get("Birthdate"), (datetime.date, datetime.datetime)):
                                row["Birthdate"] = row["Birthdate"].strftime("%Y-%m-%d")
                            
                        print("🔍 Inspecting Birthdate values in preview_data:")
                        for i, row in enumerate(preview_data[:5]):
                            print(f"Row {i+1} Birthdate:", row.get("Birthdate"), "Type:", type(row.get("Birthdate")))
                            #print(preview_data.head())
                        
                    except Exception as e:
                        flash(f"Error during validation: {str(e)}", "danger")

            elif action == "preview":
                flash("Please upload a valid CSV file.", "danger")
                return redirect(url_for("upload_bp.classlistupload"))
        #print(selected_school)
        return render_template(
            "classlistupload.html",
            funders=funders,
            schools=schools,
            selected_funder=selected_funder,
            selected_school=selected_school,
            selected_term=selected_term,
            selected_year=selected_year,
                selected_moe=selected_moe,            # <-- add this

            selected_teacher = selected_teacher,
            selected_class = selected_class,
            selected_csv = selected_csv,
            preview_data=preview_data,
            validated = validated,
            original_columns = original_columns,
            has_headers = session.get("has_headers", True)

        )
    except Exception as e:
        print("ERROR: ", e)
        traceback.print_exc()

@upload_bp.route('/classlistdownload', methods=['POST'])
@login_required
def classlistdownload():
    if not session.get("preview_data"):
        flash("No data available to export.", "danger")
        return redirect(url_for("upload_bp.classlistupload"))

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
    df["Birthdate"] = autodetect_date_column(df["Birthdate"])


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
                        # Expecting YYYY-MM-DD format from autodetect_date_column
                        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                            dt = datetime.datetime.strptime(value, "%Y-%m-%d")
                            if is_error_col:
                                fmt = orange_date_format if is_match else red_date_format
                            else:
                                fmt = date_format
                            worksheet.write_datetime(row, col, dt, fmt)
                        else:
                            worksheet.write(row, col, "", fmt)
                    except Exception:
                        worksheet.write(row, col, "", fmt)
                else:
                    worksheet.write(row, col, value, fmt)



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
    
    
@upload_bp.route('/classlistdownload_csv', methods=['POST'])
@login_required
def classlistdownload_csv():
    if not session.get("preview_data"):
        flash("No data available to export.", "danger")
        return redirect(url_for("upload_bp.classlistupload"))

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

    df = pd.DataFrame(session["preview_data"])
    df["Birthdate"] = autodetect_date_column(df["Birthdate"])
    df = df.fillna("")

    # Keep only desired columns and order
    columns_to_write = [col for col in desired_order if col in df.columns]
    df["Match"] = df["Match"].apply(lambda x: "Ready" if str(x).strip().lower() in ["1", "true", "yes"] else "Fix required")

    output = BytesIO()
    df[columns_to_write].to_csv(output, index=False)
    output.seek(0)

    classname = sanitize_filename(session.get("selected_class"))
    teachername = sanitize_filename(session.get("selected_teacher"))
    year = sanitize_filename(session.get("selected_year"))
    term = sanitize_filename(session.get("selected_term"))
    
    filename = f"{classname or 'Class'}_{teachername or 'Teacher'}_{year or 'Year'}_T{term or 'Term'}.csv"
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype='text/csv'
    )
    
    
@upload_bp.route('/submitclass', methods=['POST'])
@login_required
def submitclass():
    engine = get_db_engine()
    try:
        funder_id = session.get("selected_funder")
        moe_number = session.get("selected_moe")
        term = session.get("selected_term")
        year = session.get("selected_year")
        teacher = session.get("selected_teacher")
        classname = session.get("selected_class")
        preview_data = session.get("preview_data")
        missing = []

        if not funder_id:
            missing.append("funder_id")
        if not moe_number:
            missing.append("moe_number")
        if not term:
            missing.append("term")
        if not year:
            missing.append("year")
        if not teacher:
            missing.append("teacher")
        if not classname:
            missing.append("classname")
        if not preview_data:
            missing.append("preview_data")
        
        # DEBUG: Print all values
        #print("🔍 funder_id:", funder_id)
        #print("🔍 moe_number:", moe_number)
        #print("🔍 term:", term)
        #print("🔍 year:", year)
        #print("🔍 teacher:", teacher)
        #print("🔍 classname:", classname)
        #print("🔍 preview_data sample:", preview_data[:1])

        if missing:
            flash(f"Missing required data to submit class list: {', '.join(missing)}", "danger")
            return redirect(url_for("upload_bp.classlistupload"))
   
                
        for row in preview_data:
            for k, v in row.items():
                if isinstance(v, str):
                    row[k] = remove_macrons(v)
        for row in preview_data:
            for k, v in row.items():
                if isinstance(v, str):
                    row[k] = remove_macrons(v)
        input_json = json.dumps(preview_data)
        
        #print("🔍 Final JSON birthdate values before submit:")
        #for i, row in enumerate(preview_data[:3]):
        #    print(f"Row {i+1} Birthdate:", row.get("Birthdate"))
                

        #print(input_json)
        with engine.begin() as conn:
            if session.get("user_role") == "PRO":
                conn.execute(
                    text("""
                        EXEC FlaskInsertClassList
                            @FunderID = :FunderID,
                            @MOENumber = :MOENumber,
                            @Term = :Term,
                            @CalendarYear = :CalendarYear,
                            @TeacherName = :TeacherName,
                            @ClassName = :ClassName,
                            @InputJSON = :InputJSON,
                            @Email = :email,
                            @ProviderID = :ProviderID
                    """),
                    {
                        "FunderID": funder_id,
                        "MOENumber": moe_number,
                        "Term": term,
                        "CalendarYear": year,
                        "TeacherName": teacher,
                        "ClassName": classname,
                        "InputJSON": input_json,
                        "email": session["user_email"],
                        "ProviderID": session.get("user_id")  # or however ProviderID is stored
                    }
                )
            else:
                conn.execute(
                    text("""
                        EXEC FlaskInsertClassList
                            @FunderID = :FunderID,
                            @MOENumber = :MOENumber,
                            @Term = :Term,
                            @CalendarYear = :CalendarYear,
                            @TeacherName = :TeacherName,
                            @ClassName = :ClassName,
                            @InputJSON = :InputJSON,
                            @Email = :email
                    """),
                    {
                        "FunderID": funder_id,
                        "MOENumber": moe_number,
                        "Term": term,
                        "CalendarYear": year,
                        "TeacherName": teacher,
                        "ClassName": classname,
                        "InputJSON": input_json,
                        "email": session["user_email"]
                    }
                )
        flash("✅ Class submitted successfully!", "success")

    except Exception as e:
        flash(f"❌ Error submitting class: {str(e)}", "danger")

    return redirect(url_for("upload_bp.classlistupload"))

  
    
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

 
@upload_bp.route("/get_schools_for_funder")
@login_required
def get_schools_for_funder():
    funder_id = request.args.get("funder_id", type=int)
    #print(funder_id)
    if not funder_id:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("EXEC FlaskHelperFunctions :Request, @Number=:Number"),
            {"Request": "FilterSchoolID", "Number": funder_id}
        )
        schools = [row.School for row in result]
        #print(schools)
    return jsonify(schools)