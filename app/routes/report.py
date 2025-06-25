import io
import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Blueprint, render_template, request, session, flash, redirect, url_for, send_file
from app.utils.database import get_db_engine
from app.utils.fundernationalplot import create_competency_report as create_funder_report
from app.utils.providerplot import create_competency_report as create_provider_report
from app.utils.competencyplot import load_competency_rates, make_figure as make_comp_figure
from app.utils.schoolplot import create_school_report

from sqlalchemy import text
from app.routes.auth import login_required
import traceback
report_bp = Blueprint("report_bp", __name__)

# Store raw bytes to avoid "I/O on closed file" errors
last_pdf_bytes = None
last_pdf_filename = None
last_png_bytes = None
last_png_filename = None

def get_available_terms(nearest_year, nearest_term):
    options = [(nearest_year, nearest_term)]
    if nearest_term > 1:
        options.append((nearest_year, nearest_term - 1))
    else:
        options.append((nearest_year - 1, 4))
    return options




@report_bp.route("/Reporting", methods=["GET", "POST"])
@login_required
def reporting():
    global last_pdf_bytes, last_pdf_filename, last_png_bytes, last_png_filename
    try:
        engine = get_db_engine()
        role = session.get("user_role")
        user_id = session.get("user_id")

        providers, funders, competencies, schools = [], [], [], []
        img_data = None
        report_type = request.form.get("report_type") if request.method == "POST" else None
        term = int(request.form.get("term", 0)) if request.method == "POST" else None
        year = int(request.form.get("year", 0)) if request.method == "POST" else None

        if report_type == "Funder":
            funder_name = request.form.get("funder")
        elif report_type == "Provider":
            funder_name = request.form.get("provider")
        elif report_type == "School":
            funder_name = request.form.get("school")
        else:
            funder_name = None

        if not funder_name:
            funder_name = session.get("desc")

        dropdown_string = None

        with engine.connect() as conn:
            if role == "ADM":
                funders = [row.Description for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})]
                providers = [row.Description for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"})]
                schools = [row.SchoolName for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "SchoolDropdown"})]
                competencies = [row.Competency for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "CompetencyDropdown"})]
            elif role == "FUN":
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                    {"Request": "FunderIDDescription", "Text": session.get("desc")}
                ).fetchone()
                if result:
                    funder_id = int(result.FunderID)
                    providers = [r.Description for r in conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'ProvidersByFunderID', @Number = :FunderID"),
                        {"FunderID": funder_id}
                    )]
                    query = text("EXEC FlaskHelperFunctions @Request = 'SchoolsByFunderID', @Number = :FunderID")
                    print("Query prepared:", query)

                    # Execute the query
                    result = conn.execute(query, {"FunderID": funder_id})
                    print("Query executed successfully.")

                    # Print the keys (i.e., column names returned by the stored procedure)
                    print("Returned columns:", result.keys())

                    # Optionally print the first few rows to inspect
                    rows = result.fetchall()
                    print("Sample rows:", rows[:3])  # Show the first 3 rows for inspection

                    # Now extract SchoolName from the rows
                    schools = [r.Description for r in rows]
                    print("Extracted school names:", schools)
            elif role == "PRO":
                result = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                    {"Request": "ProviderIDDescription", "Text": session.get("desc")}
                ).fetchone()
                if result:
                    provider_id = int(result.ProviderID)
                    schools = [r.Description for r in conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = 'SchoolsByProviderID', @Number = :ProviderID"),
                        {"ProviderID": provider_id}
                    )]

        if request.method == "POST":
            selected_year, selected_term = map(int, (request.form.get("term_year") or "0_0").split("_"))

            if report_type == "Funder":
                with engine.connect() as conn:
                    row = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                        {"Request": "FunderIDDescription", "Text": funder_name}
                    ).fetchone()
                    if not row:
                        flash("Funder not found.", "danger")
                        return redirect(url_for("report_bp.reporting"))
                    funder_id = int(row.FunderID)
                    fig = create_funder_report(selected_term, selected_year, funder_id, funder_name)

            elif report_type == "Provider":
                with engine.connect() as conn:
                    row = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                        {"Request": "ProviderIDDescription", "Text": funder_name}
                    ).fetchone()
                    if not row:
                        flash("Provider not found.", "danger")
                        return redirect(url_for("report_bp.reporting"))
                    provider_id = int(row.ProviderID)
                    fig = create_provider_report(selected_term, selected_year, provider_id, funder_name)

            elif report_type == "School":
                with engine.connect() as conn:
                    row = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                        {"Request": "SchoolIDFromName", "Text": funder_name}
                    ).fetchone()
                    if not row:
                        flash("School not found.", "danger")
                        return redirect(url_for("report_bp.reporting"))
                    moe_number = int(row.MOENumber)
                    fig = create_school_report(selected_year, selected_term, moe_number)

            elif report_type == "Competency":
                dropdown_string = request.form.get("competency")
                with engine.connect() as conn:
                    row = conn.execute(
                        text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
                        {"DropdownValue": dropdown_string}
                    ).fetchone()
                    if not row:
                        flash("Invalid competency selected.", "danger")
                        return redirect(url_for("report_bp.reporting"))
                    df = load_competency_rates(engine, selected_year, selected_term, row.CompetencyID, row.YearGroupID)
                    if df.empty:
                        flash("No data found.", "warning")
                        return redirect(url_for("report_bp.reporting"))
                    title = f"{df['CompetencyDesc'].iloc[0]} ({df['YearGroupDesc'].iloc[0]})"
                    fig = create_comp_figure(df, title)

            # Save PNG to bytes
            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            last_png_bytes = png_buf.getvalue()
            last_png_filename = f"{report_type}_Report_Term{selected_term}_{selected_year}.png"
            img_data = base64.b64encode(last_png_bytes).decode("utf-8")

            # Save PDF to bytes
            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)
            last_pdf_bytes = pdf_buf.getvalue()
            last_pdf_filename = f"{report_type}_Report_Term{selected_term}_{selected_year}.pdf"
            plt.close(fig)

        return render_template("reporting.html",
            funders=funders,
            providers=providers,
            schools=schools,
            competencies=competencies,
            user_role=role,
            img_data=img_data,
            selected_report_type=report_type,
            selected_term=term,
            selected_year=year,
            selected_funder=funder_name,
            selected_competency=dropdown_string if report_type == "Competency" else None,
            term_year_options=get_available_terms(session["nearest_year"], session["nearest_term"])
        )

    except Exception as e:
        print("\u274c An error occurred in /Reporting")
        traceback.print_exc()
        flash("Something went wrong. Please check the logs.", "danger")
        return redirect(url_for("report_bp.reporting"))

@report_bp.route('/Reporting/download_pdf')
@login_required
def download_pdf():
    if not last_pdf_bytes:
        flash("No PDF report has been generated yet.", "warning")
        return redirect(url_for("report_bp.reporting"))
    return send_file(io.BytesIO(last_pdf_bytes), download_name=last_pdf_filename, as_attachment=True, mimetype='application/pdf')

@report_bp.route('/Reporting/download_png')
@login_required
def download_png():
    if not last_png_bytes:
        flash("No PNG report has been generated yet.", "warning")
        return redirect(url_for("report_bp.reporting"))
    return send_file(io.BytesIO(last_png_bytes), download_name=last_png_filename, as_attachment=True, mimetype='image/png')
