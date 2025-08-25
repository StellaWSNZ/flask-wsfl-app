import io
import base64
import traceback
from base64 import b64decode
from datetime import date, datetime

import matplotlib
matplotlib.use("Agg")  # safe backend for servers
import matplotlib.pyplot as plt
import pandas as pd
import pytz
from sqlalchemy import text
from flask import (
    Blueprint, render_template, request,
    session, flash, redirect, url_for,
    send_file, jsonify, abort
)

# App utilities
from app.utils.database import get_db_engine
from app.utils.fundernationalplot import create_competency_report as create_funder_report
from app.utils.providerplot import create_competency_report as create_provider_report
from app.utils.competencyplot import load_competency_rates, make_figure as create_comp_figure
from app.utils.schoolplot import create_school_report
import app.utils.report_three_bar_landscape as r3  # old fundernationalplot.py
import app.utils.report_two_bar_portrait as r2     # old nationalplot.py
from app.utils.one_bar_one_line import provider_portrait_with_target, use_ppmori

# Routes / auth
from app.routes.auth import login_required

report_bp = Blueprint("report_bp", __name__) 

def fig_to_png_b64(fig, *, dpi=200) -> str:
    buf = io.BytesIO()
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")

def fig_to_pdf_b64(fig) -> str:
    buf = io.BytesIO()
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(buf, format="pdf")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")
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




# @report_bp.route("/Reporting", methods=["GET", "POST"])
# @login_required
# def reporting():
#     global last_pdf_bytes, last_pdf_filename, last_png_bytes, last_png_filename
#     try:
#         engine = get_db_engine()
#         role = session.get("user_role")
#         user_id = session.get("user_id")

#         providers, funders, competencies, schools = [], [], [], []
#         img_data = None
#         report_type = request.form.get("report_type") if request.method == "POST" else None
#         term = int(request.form.get("term", 0)) if request.method == "POST" else None
#         year = int(request.form.get("year", 0)) if request.method == "POST" else None

#         if report_type == "Funder":
#             funder_name = request.form.get("funder")
#         elif report_type == "Provider":
#             funder_name = request.form.get("provider")
#         elif report_type == "School":
#             funder_name = request.form.get("school")
#         else:
#             funder_name = None

#         if not funder_name:
#             funder_name = session.get("desc")

#         dropdown_string = None

#         with engine.connect() as conn:
#             if role == "ADM":
#                 funders = [row.Description for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})]
#                 providers = [row.Description for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "ProviderDropdown"})]
#                 schools = [row.SchoolName for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "SchoolDropdown"})]
#                 competencies = [row.Competency for row in conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "CompetencyDropdown"})]
#             elif role == "FUN":
#                 result = conn.execute(
#                     text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
#                     {"Request": "FunderIDDescription", "Text": session.get("desc")}
#                 ).fetchone()
#                 if result:
#                     funder_id = int(result.FunderID)
#                     providers = [r.Description for r in conn.execute(
#                         text("EXEC FlaskHelperFunctions @Request = 'ProvidersByFunderID', @Number = :FunderID"),
#                         {"FunderID": funder_id}
#                     )]
#                     query = text("EXEC FlaskHelperFunctions @Request = 'SchoolsByFunderID', @Number = :FunderID")
#                     print("Query prepared:", query)

#                     # Execute the query
#                     result = conn.execute(query, {"FunderID": funder_id})
#                     print("Query executed successfully.")

#                     # Print the keys (i.e., column names returned by the stored procedure)
#                     print("Returned columns:", result.keys())

#                     # Optionally print the first few rows to inspect
#                     rows = result.fetchall()
#                     print("Sample rows:", rows[:3])  # Show the first 3 rows for inspection

#                     # Now extract SchoolName from the rows
#                     schools = [r.Description for r in rows]
#                     print("Extracted school names:", schools)
            
#             elif role == "PRO":
#                 result = conn.execute(
#                     text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
#                     {"Request": "ProviderIDDescription", "Text": session.get("desc")}
#                 ).fetchone()
#                 if result:
#                     provider_id = int(result.ProviderID)
#                     schools = [r.Description for r in conn.execute(
#                         text("EXEC FlaskHelperFunctions @Request = 'SchoolsByProviderID', @Number = :ProviderID"),
#                         {"ProviderID": provider_id}
#                     )]
#             elif role == "GRP":
#                 group_id = session.get("user_id")
                
