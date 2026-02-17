# app/utils/missing_classes_report.py

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
import matplotlib.patches as mpatches
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.TAB_DataframeTable import draw_dataframe_table_v2
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import close_pdf, new_page, open_pdf, save_page


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _drop_single_value_columns(
    df: pd.DataFrame,
    *,
    always_keep: List[str],
) -> pd.DataFrame:
    """Drop columns with only 1 unique non-null value, unless in always_keep."""
    if df is None or df.empty:
        return df

    keep = []
    for c in df.columns:
        if c in always_keep:
            keep.append(c)
            continue

        nunq = df[c].dropna().nunique()
        if nunq > 1:
            keep.append(c)

    # If everything got dropped except keep, that's fine
    return df.loc[:, keep].copy()


def _compute_rows_per_page(
    *,
    n_rows: int,
    table_height: float,
    header_height_frac: float,
    min_row_h: float,
    max_rows_per_page: int = 60,
) -> int:
    """
    Compute rows-per-page so each row is at least min_row_h (axes units).
    """
    if n_rows <= 0:
        return 0

    usable = max(0.0, table_height * (1.0 - header_height_frac))
    if usable <= 0:
        return 1

    rpp = int(usable // max(min_row_h, 1e-6))
    rpp = max(1, min(rpp, max_rows_per_page))
    return rpp


def paginate_rows(df: pd.DataFrame, rows_per_page: int) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []
    pages = []
    for start in range(0, len(df), rows_per_page):
        pages.append(df.iloc[start : start + rows_per_page].reset_index(drop=True))
    return pages


def _infer_title(df: pd.DataFrame, fallback: str) -> str:
    # If you dropped constant columns, title should come from the original df earlier.
    return fallback


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
) -> pd.DataFrame:
    sql = text(
        """
        EXEC dbo.FlaskGetMissingClasses
            @CalendarYear = :year,
            @Term         = :term,
            @FunderID     = :funder_id,
            @ProviderID   = :provider_id,
            @Email        = NULL,
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
            "threshold": threshold,
        },
    )

    # Normalize columns expected by the proc
    # (These are what we used in the proc output)
    # FunderName, ProviderName, SchoolName, MOENumber, CalendarYear, Term, MissingClasses
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
    header_height_frac: float = 0.05,
    min_row_h: float = 0.018,          # <- protects against tiny rows
    max_rows_per_page: int = 55,

    wrap_max_lines: int = 4,
) -> tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    """
    Multi-page PDF showing missing (unedited) classes per school.

    - Runs dbo.FlaskGetMissingClassesByProviderOrFunder
    - Drops single-value columns (unless always_keep)
    - Wraps long text
    - Ensures rows don't get too small via min_row_h
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
    )

    meta: Dict[str, Any] = {
        "rows_raw": int(len(df_raw)),
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

    # Title source BEFORE we drop constant columns
    # (If only funder_id or provider_id used, the proc may return constant names)
    title_bits = []
    if "FunderName" in df_raw.columns and df_raw["FunderName"].dropna().nunique() == 1:
        title_bits.append(str(df_raw["FunderName"].dropna().iloc[0]))
    if "ProviderName" in df_raw.columns and df_raw["ProviderName"].dropna().nunique() == 1:
        title_bits.append(str(df_raw["ProviderName"].dropna().iloc[0]))

    title_prefix = " / ".join([b for b in title_bits if b.strip()]) or "Missing Classes"
    title_prefix = f"{title_prefix} – Missing Classes"

    # Drop single-value columns to keep table clean
    always_keep = ["SchoolName", "MissingClasses"]  # these should always remain
    # Keep FunderName/ProviderName only if they vary
    df = _drop_single_value_columns(df_raw, always_keep=always_keep)

    # Defensive: ensure key columns exist
    # (If something changes in the proc, fail loudly)
    for col in ["SchoolName", "MissingClasses"]:
        if col not in df.columns:
            raise KeyError(f"Expected column {col!r} not in result. Got: {list(df.columns)}")

    # Optional: nicer ordering
    sort_cols = [c for c in ["ProviderName", "FunderName", "SchoolName"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)

    meta["rows_display"] = int(len(df))

    # Compute rows per page from min row height
    rows_per_page = _compute_rows_per_page(
        n_rows=len(df),
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

    # Build column definitions dynamically
    # Give MissingClasses lots of width + wrap
    def make_columns(df_page: pd.DataFrame) -> List[Dict[str, Any]]:
        cols: List[Dict[str, Any]] = []
        keys = list(df_page.columns)

        # Preferred order
        preferred = [k for k in ["ProviderName", "FunderName", "SchoolName"] if k in keys]
        preferred.append("MissingClasses")
        ordered = preferred + [k for k in keys if k not in preferred]

        # Width allocation
        # If funder/provider columns present, allocate smaller widths; MissingClasses gets the most.
        has_provider = "ProviderName" in ordered
        has_funder = "FunderName" in ordered

        if has_provider and has_funder:
            widths = {
                "ProviderName": 0.18,
                "FunderName": 0.18,
                "SchoolName": 0.24,
                "MissingClasses": 0.40,
            }
        elif has_provider or has_funder:
            single = "ProviderName" if has_provider else "FunderName"
            widths = {
                single: 0.22,
                "SchoolName": 0.28,
                "MissingClasses": 0.50,
            }
        else:
            widths = {
                "SchoolName": 0.30,
                "MissingClasses": 0.70,
            }

        for k in ordered:
            label = {
                "ProviderName": "Provider",
                "FunderName": "Funder",
                "SchoolName": "School",
                "MissingClasses": "Missing classes\n(Class (Teacher))",
            }.get(k, k)

            align = "left" if k in ("ProviderName", "FunderName", "SchoolName", "MissingClasses") else "left"
            cols.append(
                {
                    "key": k,
                    "label": label,
                    "width_frac": float(widths.get(k, 0.15)),
                    "align": align,
                }
            )

        # Re-normalize widths to sum to 1.0 (safety)
        s = sum(c["width_frac"] for c in cols) or 1.0
        for c in cols:
            c["width_frac"] = c["width_frac"] / s

        return cols

    for page_idx, df_page in enumerate(pages, start=1):
        fig, ax = new_page(w, h, dpi)
        ax.set_axis_off()

        # Header band
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
            wrap=True,
            max_wrap_lines=wrap_max_lines,
            shift=False,
        )

        save_page(
            pdf,
            fig,
            footer_png=str(footer_png),
            width_in=w,
            height_in=h,
            footer_bottom_margin_frac=0.0,
            footer_max_height_frac=0.20,
        )

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
    footer = Path(__file__).parent / "static" / "footer.png"

    with engine.begin() as conn:
        preview, meta = build_missing_classes_pdf(
            conn=conn,
            calendar_year=2026,
            term=1,
            provider_id=None,   # or funder_id=...
            funder_id=17,
            threshold=0.5,
            out_pdf_path=pdf_out,
            footer_png=footer,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows_display']} (raw={meta['rows_raw']})")

    if preview:
        preview_png = OUT_DIR / "Missing_Classes_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")
