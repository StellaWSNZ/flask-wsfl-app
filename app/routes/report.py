# Standard library
import base64
import os
import re
import traceback
from datetime import datetime  # or drop entirely if unused here
from pathlib import Path
import uuid

# Third-party
import matplotlib
matplotlib.use("Agg")  # Safe backend for servers
import matplotlib.pyplot as plt
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import text

# App utilities
from app.utils.database import get_db_engine, log_alert
from app.utils.funder_missing_plot import (
    add_full_width_footer_svg,
    create_funder_missing_figure,
)
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
import app.utils.report_three_bar_landscape as r3
import app.utils.report_two_bar_portrait as r2
from app.utils.one_bar_one_line import provider_portrait_with_target, use_ppmori
from app.routes.auth import login_required

REPORT_DIR = Path("/tmp/wsfl_reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Blueprint
report_bp = Blueprint("report_bp", __name__)


def slugify_filename(label: str, fallback: str = "report") -> str:
    """
    Turn a human label into a filesystem-safe filename chunk.
    """
    label = (label or "").strip()
    if not label:
        return fallback
    label = label.replace("&", "and")
    label = re.sub(r"[^A-Za-z0-9\-_]+", "_", label)
    label = re.sub(r"_+", "_", label).strip("_")
    return label or fallback


def get_available_terms(nearest_year, nearest_term):
    options = [(nearest_year, nearest_term)]
    if nearest_term > 1:
        options.append((nearest_year, nearest_term - 1))
    else:
        options.append((nearest_year - 1, 4))
    return options


# ---------- Download endpoints with log_alert ----------


@report_bp.route("/Reporting/download_pdf")
@login_required
def download_pdf():
    try:
        report_id = session.get("report_id")
        pdf_name = session.get("report_pdf_filename") or "report.pdf"

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


@report_bp.route("/Reporting/download_png")
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


def _get_form_defaults():
    """
    Get year, term, and selected report type.

    - On GET: defaults come from session (nearest term/year)
    - On POST: overrides come from the submitted form
    """
    if request.method == "POST":
        year = int(request.form.get("year", session.get("nearest_year")))
        term = int(request.form.get("term", session.get("nearest_term")))
        report_type = request.form.get("report_option")
    else:
        year = session.get("nearest_year")
        term = session.get("nearest_term")
        report_type = None

    return year, term, report_type


def _get_sticky_ids():
    """
    Return (provider_id, school_id) from the form if POST,
    otherwise (None, None).
    """
    if request.method == "POST":
        provider_id = request.form.get("provider_id") or None
        school_id = request.form.get("school_id") or None
        return provider_id, school_id
    return None, None


def _get_funder_name_from_role_or_form(role: str) -> str | None:
    """
    Decide which funder name to use based on role and request.

    - FUN: funder name comes from session.desc
    - ADM: funder name comes from the 'selected_funder' dropdown (on POST)
    - PRO: no funder name (None)
    """
    # FUN sees their own funder from the session
    if role == "FUN":
        return session.get("desc")

    # ADM chooses from dropdown (only meaningful on POST)
    if role == "ADM" and request.method == "POST":
        return request.form.get("selected_funder") or None

    # PRO and others: no selected_funder_name
    return None


def _get_request_type():
    """
    Return (is_ajax, action) based on the current request.

    - action comes from request.form["action"]
    - is_ajax is True if:
        * ajax=1 in the form, OR
        * X-Requested-With == 'fetch'
    """
    action = request.form.get("action")

    is_ajax = (
        request.form.get("ajax") == "1"
        or request.headers.get("X-Requested-With") == "fetch"
    )

    return is_ajax, action


def _validate_required_entities(
    selected_type,
    role,
    selected_provider_id,
    selected_school_id,
    funder_id,
    is_ajax,
    selected_term,
    selected_year,
):
    """
    Validate that required entities (provider/funder/school) exist
    for the selected report type.

    Returns a Flask response if validation fails.
    Returns None if validation passes.
    """
    # ---- Provider required ----
    needs_provider = selected_type in {
        "provider_ytd_vs_target",
        "provider_ytd_vs_target_vs_funder",
    }

    if needs_provider and not selected_provider_id:
        msg = "Please choose a provider."

        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=request.url,
            message="/Reports: provider required but missing",
        )

        if is_ajax:
            return jsonify({"ok": False, "error": msg}), 400

        flash(msg, "warning")
        return render_template(
            "reportingnew.html",
            role=role,
            funder_name=session.get("desc"),
            results=None,
            plot_payload=None,
            plot_png_b64=None,
            selected_term=selected_term,
            selected_year=selected_year,
            selected_type=selected_type,
            entities_url=url_for("api_bp.get_entities"),
            display=False,
            no_data_banner=None,
        )

    # ---- Funder required ----
    # (funder_missing_data uses funder *name*, so it is handled separately)
    needs_funder = selected_type in {
        "funder_ytd_vs_target",
        "ly_funder_vs_ly_national_vs_target",
        "provider_ytd_vs_target_vs_funder",
    }

    if needs_funder and not funder_id:
        msg = "Please choose a funder."

        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=request.url,
            message="/Reports: funder required but missing",
        )

        if is_ajax:
            return jsonify({"ok": False, "error": msg}), 400

        flash(msg, "warning")
        return render_template(
            "reportingnew.html",
            role=role,
            funder_name=session.get("desc"),
            results=None,
            plot_payload=None,
            plot_png_b64=None,
            selected_term=selected_term,
            selected_year=selected_year,
            selected_type=selected_type,
            entities_url=url_for("api_bp.get_entities"),
            display=False,
            no_data_banner=None,
        )

    # ---- School required ----
    needs_school = selected_type in {"school_ytd_vs_target"}

    if needs_school and not selected_school_id:
        msg = "Please choose a school."

        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=request.url,
            message="/Reports: school required but missing",
        )

        if is_ajax:
            return jsonify({"ok": False, "error": msg}), 400

        flash(msg, "warning")
        return render_template(
            "reportingnew.html",
            role=role,
            funder_name=session.get("desc"),
            results=None,
            plot_payload=None,
            plot_png_b64=None,
            selected_term=selected_term,
            selected_year=selected_year,
            selected_type=selected_type,
            entities_url=url_for("api_bp.get_entities"),
            display=False,
            no_data_banner=None,
        )

    # ✅ Everything required is present
    return None