#                 providers = [r.Description for r in conn.execute(
#                     text("EXEC FlaskHelperFunctions @Request = 'ProvidersByGroupID', @Number = :GroupID"),
#                     {"GroupID": group_id}
#                 )]

#                 schools = [r.Description for r in conn.execute(
#                     text("EXEC FlaskHelperFunctions @Request = 'SchoolsByGroupID', @Number = :GroupID"),
#                     {"GroupID": group_id}
#                 )]
#         if request.method == "POST":
#             selected_year, selected_term = map(int, (request.form.get("term_year") or "0_0").split("_"))

#             if report_type == "Funder":
#                 with engine.connect() as conn:
#                     row = conn.execute(
#                         text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
#                         {"Request": "FunderIDDescription", "Text": funder_name}
#                     ).fetchone()
#                     if not row:
#                         flash("Funder not found.", "danger")
#                         return redirect(url_for("report_bp.reporting"))
#                     funder_id = int(row.FunderID)
                    
#                     vars_to_plot = ["National Rate (YTD)", "Funder Rate (YTD)", "Funder Target"]  # default

#                     colors_dict = {
#                         "National Rate (YTD)": "#2EBDC2",
#                         "Funder Rate (YTD)": "#356FB6",
#                         "Funder Target": "#BBE6E9",
#                         "National Rate (LY)": "#2EBDC2",
#                         "Funder Rate (LY)": "#356FB6"
#                     }

#                     fig = create_funder_report(
#                         selected_term, selected_year, funder_id,
#                         vars_to_plot, colors_dict, funder_name
#                     )


#             elif report_type == "Provider":
#                 with engine.connect() as conn:
#                     row = conn.execute(
#                         text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
#                         {"Request": "ProviderIDDescription", "Text": funder_name}
#                     ).fetchone()
#                     if not row:
#                         flash("Provider not found.", "danger")
#                         return redirect(url_for("report_bp.reporting"))
#                     provider_id = int(row.ProviderID)
#                     fig = create_provider_report(selected_term, selected_year, provider_id, funder_name)

#             elif report_type == "School":
#                 with engine.connect() as conn:
#                     row = conn.execute(
#                         text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
#                         {"Request": "SchoolIDFromName", "Text": funder_name}
#                     ).fetchone()
#                     if not row:
#                         flash("School not found.", "danger")
#                         return redirect(url_for("report_bp.reporting"))
#                     moe_number = int(row.MOENumber)
#                     fig = create_school_report(selected_year, selected_term, moe_number)

#             elif report_type == "Competency":
#                 dropdown_string = request.form.get("competency")
#                 with engine.connect() as conn:
#                     row = conn.execute(
#                         text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
#                         {"DropdownValue": dropdown_string}
#                     ).fetchone()
#                     if not row:
#                         flash("Invalid competency selected.", "danger")
#                         return redirect(url_for("report_bp.reporting"))
#                     df = load_competency_rates(engine, selected_year, selected_term, row.CompetencyID, row.YearGroupID)
#                     if df.empty:
#                         flash("No data found.", "warning")
#                         return redirect(url_for("report_bp.reporting"))
#                     title = f"{df['CompetencyDesc'].iloc[0]} ({df['YearGroupDesc'].iloc[0]})"
#                     fig = create_comp_figure(df, title)

#             # Save PNG to bytes
#             png_buf = io.BytesIO()
#             plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
#             fig.savefig(png_buf, format="png")
#             png_buf.seek(0)
#             last_png_bytes = png_buf.getvalue()
#             last_png_filename = f"{report_type}_Report_Term{selected_term}_{selected_year}.png"
#             img_data = base64.b64encode(last_png_bytes).decode("utf-8")

#             # Save PDF to bytes
#             pdf_buf = io.BytesIO()
#             fig.savefig(pdf_buf, format="pdf")
#             pdf_buf.seek(0)
#             last_pdf_bytes = pdf_buf.getvalue()
#             last_pdf_filename = f"{report_type}_Report_Term{selected_term}_{selected_year}.pdf"
#             plt.close(fig)
#             session["report_pdf_bytes"] = base64.b64encode(last_pdf_bytes).decode("utf-8")
#             session["report_pdf_filename"] = last_pdf_filename
#             session["report_png_bytes"] = base64.b64encode(last_png_bytes).decode("utf-8")
#             session["report_png_filename"] = last_png_filename
#         return render_template("reporting.html",
#             funders=funders,
#             providers=providers,
#             schools=schools,
#             competencies=competencies,
#             user_role=role,
#             img_data=img_data,
#             selected_report_type=report_type,
#             selected_term=term,
#             selected_year=year,
#             selected_funder=funder_name,
#             selected_competency=dropdown_string if report_type == "Competency" else None,
#             term_year_options=get_available_terms(session["nearest_year"], session["nearest_term"])
#         )

