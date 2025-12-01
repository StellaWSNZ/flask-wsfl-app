# Standard library
import base64
import io
import traceback
from base64 import b64decode
from datetime import date, datetime
import os 
# Third-party
import matplotlib
matplotlib.use("Agg")  # Safe backend for servers
import matplotlib.pyplot as plt
import pandas as pd
import pytz
from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
    current_app,
)
from sqlalchemy import text
from app.utils.funder_missing_plot import (create_funder_missing_figure,     add_full_width_footer,)


# App utilities
from app.utils.database import get_db_engine, log_alert
from app.utils.fundernationalplot import (
    create_competency_report as create_funder_report,
)
from app.utils.providerplot import (
    create_competency_report as create_provider_report,
)
from app.utils.competencyplot import (
    load_competency_rates,
    make_figure as create_comp_figure,
)
from app.utils.schoolplot import create_school_report
import app.utils.report_three_bar_landscape as r3  # Old fundernationalplot.py
import app.utils.report_two_bar_portrait as r2      # Old nationalplot.py
from app.utils.one_bar_one_line import (
    provider_portrait_with_target,
    use_ppmori,
)

# Routes / auth
from app.routes.auth import login_required
import uuid
from pathlib import Path

REPORT_DIR = Path("/tmp/wsfl_reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)
# Blueprint
report_bp = Blueprint("report_bp", __name__)


def get_available_terms(nearest_year, nearest_term):
    options = [(nearest_year, nearest_term)]
    if nearest_term > 1:
        options.append((nearest_year, nearest_term - 1))
    else:
        options.append((nearest_year - 1, 4))
    return options


# ---------- Download endpoints with log_alert ----------

@report_bp.route('/Reporting/download_pdf')
@login_required
def download_pdf():
    try:
        report_id = session.get("report_id")
        pdf_name  = session.get("report_pdf_filename") or "report.pdf"

        # No report generated this session
        if not report_id:
            flash("No PDF report has been generated yet.", "warning")
            return redirect(url_for("report_bp.new_reports"))

        pdf_path = REPORT_DIR / f"{report_id}.pdf"

        # File missing on disk (restart / cleanup / expired)
        if not pdf_path.exists():
            flash("The PDF report has expired. Please run the report again.", "warning")
            return redirect(url_for("report_bp.new_reports"))

        return send_file(
            pdf_path,
            download_name=pdf_name,
            as_attachment=True,
            mimetype="application/pdf",
        )

    except Exception as e:
        err_text = f"/Reporting/download_pdf failed: {e}\n{traceback.format_exc()}"
        print("❌ Error in /download_pdf:", e)
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=url_for("report_bp.download_pdf", _external=True),
            message=err_text[:4000],
        )
        flash("An error occurred while downloading the PDF.", "danger")
        return redirect(url_for("report_bp.new_reports"))



