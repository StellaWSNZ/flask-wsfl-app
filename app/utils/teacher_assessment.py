from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import matplotlib.patches as mpatches
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.TAB_DataframeTable import draw_dataframe_table_v2
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import close_pdf, new_page, open_pdf, save_page


# ============================================================
# Config: stored proc + expected column names
# ============================================================
PROC_NAME = "dbo.SVY_FunderTeacherAssessmentSummary"
TOTALS_PROC_NAME = "dbo.SVY_FunderTeacherAssessmentSummaryTotals"

COL_FUNDER = "Funder"
COL_SCHOOL = "SchoolName"
COL_TERM = "Term"
COL_CALYEAR = "CalendarYear"

COL_TOTAL_CLASSES = "TotalClasses"
COL_CLASSES_WITH_ANY_REVIEW = "ClassesWithAnyReview"
COL_TOTAL_REVIEWS = "TotalReviews"
COL_LEAD_REVIEWS = "LeadReviews"
COL_RELIEF_REVIEWS = "ReliefReviews"
COL_MISSING_CLASSES = "MissingClasses"

COL_FUNDERSTAFF = "FunderStaffMember"
DEFAULT_FUNDERSTAFF = "Not assigned"

COL_SUMMARY_LEVEL = "SummaryLevel"
COL_SCHOOLS = "Schools"
COL_PCT_CLASSES_REVIEWED = "PctClassesReviewed"

# Behavior:
# - "auto": show staff column only if it exists in the data
# - True:  always show staff column (add if missing)
# - False: never show staff column
SHOW_FUNDERSTAFF_COLUMN: bool | str = "auto"


# ------------------------------------------------------------
# Pagination helper
# ------------------------------------------------------------
def paginate_rows(df: pd.DataFrame, rows_per_page: int = 28) -> List[pd.DataFrame]:
    print("🔵 paginate_rows START rows:", 0 if df is None else len(df), "| rows_per_page:", rows_per_page)

    if df is None or df.empty:
        print("🟡 paginate_rows early return: empty df")
        return []

    pages: List[pd.DataFrame] = []
    for start in range(0, len(df), rows_per_page):
        print("   ➡️ paginate_rows slice:", start, "to", start + rows_per_page)
        pages.append(df.iloc[start:start + rows_per_page].reset_index(drop=True))

    print("🟢 paginate_rows DONE pages:", len(pages))
    return pages


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _normalize_staff_name_col(df: pd.DataFrame) -> pd.DataFrame:
    print("🔵 _normalize_staff_name_col START")

    if df is None or df.empty:
        print("🟡 _normalize_staff_name_col early return: empty df")
        return df

    if COL_FUNDERSTAFF in df.columns:
        print("   ℹ️ staff column found")
        s = df[COL_FUNDERSTAFF].fillna("").astype(str).str.strip()
        df[COL_FUNDERSTAFF] = s.replace("", DEFAULT_FUNDERSTAFF)
    else:
        print("   ℹ️ staff column not present")

    print("🟢 _normalize_staff_name_col DONE")
    return df


def _ensure_staff_column(df: pd.DataFrame) -> pd.DataFrame:
    print("🔵 _ensure_staff_column START | mode:", SHOW_FUNDERSTAFF_COLUMN)

    if df is None or df.empty:
        print("🟡 _ensure_staff_column early return: empty df")
        return df

    if SHOW_FUNDERSTAFF_COLUMN is True:
        print("   ℹ️ force showing staff column")
        if COL_FUNDERSTAFF not in df.columns:
            print("   ➕ adding missing staff column")
            df[COL_FUNDERSTAFF] = DEFAULT_FUNDERSTAFF
        df = _normalize_staff_name_col(df)

    elif SHOW_FUNDERSTAFF_COLUMN is False:
        print("   ℹ️ force hiding staff column")
        if COL_FUNDERSTAFF in df.columns:
            print("   ➖ dropping staff column")
            df = df.drop(columns=[COL_FUNDERSTAFF])

    else:
        print("   ℹ️ auto mode for staff column")
        df = _normalize_staff_name_col(df)

    print("🟢 _ensure_staff_column DONE | columns:", list(df.columns))
    return df


