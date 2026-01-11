from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
import matplotlib.patches as mpatches
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.TAB_DataframeTable import draw_dataframe_table_v2
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import close_pdf, new_page, open_pdf, save_page


# ------------------------------------------------------------
# Pagination helper
# ------------------------------------------------------------
def paginate_month_blocks_end_with_total(
    df: pd.DataFrame,
    rows_per_page: int = 30,
) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []

    month_order = sorted(df["MonthLabel"].drop_duplicates().tolist())

    pages: List[pd.DataFrame] = []
    current_parts: List[pd.DataFrame] = []
    current_n = 0

    for m in month_order:
        g = df[df["MonthLabel"] == m].copy()

        if (g["ProviderName"] == "Total").any():
            g = pd.concat(
                [g[g["ProviderName"] != "Total"], g[g["ProviderName"] == "Total"]],
                ignore_index=True,
            )

        g_n = len(g)

        if current_n + g_n <= rows_per_page:
            current_parts.append(g)
            current_n += g_n
            continue

        if current_parts:
            pages.append(pd.concat(current_parts, ignore_index=True))
            current_parts = []
            current_n = 0

        if g_n > rows_per_page:
            total_row = g[g["ProviderName"] == "Total"].tail(1)
            details = g[g["ProviderName"] != "Total"]

            chunk_size = max(1, rows_per_page - len(total_row))
            for start in range(0, len(details), chunk_size):
                chunk = details.iloc[start : start + chunk_size]
                page = pd.concat([chunk, total_row], ignore_index=True)
                pages.append(page.reset_index(drop=True))
            continue

        current_parts.append(g)
        current_n = g_n

    if current_parts:
        pages.append(pd.concat(current_parts, ignore_index=True))

    return [p.reset_index(drop=True) for p in pages]


# ------------------------------------------------------------
# Data builder
# ------------------------------------------------------------
def _make_counts_df(
    conn,
    funder_id: int,
    term: int | None,
    year: int | None,
) -> tuple[pd.DataFrame, str]:    
    sql = text("""
        EXEC dbo.MonthlyStudentCounts
            @FunderID = :funder_id,
            @Term = :term,
            @CalendarYear = :year
    """)

    df = pd.read_sql(
        sql,
        conn,
        params={
            "funder_id": funder_id,
            "term": term,
            "year": year,
        },
    )
    if df.empty:
        return df, ""

    funder_name = str(df["FunderName"].unique()[0])

    df = df[df["ProviderCumulativeCount"] != 0][
        [
            "MonthLabel",
            "Month",
            "FunderCumulativeCount",
            "ProviderName",
            "ProviderCumulativeCount",
        ]
    ].copy()

    totals = (
        df[["MonthLabel", "Month", "FunderCumulativeCount"]]
        .drop_duplicates()
        .assign(
            ProviderName="Total",
            ProviderCumulativeCount=lambda d: d["FunderCumulativeCount"],
            IsTotal=True,
        )
    )

    df = df.assign(IsTotal=False)

    df = (
        pd.concat([df, totals], ignore_index=True)
        .sort_values(
            by=["MonthLabel", "IsTotal", "ProviderName"],
            ascending=[True, True, True],
        )
        .loc[:, ["MonthLabel", "Month", "ProviderName", "ProviderCumulativeCount"]]
        .reset_index(drop=True)
    )

    return df, funder_name


# ------------------------------------------------------------
# Public PDF builder (Option A)
# ------------------------------------------------------------
def build_funder_student_counts_pdf(
    *,
    conn,
    funder_id: int,
    term: int | None,
    year: int | None,

    out_pdf_path: str | Path,
    footer_png: str | Path,
    rows_per_page: int = 35,
    dpi: int = 300,
    page_size: str = "A4",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
) -> tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    """
    Writes a multi-page PDF to out_pdf_path.

    Returns:
        preview_fig : matplotlib Figure for page 1 (not closed)
        meta        : dict with page / row counts
    """
    family = load_ppmori_fonts(str(fonts_dir))   # make your loader RETURN chosen


    df, funder_name = _make_counts_df(conn, funder_id, term, year)
    meta: Dict[str, Any] = {
        "funder_name": funder_name,
        "pages": 0,
        "rows": int(len(df)),
    }

    if df is None or df.empty:
        return None, meta

    pages = paginate_month_blocks_end_with_total(df, rows_per_page=rows_per_page)
    meta["pages"] = len(pages)

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    def row_highlight(row: pd.Series, r: int) -> Optional[Tuple[str, str]]:
        if row.get("ProviderName") == "Total":
            return "#1a427d", "#eeeeee"
        return None

    preview_fig = None

    for page_idx, df_page in enumerate(pages, start=1):
        fig, ax = new_page(w, h, dpi)

        poly = rounded_rect_polygon(
            cx=0.5,
            cy=0.955,
            width=0.9,
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

        title = f"{funder_name} Student Counts"
        if len(pages) > 1:
            title = f"{title} (Page {page_idx} of {len(pages)})"

        draw_text_in_polygon(
            ax,
            poly=poly,
            text=title,
            fontfamily=family,
            fontsize=20,
            fontweight="semibold",
            color="#ffffff",
            pad_frac=0.05,
            wrap=True,
            autoshrink=True,
            clip_to_polygon=True,
            max_lines=None
        )

        draw_dataframe_table_v2(
            ax,
            df=df_page,
            x=0.05,
            y=0.12,
            width=0.9,
            height=0.8,
            header_height_frac=0.04,
            columns=[
                {"key": "Month", "label": "Month", "width_frac": 0.2, "align": "left"},
                {"key": "ProviderName", "label": "Provider", "width_frac": 0.34, "align": "left"},
                {
                    "key": "ProviderCumulativeCount",
                    "label": "Cumulative Count",
                    "width_frac": 0.34,
                    "align": "right",
                },
            ],
            base_row_facecolor="#ffffff",
            row_color_fn=row_highlight,
            merge_first_col=True,
            merge_key="Month",
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
# Optional CLI runner (keeps Option A runnable)
# ------------------------------------------------------------
if __name__ == "__main__":
    import os
    from sqlalchemy import create_engine
    from dotenv import load_dotenv

    load_dotenv()

    FUNDER_ID = 17
    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    engine = create_engine(os.getenv("db_url"))
    footer = Path(__file__).parent / "static" / "footer.png"
    pdf_out = OUT_DIR / f"Funder_{FUNDER_ID}_Student_Counts.pdf"

    with engine.begin() as conn:
        preview, meta = build_funder_student_counts_pdf(
            conn=conn,
            funder_id=FUNDER_ID,
            out_pdf_path=pdf_out,
            footer_png=footer,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows']}")

    if preview:
        preview_png = OUT_DIR / f"Funder_{FUNDER_ID}_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")
