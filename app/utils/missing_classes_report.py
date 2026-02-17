# app/utils/missing_classes_report.py

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, List

import pandas as pd
import matplotlib.patches as mpatches
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.TAB_DataframeTable import draw_dataframe_table_v2
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import close_pdf, new_page, open_pdf, save_page
from app.utils.funder_missing_plot import add_full_width_footer_svg


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _drop_single_value_columns(df: pd.DataFrame, *, always_keep: List[str]) -> pd.DataFrame:
    """Drop columns with only 1 unique non-null value, unless in always_keep."""
    if df is None or df.empty:
        return df

    keep: List[str] = []
    for c in df.columns:
        if c in always_keep:
            keep.append(c)
            continue
        if df[c].dropna().nunique() > 1:
            keep.append(c)

    return df.loc[:, keep].copy()


def _compute_rows_per_page(
    *,
    table_height: float,
    header_height_frac: float,
    min_row_h: float,
    max_rows_per_page: int,
) -> int:
    """
    Compute rows-per-page so each row is at least min_row_h (axes units).
    """
    usable = max(0.0, table_height * (1.0 - header_height_frac))
    if usable <= 0:
        return 1

    rpp = int(usable // max(min_row_h, 1e-9))
    rpp = max(1, min(rpp, max_rows_per_page))
    return rpp


def paginate_rows(df: pd.DataFrame, rows_per_page: int) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []
    pages: List[pd.DataFrame] = []
    for start in range(0, len(df), rows_per_page):
        pages.append(df.iloc[start : start + rows_per_page].reset_index(drop=True))
    return pages


# ------------------------------------------------------------
# Data builder
# ------------------------------------------------------------
def _make_missing_df(
    conn,
    *,
    calendar_year: int,
    term: int,
    funder_id: Optional[int],
    provider_id: Optional[int],
    threshold: float,
    email: Optional[str] = None,
) -> pd.DataFrame:
    sql = text(
        """
        EXEC dbo.FlaskGetMissingClasses
            @CalendarYear = :year,
            @Term         = :term,
            @FunderID     = :funder_id,
            @ProviderID   = :provider_id,
            @Email        = :email,
            @Threshold    = :threshold
        """
    )

    df = pd.read_sql(
        sql,
        conn,
        params={
            "year": calendar_year,
            "term": term,
            "funder_id": funder_id,
            "provider_id": provider_id,
            "email": email,
            "threshold": threshold,
        },
    )

    return df


# ------------------------------------------------------------
# Public PDF builder
# ------------------------------------------------------------
def build_missing_classes_pdf(
    *,
    conn,
    calendar_year: int,
    term: int,
    funder_id: Optional[int] = None,
    provider_id: Optional[int] = None,
    threshold: float = 0.5,
    email: Optional[str] = None,

    out_pdf_path: str | Path,
    footer_png: str | Path,

    dpi: int = 300,
    page_size: str = "A4",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",

    # layout controls
    table_x: float = 0.05,
    table_y: float = 0.12,
    table_w: float = 0.90,
    table_h: float = 0.80,

    # make header a bit bigger than your earlier 0.05
    header_height_frac: float = 0.07,

    # stable row height now that we don't wrap
    min_row_h: float = 0.016,
    max_rows_per_page: int = 25,
) -> tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    """
    Multi-page PDF showing missing (unedited) classes per school.
    One row per missing class.

    Output columns expected from dbo.FlaskGetMissingClasses:
      - FunderName, ProviderName, SchoolName, ClassName, TeacherName
      - (optional extras) MOENumber, CalendarYear, Term, TotalStudents, EditedStudents, EditedRatio
    """
    family = load_ppmori_fonts(str(fonts_dir))

    if funder_id is None and provider_id is None:
        raise ValueError("Provide at least one of funder_id or provider_id.")

    df_raw = _make_missing_df(
        conn,
        calendar_year=calendar_year,
        term=term,
        funder_id=funder_id,
        provider_id=provider_id,
        threshold=threshold,
        email=email,
    )

    meta: Dict[str, Any] = {
        "rows_raw": int(len(df_raw)) if df_raw is not None else 0,
        "rows_display": 0,
        "pages": 0,
        "calendar_year": calendar_year,
        "term": term,
        "funder_id": funder_id,
        "provider_id": provider_id,
        "threshold": threshold,
    }

    if df_raw is None or df_raw.empty:
        return None, meta

    # title bits (optional)
    title_bits: List[str] = []
    if "FunderName" in df_raw.columns and df_raw["FunderName"].dropna().nunique() == 1:
        title_bits.append(str(df_raw["FunderName"].dropna().iloc[0]))
    #if "ProviderName" in df_raw.columns and df_raw["ProviderName"].dropna().nunique() == 1:
    #    title_bits.append(str(df_raw["ProviderName"].dropna().iloc[0]))

    title_prefix = " / ".join([b for b in title_bits if b.strip()]) or "Missing Classes"
    title_prefix = f"{title_prefix} – Missing Classes"

    # Keep these always
    always_keep = ["ProviderName","SchoolName", "ClassName", "TeacherName"]

    # Keep Provider/Funder if present (even if constant you can drop; your call)
    """
    for k in ["ProviderName", "FunderName"]:
        if k in df_raw.columns:
            always_keep.append(k)

    # Optional: keep TotalStudents/EditedStudents if you want (helps sanity-checking)
    for k in ["TotalStudents", "EditedStudents"]:
        if k in df_raw.columns:
            always_keep.append(k)
    """
    df = _drop_single_value_columns(df_raw, always_keep=always_keep)
    df = df.drop(['TotalStudents', 'EditedStudents','EditedRatio'], axis=1)
    # defensive: required columns
    required = [c for c in ["SchoolName", "ClassName", "TeacherName"] if c not in df.columns]
    if required:
        raise KeyError(f"Missing expected columns from proc: {required}. Got: {list(df.columns)}")

    # sort nicely
    sort_cols = [c for c in ["ProviderName", "FunderName", "SchoolName", "ClassName"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)

    meta["rows_display"] = int(len(df))

    rows_per_page = _compute_rows_per_page(
        table_height=table_h,
        header_height_frac=header_height_frac,
        min_row_h=min_row_h,
        max_rows_per_page=max_rows_per_page,
    )

    pages = paginate_rows(df, rows_per_page=rows_per_page)
    meta["pages"] = len(pages)

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    preview_fig = None

    def make_columns(df_page: pd.DataFrame) -> List[Dict[str, Any]]:
        # Choose order + widths
        cols: List[Dict[str, Any]] = []

        ordered: List[str] = []
        for k in ["ProviderName", "FunderName", "SchoolName", "ClassName", "TeacherName", "TotalStudents", "EditedStudents"]:
            if k in df_page.columns:
                ordered.append(k)

        widths = {
            "ProviderName": 0.16,
            "FunderName": 0.16,
            "SchoolName": 0.26,
            "ClassName": 0.20,
            "TeacherName": 0.16,
            "TotalStudents": 0.03,
            "EditedStudents": 0.03,
        }

        labels = {
            "ProviderName": "Provider",
            "FunderName": "Funder",
            "SchoolName": "School",
            "ClassName": "Class",
            "TeacherName": "Teacher",
            "TotalStudents": "Tot",
            "EditedStudents": "Ed",
        }

        aligns = {
            "TotalStudents": "center",
            "EditedStudents": "center",
        }

        for k in ordered:
            cols.append(
                {
                    "key": k,
                    "label": labels.get(k, k),
                    "width_frac": float(widths.get(k, 0.15)),
                    "align": aligns.get(k, "left"),
                }
            )

        # normalize widths to 1.0
        s = sum(c["width_frac"] for c in cols) or 1.0
        for c in cols:
            c["width_frac"] = c["width_frac"] / s

        return cols

    for page_idx, df_page in enumerate(pages, start=1):
        fig, ax = new_page(w, h, dpi)
        ax.set_axis_off()

        # header band
        poly = rounded_rect_polygon(
            cx=0.5,
            cy=0.955,
            width=0.90,
            height=0.05,
            ratio=0.45,
            corners_round=[1, 3],
            n_arc=64,
        )
        ax.add_patch(
            mpatches.Polygon(
                list(poly.exterior.coords),
                closed=True,
                facecolor="#1a427d",
                edgecolor="#1a427d",
                linewidth=1.5,
                transform=ax.transAxes,
            )
        )

        title = f"{title_prefix} (Term {term}, {calendar_year})"
        if len(pages) > 1:
            title = f"{title} (Page {page_idx} of {len(pages)})"

        draw_text_in_polygon(
            ax,
            poly=poly,
            text=title,
            fontfamily=family,
            fontsize=18,
            fontweight="semibold",
            color="#ffffff",
            pad_frac=0.05,
            wrap=True,
            autoshrink=True,
            clip_to_polygon=True,
            max_lines=None,
        )

        cols = make_columns(df_page)

        # KEY CHANGE: no wrap => stable row heights
        draw_dataframe_table_v2(
    ax,
    df=df_page,
    x=table_x,
    y=table_y,
    width=table_w,
    height=table_h,
    header_height_frac=header_height_frac,
    columns=cols,
    base_row_facecolor="#ffffff",
    row_alt_facecolor=None,
    wrap=True,
    max_wrap_lines=3,
    merge_col_indices=[0,1],   # ✅ merge second column
    shift=False,
)

        footer_svg = Path("app/static/footer.svg")

        c = "#1a427d" 
        add_full_width_footer_svg(
            fig,
            footer_svg,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master=c,
        )
        pdf.savefig(fig, dpi=dpi) 
        if page_idx == 1:
            preview_fig = fig
        else:
            import matplotlib.pyplot as plt
            plt.close(fig)

    close_pdf(pdf)
    return preview_fig, meta


# ------------------------------------------------------------
# Optional CLI runner
# ------------------------------------------------------------
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv()

    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    engine = create_engine(os.getenv("db_url"))

    pdf_out = OUT_DIR / "Missing_Classes.pdf"
    footer = Path(__file__).parent /"app"/ "static" / "footer.png"

    with engine.begin() as conn:
        preview, meta = build_missing_classes_pdf(
            conn=conn,
            calendar_year=2025,
            term=4,
            funder_id=6,
            provider_id=None,
            threshold=0.5,
            email=None,
            out_pdf_path=pdf_out,
            footer_png=footer,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows_display']} (raw={meta['rows_raw']})")

    if preview:
        preview_png = OUT_DIR / "Missing_Classes_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")