# ------------------------------------------------------------
# Fetch data
# ------------------------------------------------------------
def _fetch_teacher_df(conn, funder_id: int) -> Tuple[pd.DataFrame, str]:
    print("🔵 _fetch_teacher_df START | funder_id =", funder_id)

    sql = text(f"EXEC {PROC_NAME} @FunderID = :funder_id")
    print("   🟦 running SQL:", sql)

    df = pd.read_sql(sql, conn, params={"funder_id": funder_id})
    print("   🟩 SQL returned rows:", 0 if df is None else len(df))
    if df is not None:
        print("   🟩 SQL returned columns:", list(df.columns))

    if df is None or df.empty:
        print("🟡 _fetch_teacher_df early return: empty df")
        return df, ""

    funder_name = ""
    if COL_FUNDER in df.columns:
        vals = df[COL_FUNDER].dropna().unique().tolist()
        funder_name = str(vals[0]) if vals else ""
    print("   ℹ️ resolved funder_name:", funder_name)

    df = _ensure_staff_column(df)

    if COL_CALYEAR in df.columns:
        print("   ℹ️ normalizing CalendarYear")
        df[COL_CALYEAR] = pd.to_numeric(df[COL_CALYEAR], errors="coerce").astype("Int64")
    if COL_TERM in df.columns:
        print("   ℹ️ normalizing Term")
        df[COL_TERM] = pd.to_numeric(df[COL_TERM], errors="coerce").astype("Int64")

    for c in [
        COL_TOTAL_CLASSES,
        COL_CLASSES_WITH_ANY_REVIEW,
        COL_TOTAL_REVIEWS,
        COL_LEAD_REVIEWS,
        COL_RELIEF_REVIEWS,
    ]:
        if c in df.columns:
            print("   ℹ️ normalizing numeric column:", c)
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    if COL_MISSING_CLASSES in df.columns:
        print("   ℹ️ normalizing missing classes column")
        s = df[COL_MISSING_CLASSES].fillna("").astype(str).str.strip()
        df[COL_MISSING_CLASSES] = s

    print("🟢 _fetch_teacher_df DONE")
    return df, funder_name