@report_bp.route('/Reporting/download_png')
@login_required
def download_png():
    try:
        report_id = session.get("report_id")
        if not report_id:
            flash("No PNG report has been generated yet.", "warning")
            return redirect(url_for("report_bp.new_reports"))

        png_name = session.get("report_png_filename") or "report.png"
        png_path = REPORT_DIR / f"{report_id}.png"

        if not png_path.exists():
            flash("Report image has expired. Please re-run the report.", "warning")
            return redirect(url_for("report_bp.new_reports"))

        return send_file(
            png_path,
            download_name=png_name,
            as_attachment=True,
                mimetype="image/png",

        )
    except Exception as e:
        err_text = f"/Reporting/download_png failed: {e}\n{traceback.format_exc()}"
        print("❌ Error in /download_png:", e)
        # ✅ log to DB
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=url_for("report_bp.download_png", _external=True),
            message=err_text[:4000],
        )
        flash("An error occurred while downloading the PNG.", "danger")
        return redirect(url_for("report_bp.new_reports"))

    


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
    if session.get("user_admin") != 1:
        return render_template(
            "error.html",
            error="You are not authorised to view that page.",
            code=403
        ), 403
    engine = get_db_engine()

    # --------- UI state (defaults shown in form) ----------
    selected_year  = None
    selected_term  = None
    selected_type  = None
    selected_funder_name = None

    # sticky IDs from POST if present
    selected_provider_id = request.form.get("provider_id") if request.method == "POST" else None
    selected_school_id   = request.form.get("school_id")   if request.method == "POST" else None  # 🆕
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
                        # ✅ log it
                        log_alert(
                            email=session.get("user_email"),
                            role=session.get("user_role"),
                            entity_id=None,
                            link=request.url,
                            message=f"/Reports: funder not found for name '{selected_funder_name}'"
                        )
                        if is_ajax:
                            return jsonify({"ok": False, "error": msg}), 400
                        flash(msg, "danger")
                        return redirect(url_for("report_bp.new_reports"))
                    funder_id = row[0] if not hasattr(row, "_mapping") else int(row._mapping.get("FunderID") or row[0])
                    print(f"🔑 resolved funder_id={funder_id}")
                if selected_type == "funder_missing_data" and not selected_funder_name:
                    msg = "Please choose a funder for the missing data report."
                    log_alert(
                        email=session.get("user_email"),
                        role=session.get("user_role"),
                        entity_id=None,
                        link=request.url,
                        message="/Reports: funder_missing_data selected but no funder chosen",
                    )
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
                # Do we need a provider?
                needs_provider = selected_type in {"provider_ytd_vs_target", "provider_ytd_vs_target_vs_funder"}
                if needs_provider and not selected_provider_id:
                    if role == "PRO":
                        selected_provider_id = session.get("id")
                    if not selected_provider_id:
                        msg = "Please choose a provider."
                        # ✅ log it (validation miss)
                        log_alert(
                            email=session.get("user_email"),
                            role=session.get("user_role"),
                            entity_id=funder_id,
                            link=request.url,
                            message="/Reports: provider required but missing for provider report"
                        )
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
                    # ✅ log it (validation miss)
                    log_alert(
                        email=session.get("user_email"),
                        role=session.get("user_role"),
                        entity_id=funder_id,
                        link=request.url,
                        message="/Reports: school required but missing for school report"
                    )
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
                fig = None

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
                elif selected_type == "funder_missing_data":
                    # threshold – keep same as your script for now
                    threshold = 0.5

                    # Pull the same data your standalone script uses
                    sql = text("""
                        SET NOCOUNT ON;
                        EXEC FlaskGetSchoolSummaryAllFunders
                            @CalendarYear = :CalendarYear,
                            @Term         = :Term,
                            @Threshold    = :Threshold,
                            @Email        = :Email;
                    """)

                    params = {
                        "CalendarYear": selected_year,
                        "Term": selected_term,
                        "Threshold": threshold,
                        "Email": session.get("user_email")  # fix: your session key is user_email, not email
                    }

                    res = conn.execute(sql, params)
                    rows = res.mappings().all()
                    results = rows

                    # Convert to DataFrame
                    import pandas as pd
                    df_all = pd.DataFrame(rows)

                    # ---- FILTER TO THIS FUNDER ----
                    if selected_funder_name:
                        df_funder = df_all[df_all["FunderName"] == selected_funder_name].copy()
                    else:
                        df_funder = df_all.copy()  # fallback – probably not needed

                    # If no rows, return friendly error
                    if df_funder.empty:
                        msg = f"No data found for funder: {selected_funder_name}"
                        if is_ajax:
                            return jsonify({"ok": False, "error": msg}), 400
                        flash(msg, "warning")
                        return redirect(url_for("report_bp.new_reports"))

                    # ---- Build the figure ----
                    fig = create_funder_missing_figure(
                        df_all=df_funder,                 # <--- THIS is the correct filtered frame
                        funder_name=selected_funder_name,
                        term=selected_term,
                        calendaryear=selected_year,
                        threshold=threshold,
                    )
                    try:
                        footer_png = os.path.join(current_app.static_folder, "footer.png")
                        add_full_width_footer(
                            fig,
                            footer_png,
                            bottom_margin_frac=0.0,   # tweak if it clashes with axes
                            max_footer_height_frac=0.20,
                        )
                    except Exception as footer_e:
                        print(f"⚠ Could not add footer to funder_missing_data figure: {footer_e}")

                elif selected_type == "ly_funder_vs_ly_national_vs_target":
                    ly = selected_year - 1  # (kept for clarity; proc uses constants below)
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

                # 🆕 SCHOOL: YTD vs National
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
                    # ✅ log it
                    log_alert(
                        email=session.get("user_email"),
                        role=session.get("user_role"),
                        entity_id=funder_id,
                        link=request.url,
                        message=f"/Reports: invalid report_option '{selected_type}'"
                    )
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
                if results and fig is None:

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
                        try:
                            use_ppmori("app/static/fonts")
                        except Exception as font_e:
                            print(f"⚠️ font setup skipped: {font_e}")

                        mode = "provider"  # always provider for this report

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

                        chart_title = f"{subject_name} — YTD vs Target (Term {selected_term}, {selected_year})"

                        fig = provider_portrait_with_target(
                            filtered_results,
                            term=selected_term,
                            year=selected_year,
                            mode=mode,
                            subject_name=subject_name,
                            title=chart_title,
                        )

                        if no_data_banner and fig is not None:
                            try:
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
                    report_id = uuid.uuid4().hex

                    png_path = REPORT_DIR / f"{report_id}.png"
                    pdf_path = REPORT_DIR / f"{report_id}.pdf"

                    # save *once* per format
                    fig.savefig(png_path, format="png", dpi=200)
                    fig.savefig(pdf_path, format="pdf")
                    plt.close(fig)

                    session["report_id"] = report_id
                    session["report_png_filename"] = f"Report_{selected_type}_{selected_term}_{selected_year}.png"
                    session["report_pdf_filename"] = f"Report_{selected_type}_{selected_term}_{selected_year}.pdf"

                    plot_png_b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")  # for inline display only
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

            # ✅ log to AUD_Alerts (with underlying DB error text if present)
            err_text = str(getattr(e, "orig", e))
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=None,
                link=request.url,
                message=f"/Reports error: {err_text}"
            )

            # Friendly message + status code (unchanged behavior)
            if "Provider is not linked to the supplied FunderID" in err_text:
                user_msg = "You must select a provider and funder that are linked for this report"
                status_code = 400
            else:
                user_msg = "An error occurred while generating the report."
                status_code = 500

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