#     except Exception as e:
#         print("\u274c An error occurred in /Reporting")
#         traceback.print_exc()
#         #flash("Something went wrong. Please check the logs.", "danger")
#         return redirect(url_for("report_bp.reporting"))

@report_bp.route('/Reporting/download_pdf')
@login_required
def download_pdf():
    try:
        pdf_data = session.get("report_pdf_bytes")
        pdf_name = session.get("report_pdf_filename") or "report.pdf"
        if not pdf_data:
            flash("No PDF report has been generated yet.", "warning")
            return redirect(url_for("report_bp.new_reports"))

        return send_file(
            io.BytesIO(b64decode(pdf_data)),
            download_name=pdf_name,
            as_attachment=True,
            mimetype='application/pdf'
        )
    except Exception as e:
        print("❌ Error in /download_pdf:", e)
        traceback.print_exc()
        flash("An error occurred while downloading the PDF.", "danger")
        return redirect(url_for("report_bp.new_reports"))


@report_bp.route('/Reporting/download_png')
@login_required
def download_png():
    try:
        png_data = session.get("report_png_bytes")
        png_name = session.get("report_png_filename") or "report.png"
        if not png_data:
            flash("No PNG report has been generated yet.", "warning")
            return redirect(url_for("report_bp.new_reports"))

        return send_file(
            io.BytesIO(b64decode(png_data)),
            download_name=png_name,
            as_attachment=True,
            mimetype='image/png'
        )
    except Exception as e:
        print("❌ Error in /download_png:", e)
        traceback.print_exc()
        flash("An error occurred while downloading the PNG.", "danger")
        return redirect(url_for("report_bp.new_reports"))
    
# at top of the file with your other imports:
from flask import Blueprint, render_template, request, session, flash, redirect, url_for
from sqlalchemy import text
import traceback


from app.utils.one_bar_one_line import provider_portrait_with_target, use_ppmori


def is_one_var_vs_target(rows):
    """Return True iff dataset contains exactly one rate series + WSNZ Target."""
    def norm(s):
        return str(s or "").strip().lower()

    rtypes = {norm(r.get("ResultType")) for r in rows}
    has_target = ("wsnz target" in rtypes) or ("target" in rtypes)

    has_prov = ("provider rate (ytd)" in rtypes) or ("provider rate ytd" in rtypes)
    has_fund = ("funder rate (ytd)"   in rtypes) or ("funder rate ytd"  in rtypes)
    has_nat  = ("national rate (ytd)" in rtypes) or ("national rate ytd" in rtypes)

    num_rates = sum([has_prov, has_fund, has_nat])
    return has_target and (num_rates == 1)

