# report.py

# Standard library
import base64
import os
import re
import traceback
import uuid
import pandas as pd

from pathlib import Path

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
from app.routes.auth import login_required
from app.utils.database import get_db_engine, log_alert
from app.utils.funder_missing_plot import (
    add_full_width_footer_svg,
    create_funder_missing_figure,
)
from app.utils.one_bar_one_line import provider_portrait_with_target, use_ppmori

import app.utils.report_three_bar_landscape as r3  # kept for other report types

REPORT_DIR = Path("/tmp/wsfl_reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Blueprint
report_bp = Blueprint("report_bp", __name__)


# ----------------------------
# Logging helpers
# ----------------------------
def _user_ctx():
    """Common fields for structured-ish logs (kept simple)."""
    return {
        "user": session.get("user_email"),
        "role": session.get("user_role"),
        "admin": session.get("user_admin"),
        "path": request.path if request else None,
    }


def _safe_form_keys():
    """Avoid logging PII-heavy form contents; keys are enough for debugging."""
    try:
        return sorted(list(request.form.keys()))
    except Exception:
        return []


# ----------------------------
# Small helpers
# ----------------------------
def slugify_filename(label: str, fallback: str = "report") -> str:
    """Turn a human label into a filesystem-safe filename chunk."""
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
        current_app.logger.exception("❌ Error in /Reporting/download_pdf")
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=None,
                link=url_for("report_bp.download_pdf", _external=True),
                message=err_text[:4000],
            )
        except Exception:
            pass
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
        current_app.logger.exception("❌ Error in /Reporting/download_png")
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=None,
                link=url_for("report_bp.download_png", _external=True),
                message=err_text[:4000],
            )
        except Exception:
            pass
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
def _persist_preview_for_existing_report(
    *,
    report_id: str,
    fig,
    selected_term: int,
    selected_year: int,
    selected_funder_name: str | None,
    base_label_prefix: str,  # <-- NEW
):
    png_path = REPORT_DIR / f"{report_id}.png"
    fig.savefig(png_path, format="png", dpi=200)
    plt.close(fig)

    funder_chunk = slugify_filename(selected_funder_name or "Funder")
    base_label = f"{base_label_prefix}_{funder_chunk}"

    session["report_id"] = report_id
    session["report_png_filename"] = f"{base_label}.png"
    session["report_pdf_filename"] = f"{base_label}.pdf"

    return base64.b64encode(png_path.read_bytes()).decode("ascii")

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
    if role == "FUN":
        return session.get("desc")

    if role == "ADM" and request.method == "POST":
        return request.form.get("selected_funder") or None

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

        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message="/Reports: provider required but missing",
            )
        except Exception:
            pass

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
    needs_funder = selected_type in {
        "funder_ytd_vs_target",
        "ly_funder_vs_ly_national_vs_target",
        "provider_ytd_vs_target_vs_funder",
        "funder_missing_data",
        "funder_student_count",
        "funder_progress_summary",
    }

    if needs_funder and not funder_id:
        msg = "Please choose a funder."

        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message="/Reports: funder required but missing",
            )
        except Exception:
            pass

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

        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=request.url,
                message="/Reports: school required but missing",
            )
        except Exception:
            pass

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
    """
    results = None
    fig = None
    no_data_banner = None
    early_response = None

    # 1) Funder YTD vs Target (data only)
    if selected_type == "funder_ytd_vs_target":
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC dbo.GetFunderNationalRates_All
                 @Term = :Term,
                 @CalendarYear = :CalendarYear;
        """
        )
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

        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)

    # 2) National LY vs National YTD vs Target
    elif selected_type == "national_ly_vs_national_ytd_vs_target":
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC GetNationalRates
                @CalendarYear = :CalendarYear,
                @Term         = :Term;
        """
        )
        params = {"CalendarYear": selected_year, "Term": selected_term}
        res = conn.execute(sql, params)
        results = res.mappings().all()
        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)
    elif selected_type == "national_ytd_vs_target":
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC GetNationalRates
                @CalendarYear = :CalendarYear,
                @Term         = :Term;
        """
        )
        params = {"CalendarYear": selected_year, "Term": selected_term}
        res = conn.execute(sql, params)
        results = res.mappings().all()
        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)
    # 3) Funder Missing Data (builds fig here)
    elif selected_type == "funder_missing_data":
        threshold = 0.25
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC FlaskGetSchoolSummaryAllFunders
                @CalendarYear = :CalendarYear,
                @Term         = :Term,
                @Threshold    = :Threshold,
                @Email        = :Email;
        """
        )
        params = {
            "CalendarYear": selected_year,
            "Term": selected_term,
            "Threshold": threshold,
            "Email": session.get("user_email"),
        }
        res = conn.execute(sql, params)
        rows = res.mappings().all()
        results = rows


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
        

        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)

    # 4) Funder LY vs National LY vs Target (data only)
    elif selected_type == "ly_funder_vs_ly_national_vs_target":
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC dbo.GetFunderNationalRates_All
                @Term = :Term,
                @CalendarYear = :CalendarYear;
        """
        )
        # NOTE: you may want to parameterise this later
        params = {"Term": 2, "CalendarYear": 2025}
        res = conn.execute(sql, params)

        raw_rows = res.mappings().all()
        filtered_rows = []
        for r in raw_rows:
            funder_matches = not funder_id or int(r.get("FunderID", 0) or 0) == funder_id
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
        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)

    # 5) Provider vs Funder (data only)
    elif selected_type == "provider_ytd_vs_target_vs_funder":
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC dbo.GetProviderNationalRates
                @Term         = :Term,
                @CalendarYear = :CalendarYear,
                @ProviderID   = :ProviderID,
                @FunderID     = :FunderID;
        """
        )
        params = {
            "Term": selected_term,
            "CalendarYear": selected_year,
            "ProviderID": int(selected_provider_id),
            "FunderID": int(funder_id) if funder_id is not None else None,
        }
        res = conn.execute(sql, params)
        rows = res.mappings().all()

        if len(rows) == 0:
            # fallback driver_sql (kept as-is)
            res2 = conn.exec_driver_sql(
                "SET NOCOUNT ON; EXEC dbo.GetProviderNationalRates @Term=?, @CalendarYear=?, @ProviderID=?, @FunderID=?",
                (selected_term, selected_year, int(selected_provider_id), funder_id if funder_id is not None else None),
            )
            if getattr(res2, "cursor", None) and res2.cursor.description:
                cols = [d[0] for d in res2.cursor.description]
                rows = [dict(zip(cols, row)) for row in res2.fetchall()]

        results = rows
        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)

    # 6) Provider YTD vs Target (data only)
    elif selected_type == "provider_ytd_vs_target":
        if role == "ADM":
            funder_id = None

        sql = text(
            """
            SET NOCOUNT ON;
            EXEC dbo.GetProviderNationalRates
                 @Term         = :Term,
                 @CalendarYear = :CalendarYear,
                 @ProviderID   = :ProviderID,
                 @FunderID     = :FunderID;
        """
        )
        params = {
            "Term": selected_term,
            "CalendarYear": selected_year,
            "ProviderID": int(selected_provider_id),
            "FunderID": int(funder_id) if funder_id is not None else None,
        }
        res = conn.execute(sql, params)
        rows = res.mappings().all()

        current_app.logger.info("🧪 Rows fetched: %d", len(rows))

        if rows:
            first = rows[0]
            current_app.logger.debug("🧪 First row keys: %s", list(first.keys()))
            current_app.logger.debug(
                "🧪 Distinct ResultTypes: %s", {r.get("ResultType") for r in rows}
            )
        else:
            current_app.logger.warning("⚠️ Stored procedure returned 0 rows for these params.")

        results = rows

    # 7) School YTD vs Target (data only)
    elif selected_type == "school_ytd_vs_target":
        sql = text(
            """
            SET NOCOUNT ON;
            EXEC dbo.GetSchoolNationalRates
                 @CalendarYear = :CalendarYear,
                 @Term         = :Term,
                 @MoeNumber    = :MoeNumber;
        """
        )
        params = {
            "CalendarYear": selected_year,
            "Term": selected_term,
            "MoeNumber": int(selected_school_id),
        }
        res = conn.execute(sql, params)
        results = res.mappings().all()
        current_app.logger.info("🔎 rows=%d | type=%s", len(results or []), selected_type)
    elif selected_type == "funder_student_count":
        from app.utils.funder_student_counts import build_funder_student_counts_pdf
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            current_app.logger.info("⚠️ font setup skipped: %s", font_e)
        report_id = session.get("report_id") or uuid.uuid4().hex
        session["report_id"] = report_id  # <-- important

        pdf_path = REPORT_DIR / f"{report_id}.pdf"
        footer_png = Path(current_app.static_folder) / "footer.png"

        preview_fig, meta = build_funder_student_counts_pdf(
            conn=conn,
            funder_id=funder_id,
            term = selected_term,
            year = selected_year,
            out_pdf_path=pdf_path,
            footer_png=footer_png,
        )

        results = None
        fig = preview_fig
    elif selected_type == "funder_progress_summary":
        from app.utils.funder_summary import build_funder_progress_summary_pdf

        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            current_app.logger.info("⚠️ font setup skipped: %s", font_e)

        report_id = session.get("report_id") or uuid.uuid4().hex
        session["report_id"] = report_id

        pdf_path = REPORT_DIR / f"{report_id}.pdf"
        footer_png = Path(current_app.static_folder) / "footer.png"

        preview_fig, meta = build_funder_progress_summary_pdf(
            conn=conn,
            funder_id=funder_id,
            from_year=selected_year,     # or fixed start year if you prefer
            threshold=0.2,
            out_pdf_path=pdf_path,
            footer_png=None,
        )

        results = None
        fig = preview_fig
    else:
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
    """Build a Matplotlib figure from the result rows. Returns (fig, no_data_banner)."""
    fig = None
    no_data_banner = None

    if not results:
        return None, None

    # Provider vs Funder (three-bar landscape)
    if selected_type == "provider_ytd_vs_target_vs_funder":
        vars_to_plot = ["Provider Rate (YTD)", "Funder Rate (YTD)", "WSNZ Target"]
        colors_dict = {
            "Provider Rate (YTD)": "#2EBDC2",
            "WSNZ Target": "#356FB6",
            "Funder Rate (YTD)": "#BBE6E9",
        }

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
    elif selected_type == "national_ytd_vs_target":
        vars_to_plot = ["National Rate (LY)", "National Rate (YTD)", "WSNZ Target"]
        colors_dict = {
            "National Rate (YTD)": "#2EBDC2",
            "WSNZ Target": "#356FB6",
            "National Rate (LY)": "#BBE6E9",
        }
        fig = provider_portrait_with_target(
            results,
            term=selected_term,
            year=selected_year,
            mode="national",
            subject_name="",
            title=f"National YTD vs WSNZ Target",
        )
    # Provider portrait (YTD vs Target)
    elif selected_type == "provider_ytd_vs_target":
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            current_app.logger.info("⚠️ font setup skipped: %s", font_e)

        mode = "provider"

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
            no_data_banner = (
                f"No YTD provider data found for {subject_name} "
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

        # Overlay warning banner (kept as-is)
        if no_data_banner and fig is not None:
            try:
                ax = fig.gca()
                ax.annotate(
                    no_data_banner,
                    xy=(0.5, 1.0),
                    xycoords="axes fraction",
                    ha="center",
                    va="bottom",
                    fontsize=10,
                    fontweight="bold",
                    color="white",
                    bbox=dict(boxstyle="round,pad=0.4", fc="#1a427d", ec="#1a427d"),
                )
            except Exception as _e:
                current_app.logger.info("⚠️ Could not annotate no-data banner: %s", _e)

        

    # Funder portrait (YTD vs Target)
    elif selected_type == "funder_ytd_vs_target":
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            current_app.logger.info("⚠️ font setup skipped: %s", font_e)

        fig = provider_portrait_with_target(
            results,
            term=selected_term,
            year=selected_year,
            mode="funder",
            subject_name=selected_funder_name,
            title=f"{selected_funder_name or 'Funder'} YTD vs Target",
        )
        

    # School portrait (YTD vs Target)
    elif selected_type == "school_ytd_vs_target":
        try:
            use_ppmori("app/static/fonts")
        except Exception as font_e:
            current_app.logger.info("⚠️ font setup skipped: %s", font_e)

        school_name = request.form.get("school_name") or next(
            (r.get("SchoolName") for r in results if r.get("SchoolName")), None
        )

        fig = provider_portrait_with_target(
            results,
            term=selected_term,
            year=selected_year,
            mode="school",
            subject_name=school_name,
            title=f"{school_name or 'School'} YTD vs WSNZ Target",
        )
    elif selected_type in {"funder_student_count", "funder_progress_summary"}:
        return None, None

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
    if selected_type == "funder_missing_data":
        c = "#1a427d"
    else:
        c = "#1a427d40"
    try:
        footer_svg = os.path.join(current_app.static_folder, "footer.svg")
        add_full_width_footer_svg(
            fig,
            footer_svg,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master=c
        )
    except Exception as footer_e:
        print(
            f"⚠ Could not add footer to figure: %s", footer_e
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
        school_name = next((r.get("SchoolName") for r in results if r.get("SchoolName")), "")

    if selected_type == "funder_missing_data":
        base_label = f"MissingData_{selected_funder_name or 'Funder'}"
    elif selected_type == "funder_ytd_vs_target":
        base_label = f"FunderYTDvsTarget_{selected_funder_name or 'Funder'}"
    elif selected_type == "ly_funder_vs_ly_national_vs_target":
        base_label = f"FunderLY_vs_National_vs_Target_{selected_funder_name or 'Funder'}"
    elif selected_type == "provider_ytd_vs_target_vs_funder":
        base_label = f"ProviderVsFunder_{provider_name or 'Provider'}_{selected_funder_name or 'Funder'}"
    elif selected_type == "provider_ytd_vs_target":
        base_label = f"ProviderYTDvsTarget_{provider_name or 'Provider'}"
    elif selected_type == "school_ytd_vs_target":
        base_label = f"SchoolYTDvsTarget_{school_name or f'MOE_{selected_school_id}'}"
    elif selected_type == "national_ly_vs_national_ytd_vs_target":
        base_label = "NationalLYvsNationalYTDvsTarget"
    elif selected_type == "national_ytd_vs_target":
        base_label = "NationalYTDvsTarget"
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
    ctx = _user_ctx()
    role = session.get("user_role")  # "ADM", "FUN", or "PRO"

    current_app.logger.info(
        "📄 /Reports hit | method=%s role=%s user=%s admin=%s",
        request.method,
        role,
        ctx["user"],
        ctx["admin"],
    )

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
        current_app.logger.debug("PRO effective provider_id=%s", selected_provider_id)
    elif role == "FUN":
        current_app.logger.debug("FUN default funder_name(session.desc)=%s", selected_funder_name)

    current_app.logger.debug(
        "📥 initial ids | provider_id=%s school_id=%s | year=%s term=%s type=%s | form_keys=%s",
        selected_provider_id,
        selected_school_id,
        selected_year,
        selected_term,
        selected_type,
        _safe_form_keys(),
    )

    results = None
    plot_payload = None
    plot_png_b64 = None
    no_data_banner = None
    display = False

    is_ajax, action = _get_request_type()

    if request.method == "POST" and action == "show_report":
        current_app.logger.info(
            "📩 /Reports POST show_report | ajax=%s role=%s user=%s",
            is_ajax,
            role,
            ctx["user"],
        )

        # initialise here so we can reference them in except logging
        funder_id = None

        try:
            with engine.connect() as conn:
                current_app.logger.debug(
                    "📥 POST params | year=%s term=%s type=%s provider_id=%s school_id=%s",
                    selected_year,
                    selected_term,
                    selected_type,
                    selected_provider_id,
                    selected_school_id,
                )

                # Resolve funder from ADM dropdown (FUN is implied from session, PRO has none)
                if role == "ADM":
                    selected_funder_name = request.form.get("funder_name") or None

                if selected_funder_name == "Loading funders…":
                    selected_funder_name = None

                current_app.logger.debug("effective funder_name=%s", selected_funder_name)

                # If we have a funder *name*, resolve it to an ID
                if selected_funder_name:
                    row = conn.execute(
                        text("EXEC FlaskHelperFunctions @Request='FunderIDDescription', @Text=:t"),
                        {"t": selected_funder_name},
                    ).fetchone()

                    if not row:
                        msg = "Funder not found."

                        try:
                            log_alert(
                                email=session.get("user_email"),
                                role=session.get("user_role"),
                                entity_id=None,
                                link=request.url,
                                message=f"/Reports: funder not found for name '{selected_funder_name}'",
                            )
                        except Exception:
                            pass

                        if is_ajax:
                            return jsonify({"ok": False, "error": msg}), 400

                        flash(msg, "danger")
                        return redirect(url_for("report_bp.new_reports"))

                    funder_id = (
                        row[0]
                        if not hasattr(row, "_mapping")
                        else int(row._mapping.get("FunderID") or row[0])
                    )
                    current_app.logger.info("✅ resolved funder_id=%s", funder_id)

                # ---- Special case: funder_missing_data needs a funder *name* ----
                if selected_type == "funder_missing_data" and not selected_funder_name:
                    msg = "Please choose a funder for the missing data report."
                    try:
                        log_alert(
                            email=session.get("user_email"),
                            role=session.get("user_role"),
                            entity_id=None,
                            link=request.url,
                            message="/Reports: funder_missing_data selected but no funder chosen",
                        )
                    except Exception:
                        pass

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

                current_app.logger.info("▶ executing report type=%s", selected_type)

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
                if fig is not None:
                    try:
                        footer_svg = os.path.join(current_app.static_folder, "footer.svg")
                        c = "#1a427d" if selected_type == "funder_missing_data" else "#1a427d40"
                        add_full_width_footer_svg(
                            fig,
                            footer_svg,
                            bottom_margin_frac=0.0,
                            max_footer_height_frac=0.20,
                            col_master=c,
                        )
                    except Exception as footer_e:
                        current_app.logger.info("⚠ Could not add footer: %s", footer_e)
                if early:
                    return early

                no_data_banner = no_data_banner_inner

                plot_payload = {
                    "year": selected_year,
                    "term": selected_term,
                    "type": selected_type,
                    "funder_id": funder_id,
                    "provider_id": int(selected_provider_id) if selected_provider_id else None,
                    "school_id": int(selected_school_id) if selected_school_id else None,
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
                
                if selected_type in {"funder_student_count", "funder_progress_summary"}:
                    if fig is not None:
                        prefix = (
                            "Funder_Student_Count"
                            if selected_type == "funder_student_count"
                            else "Funder_Progress_Summary"
                        )

                        plot_png_b64 = _persist_preview_for_existing_report(
                            report_id=session["report_id"],
                            fig=fig,
                            selected_term=selected_term,
                            selected_year=selected_year,
                            selected_funder_name=selected_funder_name,
                            base_label_prefix=prefix,
                        )
                        display = True
                
                else:
                    # Existing behaviour for all other report types
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

                    if selected_type == "school_ytd_vs_target" and (school_name or school_id):
                        left_bits.append(school_name or f"School MOE {school_id}")
                    elif provider_name:
                        left_bits.append(provider_name)
                    elif provider_id:
                        left_bits.append(f"Provider ID {provider_id}")

                    header_html = " • ".join(left_bits)
                    allow_png = bool(display) and ( selected_type not in {"funder_student_count", "funder_progress_summary"})

                    return jsonify(
                        {
                            "ok": True,
                            "plot_png_b64": plot_png_b64,
                            "header_html": header_html,
                            "display": bool(display),
                            "allow_png": bool(allow_png),
                            "notice": no_data_banner,
                        }
                    )


        except Exception as e:
            # one clean traceback in logs (no print spam)
            current_app.logger.exception(
                "❌ /Reports failed | role=%s year=%s term=%s type=%s funder_name=%s funder_id=%s provider_id=%s school_id=%s user=%s ajax=%s",
                role,
                selected_year,
                selected_term,
                selected_type,
                selected_funder_name,
                funder_id,
                selected_provider_id,
                selected_school_id,
                session.get("user_email"),
                is_ajax,
            )

            err_text = str(getattr(e, "orig", e))
            tb = traceback.format_exc()

            # ✅ log to AUD_Alerts (best-effort)
            try:
                log_alert(
                    email=session.get("user_email"),
                    role=session.get("user_role"),
                    entity_id=None,
                    link=request.url,
                    message=f"/Reports error: {err_text}\n{tb}"[:4000],
                )
            except Exception:
                pass

            # Friendly message + status code (unchanged behaviour)
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

    # Post-sort for stable ordering
    if results and isinstance(results, list) and results and "CompetencyDesc" in results[0]:
        results = sorted(results, key=lambda x: (x.get("CompetencyDesc") or ""))

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
