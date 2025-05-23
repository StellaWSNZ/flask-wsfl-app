# app/routes/report.py
import io
import base64
import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt

from flask import Blueprint, render_template, request, session, flash, redirect, url_for, send_file
from app.utils.database import get_db_engine
from app.utils.fundernationalplot import create_competency_report
from app.utils.competencyplot import load_competency_rates, make_figure as make_comp_figure
from sqlalchemy import text
from app.routes.auth import login_required
report_bp = Blueprint("report_bp", __name__)

last_pdf_generated = None
last_pdf_filename = None
last_png_generated = None
last_png_filename = None

@report_bp.route('/reporting', methods=["GET", "POST"])
@login_required
def reporting():
    global last_pdf_generated, last_pdf_filename, last_png_generated, last_png_filename

    engine = get_db_engine()
    role = session.get("user_role")
    user_id = session.get("user_id")

    funders, competencies = [], []
    img_data = None
    report_type = request.form.get("report_type") if request.method == "POST" else None
    term = int(request.form.get("term", 0)) if request.method == "POST" else None
    year = int(request.form.get("year", 0)) if request.method == "POST" else None
    funder_name = request.form.get("funder") 
    if role == "FUN" and not funder_name:
        funder_name = session.get("desc")
    dropdown_string = None

    with engine.connect() as conn:
        if role == "ADM":
            result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})
            funders = [row.Description for row in result]

        result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "CompetencyDropdown"})
        competencies = [row.Competency for row in result]

    if request.method == "POST":
        if report_type == "Funder":
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT FunderID FROM Funder WHERE Description = :Description"),
                    {"Description": funder_name}
                )
                row = result.fetchone()

            if not row:
                flash("Funder not found.", "danger")
                return redirect(url_for("report_bp.reporting"))

            funder_id = int(row.FunderID)
            fig = create_competency_report(term, year, funder_id, funder_name)

        elif report_type == "Competency":
            dropdown_string = request.form.get("competency")
            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
                    {"DropdownValue": dropdown_string}
                )
                row = result.fetchone()

            if not row:
                flash("Invalid competency selected.", "danger")
                return redirect(url_for("report_bp.reporting"))

            df = load_competency_rates(engine, year, term, row.CompetencyID, row.YearGroupID)
            if df.empty:
                flash("No data found.", "warning")
                return redirect(url_for("report_bp.reporting"))

            title = f"{df['CompetencyDesc'].iloc[0]} ({df['YearGroupDesc'].iloc[0]})"
            fig = make_comp_figure(df, title)

        # Generate image
        png_buf = io.BytesIO()
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.savefig(png_buf, format="png")
        png_buf.seek(0)
        img_data = base64.b64encode(png_buf.read()).decode("utf-8")
        last_png_generated = io.BytesIO(png_buf.getvalue())
        last_png_filename = f"{report_type}_Report_{term}_{year}.png"

        # Generate PDF
        pdf_buf = io.BytesIO()
        fig.savefig(pdf_buf, format="pdf")
        pdf_buf.seek(0)
        last_pdf_generated = pdf_buf
        last_pdf_filename = f"{report_type}_Report_{term}_{year}.pdf"
        plt.close(fig)

    return render_template("reporting.html",
        funders=funders,
        competencies=competencies,
        user_role=role,
        img_data=img_data,
        selected_report_type=report_type,
        selected_term=term,
        selected_year=year,
        selected_funder=funder_name,
        selected_competency=dropdown_string if report_type == "Competency" else None
    )

@report_bp.route('/reporting/download_pdf')
@login_required
def download_pdf():
    if not last_pdf_generated:
        flash("No PDF report has been generated yet.", "warning")
        return redirect(url_for("report_bp.reporting"))

    last_pdf_generated.seek(0)
    return send_file(last_pdf_generated, download_name=last_pdf_filename, as_attachment=True, mimetype='application/pdf')

@report_bp.route('/reporting/download_png')
@login_required
def download_png():
    if not last_png_generated:
        flash("No PNG report has been generated yet.", "warning")
        return redirect(url_for("report_bp.reporting"))

    last_png_generated.seek(0)
    return send_file(last_png_generated, download_name=last_png_filename, as_attachment=True, mimetype='image/png')