def _fetch_teacher_totals_df(conn, funder_id: int) -> pd.DataFrame:
    print("🔵 _fetch_teacher_totals_df START | funder_id =", funder_id)

    sql = text(f"EXEC {TOTALS_PROC_NAME} @FunderID = :funder_id")
    print("   🟦 running SQL:", sql)

    df = pd.read_sql(sql, conn, params={"funder_id": funder_id})
    print("   🟩 totals SQL returned rows:", 0 if df is None else len(df))
    if df is not None:
        print("   🟩 totals SQL returned columns:", list(df.columns))

    if df is None or df.empty:
        print("🟡 _fetch_teacher_totals_df early return: empty df")
        return pd.DataFrame()

    if COL_CALYEAR in df.columns:
        df[COL_CALYEAR] = pd.to_numeric(df[COL_CALYEAR], errors="coerce").astype("Int64")
    if COL_TERM in df.columns:
        df[COL_TERM] = pd.to_numeric(df[COL_TERM], errors="coerce").astype("Int64")

    for c in [
        COL_SCHOOLS,
        COL_TOTAL_CLASSES,
        COL_CLASSES_WITH_ANY_REVIEW,
        COL_TOTAL_REVIEWS,
        COL_LEAD_REVIEWS,
        COL_RELIEF_REVIEWS,
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    if COL_PCT_CLASSES_REVIEWED in df.columns:
        df[COL_PCT_CLASSES_REVIEWED] = pd.to_numeric(df[COL_PCT_CLASSES_REVIEWED], errors="coerce").fillna(0)

    if COL_SUMMARY_LEVEL in df.columns:
        df[COL_SUMMARY_LEVEL] = df[COL_SUMMARY_LEVEL].fillna("").astype(str).str.strip()

    print("🟢 _fetch_teacher_totals_df DONE")
    return df


def _split_by_term(df: pd.DataFrame) -> List[Tuple[int, int, pd.DataFrame]]:
    print("🔵 _split_by_term START")

    if df is None or df.empty:
        print("🟡 _split_by_term early return: empty df")
        return []

    if COL_CALYEAR not in df.columns or COL_TERM not in df.columns:
        raise ValueError(f"Expected columns {COL_CALYEAR} and {COL_TERM} in stored proc output.")

    out: List[Tuple[int, int, pd.DataFrame]] = []
    keys = (
        df[[COL_CALYEAR, COL_TERM]]
        .drop_duplicates()
        .sort_values([COL_CALYEAR, COL_TERM], ascending=[True, True])
        .itertuples(index=False, name=None)
    )

    for y, t in keys:
        print(f"   ➡️ building term block for {y} T{t}")
        d = df[(df[COL_CALYEAR] == y) & (df[COL_TERM] == t)].copy()
        print(f"      rows in block: {len(d)}")

        for col in [COL_CALYEAR, COL_TERM, COL_FUNDER]:
            if col in d.columns:
                d = d.drop(columns=[col])

        sort_cols: List[str] = []
        if COL_SCHOOL in d.columns:
            d[COL_SCHOOL] = d[COL_SCHOOL].fillna("").astype(str)
            sort_cols.append(COL_SCHOOL)
        if COL_FUNDERSTAFF in d.columns:
            d[COL_FUNDERSTAFF] = d[COL_FUNDERSTAFF].fillna("").astype(str)
            sort_cols.append(COL_FUNDERSTAFF)

        if sort_cols:
            print("      sorting by:", sort_cols)
            d = d.sort_values(sort_cols, ascending=True)

        out.append((int(y), int(t), d.reset_index(drop=True)))

    print("🟢 _split_by_term DONE. Terms:", len(out))
    return out


# ------------------------------------------------------------
# Summaries
# ------------------------------------------------------------
def _term_summary(df_term: pd.DataFrame) -> Dict[str, Any]:
    print("🔵 _term_summary START | rows:", 0 if df_term is None else len(df_term))

    out = {
        "schools": 0,
        "total_classes": 0,
        "classes_with_any_review": 0,
        "total_reviews": 0,
        "lead_reviews": 0,
        "relief_reviews": 0,
    }
    if df_term is None or df_term.empty:
        print("🟡 _term_summary early return: empty df_term")
        return out

    if COL_SCHOOL in df_term.columns:
        out["schools"] = int(
            df_term[COL_SCHOOL].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        )

    out["total_classes"] = int(df_term[COL_TOTAL_CLASSES].sum()) if COL_TOTAL_CLASSES in df_term.columns else 0
    out["classes_with_any_review"] = int(df_term[COL_CLASSES_WITH_ANY_REVIEW].sum()) if COL_CLASSES_WITH_ANY_REVIEW in df_term.columns else 0
    out["total_reviews"] = int(df_term[COL_TOTAL_REVIEWS].sum()) if COL_TOTAL_REVIEWS in df_term.columns else 0
    out["lead_reviews"] = int(df_term[COL_LEAD_REVIEWS].sum()) if COL_LEAD_REVIEWS in df_term.columns else 0
    out["relief_reviews"] = int(df_term[COL_RELIEF_REVIEWS].sum()) if COL_RELIEF_REVIEWS in df_term.columns else 0

    print("🟢 _term_summary DONE |", out)
    return out


def _draw_totals_table_page(
    *,
    fig,
    ax,
    family: str,
    funder_name: str,
    df_totals: pd.DataFrame,
):
    print("🔵 _draw_totals_table_page START")

    if df_totals is None or df_totals.empty:
        print("🟡 _draw_totals_table_page early return: empty df_totals")
        return

    term_rows = df_totals[df_totals[COL_SUMMARY_LEVEL].str.lower() == "term"].copy()
    overall_rows = df_totals[df_totals[COL_SUMMARY_LEVEL].str.lower() == "overall"].copy()

    header_poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.955,
        width=0.96,
        height=0.055,
        ratio=0.45,
        corners_round=[1, 3],
        n_arc=64,
    )
    ax.add_patch(
        mpatches.Polygon(
            list(header_poly.exterior.coords),
            closed=True,
            facecolor="#1a427d",
            edgecolor="#1a427d",
            linewidth=1.5,
            transform=ax.transAxes,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=header_poly,
        text=f"{funder_name} - Teacher Assessments Totals",
        fontfamily=family,
        fontsize=20,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.05,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=None,
    )
    """
    label_poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.885,
        width=0.96,
        height=0.03,
        ratio=0.45,
        corners_round=[1, 2],
        n_arc=64,
    )
    ax.add_patch(
        mpatches.Polygon(
            list(label_poly.exterior.coords),
            closed=True,
            facecolor="#eef2ff",
            edgecolor="#1a427d",
            linewidth=1.2,
            transform=ax.transAxes,
        )
    )
    draw_text_in_polygon(
        ax,
        poly=label_poly,
        text="Term breakdown",
        fontfamily=family,
        fontsize=11,
        fontweight="semibold",
        color="#1a427d",
        pad_frac=0.04,
        wrap=False,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=1,
    )
    """
    if term_rows.empty:
        print("🟡 _draw_totals_table_page no term rows")
        return

    term_rows = term_rows.copy()
    term_rows["TermLabel"] = (
        "Term "
        + term_rows[COL_TERM].astype("Int64").astype(str)
        + ", "
        + term_rows[COL_CALYEAR].astype("Int64").astype(str)
    )

    term_display = term_rows[[
        "TermLabel",
        COL_SCHOOLS,
        COL_TOTAL_CLASSES,
        COL_CLASSES_WITH_ANY_REVIEW,
        COL_PCT_CLASSES_REVIEWED,
        COL_TOTAL_REVIEWS,
        COL_LEAD_REVIEWS,
        COL_RELIEF_REVIEWS,
    ]].copy()

    term_display[COL_PCT_CLASSES_REVIEWED] = term_display[COL_PCT_CLASSES_REVIEWED].map(lambda x: f"{x:.1f}%")

    if not overall_rows.empty:
        ov = overall_rows.iloc[0]
        overall_row = {
            "TermLabel": "Overall",
            COL_SCHOOLS: int(ov.get(COL_SCHOOLS, 0)),
            COL_TOTAL_CLASSES: int(ov.get(COL_TOTAL_CLASSES, 0)),
            COL_CLASSES_WITH_ANY_REVIEW: int(ov.get(COL_CLASSES_WITH_ANY_REVIEW, 0)),
            COL_PCT_CLASSES_REVIEWED: f"{float(ov.get(COL_PCT_CLASSES_REVIEWED, 0)):.1f}%",
            COL_TOTAL_REVIEWS: int(ov.get(COL_TOTAL_REVIEWS, 0)),
            COL_LEAD_REVIEWS: int(ov.get(COL_LEAD_REVIEWS, 0)),
            COL_RELIEF_REVIEWS: int(ov.get(COL_RELIEF_REVIEWS, 0)),
        }
        term_display = pd.concat(
            [term_display, pd.DataFrame([overall_row])],
            ignore_index=True,
        )

    def summary_row_highlight(row: pd.Series, r: int) -> Optional[Tuple[str, str]]:
        if str(row.get("TermLabel", "")).strip().lower() == "overall":
            return "#1a427d", "#ffffff"
        return None

    term_columns = [
        {"key": "TermLabel", "label": "Term", "width_frac": 0.16, "align": "left", "wrap": True},
        {"key": COL_SCHOOLS, "label": "Schools", "width_frac": 0.08, "align": "center"},
        {"key": COL_TOTAL_CLASSES, "label": "Total\nClasses", "width_frac": 0.12, "align": "center"},
        {"key": COL_CLASSES_WITH_ANY_REVIEW, "label": "Classes\nReviewed", "width_frac": 0.14, "align": "center"},
        {"key": COL_PCT_CLASSES_REVIEWED, "label": "% Classes\nReviewed", "width_frac": 0.12, "align": "center"},
        {"key": COL_TOTAL_REVIEWS, "label": "Total\nReviews", "width_frac": 0.12, "align": "center"},
        {"key": COL_LEAD_REVIEWS, "label": "Lead\nReviews", "width_frac": 0.13, "align": "center"},
        {"key": COL_RELIEF_REVIEWS, "label": "Relief\nReviews", "width_frac": 0.13, "align": "center"},
    ]

    draw_dataframe_table_v2(
        ax,
        df=term_display.reset_index(drop=True),
        x=0.02,
        y=0.55,
        width=0.96,
        height=0.35,
        header_height_frac=0.09,
        columns=term_columns,
        base_row_facecolor="#ffffff",
        row_color_fn=summary_row_highlight,
        wrap=True,
        max_wrap_lines=4,
        shift=True,
    )

    note_poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.06,
        width=0.96,
        height=0.03,
        ratio=0.45,
        corners_round=[4, 3],
        n_arc=64,
    )
    ax.add_patch(
        mpatches.Polygon(
            list(note_poly.exterior.coords),
            closed=True,
            facecolor="#1a427d",
            edgecolor="#1a427d",
            linewidth=1.0,
            transform=ax.transAxes,
        )
    )
    note_text = (
        "This totals table reflects Kaiako Led classes only. "
        "The Overall row appears only on this final page."
    )
    draw_text_in_polygon(
        ax,
        poly=note_poly,
        text=note_text,
        fontfamily=family,
        fontsize=10,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.04,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=3,
    )

    print("🟢 _draw_totals_table_page DONE")


