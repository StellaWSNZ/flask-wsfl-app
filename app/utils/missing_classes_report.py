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
from app.report_utils.pdf_builder import close_pdf, new_page, open_pdf
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
    """Compute rows-per-page so each row is at least min_row_h (axes units)."""
    usable = max(0.0, table_height * (1.0 - header_height_frac))
    if usable <= 0:
        return 1

    rpp = int(usable // max(min_row_h, 1e-9))
    rpp = max(1, min(rpp, max_rows_per_page))
    return rpp


def paginate_rows(df: pd.DataFrame, rows_per_page: int) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []
    return [df.iloc[i : i + rows_per_page].reset_index(drop=True) for i in range(0, len(df), rows_per_page)]


def _report_mode(funder_id: Optional[int], provider_id: Optional[int]) -> str:
    if funder_id is not None and provider_id is not None:
        return "both"
    if funder_id is not None:
        return "funder"
    if provider_id is not None:
        return "provider"
    return "none"


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

    return pd.read_sql(
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
    footer_svg: str | Path = "app/static/footer.svg",
    dpi: int = 300,
    page_size: str = "A4",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
    # layout controls
    table_x: float = 0.05,
    table_y: float = 0.07,
    table_w: float = 0.90,
    table_h: float = 0.85,
    header_height_frac: float = 0.07,
    min_row_h: float = 0.016,
    max_rows_per_page: int = 25,
) -> tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    """
    Multi-page PDF showing missing (unedited) classes per school.
    One row per missing class.

    Modes:
      - funder_id only:    Provider + School + Class + Teacher
      - provider_id only:  Funder   + School + Class + Teacher
      - both IDs:          School + Class + Teacher
    """
    family = load_ppmori_fonts(str(fonts_dir))

    if funder_id is None and provider_id is None:
        raise ValueError("Provide at least one of funder_id or provider_id.")

    mode = _report_mode(funder_id, provider_id)

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
        "mode": mode,
    }

    if df_raw is None or df_raw.empty:
        return None, meta

    # ---- Title (prefer names if single-valued in slice) ----
    title_bits: List[str] = []
    if "FunderName" in df_raw.columns and df_raw["FunderName"].dropna().nunique() == 1:
        title_bits.append(str(df_raw["FunderName"].dropna().iloc[0]))
    if "ProviderName" in df_raw.columns and df_raw["ProviderName"].dropna().nunique() == 1:
        # only include provider in title when we're not printing provider column
        if mode in ("provider", "both"):
            title_bits.append(str(df_raw["ProviderName"].dropna().iloc[0]))

    title_prefix = " / ".join([b for b in title_bits if b.strip()]) or "Missing Classes"
    title_prefix = f"{title_prefix} – Missing Classes"

    # ---- Always-keep based on mode (controls _drop_single_value_columns) ----
    if mode == "funder":
        always_keep = ["ProviderName", "SchoolName", "ClassName", "TeacherName"]
    elif mode == "provider":
        always_keep = ["FunderName", "SchoolName", "ClassName", "TeacherName"]
    else:  # both
        always_keep = ["SchoolName", "ClassName", "TeacherName"]

    df = _drop_single_value_columns(df_raw, always_keep=always_keep)

    # Drop optional metrics (safe)
    df = df.drop(["TotalStudents", "EditedStudents", "EditedRatio"], axis=1, errors="ignore")

    # defensive: required columns
    required = [c for c in ["SchoolName", "ClassName", "TeacherName"] if c not in df.columns]
    if required:
        raise KeyError(f"Missing expected columns from proc: {required}. Got: {list(df.columns)}")

    # sort nicely
    if mode == "funder":
        sort_pref = ["ProviderName", "SchoolName", "ClassName"]
    elif mode == "provider":
        sort_pref = ["FunderName", "SchoolName", "ClassName"]
    else:
        sort_pref = ["SchoolName", "ClassName"]

    sort_cols = [c for c in sort_pref if c in df.columns]
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
        cols: List[Dict[str, Any]] = []

        if mode == "funder":
            ordered = ["ProviderName", "SchoolName", "ClassName", "TeacherName"]
            widths = {"ProviderName": 0.20, "SchoolName": 0.30, "ClassName": 0.26, "TeacherName": 0.24}
        elif mode == "provider":
            ordered = ["FunderName", "SchoolName", "ClassName", "TeacherName"]
            widths = {"FunderName": 0.20, "SchoolName": 0.30, "ClassName": 0.26, "TeacherName": 0.24}
        else:  # both
            ordered = ["SchoolName", "ClassName", "TeacherName"]
            widths = {"SchoolName": 0.38, "ClassName": 0.32, "TeacherName": 0.30}

        labels = {
            "ProviderName": "Provider",
            "FunderName": "Funder",
            "SchoolName": "School",
            "ClassName": "Class",
            "TeacherName": "Teacher",
        }

        for k in ordered:
            if k in df_page.columns:
                cols.append(
                    {
                        "key": k,
                        "label": labels.get(k, k),
                        "width_frac": float(widths.get(k, 0.25)),
                        "align": "left",
                    }
                )

        s = sum(c["width_frac"] for c in cols) or 1.0
        for c in cols:
            c["width_frac"] = c["width_frac"] / s
        return cols

    footer_svg_path = Path(footer_svg)
    if not footer_svg_path.is_absolute():
        # make it robust when running from anywhere
        footer_svg_path = Path(__file__).resolve().parents[1] / "static" / footer_svg_path.name

    c_master = "#1a427d"

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
                facecolor=c_master,
                edgecolor=c_master,
                linewidth=1.5,
                transform=ax.transAxes,
            )
        )

        title = f"{title_prefix} (Term {term}, {calendar_year})"
        if len(pages) > 1:
            title = f"{title}"

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

        # merge indices depend on visible columns
        if mode in ("funder", "provider"):
            merge_cols = [0, 1]  # (Provider/Funder) + School
        else:
            merge_cols = [0]     # School only

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
            merge_col_indices=merge_cols,
            shift=False,
        )

        
        pct = int(round(threshold * 100))
        fs = 4 # <- one font size for both pills

        # ---- left pill (message) ----
        poly_left = rounded_rect_polygon(
            cx=0.27,          # left-ish
            cy=0.055,
            width=0.44,       # wider for the long message
            height=0.015,
            ratio=0.45,
            corners_round=[1,3],  # or keep [1,3] if you want only 2 corners rounded
            n_arc=64,
        )
        ax.add_patch(
            mpatches.Polygon(
                list(poly_left.exterior.coords),
                closed=True,
                facecolor=c_master,
                edgecolor=c_master,
                linewidth=1.5,
                transform=ax.transAxes,
            )
        )
        draw_text_in_polygon(
            ax,
            poly=poly_left,
            text=f"Classes with less than {pct}% of students edited.",
            fontfamily=family,
            fontsize=fs,          # same
            color="#ffffff",
            pad_frac=0.06,
            wrap=False,
            autoshrink=True,     # <- forces same size
            clip_to_polygon=True,
            max_lines=1,
        )

        # ---- right pill (page x of y) ----
        poly_right = rounded_rect_polygon(
            cx=0.88,          # right-ish
            cy=0.055,
            width=0.14,       # narrower for the page text
            height=0.015,
            ratio=0.45,
            corners_round=[2, 4],
            n_arc=64,
        )
        ax.add_patch(
            mpatches.Polygon(
                list(poly_right.exterior.coords),
                closed=True,
                facecolor=c_master,
                edgecolor=c_master,
                linewidth=1.5,
                transform=ax.transAxes,
            )
        )
        draw_text_in_polygon(
            ax,
            poly=poly_right,
            text=f"Page {page_idx} of {len(pages)}",
            fontfamily=family,
            fontsize=fs,          # same
            color="#ffffff",
            pad_frac=0.10,
            wrap=False,
            autoshrink=True,     # <- forces same size
            clip_to_polygon=True,
            max_lines=1,
        )
        add_full_width_footer_svg(
            fig,
            footer_svg_path,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master=f"{c_master}80",
        )

        # ✅ write page to PDF
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

    with engine.begin() as conn:
        preview, meta = build_missing_classes_pdf(
            conn=conn,
            calendar_year=2025,
            term=4,
            funder_id=2,
            provider_id=2,   # set this to test provider/both modes
            threshold=0.5,
            email=None,
            out_pdf_path=pdf_out,
            footer_svg="app/static/footer.svg",
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows_display']} (raw={meta['rows_raw']}) | Mode: {meta['mode']}")

    if preview:
        preview_png = OUT_DIR / "Missing_Classes_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")
