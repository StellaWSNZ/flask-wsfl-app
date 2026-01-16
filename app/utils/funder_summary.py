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


# ------------------------------------------------------------
# Pagination helper: keep Provider blocks together (like Month blocks)
# ------------------------------------------------------------
def paginate_provider_blocks(
    df: pd.DataFrame,
    rows_per_page: int = 28,
    provider_col: str = "Provider",
) -> List[pd.DataFrame]:
    """
    Paginate df so that provider groups are not split across pages when possible.
    If a single provider group exceeds rows_per_page, it will be split.
    """
    if df is None or df.empty:
        return []

    prov_order = df[provider_col].fillna("").drop_duplicates().tolist()

    pages: List[pd.DataFrame] = []
    current_parts: List[pd.DataFrame] = []
    current_n = 0

    for prov in prov_order:
        g = df[df[provider_col].fillna("") == prov].copy()
        g_n = len(g)

        # fits on current page
        if current_n + g_n <= rows_per_page:
            current_parts.append(g)
            current_n += g_n
            continue

        # flush current page first
        if current_parts:
            pages.append(pd.concat(current_parts, ignore_index=True))
            current_parts = []
            current_n = 0

        # provider group itself bigger than a page -> split
        if g_n > rows_per_page:
            for start in range(0, g_n, rows_per_page):
                pages.append(g.iloc[start : start + rows_per_page].reset_index(drop=True))
            continue

        # start new page with this provider group
        current_parts.append(g)
        current_n = g_n

    if current_parts:
        pages.append(pd.concat(current_parts, ignore_index=True))

    return [p.reset_index(drop=True) for p in pages]


# ------------------------------------------------------------
# Data builder: call stored proc + shape into per-term dataframes
# ------------------------------------------------------------
def _fetch_progress_df(
    conn,
    funder_id: int,
    from_year: int = 2025,
    threshold: float = 0.2,
) -> Tuple[pd.DataFrame, str]:
    """
    Calls dbo.FunderProgressSummary and returns (df, funder_name).
    """
    sql = text(
        """
        EXEC dbo.FunderProgressSummary
            @FunderID = :funder_id,
            @threshold = :threshold,
            @FromYear = :from_year
        """
    )

    df = pd.read_sql(
        sql,
        conn,
        params={
            "funder_id": funder_id,
            "threshold": threshold,
            "from_year": from_year,
        },
    )

    if df is None or df.empty:
        return df, ""

    # funder name should be constant in result set
    funder_name = str(df["Funder"].dropna().unique()[0]) if "Funder" in df.columns else ""

    # normalize expected types
    if "CalendarYear" in df.columns:
        df["CalendarYear"] = pd.to_numeric(df["CalendarYear"], errors="coerce").astype("Int64")
    if "Term" in df.columns:
        df["Term"] = pd.to_numeric(df["Term"], errors="coerce").astype("Int64")

    return df, funder_name


def _split_by_term(df: pd.DataFrame) -> List[Tuple[int, int, pd.DataFrame]]:
    """
    Returns list of (CalendarYear, Term, df_term) ordered chronologically.
    """
    if df is None or df.empty:
        return []

    if "CalendarYear" not in df.columns or "Term" not in df.columns:
        raise ValueError("Expected columns CalendarYear and Term in stored proc output.")

    out: List[Tuple[int, int, pd.DataFrame]] = []
    keys = (
        df[["CalendarYear", "Term"]]
        .drop_duplicates()
        .sort_values(["CalendarYear", "Term"], ascending=[True, True])
        .itertuples(index=False, name=None)
    )

    for y, t in keys:
        d = df[(df["CalendarYear"] == y) & (df["Term"] == t)].copy()

        # remove columns you don't want in the table
        # (you asked: all columns except CalendarYear, Term, Funder)
        for col in ["CalendarYear", "Term", "Funder"]:
            if col in d.columns:
                d = d.drop(columns=[col])

        # sensible ordering: Provider then School
        sort_cols = [c for c in ["Provider", "School"] if c in d.columns]
        if sort_cols:
            d = d.sort_values(sort_cols, ascending=True)

        out.append((int(y), int(t), d.reset_index(drop=True)))

    return out