@report_bp.route("/Reports", methods=["GET", "POST"])
@login_required
def new_reports():
    role = session.get("user_role")  # "ADM", "FUN", or "PRO"
    print(f"🔑 Session role: {role}")

    engine = get_db_engine()

    # --------- UI state (defaults shown in form) ----------
    selected_year  = None
    selected_term  = None
    selected_type  = None
    selected_funder_name = None

    # sticky IDs from POST if present
    selected_provider_id = request.form.get("provider_id") if request.method == "POST" else None
    selected_school_id   = request.form.get("school_id") if request.method == "POST" else None  # 🆕
    print(f"📥 initial provider_id from POST: {selected_provider_id} (type {type(selected_provider_id)})")
    print(f"📥 initial school_id from POST: {selected_school_id} (type {type(selected_school_id)})")
    display = False

    # FUN sees their funder in the UI; providers list is loaded via AJAX using that name
    if role == "FUN":
        selected_funder_name = session.get("desc")
        print(f"FUN default funder_name (session.desc): {selected_funder_name}")
    elif role == "PRO":
        selected_provider_id = selected_provider_id or session.get("id")
        print(f"PRO effective provider_id: {selected_provider_id}")

    results = None
    plot_payload = None
    plot_png_b64 = None
    no_data_banner = None  # 🆕 banner we can pass to UI/figure

    # --------- Figure out intent ----------
    action = request.form.get("action")
    is_ajax = (request.form.get("ajax") == "1") or (request.headers.get("X-Requested-With") == "fetch")

    if request.method == "POST" and action == "show_report":
        print("📩 POST detected (show_report)")
        try:
            with engine.connect() as conn:
                selected_year = int(request.form.get("year", 2025))
                selected_term = int(request.form.get("term", 3))
                selected_type = request.form.get("report_option")
                print(f"📥 POST params: year={selected_year}, term={selected_term}, type={selected_type}")

                # Resolve funder from ADM dropdown (FUN is implied from session, PRO has none)
                if role == "ADM":
                    selected_funder_name = request.form.get("selected_funder") or None
                print(f"effective funder_name: {selected_funder_name}")

                funder_id = None
                if selected_funder_name:
                    row = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request='FunderIDDescription', @Text=:t"),
                        {"t": selected_funder_name}
                    ).fetchone()
                    if not row:
                        msg = "Funder not found."
                        if is_ajax:
                            return jsonify({"ok": False, "error": msg}), 400
                        flash(msg, "danger")
                        return redirect(url_for("report_bp.new_reports"))
                    funder_id = row[0] if not hasattr(row, "_mapping") else int(row._mapping.get("FunderID") or row[0])
                    print(f"🔑 resolved funder_id={funder_id}")

                # Do we need a provider?
                needs_provider = selected_type in {"provider_ytd_vs_target", "provider_ytd_vs_target_vs_funder"}
                if needs_provider and not selected_provider_id:
                    if role == "PRO":
                        selected_provider_id = session.get("id")
                    if not selected_provider_id:
                        msg = "Please choose a provider."
                        if is_ajax:
                            return jsonify({"ok": False, "error": msg}), 400
                        flash("Please choose a provider to run that report.", "warning")
                        return render_template(
                            "reportingnew.html",
                            role=role,
                            funder_name=selected_funder_name,
                            results=None,
                            plot_payload=None,
                            plot_png_b64=None,
                            selected_term=selected_term,
                            selected_year=selected_year,
                            selected_type=selected_type,
                            entities_url=url_for("funder_bp.get_entities"),
                        )

                # Do we need a school?  🆕
                needs_school = selected_type in {"school_ytd_vs_national"}
                if needs_school and not selected_school_id:
                    msg = "Please choose a school."
                    if is_ajax:
                        return jsonify({"ok": False, "error": msg}), 400
                    flash(msg, "warning")
                    return render_template(
                        "reportingnew.html",
                        role=role,
                        funder_name=selected_funder_name,
                        results=None,
                        plot_payload=None,
                        plot_png_b64=None,
                        selected_term=selected_term,
                        selected_year=selected_year,
                        selected_type=selected_type,
                        entities_url=url_for("funder_bp.get_entities"),
                    )

                # ===== Execute the selected report =====
                print(f"▶ executing report type: {selected_type}")

                if selected_type == "funder_ytd_vs_target":
                    sql = text("""
                        SET NOCOUNT ON;
                        EXEC dbo.GetFunderNationalRates_All
                             @Term = :Term,
                             @CalendarYear = :CalendarYear;
                    """)
                    params = {"Term": selected_term, "CalendarYear": selected_year}
                    res = conn.execute(sql, params)
                    rows = res.mappings().all()

                    if funder_id:
                        funder_rows = [
                            r for r in rows
                            if (int(r.get("FunderID", 0) or 0) == funder_id)
                            or (r.get("ResultType") == "WSNZ Target")
                        ]
                        if not funder_rows:
                            unique_comps = {
                                (r["CompetencyID"], r["CompetencyDesc"], r["YearGroupID"], r["YearGroupDesc"])
                                for r in rows
                            }
                            funder_rows = []
                            for cid, cdesc, yid, ydesc in unique_comps:
                                funder_rows.append({
                                    "FunderID": funder_id,
                                    "CompetencyID": cid,
                                    "CompetencyDesc": cdesc,
                                    "YearGroupID": yid,
                                    "YearGroupDesc": ydesc,
                                    "ResultType": "Funder Rate (YTD)",
                                    "Rate": 0,
                                    "StudentCount": 0
                                })
                                funder_rows.append({
                                    "FunderID": funder_id,
                                    "CompetencyID": cid,
                                    "CompetencyDesc": cdesc,
                                    "YearGroupID": yid,
                                    "YearGroupDesc": ydesc,
                                    "ResultType": "Funder Student Count (YTD)",
                                    "Rate": 0,
                                    "StudentCount": 0
                                })
                        results = funder_rows
                    else:
                        results = rows

                    print("🔎 rows:", len(results))

                elif selected_type == "ly_funder_vs_ly_national_vs_target":
                    ly = selected_year - 1
                    sql = text("""
                        SET NOCOUNT ON;
                        EXEC dbo.GetFunderNationalRates_All
                            @Term = :Term,
                            @CalendarYear = :CalendarYear;
                    """)
                    params = {"Term": 2, "CalendarYear": 2025}
                    res = conn.execute(sql, params)
                    rows = res.mappings().all()

                    if funder_id:
                        rows = [
                            r for r in rows
                            if int(r.get("FunderID", 0) or 0) == funder_id
                            or r.get("Funder") == "National"
                            or r.get("ResultType") == "WSNZ Target"
                            or r.get("ResultType") == "National Rate (YTD)"
                        ]
                    results = rows

                elif selected_type == "provider_ytd_vs_target_vs_funder":
                    sql = text("""
                        SET NOCOUNT ON;
                        EXEC dbo.GetProviderNationalRates
                            @Term         = :Term,
                            @CalendarYear = :CalendarYear,
                            @ProviderID   = :ProviderID,
                            @FunderID     = :FunderID;
                    """)
                    params = {
                        "Term": selected_term,
                        "CalendarYear": selected_year,
                        "ProviderID": int(selected_provider_id),
                        "FunderID": int(funder_id) if funder_id is not None else None
                    }
                    res = conn.execute(sql, params)
                    rows = res.mappings().all()

                    if len(rows) == 0:
                        res2 = conn.exec_driver_sql(
                            "SET NOCOUNT ON; EXEC dbo.GetProviderNationalRates @Term=?, @CalendarYear=?, @ProviderID=?, @FunderID=?",
                            (selected_term, selected_year, int(selected_provider_id),
                             funder_id if funder_id is not None else None)
                        )
                        if getattr(res2, "cursor", None) and res2.cursor.description:
                            cols = [d[0] for d in res2.cursor.description]
                            rows = [dict(zip(cols, row)) for row in res2.fetchall()]
                    results = rows

                elif selected_type == "provider_ytd_vs_target":
                    sql = text("""
                        SET NOCOUNT ON;
                        EXEC dbo.GetProviderNationalRates
                             @Term         = :Term,
                             @CalendarYear = :CalendarYear,
                             @ProviderID   = :ProviderID,
                             @FunderID     = :FunderID;
                    """)
                    params = {
                        "Term": selected_term,
                        "CalendarYear": selected_year,
                        "ProviderID": int(selected_provider_id),
                        "FunderID": int(funder_id) if funder_id is not None else None
                    }
                    res = conn.execute(sql, params)
                    rows = res.mappings().all()

                    # Debug: how many rows + first row
                    print(f"🧪 Rows fetched: {len(rows)}")
                    if rows:
                        first = rows[0]
                        print("🧪 First row keys:", list(first.keys()))
                        print("🧪 Distinct ResultTypes:", {r.get("ResultType") for r in rows})
                    else:
                        print("⚠️ Stored procedure returned 0 rows for these params.")

                    results = rows

                # 🆕 SCHOOL: YTD vs National (uses GetSchoolNationalRates @CalendarYear, @Term, @MoeNumber)
                elif selected_type == "school_ytd_vs_national":
                    sql = text("""
                        SET NOCOUNT ON;
                        EXEC dbo.GetSchoolNationalRates
                             @CalendarYear = :CalendarYear,
                             @Term         = :Term,
                             @MoeNumber    = :MoeNumber;
                    """)
                    params = {
                        "CalendarYear": selected_year,
                        "Term": selected_term,
                        "MoeNumber": int(selected_school_id)
                    }
                    print(f"📥 SQL params (school): {params}")
                    res = conn.execute(sql, params)
                    rows = res.mappings().all()

                    results = rows

                else:
                    msg = "Invalid report option."
                    if is_ajax:
                        return jsonify({"ok": False, "error": msg}), 400
                    flash(msg, "warning")
                    return redirect(url_for("report_bp.new_reports"))

                # --- keep payload ---
                plot_payload = {
                    "year": selected_year,
                    "term": selected_term,
                    "type": selected_type,
                    "funder_id": funder_id,
                    "provider_id": int(selected_provider_id) if selected_provider_id else None,
                    "school_id": int(selected_school_id) if selected_school_id else None,  # 🆕
                    "rows": results
                }

                # ========= Render figs =========
                fig = None
                if results:
                    if selected_type == "provider_ytd_vs_target_vs_funder":
                        vars_to_plot = ["Provider Rate (YTD)", "Funder Rate (YTD)", "WSNZ Target"]
                        colors_dict = {
                            "Provider Rate (YTD)": "#2EBDC2",
                            "WSNZ Target": "#356FB6",
                            "Funder Rate (YTD)": "#BBE6E9",
                        }
                        provider_display_name = (
                            request.form.get("provider_name")
                            or next((r.get("ProviderName") or r.get("Provider")
                                    for r in (results or []) if r.get("ProviderName") or r.get("Provider")), None)
                            or f"Provider {selected_provider_id}"
                        )
                        funder_display_name = selected_funder_name or "Funder"
                        title_text = f"{provider_display_name} & {funder_display_name}"
                        fig = r3.create_competency_report(
                            term=selected_term,
                            year=selected_year,
                            funder_id=funder_id or 0,
                            rows=results,
                            vars_to_plot=vars_to_plot,
                            colors_dict=colors_dict,
                            funder_name=title_text
                        )

                    elif selected_type == "ly_funder_vs_ly_national_vs_target":
                        vars_to_plot = ["National Rate (YTD)", "Funder Rate (YTD)", "WSNZ Target"]
                        colors_dict = {
                            "Funder Rate (YTD)": "#2EBDC2",
                            "WSNZ Target": "#356FB6",
                            "National Rate (YTD)": "#BBE6E9",
                        }
                        fig = r3.create_competency_report(
                            term=2,
                            year=2025,
                            funder_id=funder_id or 0,
                            rows=results,
                            vars_to_plot=vars_to_plot,
                            colors_dict=colors_dict,
                            funder_name=selected_funder_name
                        )

                    elif selected_type == "provider_ytd_vs_target":
                        # 1) Font (safe to skip if missing)
                        try:
                            use_ppmori("app/static/fonts")
                        except Exception as font_e:
                            print(f"⚠️ font setup skipped: {font_e}")

                        mode = "provider"  # always provider for this report

                        # 2) Resolve provider name robustly
                        provider_id_val = request.form.get("provider_id") or session.get("provider_id")
                        provider_id_val = int(provider_id_val) if provider_id_val not in (None, "", "None") else None

                        subject_name = (request.form.get("provider_name") or "").strip()
                        if not subject_name:
                            subject_name = (session.get("desc") or "").strip()
                        if not subject_name:
                            for r in results or []:
                                subj = r.get("ProviderName") or r.get("Provider") or r.get("ProviderDesc")
                                if subj:
                                    subject_name = str(subj).strip()
                                    break
                        if not subject_name:
                            subject_name = "Unknown Provider"

                        # 3) Detect provider-rate rows
                        def _is_provider_row(r):
                            return str(r.get("ResultType", "")).lower().startswith("provider rate")
                        provider_rows = [r for r in results if _is_provider_row(r)]

                        filtered_results = results
                        if not provider_rows:
                            print("⚠️ No 'provider rate' rows found in results for provider_ytd_vs_target")
                            no_data_banner = (
                                f"⚠️ No YTD provider data found for {subject_name} "
                                f"(Term {selected_term}, {selected_year}). Showing national/target series only."
                            )
                            filtered_results = [r for r in results if not _is_provider_row(r)]

                        # 4) Title
                        chart_title = f"{subject_name} — YTD vs Target (Term {selected_term}, {selected_year})"

                        # 5) Draw
                        fig = provider_portrait_with_target(
                            filtered_results,
                            term=selected_term,
                            year=selected_year,
                            mode=mode,
                            subject_name=subject_name,
                            title=chart_title,
                        )

                        # Optional: annotate banner on figure
                        if no_data_banner and fig is not None:
                            try:
                                import matplotlib.pyplot as plt  # ensure available
                                ax = fig.gca()
                                ax.annotate(
                                    no_data_banner,
                                    xy=(0.5, 1.02),
                                    xycoords="axes fraction",
                                    ha="center", va="bottom", fontsize=10,
                                    bbox=dict(boxstyle="round,pad=0.4", fc="#fff3cd", ec="#ffeeba")
                                )
                            except Exception as _e:
                                print(f"⚠️ Could not annotate no-data banner: {_e}")

                    elif selected_type == "funder_ytd_vs_target":
                        try:
                            use_ppmori("app/static/fonts")
                        except Exception as font_e:
                            print(f"⚠️ font setup skipped: {font_e}")

                        fig = provider_portrait_with_target(
                            results, term=selected_term, year=selected_year,
                            mode="funder", subject_name=selected_funder_name,
                            title=f"{selected_funder_name or 'Funder'} YTD vs Target"
                        )

                    # 🆕 School portrait (one var + target)
                    elif selected_type == "school_ytd_vs_national":
                        try:
                            use_ppmori("app/static/fonts")
                        except Exception as font_e:
                            print(f"⚠️ font setup skipped: {font_e}")

                        school_name = request.form.get("school_name") or next(
                            (r.get("SchoolName") for r in results if r.get("SchoolName")), None
                        )
                        fig = provider_portrait_with_target(
                            results,
                            term=selected_term,
                            year=selected_year,
                            mode="school",
                            subject_name=school_name,
                            title=f"{school_name or 'School'} YTD vs National"
                        )

                    else:
                        fig = r3.create_competency_report(
                            term=selected_term, year=selected_year, funder_id=funder_id or 0,
                            rows=results, vars_to_plot=r3.vars_to_plot, colors_dict=r3.colors_dict,
                            funder_name=selected_funder_name
                        )

                if fig is not None:
                    png_b64 = fig_to_png_b64(fig)
                    pdf_b64 = fig_to_pdf_b64(fig)
                    session["report_png_bytes"] = png_b64
                    session["report_png_filename"] = f"Report_{selected_type}_{selected_term}_{selected_year}.png"
                    session["report_pdf_bytes"] = pdf_b64
                    session["report_pdf_filename"] = f"Report_{selected_type}_{selected_term}_{selected_year}.pdf"
                    plot_png_b64 = png_b64
                    display = True

                # ===== AJAX response (no full reload) =====
                if is_ajax:
                    provider_name = request.form.get("provider_name")
                    school_name   = request.form.get("school_name")   # 🆕
                    provider_id   = request.form.get("provider_id")
                    school_id     = request.form.get("school_id")     # 🆕

                    left_bits = []
                    if selected_funder_name:
                        left_bits.append(selected_funder_name)
                    left_bits.append(f"Term {selected_term}, {selected_year}")

                    # prefer readable names over IDs
                    if selected_type == "school_ytd_vs_national" and (school_name or school_id):
                        left_bits.append(school_name or f"School MOE {school_id}")
                    elif provider_name:
                        left_bits.append(provider_name)
                    elif provider_id:
                        left_bits.append(f"Provider ID {provider_id}")

                    header_html = " • ".join(left_bits)

                    return jsonify({
                        "ok": True,
                        "plot_png_b64": plot_png_b64,
                        "header_html": header_html,
                        "display": True,
                        "notice": no_data_banner,   # 🆕 let frontend show a toast/banner if present
                    })

        except Exception as e:
            # Keep full details in logs/console
            print("❌ Error in /NewReporting (POST):", e)
            traceback.print_exc()

            # Inspect the underlying DB error text (SQLAlchemy wraps pyodbc errors)
            err_text = str(getattr(e, "orig", e))

            # Friendly message + status code
            if "Provider is not linked to the supplied FunderID" in err_text:
                user_msg = "You must select a provider and funder that are linked for this report"
                status_code = 400  # Bad Request (validation/config issue)
            else:
                user_msg = "An error occurred while generating the report."
                status_code = 500  # Generic server error

            if is_ajax:
                return jsonify({"ok": False, "error": user_msg, "display": False}), status_code

            flash(user_msg, "danger")
            return redirect(url_for("report_bp.new_reports"))

    # GET, or POST without show_report → just render the page; dropdowns loaded via AJAX
    return render_template(
        "reportingnew.html",
        role=role,
        funder_name=selected_funder_name,
        results=results,
        plot_payload=plot_payload,
        plot_png_b64=plot_png_b64,
        selected_term=selected_term,
        selected_year=selected_year,
        selected_type=selected_type,
        entities_url=url_for("funder_bp.get_entities"),
        display=display,
        no_data_banner=no_data_banner,  # 🆕 optional template banner
    )