# ------------------------------------------------------------
# Public PDF builder
# ------------------------------------------------------------
def build_funder_teacher_assessment_summary_pdf(
    *,
    conn,
    funder_id: int,
    out_pdf_path: str | Path,
    footer_png: str | Path | None,
    rows_per_page: int = 30,
    dpi: int = 300,
    page_size: str = "A3",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
) -> Tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    print("🚀 build_funder_teacher_assessment_summary_pdf START")
    print("   funder_id:", funder_id)
    print("   out_pdf_path:", out_pdf_path)
    print("   rows_per_page:", rows_per_page)
    print("   dpi:", dpi)
    print("   page_size:", page_size)
    print("   orientation:", orientation)
    print("   fonts_dir:", fonts_dir)

    family = load_ppmori_fonts(str(fonts_dir))
    print("🟢 fonts loaded | family:", family)

    df_all, funder_name = _fetch_teacher_df(conn, funder_id=funder_id)
    print("📊 fetched df_all rows:", 0 if df_all is None else len(df_all))
    print("📊 funder_name:", funder_name)

    df_totals = _fetch_teacher_totals_df(conn, funder_id=funder_id)
    print("📊 fetched df_totals rows:", 0 if df_totals is None else len(df_totals))

    meta: Dict[str, Any] = {
        "funder_name": funder_name,
        "rows": int(0 if df_all is None else len(df_all)),
        "pages": 0,
        "terms": [],
        "has_totals_page": bool(df_totals is not None and not df_totals.empty),
    }

    if df_all is None or df_all.empty:
        print("🟡 builder early return: empty df_all")
        return None, meta

    term_blocks = _split_by_term(df_all)
    print("📊 term_blocks:", len(term_blocks))
    meta["terms"] = [f"{y}T{t}" for (y, t, _d) in term_blocks]
    print("📊 meta terms:", meta["terms"])

    render_plan: List[Tuple[int, int, int]] = []
    term_pages_cache: Dict[Tuple[int, int], List[pd.DataFrame]] = {}

    print("🔵 building render_plan")
    for (y, t, df_term) in term_blocks:
        if df_term is None or df_term.empty:
            print(f"   🟡 skipping empty term block {y} T{t}")
            continue
        pages_tmp = paginate_rows(df_term, rows_per_page=rows_per_page)
        term_pages_cache[(y, t)] = pages_tmp
        for page_idx in range(1, len(pages_tmp) + 1):
            render_plan.append((y, t, page_idx))

    print("🟢 render_plan length:", len(render_plan))
    last_key = render_plan[-1] if render_plan else None
    print("🟢 last_key:", last_key)

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )
    print("📄 PDF opened | width:", w, "| height:", h, "| dpi:", _dpi)

    preview_fig = None
    page_count = 0

    staff_col = {
        "key": COL_FUNDERSTAFF,
        "label": "Assigned Funder\nStaff Member",
        "width_frac": 0.16,
        "align": "left",
        "wrap": True,
        "max_lines": 2,
    }

    def _make_table_columns(df: pd.DataFrame) -> List[Dict[str, Any]]:
        print("🔵 _make_table_columns START | columns:", list(df.columns))
        has_staff = (df is not None) and (COL_FUNDERSTAFF in df.columns)
        want_staff = (SHOW_FUNDERSTAFF_COLUMN is True) or (SHOW_FUNDERSTAFF_COLUMN == "auto" and has_staff)

        cols: List[Dict[str, Any]] = [
            {"key": COL_SCHOOL, "label": "School", "width_frac": 0.26, "align": "left", "wrap": True, "max_lines": 2},
        ]

        if want_staff and has_staff:
            cols.append(staff_col)

        cols += [
            {"key": COL_TOTAL_CLASSES, "label": "Total\nClasses", "width_frac": 0.09, "align": "center"},
            {"key": COL_CLASSES_WITH_ANY_REVIEW, "label": "Classes\nReviewed", "width_frac": 0.10, "align": "center"},
            {"key": COL_TOTAL_REVIEWS, "label": "Total\nReviews", "width_frac": 0.09, "align": "center"},
            {"key": COL_LEAD_REVIEWS, "label": "Lead\nTeacher\nReviews", "width_frac": 0.09, "align": "center"},
            {"key": COL_RELIEF_REVIEWS, "label": "Relief\nTeacher\nReviews", "width_frac": 0.09, "align": "center"},
            {
                "key": COL_MISSING_CLASSES,
                "label": "Missing Classes",
                "width_frac": 0.22,
                "align": "left",
                "wrap": True,
            },
        ]

        keys = set(df.columns)
        final_cols = [c for c in cols if c["key"] in keys]
        print("🟢 _make_table_columns DONE | final column keys:", [c["key"] for c in final_cols])
        return final_cols

    def row_highlight(row: pd.Series, r: int) -> Optional[Tuple[str, str]]:
        try:
            tot = int(row.get(COL_TOTAL_CLASSES, 0) or 0)
            reviewed = int(row.get(COL_CLASSES_WITH_ANY_REVIEW, 0) or 0)
            if tot > 0 and reviewed < tot:
                return "#f4f6ff", "#111111"
        except Exception:
            pass
        return None

    TABLE_X = 0.02
    TABLE_W = 0.96
    TABLE_Y = 0.12
    TABLE_H = 0.80

    BAR_W = 0.96
    GAP = 0.010

    BAR_H_TERM = 0.022
    BAR_H_NOTE = 0.06

    for (year, term, df_term) in term_blocks:
        print(f"➡️ Processing term {year} T{term}, rows={0 if df_term is None else len(df_term)}")

        if df_term is None or df_term.empty:
            print(f"   🟡 skipping empty term {year} T{term}")
            continue

        pages = term_pages_cache.get((year, term)) or paginate_rows(df_term, rows_per_page=rows_per_page)
        print(f"📄 Pages for term {year} T{term}: {len(pages)}")

        for term_page_idx, df_page in enumerate(pages, start=1):
            print(f"   📄 Page {term_page_idx}/{len(pages)} rows={len(df_page)}")

            ts = _term_summary(df_page)

            pct_reviewed = (
                round(100 * ts["classes_with_any_review"] / ts["total_classes"])
                if ts["total_classes"] > 0
                else 0
            )

            term_text = (
                f"Page Summary: schools: {ts['schools']}   |   "
                f"total classes: {ts['total_classes']}   |   "
                f"classes reviewed: {ts['classes_with_any_review']} ({pct_reviewed}%)   |   "
                f"total reviews: {ts['total_reviews']}   |   "
                f"lead reviews: {ts['lead_reviews']}   |   "
                f"relief reviews: {ts['relief_reviews']}"
            )

            page_count += 1
            is_last_rendered_page = (last_key == (year, term, term_page_idx))
            print("   ℹ️ is_last_rendered_page:", is_last_rendered_page)

            fig, ax = new_page(w, h, dpi)
            print("   🟦 new page created")

            header_poly = rounded_rect_polygon(
                cx=0.5,
                cy=0.955,
                width=0.96,
                height=0.055,
                ratio=0.45,
                corners_round=[1, 3],
                n_arc=64,
            )
            ax.add_patch(
                mpatches.Polygon(
                    list(header_poly.exterior.coords),
                    closed=True,
                    facecolor="#1a427d",
                    edgecolor="#1a427d",
                    linewidth=1.5,
                    transform=ax.transAxes,
                )
            )

            title = f"{funder_name} - Teacher Assessments (Term {term}, {year})"
            if len(pages) > 1:
                title = f"{title} (Page {term_page_idx} of {len(pages)})"

            print("   🟦 drawing header text")
            draw_text_in_polygon(
                ax,
                poly=header_poly,
                text=title,
                fontfamily=family,
                fontsize=20,
                fontweight="semibold",
                color="#ffffff",
                pad_frac=0.05,
                wrap=True,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=None,
            )
            print("   🟩 header text drawn")

            cols = _make_table_columns(df_page)
            print("   🟦 drawing table")
            draw_dataframe_table_v2(
                ax,
                df=df_page,
                x=TABLE_X,
                y=TABLE_Y,
                width=TABLE_W,
                height=TABLE_H,
                header_height_frac=0.042,
                columns=cols,
                base_row_facecolor="#ffffff",
                row_color_fn=row_highlight,
                wrap=True,
                max_wrap_lines=10,
                shift=True,
            )
            print("   🟩 table drawn")

            table_bottom_y = TABLE_Y

            term_top = table_bottom_y - GAP
            term_cy = term_top - (BAR_H_TERM / 2)

            note_top = (term_cy - BAR_H_TERM / 2) - GAP
            note_cy = note_top - (BAR_H_NOTE / 2)

            term_sum_poly = rounded_rect_polygon(
                cx=0.5,
                cy=term_cy,
                width=BAR_W,
                height=BAR_H_TERM,
                ratio=0.45,
                corners_round=[1, 2],
                n_arc=64,
            )
            ax.add_patch(
                mpatches.Polygon(
                    list(term_sum_poly.exterior.coords),
                    closed=True,
                    facecolor="#eef2ff",
                    edgecolor="#1a427d",
                    linewidth=1.2,
                    transform=ax.transAxes,
                )
            )
            print("   🟦 drawing page summary")
            draw_text_in_polygon(
                ax,
                poly=term_sum_poly,
                text=term_text,
                fontfamily=family,
                fontsize=12,
                fontweight="semibold",
                color="#1a427d",
                pad_frac=0.06,
                wrap=False,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=1,
            )
            print("   🟩 page summary drawn")

            note_poly = rounded_rect_polygon(
                cx=0.5,
                cy=note_cy,
                width=BAR_W,
                height=BAR_H_NOTE,
                ratio=0.45,
                corners_round=[4, 3],
                n_arc=64,
            )
            ax.add_patch(
                mpatches.Polygon(
                    list(note_poly.exterior.coords),
                    closed=True,
                    facecolor="#1a427d",
                    edgecolor="#1a427d",
                    linewidth=1.0,
                    transform=ax.transAxes,
                )
            )
            note_text = (
                f"Assigned Funder Staff Member is the {funder_name} staff member responsible for supporting this school (set in Provider Maintenance). "
                "Total Classes is the number of classes recorded for the school in this term. "
                "Classes Reviewed is the number of classes that have at least one teacher assessment completed. "
                "Total Reviews is the total number of teacher assessments submitted for the school this term "
                "(more than one teacher assessment can be submitted per class). "
                "Lead classroom teacher and relief teacher reviews are shown separately based on the teacher role selected in the form."
            )
            print("   🟦 drawing note text")
            draw_text_in_polygon(
                ax,
                poly=note_poly,
                text=note_text,
                fontfamily=family,
                fontsize=12,
                fontweight="semibold",
                color="#ffffff",
                pad_frac=0.04,
                wrap=True,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=6,
            )
            print("   🟩 note text drawn")

            print("   💾 saving page")
            save_page(
                pdf,
                fig,
                footer_png=str(footer_png) if footer_png else None,
                width_in=w,
                height_in=h,
                footer_bottom_margin_frac=0.0,
                footer_max_height_frac=0.20,
            )
            print("   ✅ page saved")

            if preview_fig is None:
                print("   🖼 setting preview_fig")
                preview_fig = fig
            else:
                print("   ♻️ closing non-preview fig")
                import matplotlib.pyplot as plt
                plt.close(fig)

    if df_totals is not None and not df_totals.empty:
        print("➡️ Rendering totals page with overall row only on this last page")

        fig, ax = new_page(w, h, dpi)
        _draw_totals_table_page(
            fig=fig,
            ax=ax,
            family=family,
            funder_name=funder_name,
            df_totals=df_totals,
        )

        save_page(
            pdf,
            fig,
            footer_png=str(footer_png) if footer_png else None,
            width_in=w,
            height_in=h,
            footer_bottom_margin_frac=0.0,
            footer_max_height_frac=0.20,
        )

        page_count += 1

        if preview_fig is None:
            preview_fig = fig
        else:
            import matplotlib.pyplot as plt
            plt.close(fig)

    print("🔵 closing PDF")
    close_pdf(pdf)
    meta["pages"] = page_count
    print("🏁 build_funder_teacher_assessment_summary_pdf DONE pages:", page_count)
    print("🏁 meta:", meta)

    return preview_fig, meta


# ------------------------------------------------------------
# Optional CLI runner
# ------------------------------------------------------------
if __name__ == "__main__":
    import os
    from sqlalchemy import create_engine
    from dotenv import load_dotenv

    load_dotenv()

    FUNDER_ID = 7

    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    engine = create_engine(os.getenv("db_url"))
    footer = Path(__file__).parent / "static" / "footer.png"
    pdf_out = OUT_DIR / f"WSFL_TeacherReviewsSummary_{FUNDER_ID}.pdf"

    with engine.begin() as conn:
        preview, meta = build_funder_teacher_assessment_summary_pdf(
            conn=conn,
            funder_id=FUNDER_ID,
            out_pdf_path=pdf_out,
            footer_png=footer if footer.exists() else None,
            rows_per_page=30,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows']} | Terms: {meta['terms']}")

    if preview:
        preview_png = OUT_DIR / f"WSFL_TeacherReviewsSummary_{FUNDER_ID}_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")