def _execute_report(
    conn,
    selected_type,
    selected_year,
    selected_term,
    role,
    funder_id,
    selected_provider_id,
    selected_school_id,
    selected_funder_name,
    is_ajax,
):
    """
    Run the appropriate SQL/stored procedure and return:
        results, fig, no_data_banner, early_response

    - results: list of row dicts / RowMapping (or None)
    - fig: a Matplotlib figure if already built (e.g. funder_missing_data)
    - no_data_banner: optional string for the plot overlay
    - early_response: a Flask response (redirect/json) if we already
      handled an error condition (e.g. no rows for funder_missing_data)
    """
    results = None
    fig = None
    no_data_banner = None
    early_response = None

    # 1) Funder YTD vs Target (data only)
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
                r
                for r in rows
                if (int(r.get("FunderID", 0) or 0) == funder_id)
                or (r.get("ResultType") == "WSNZ Target")
            ]
            if not funder_rows:
                unique_comps = {
                    (
                        r["CompetencyID"],
                        r["CompetencyDesc"],
                        r["YearGroupID"],
                        r["YearGroupDesc"],
                    )
                    for r in rows
                }
                funder_rows = []
                for cid, cdesc, yid, ydesc in unique_comps:
                    funder_rows.append(
                        {
                            "FunderID": funder_id,
                            "CompetencyID": cid,
                            "CompetencyDesc": cdesc,
                            "YearGroupID": yid,
                            "YearGroupDesc": ydesc,
                            "ResultType": "Funder Rate (YTD)",
                            "Rate": 0,
                            "StudentCount": 0,
                        }
                    )
                    funder_rows.append(
                        {
                            "FunderID": funder_id,
                            "CompetencyID": cid,
                            "CompetencyDesc": cdesc,
                            "YearGroupID": yid,
                            "YearGroupDesc": ydesc,
                            "ResultType": "Funder Student Count (YTD)",
                            "Rate": 0,
                            "StudentCount": 0,
                        }
                    )
            results = funder_rows
        else:
            results = rows

        print("🔎 rows:", len(results))

    # 2) National LY vs National YTD vs Target
    elif selected_type == "national_ly_vs_national_ytd_vs_target":
        sql = text("""
            SET NOCOUNT ON;
            EXEC GetNationalRates
                @CalendarYear = :CalendarYear,
                @Term         = :Term;
        """)
        params = {
            "CalendarYear": selected_year,
            "Term": selected_term,
        }
        res = conn.execute(sql, params)
        results = res.mappings().all()

    # 3) Funder Missing Data (builds fig here)
    elif selected_type == "funder_missing_data":
        threshold = 0.5
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
            "Email": session.get("user_email"),
        }
        res = conn.execute(sql, params)
        rows = res.mappings().all()
        results = rows

        import pandas as pd

        df_all = pd.DataFrame(rows)

        if selected_funder_name:
            df_funder = df_all[df_all["FunderName"] == selected_funder_name].copy()
        else:
            df_funder = df_all.copy()

        if df_funder.empty:
            msg = f"No data found for funder: {selected_funder_name}"
            if is_ajax:
                early_response = (jsonify({"ok": False, "error": msg}), 400)
            else:
                flash(msg, "warning")
                early_response = redirect(url_for("report_bp.new_reports"))
            return results, fig, no_data_banner, early_response

        fig = create_funder_missing_figure(
            df_all=df_funder,
            funder_name=selected_funder_name,
            term=selected_term,
            calendaryear=selected_year,
            threshold=threshold,
            debug=False,
        )
        try:
            footer_png = os.path.join(current_app.static_folder, "footer.svg")
            add_full_width_footer_svg(
                fig,
                footer_png,
                bottom_margin_frac=0.0,
                max_footer_height_frac=0.20,
            )
        except Exception as footer_e:
            print(f"⚠ Could not add footer to funder_missing_data figure: {footer_e}")

    # 4) Funder LY vs National LY vs Target (data only)
    elif selected_type == "ly_funder_vs_ly_national_vs_target":
        sql = text("""
            SET NOCOUNT ON;
            EXEC dbo.GetFunderNationalRates_All
                @Term = :Term,
                @CalendarYear = :CalendarYear;
        """)
        # NOTE: you may want to parameterise this later
        params = {"Term": 2, "CalendarYear": 2025}
        res = conn.execute(sql, params)

        raw_rows = res.mappings().all()
        filtered_rows = []
        for r in raw_rows:
            funder_matches = (
                not funder_id or int(r.get("FunderID", 0) or 0) == funder_id
            )
            keep = (
                (funder_matches and r.get("ResultType") == "Funder Rate (YTD)")
                or r.get("ResultType") == "WSNZ Target"
                or r.get("ResultType") == "National Rate (YTD)"
            )
            if not keep:
                continue

            d = dict(r)
            if d.get("ResultType") == "National Rate (YTD)":
                d["ResultType"] = "National Rate (LY)"
            elif d.get("ResultType") == "Funder Rate (YTD)":
                d["ResultType"] = "Funder Rate (LY)"

            filtered_rows.append(d)

        results = filtered_rows

    # 5) Provider vs Funder (data only)
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
            "FunderID": int(funder_id) if funder_id is not None else None,
        }
        res = conn.execute(sql, params)
        rows = res.mappings().all()

        if len(rows) == 0:
            res2 = conn.exec_driver_sql(
                "SET NOCOUNT ON; EXEC dbo.GetProviderNationalRates @Term=?, @CalendarYear=?, @ProviderID=?, @FunderID=?",
                (
                    selected_term,
                    selected_year,
                    int(selected_provider_id),
                    funder_id if funder_id is not None else None,
                ),
            )
            if getattr(res2, "cursor", None) and res2.cursor.description:
                cols = [d[0] for d in res2.cursor.description]
                rows = [dict(zip(cols, row)) for row in res2.fetchall()]
        results = rows

    # 6) Provider YTD vs Target (data only)
    elif selected_type == "provider_ytd_vs_target":
        if role == "ADM":
            funder_id = None
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
            "FunderID": int(funder_id) if funder_id is not None else None,
        }
        res = conn.execute(sql, params)
        rows = res.mappings().all()

        print(f"🧪 Rows fetched: {len(rows)}")
        if rows:
            first = rows[0]
            print("🧪 First row keys:", list(first.keys()))
            print("🧪 Distinct ResultTypes:", {r.get("ResultType") for r in rows})
        else:
            print("⚠️ Stored procedure returned 0 rows for these params.")

        results = rows

    # 7) School YTD vs Target (data only)
    elif selected_type == "school_ytd_vs_target":
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
            "MoeNumber": int(selected_school_id),
        }
        print(f"📥 SQL params (school): {params}")
        res = conn.execute(sql, params)
        results = res.mappings().all()

    else:
        # Invalid option — let caller handle as a normal error
        results = None

    return results, fig, no_data_banner, early_response


