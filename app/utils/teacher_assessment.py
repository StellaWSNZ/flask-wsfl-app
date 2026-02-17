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

COL_FUNDER = "Funder"
COL_SCHOOL = "SchoolName"
COL_TERM = "Term"
COL_CALYEAR = "CalendarYear"

COL_TOTAL_CLASSES = "NumberOfClassesTotal"
COL_CLASSES_WITH_ANY_REVIEW = "ClassesWithAnyReview"
COL_TOTAL_REVIEWS = "TotalReviews"
COL_LEAD_REVIEWS = "LeadReviews"
COL_RELIEF_REVIEWS = "ReliefReviews"
COL_MISSING_CLASSES = "MissingClasses"

COL_FUNDERSTAFF = "FunderStaffMember"
DEFAULT_FUNDERSTAFF = "Not assigned"

# Behavior:
# - "auto": show staff column only if it exists in the data
# - True:  always show staff column (add if missing)
# - False: never show staff column
SHOW_FUNDERSTAFF_COLUMN: bool | str = "auto"


# ------------------------------------------------------------
# Pagination helper
# ------------------------------------------------------------
def paginate_rows(df: pd.DataFrame, rows_per_page: int = 28) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []
    pages: List[pd.DataFrame] = []
    for start in range(0, len(df), rows_per_page):
        pages.append(df.iloc[start : start + rows_per_page].reset_index(drop=True))
    return pages


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _normalize_staff_name_col(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if COL_FUNDERSTAFF in df.columns:
        s = df[COL_FUNDERSTAFF].fillna("").astype(str).str.strip()
        df[COL_FUNDERSTAFF] = s.replace("", DEFAULT_FUNDERSTAFF)
    return df


def _ensure_staff_column(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    if SHOW_FUNDERSTAFF_COLUMN is True:
        if COL_FUNDERSTAFF not in df.columns:
            df[COL_FUNDERSTAFF] = DEFAULT_FUNDERSTAFF
        df = _normalize_staff_name_col(df)

    elif SHOW_FUNDERSTAFF_COLUMN is False:
        if COL_FUNDERSTAFF in df.columns:
            df = df.drop(columns=[COL_FUNDERSTAFF])

    else:
        # "auto"
        df = _normalize_staff_name_col(df)

    return df


# ------------------------------------------------------------
# Fetch data
# ------------------------------------------------------------
def _fetch_teacher_df(conn, funder_id: int) -> Tuple[pd.DataFrame, str]:
    sql = text(f"EXEC {PROC_NAME} @FunderID = :funder_id")
    df = pd.read_sql(sql, conn, params={"funder_id": funder_id})

    if df is None or df.empty:
        return df, ""

    funder_name = ""
    if COL_FUNDER in df.columns:
        vals = df[COL_FUNDER].dropna().unique().tolist()
        funder_name = str(vals[0]) if vals else ""

    # staff handling
    df = _ensure_staff_column(df)

    # normalize term/year
    if COL_CALYEAR in df.columns:
        df[COL_CALYEAR] = pd.to_numeric(df[COL_CALYEAR], errors="coerce").astype("Int64")
    if COL_TERM in df.columns:
        df[COL_TERM] = pd.to_numeric(df[COL_TERM], errors="coerce").astype("Int64")

    # normalize numeric columns
    for c in [
        COL_TOTAL_CLASSES,
        COL_CLASSES_WITH_ANY_REVIEW,
        COL_TOTAL_REVIEWS,
        COL_LEAD_REVIEWS,
        COL_RELIEF_REVIEWS,
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    # normalize missing classes (keep line breaks)
    if COL_MISSING_CLASSES in df.columns:
        s = df[COL_MISSING_CLASSES].fillna("").astype(str).str.strip()
        df[COL_MISSING_CLASSES] = s

    return df, funder_name


def _split_by_term(df: pd.DataFrame) -> List[Tuple[int, int, pd.DataFrame]]:
    if df is None or df.empty:
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
        d = df[(df[COL_CALYEAR] == y) & (df[COL_TERM] == t)].copy()

        # drop meta columns
        for col in [COL_CALYEAR, COL_TERM, COL_FUNDER]:
            if col in d.columns:
                d = d.drop(columns=[col])

        # sort by school, then staff (if present)
        sort_cols: List[str] = []
        if COL_SCHOOL in d.columns:
            d[COL_SCHOOL] = d[COL_SCHOOL].fillna("").astype(str)
            sort_cols.append(COL_SCHOOL)
        if COL_FUNDERSTAFF in d.columns:
            d[COL_FUNDERSTAFF] = d[COL_FUNDERSTAFF].fillna("").astype(str)
            sort_cols.append(COL_FUNDERSTAFF)

        if sort_cols:
            d = d.sort_values(sort_cols, ascending=True)

        out.append((int(y), int(t), d.reset_index(drop=True)))

    return out


# ------------------------------------------------------------
# Summaries
# ------------------------------------------------------------
def _term_summary(df_term: pd.DataFrame) -> Dict[str, Any]:
    
    out = {
        "schools": 0,
        "total_classes": 0,
        "classes_with_any_review": 0,
        "total_reviews": 0,
        "lead_reviews": 0,
        "relief_reviews": 0,
    }
    if df_term is None or df_term.empty:
        return out

    if COL_SCHOOL in df_term.columns:
        out["schools"] = int(
            df_term[COL_SCHOOL].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        )

    out["total_classes"] = int(df_term.get(COL_TOTAL_CLASSES, 0).sum())
    out["classes_with_any_review"] = int(df_term.get(COL_CLASSES_WITH_ANY_REVIEW, 0).sum())
    out["total_reviews"] = int(df_term.get(COL_TOTAL_REVIEWS, 0).sum())
    out["lead_reviews"] = int(df_term.get(COL_LEAD_REVIEWS, 0).sum())
    out["relief_reviews"] = int(df_term.get(COL_RELIEF_REVIEWS, 0).sum())

    return out


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
    family = load_ppmori_fonts(str(fonts_dir))

    df_all, funder_name = _fetch_teacher_df(conn, funder_id=funder_id)

    meta: Dict[str, Any] = {
        "funder_name": funder_name,
        "rows": int(0 if df_all is None else len(df_all)),
        "pages": 0,
        "terms": [],
    }

    if df_all is None or df_all.empty:
        return None, meta

    term_blocks = _split_by_term(df_all)
    meta["terms"] = [f"{y}T{t}" for (y, t, _d) in term_blocks]

    # Precompute last rendered page
    render_plan: List[Tuple[int, int, int]] = []
    term_pages_cache: Dict[Tuple[int, int], List[pd.DataFrame]] = {}

    for (y, t, df_term) in term_blocks:
        if df_term is None or df_term.empty:
            continue
        pages_tmp = paginate_rows(df_term, rows_per_page=rows_per_page)
        term_pages_cache[(y, t)] = pages_tmp
        for page_idx in range(1, len(pages_tmp) + 1):
            render_plan.append((y, t, page_idx))

    last_key = render_plan[-1] if render_plan else None

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    preview_fig = None
    page_count = 0

    # Columns shown in table
    # Note: widths assume A3 portrait. Adjust if you switch to A4.
    staff_col = {
        "key": COL_FUNDERSTAFF,
        "label": "Assigned Funder\nStaff Member",
        "width_frac": 0.16,
        "align": "left",
        "wrap": True,
        "max_lines": 2,
    }

    def _make_table_columns(df: pd.DataFrame) -> List[Dict[str, Any]]:
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
{"key": COL_MISSING_CLASSES, "label": "Missing Classes",
 "width_frac": 0.22, "align": "left", "wrap": True}        ]

        keys = set(df.columns)
        return [c for c in cols if c["key"] in keys]

    def row_highlight(row: pd.Series, r: int) -> Optional[Tuple[str, str]]:
        # highlight if not all classes have a review
        try:
            tot = int(row.get(COL_TOTAL_CLASSES, 0) or 0)
            reviewed = int(row.get(COL_CLASSES_WITH_ANY_REVIEW, 0) or 0)
            if tot > 0 and reviewed < tot:
                return "#f4f6ff", "#111111"
        except Exception:
            pass
        return None

    # ---- Layout constants (axes fractions 0..1)
    TABLE_X = 0.02
    TABLE_W = 0.96
    TABLE_Y = 0.12
    TABLE_H = 0.80

    BAR_W = 0.96
    GAP = 0.010

    BAR_H_TERM = 0.022
    BAR_H_NOTE = 0.06
    BAR_H_OVER = 0.022

    for (year, term, df_term) in term_blocks:
        if df_term is None or df_term.empty:
            continue

        pages = term_pages_cache.get((year, term)) or paginate_rows(df_term, rows_per_page=rows_per_page)
        ts = _term_summary(df_term)
        pct_reviewed = (
            round(100 * ts["classes_with_any_review"] / ts["total_classes"])
            if ts["total_classes"] > 0
            else 0
        )

        term_text = (
    f"Term Summary: schools: {ts['schools']}   |   "
    f"total classes: {ts['total_classes']}   |   "
    f"classes reviewed: {ts['classes_with_any_review']} ({pct_reviewed}%)   |   "
    f"total reviews: {ts['total_reviews']}   |   "
    f"lead reviews: {ts['lead_reviews']}   |   "
    f"relief reviews: {ts['relief_reviews']}"
)

        for term_page_idx, df_page in enumerate(pages, start=1):
            page_count += 1
            is_last_rendered_page = (last_key == (year, term, term_page_idx))

            fig, ax = new_page(w, h, dpi)

            # ---- Header bar
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

            # ---- Table
            cols = _make_table_columns(df_page)
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

            # ---- Bottom section with consistent gaps
            table_bottom_y = TABLE_Y

            term_top = table_bottom_y - GAP
            term_cy = term_top - (BAR_H_TERM / 2)

            note_top = (term_cy - BAR_H_TERM / 2) - GAP
            note_cy = note_top - (BAR_H_NOTE / 2)

            over_top = (note_cy - BAR_H_NOTE / 2) - GAP
            over_cy = over_top - (BAR_H_OVER / 2)

            # Term summary bar
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
            draw_text_in_polygon(
                ax,
                poly=term_sum_poly,
                text=term_text,
                fontfamily=family,
                fontsize=11,
                fontweight="semibold",
                color="#1a427d",
                pad_frac=0.06,
                wrap=False,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=1,
            )

            # Note bar
            note_poly = rounded_rect_polygon(
                cx=0.5,
                cy=note_cy,
                width=BAR_W,
                height=BAR_H_NOTE,
                ratio=0.45,
                corners_round=[4,3],
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
            draw_text_in_polygon(
                ax,
                poly=note_poly,
                text=note_text,
                fontfamily=family,
                fontsize=8,
                fontweight="semibold",
                color="#ffffff",
                pad_frac=0.04,
                wrap=True,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=6,
            )

            # Optional overall summary only on the last rendered page
            
            save_page(
                pdf,
                fig,
                footer_png=str(footer_png) if footer_png else None,
                width_in=w,
                height_in=h,
                footer_bottom_margin_frac=0.0,
                footer_max_height_frac=0.20,
            )

            if preview_fig is None:
                preview_fig = fig
            else:
                import matplotlib.pyplot as plt
                plt.close(fig)

    close_pdf(pdf)
    meta["pages"] = page_count
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