# ------------------------------------------------------------
# Public PDF builder
# ------------------------------------------------------------
def build_funder_progress_summary_pdf(
    *,
    conn,
    funder_id: int,
    from_year: int,
    threshold: float,

    out_pdf_path: str | Path,
    footer_png: str | Path,
    rows_per_page: int = 28,
    dpi: int = 300,
    page_size: str = "A3",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
) -> Tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    """
    Multi-page PDF: one section per term (new page for each term).
    Provider cells are merged down like Month in your other report.

    Returns:
        preview_fig : matplotlib Figure for first page (not closed)
        meta        : dict with page / row counts
    """
    family = load_ppmori_fonts(str(fonts_dir))

    df_all, funder_name = _fetch_progress_df(
        conn,
        funder_id=funder_id,
        from_year=from_year,
        threshold=threshold,
    )

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

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    preview_fig = None
    page_count = 0

    # Table column config (edit labels/widths if you want)
    # Must match the post-drop columns (no CalendarYear/Term/Funder)
    table_columns = [
        {"key": "Provider", "label": "Provider", "width_frac": 0.22, "align": "left","wrap": True,"max_lines": 3,},
        {"key": "School", "label": "School", "width_frac": 0.22, "align": "left","wrap": True,"max_lines": 3,},
        {"key": "EQI", "label": "EQI", "width_frac": 0.06, "align": "center","wrap": True,},
        {
            "key": "Students Progressed (Progressed/Total)",
            "label": "Students Count\n(Progressed/Total)",
            "width_frac": 0.18,
            "align": "center",
        },
        {"key": "Classes Edited (Edited/Total)", "label": "Classes Count\n(Edited/Total)", "width_frac": 0.18, "align": "center"},
        {"key": "Classes with no progress", "label": "Classes With\nNo progress", "width_frac": 0.18, "align": "center"},
    ]

    # Only keep columns that exist (so it doesn't crash if you rename one)
    def _filter_table_columns(df: pd.DataFrame) -> List[Dict[str, Any]]:
        keys = set(df.columns)
        return [c for c in table_columns if c["key"] in keys]

    def row_highlight(row: pd.Series, r: int) -> Optional[Tuple[str, str]]:
        # you can highlight something if you want (eg: no progress)
        # Example: highlight rows where "Classes with no progress" > 0
        try:
            if "Classes with no progress" in row and pd.notna(row["Classes with no progress"]):
                if int(row["Classes with no progress"]) > 0:
                    return "#f4f6ff", "#111111"  # light background, dark text
        except Exception:
            pass
        return None

    for (year, term, df_term) in term_blocks:
        if df_term is None or df_term.empty:
            # still produce a page with "No data" if you want; for now, skip
            continue

        # paginate within this term
        pages = paginate_provider_blocks(df_term, rows_per_page=rows_per_page, provider_col="Provider")

        for term_page_idx, df_page in enumerate(pages, start=1):
            page_count += 1
            fig, ax = new_page(w, h, dpi)

            # ---- Header bar (rounded rect)
            poly = rounded_rect_polygon(
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
                    list(poly.exterior.coords),
                    closed=True,
                    facecolor="#1a427d",
                    edgecolor="#1a427d",
                    linewidth=1.5,
                    transform=ax.transAxes,
                )
            )

            title = f"{funder_name} – {year} Term {term}"
            if len(pages) > 1:
                title = f"{title} (Page {term_page_idx} of {len(pages)})"

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
                max_lines=None,
            )

            # ---- Table
            cols = _filter_table_columns(df_page)

            draw_dataframe_table_v2(
                ax,
                df=df_page,
                x=0.02,
                y=0.12,
                width=0.96,
                height=0.80,
                header_height_frac=0.042,
                columns=cols,
                base_row_facecolor="#ffffff",
                row_color_fn=row_highlight,

                # ✅ merge provider column cells like Month merge in your other report
                merge_first_col=True,
                merge_key="Provider",
                shift = False,
             
            )
            poly = rounded_rect_polygon(
                cx=0.5,
                cy=0.08,
                width=0.96,
                height=0.025,
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
            draw_text_in_polygon(
                ax,
                poly=poly,
                text=f"For a student to progress they need to have at least one new competency marked as achieved.\nFor a class to be edited {threshold * 100}% of students must be marked as complete.",
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
            save_page(
                pdf,
                fig,
                footer_png=str(footer_png),
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

    FUNDER_ID = 17
    FROM_YEAR = 2025
    THRESHOLD = 0.1

    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    engine = create_engine(os.getenv("db_url"))
    footer = Path(__file__).parent / "static" / "footer.png"
    pdf_out = OUT_DIR / f"WSFL_FunderProgressSummary_{FUNDER_ID}_{FROM_YEAR}.pdf"

    with engine.begin() as conn:
        preview, meta = build_funder_progress_summary_pdf(
            conn=conn,
            funder_id=FUNDER_ID,
            from_year=FROM_YEAR,
            threshold=THRESHOLD,
            out_pdf_path=pdf_out,
            footer_png=footer,
            rows_per_page=28,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows']} | Terms: {meta['terms']}")

    if preview:
        preview_png = OUT_DIR / f"WSFL_FunderProgressSummary_{FUNDER_ID}_{FROM_YEAR}_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")