def _build_figure_from_results(
    selected_type,
    results,
    selected_term,
    selected_year,
    funder_id,
    selected_funder_name,
    selected_provider_id,
    selected_school_id,
):
    """
    Build a Matplotlib figure from the result rows.

    Returns (fig, no_data_banner).
    """
    fig = None
    no_data_banner = None

    if not results:
        return None, None

    # Provider vs Funder
    if selected_type == "provider_ytd_vs_target_vs_funder":
        vars_to_plot = ["Provider Rate (YTD)", "Funder Rate (YTD)", "WSNZ Target"]
        colors_dict = {
            "Provider Rate (YTD)": "#2EBDC2",
            "WSNZ Target": "#356FB6",
            "Funder Rate (YTD)": "#BBE6E9",
        }

        print("DEBUG ResultTypes in results:")
        print(sorted(set(r.get("ResultType") for r in results if r.get("ResultType"))))

        provider_display_name = (
            request.form.get("provider_name")
            or next(
                (
                    r.get("ProviderName") or r.get("Provider")
                    for r in (results or [])
                    if r.get("ProviderName") or r.get("Provider")
                ),
                None,
            )
            or f"Provider {selected_provider_id}"
        )
        funder_display_name = selected_funder_name or "Funder"
        title_text = f"{provider_display_name} & {funder_display_name}"

        fig = r3.create_competency_report(
            term=selected_term,
            year=selected_year,
            funder_id=funder_id,
            rows=results,
            vars_to_plot=vars_to_plot,
            colors_dict=colors_dict,
            funder_name=title_text,
        )

    # LY Funder vs LY National vs Target
    elif selected_type == "ly_funder_vs_ly_national_vs_target":
        vars_to_plot = ["National Rate (LY)", "Funder Rate (LY)", "WSNZ Target"]
        colors_dict = {
            "Funder Rate (LY)": "#2EBDC2",
            "WSNZ Target": "#356FB6",
            "National Rate (LY)": "#BBE6E9",
        }
        fig = r3.create_competency_report(
            term=selected_term,
            year=selected_year,
            funder_id=funder_id or 0,
            rows=results,
            vars_to_plot=vars_to_plot,
            colors_dict=colors_dict,
            funder_name=selected_funder_name,
        )

    # National LY vs National YTD vs Target
    elif selected_type == "national_ly_vs_national_ytd_vs_target":
        vars_to_plot = ["National Rate (LY)", "National Rate (YTD)", "WSNZ Target"]
        colors_dict = {
            "National Rate (YTD)": "#2EBDC2",
            "WSNZ Target": "#356FB6",
            "National Rate (LY)": "#BBE6E9",
        }
        fig = r3.create_competency_report(
            term=selected_term,
            year=selected_year,
            rows=results,
            vars_to_plot=vars_to_plot,
            colors_dict=colors_dict,
            funder_id=None,
        )
        
    # Provider portrait (YTD vs Target)
    elif selected_type == "provider_ytd_vs_target":
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            print(f"⚠️ font setup skipped: {font_e}")

        mode = "provider"

        provider_id_val = request.form.get("provider_id") or session.get("provider_id")
        provider_id_val = (
            int(provider_id_val)
            if provider_id_val not in (None, "", "None")
            else None
        )

        subject_name = (request.form.get("provider_name") or "").strip()
        if not subject_name:
            subject_name = (session.get("desc") or "").strip()
        if not subject_name:
            for r in results or []:
                subj = (
                    r.get("ProviderName")
                    or r.get("Provider")
                    or r.get("ProviderDesc")
                )
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
            print(
                "⚠️ No 'provider rate' rows found in results for provider_ytd_vs_target"
            )
            no_data_banner = (
                f"No YTD provider data found for {subject_name} "
                f"(Term {selected_term}, {selected_year}). Showing national/target series only."
            )
            filtered_results = [r for r in results if not _is_provider_row(r)]

        chart_title = (
            f"{subject_name} — YTD vs Target (Term {selected_term}, {selected_year})"
        )

        fig = provider_portrait_with_target(
            filtered_results,
            term=selected_term,
            year=selected_year,
            mode=mode,
            subject_name=subject_name,
            title=chart_title,
        )

        # If we have a warning, overlay the banner
        if no_data_banner and fig is not None:
            try:
                ax = fig.gca()
                ax.annotate(
                    no_data_banner,
                    xy=(0.5, 1.02),
                    xycoords="axes fraction",
                    ha="center",
                    va="bottom",
                    fontsize=10,
                    fontweight="bold",
                    color="white",
                    bbox=dict(
                        boxstyle="round,pad=0.4",
                        fc="#1a427d",  # dark blue fill
                        ec="#1a427d",  # matching border
                    ),
                )
            except Exception as _e:
                print(f"⚠️ Could not annotate no-data banner: {_e}")
        footer_png = os.path.join(current_app.static_folder, "footer.svg")
        add_full_width_footer_svg(
            fig,
            footer_png,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master="#1a427d40"
        )
    # Funder portrait (YTD vs Target)
    elif selected_type == "funder_ytd_vs_target":
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            print(f"⚠️ font setup skipped: {font_e}")

        fig = provider_portrait_with_target(
            results,
            term=selected_term,
            year=selected_year,
            mode="funder",
            subject_name=selected_funder_name,
            title=f"{selected_funder_name or 'Funder'} YTD vs Target",
        )
        footer_png = os.path.join(current_app.static_folder, "footer.svg")
        add_full_width_footer_svg(
            fig,
            footer_png,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master="#1a427d40"
        )
    # School portrait (YTD vs Target)
    elif selected_type == "school_ytd_vs_target":
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            print(f"⚠️ font setup skipped: {font_e}")

        school_name = request.form.get("school_name") or next(
            (r.get("SchoolName") for r in results if r.get("SchoolName")),
            None,
        )

        fig = provider_portrait_with_target(
            results,
            term=selected_term,
            year=selected_year,
            mode="school",
            subject_name=school_name,
            title=f"{school_name or 'School'} YTD vs WSNZ Target",
        )
        footer_png = os.path.join(current_app.static_folder, "footer.svg")
        add_full_width_footer_svg(
            fig,
            footer_png,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master="#1a427d40"
        )

    # Default: use three-bar landscape report logic
    else:
        fig = r3.create_competency_report(
            term=selected_term,
            year=selected_year,
            funder_id=funder_id or 0,
            rows=results,
            vars_to_plot=r3.vars_to_plot,
            colors_dict=r3.colors_dict,
            funder_name=selected_funder_name,
        )

    return fig, no_data_banner


def _persist_figure_and_session(
    fig,
    selected_type,
    selected_term,
    selected_year,
    selected_funder_name,
    selected_provider_id,
    selected_school_id,
    results,
):
    report_id = uuid.uuid4().hex

    png_path = REPORT_DIR / f"{report_id}.png"
    pdf_path = REPORT_DIR / f"{report_id}.pdf"

    fig.savefig(png_path, format="png", dpi=200)
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)

    provider_name = (request.form.get("provider_name") or "").strip()
    school_name = (request.form.get("school_name") or "").strip()

    if not provider_name and results:
        provider_name = next(
            (
                (r.get("ProviderName") or r.get("Provider") or r.get("ProviderDesc"))
                for r in results
                if r.get("ProviderName") or r.get("Provider") or r.get("ProviderDesc")
            ),
            "",
        )

    if not school_name and results:
        school_name = next(
            (r.get("SchoolName") for r in results if r.get("SchoolName")),
            "",
        )

    if selected_type == "funder_missing_data":
        base_label = f"MissingData_{selected_funder_name or 'Funder'}"
    elif selected_type == "funder_ytd_vs_target":
        base_label = f"FunderYTDvsTarget_{selected_funder_name or 'Funder'}"
    elif selected_type == "ly_funder_vs_ly_national_vs_target":
        base_label = (
            f"FunderLY_vs_National_vs_Target_{selected_funder_name or 'Funder'}"
        )
    elif selected_type == "provider_ytd_vs_target_vs_funder":
        base_label = (
            f"ProviderVsFunder_{provider_name or 'Provider'}_"
            f"{selected_funder_name or 'Funder'}"
        )
    elif selected_type == "provider_ytd_vs_target":
        base_label = f"ProviderYTDvsTarget_{provider_name or 'Provider'}"
    elif selected_type == "school_ytd_vs_target":
        base_label = f"SchoolYTDvsTarget_{school_name or f'MOE_{selected_school_id}'}"
    elif selected_type == "national_ly_vs_national_ytd_vs_target":
        base_label = "NationalLYvsNationalYTDvsTarget"
    else:
        base_label = f"Report_{selected_type or 'Unknown'}"

    base_label = slugify_filename(base_label, fallback="WSFL_Report")
    base_label = f"{base_label}_T{selected_term}_{selected_year}"

    session["report_id"] = report_id
    session["report_png_filename"] = f"{base_label}.png"
    session["report_pdf_filename"] = f"{base_label}.pdf"

    plot_png_b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    return plot_png_b64


@report_bp.route("/Reports", methods=["GET", "POST"])
@login_required
def new_reports():
    role = session.get("user_role")  # "ADM", "FUN", or "PRO"
    print(f"🔑 Session role: {role}")

    if session.get("user_admin") != 1:
        return (
            render_template(
                "error.html",
                error="You are not authorised to view that page.",
                code=403,
            ),
            403,
        )

    engine = get_db_engine()

    # --------- UI state (defaults shown in form) ----------
    selected_year, selected_term, selected_type = _get_form_defaults()
    selected_provider_id, selected_school_id = _get_sticky_ids()

    # FUN sees their funder in the UI; providers list is loaded via AJAX using that name
    selected_funder_name = _get_funder_name_from_role_or_form(role)

    if role == "PRO":
        selected_provider_id = selected_provider_id or session.get("id")
        print(f"PRO effective provider_id: {selected_provider_id}")
    elif role == "FUN":
        print(f"FUN default funder_name (session.desc): {selected_funder_name}")

    print(
        f"📥 initial provider_id from POST: {selected_provider_id} "
        f"(type {type(selected_provider_id)})"
    )
    print(
        f"📥 initial school_id from POST: {selected_school_id} "
        f"(type {type(selected_school_id)})"
    )

    results = None
    plot_payload = None
    plot_png_b64 = None
    no_data_banner = None
    display = False

    is_ajax, action = _get_request_type()

    if request.method == "POST" and action == "show_report":
        print("📩 POST detected (show_report)")
        try:
            with engine.connect() as conn:
                print(
                    f"📥 POST params: year={selected_year}, "
                    f"term={selected_term}, type={selected_type}"
                )

                # Resolve funder from ADM dropdown (FUN is implied from session, PRO has none)
                print(request.form)
                if role == "ADM":
                    selected_funder_name = request.form.get("funder_name") or None
                print(f"effective funder_name: {selected_funder_name}")
                print(selected_funder_name)
                if(selected_funder_name=="Loading funders…"):
                    selected_funder_name= None
                funder_id = None
                provider_id = None
                school_id = None

                # If we have a funder *name*, resolve it to an ID
                if selected_funder_name:
                    row = conn.execute(
                        text(
                            "EXEC FlaskHelperFunctions "
                            "@Request='FunderIDDescription', @Text=:t"
                        ),
                        {"t": selected_funder_name},
                    ).fetchone()

                    if not row:
                        msg = "Funder not found."
                        # ✅ log it
                        log_alert(
                            email=session.get("user_email"),
                            role=session.get("user_role"),
                            entity_id=None,
                            link=request.url,
                            message=(
                                "/Reports: funder not found "
                                f"for name '{selected_funder_name}'"
                            ),
                        )
                        if is_ajax:
                            return jsonify({"ok": False, "error": msg}), 400

                        flash(msg, "danger")
                        return redirect(url_for("report_bp.new_reports"))

                    funder_id = (
                        row[0]
                        if not hasattr(row, "_mapping")
                        else int(row._mapping.get("FunderID") or row[0])
                    )
                    print(f"🔑 resolved funder_id={funder_id}")

                # ---- Special case: funder_missing_data needs a funder *name* ----
                if selected_type == "funder_missing_data" and not selected_funder_name:
                    msg = "Please choose a funder for the missing data report."
                    log_alert(
                        email=session.get("user_email"),
                        role=session.get("user_role"),
                        entity_id=None,
                        link=request.url,
                        message=(
                            "/Reports: funder_missing_data selected "
                            "but no funder chosen"
                        ),
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
                        entities_url=url_for("api_bp.get_entities"),
                        display=False,
                        no_data_banner=None,
                    )

                # ---- Generic validation for provider / funder / school ----
                validation_response = _validate_required_entities(
                    selected_type=selected_type,
                    role=role,
                    selected_provider_id=selected_provider_id,
                    selected_school_id=selected_school_id,
                    funder_id=funder_id,
                    is_ajax=is_ajax,
                    selected_term=selected_term,
                    selected_year=selected_year,
                )
                if validation_response:
                    return validation_response

                fig = None

                # ===== Execute the selected report =====
                print(f"▶ executing report type: {selected_type}")

                results, fig, no_data_banner_inner, early = _execute_report(
                    conn=conn,
                    selected_type=selected_type,
                    selected_year=selected_year,
                    selected_term=selected_term,
                    role=role,
                    funder_id=funder_id,
                    selected_provider_id=selected_provider_id,
                    selected_school_id=selected_school_id,
                    selected_funder_name=selected_funder_name,
                    is_ajax=is_ajax,
                )
                if early:
                    return early

                no_data_banner = no_data_banner_inner

                plot_payload = {
                    "year": selected_year,
                    "term": selected_term,
                    "type": selected_type,
                    "funder_id": funder_id,
                    "provider_id": (
                        int(selected_provider_id)
                        if selected_provider_id
                        else None
                    ),
                    "school_id": (
                        int(selected_school_id) if selected_school_id else None
                    ),
                    "rows": results,
                }

                if results and fig is None:
                    fig, extra_banner = _build_figure_from_results(
                        selected_type,
                        results,
                        selected_term,
                        selected_year,
                        funder_id,
                        selected_funder_name,
                        selected_provider_id,
                        selected_school_id,
                    )
                    if extra_banner:
                        no_data_banner = extra_banner

                if fig is not None:
                    plot_png_b64 = _persist_figure_and_session(
                        fig,
                        selected_type,
                        selected_term,
                        selected_year,
                        selected_funder_name,
                        selected_provider_id,
                        selected_school_id,
                        results,
                    )
                    display = True

                # ===== AJAX response (no full reload) =====
                if is_ajax:
                    provider_name = request.form.get("provider_name")
                    school_name = request.form.get("school_name")
                    provider_id = request.form.get("provider_id")
                    school_id = request.form.get("school_id")

                    left_bits = []
                    if selected_funder_name:
                        left_bits.append(selected_funder_name)
                    left_bits.append(f"Term {selected_term}, {selected_year}")

                    if (
                        selected_type == "school_ytd_vs_target"
                        and (school_name or school_id)
                    ):
                        left_bits.append(school_name or f"School MOE {school_id}")
                    elif provider_name:
                        left_bits.append(provider_name)
                    elif provider_id:
                        left_bits.append(f"Provider ID {provider_id}")

                    header_html = " • ".join(left_bits)

                    return jsonify(
                        {
                            "ok": True,
                            "plot_png_b64": plot_png_b64,
                            "header_html": header_html,
                            "display": True,
                            "notice": no_data_banner,
                        }
                    )

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
                message=f"/Reports error: {err_text}",
            )

            # Friendly message + status code (unchanged behavior)
            if "Provider is not linked to the supplied FunderID" in err_text:
                user_msg = (
                    "You must select a provider and funder that are linked "
                    "for this report"
                )
                status_code = 400
            else:
                user_msg = "An error occurred while generating the report."
                status_code = 500

            if is_ajax:
                return (
                    jsonify(
                        {"ok": False, "error": user_msg, "display": False}
                    ),
                    status_code,
                )

            flash(user_msg, "danger")
            return redirect(url_for("report_bp.new_reports"))

    # Post-sort for stable ordering
    if results and isinstance(results, list) and "CompetencyDesc" in results[0]:
        results = sorted(
            results,
            key=lambda x: (x.get("CompetencyDesc") or ""),
        )

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
        entities_url=url_for("api_bp.get_entities"),
        display=display,
        no_data_banner=no_data_banner,
    )
