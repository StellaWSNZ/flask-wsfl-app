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
# Config
# ============================================================
PROC_NAME = "dbo.GetStuffFundersSummary"  # <-- update if you named it differently

COL_FUNDER_MAPPED = "FunderNameMapped"
COL_TARGET = "TargetStudents"
COL_NUM = "NumStudents"
COL_FACILITIES = "FacilitiesProviding"
COL_AWAIT = "SchoolsAwaitingData"

TITLE_TEXT = "Life Savings Campaign Student Co unts"


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
# Fetch + shape
# ------------------------------------------------------------
def _fetch_campaign_df(conn) -> pd.DataFrame:
    sql = text(
        f"EXEC {PROC_NAME}"
    )
    df = pd.read_sql(sql, conn)

    if df is None or df.empty:
        return df

    # normalize expected columns
    for c in [COL_TARGET, COL_NUM]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
        else:
            df[c] = 0

    for c in [COL_FUNDER_MAPPED, COL_FACILITIES, COL_AWAIT]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
        else:
            df[c] = ""

    # Build the exact output columns requested
    out = pd.DataFrame({
        "Funder": df[COL_FUNDER_MAPPED],
        "Students": df[COL_NUM].astype(int),
        "Target": df[COL_TARGET].astype(int),
        "% to target": (df[COL_NUM] / df[COL_TARGET].replace(0, pd.NA)).astype(float),
        "Facilities": df[COL_FACILITIES],
        "Schools awaiting data": df[COL_AWAIT],
    })

    # Format % nicely as a string for the PDF table
    out["% to target"] = (out["% to target"] * 100).round(0).fillna(0).astype(int).astype(str) + "%"
    print(out["Facilities"])
    # Optional: if you want negative gaps to show as 0 (or keep negative to show “over target”)
    # out["Target - Students"] = out["Target - Students"].clip(lower=0)
       # nice sorting
    out = out.sort_values(["Funder"], ascending=True).reset_index(drop=True)
    return out


# ------------------------------------------------------------
# PDF builder
# ------------------------------------------------------------
def build_lifesavings_campaign_counts_pdf(
    *,
    conn,
    funder_id: int,                      # likely 20
    out_pdf_path: str | Path,
    footer_png: str | Path | None,
    rows_per_page: int = 26,
    dpi: int = 300,
    page_size: str = "A3",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
    use_school_year_scope: bool = True,  # 2025 T3-4 + 2026 T1-2
) -> Tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:

    family = load_ppmori_fonts(str(fonts_dir))
    df_all = _fetch_campaign_df(conn)
    if footer_png is None:
        footer_png = Path(__file__).resolve().parents[1] / "static" / "footer.png"

    footer_path = None
    if footer_png is not None:
        footer_path = Path(footer_png).expanduser().resolve()
        if not footer_path.exists():
            print(f"⚠️ footer not found: {footer_path}")
            footer_path = None
    meta: Dict[str, Any] = {
        "rows": int(0 if df_all is None else len(df_all)),
        "pages": 0,
        "funder_id": funder_id,
        "scope": "school_year" if use_school_year_scope else "all",
    }

    if df_all is None or df_all.empty:
        return None, meta

    pages = paginate_rows(df_all, rows_per_page=rows_per_page)

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    preview_fig = None
    page_count = 0

    # ---- Layout constants
    TABLE_X = 0.02
    TABLE_W = 0.96
    TABLE_Y = 0.12
    TABLE_H = 0.80

    for page_idx, df_page in enumerate(pages, start=1):
        page_count += 1

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

        title = TITLE_TEXT
        if len(pages) > 1:
            title = f"{title} (Page {page_idx} of {len(pages)})"

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

        # ---- Table columns (last two behave like Missing Classes: wrap + wide)
        columns = [
            {"key": "Funder", "label": "Funder", "width_frac": 0.23, "align": "left", "wrap": True, "max_lines": 2},
            {"key": "Students", "label": "Students", "width_frac": 0.08, "align": "center"},
            {"key": "Target", "label": "Target", "width_frac": 0.08, "align": "center"},
            {"key": "% to target", "label": "% to target", "width_frac": 0.08, "align": "center"},
            {"key": "Facilities", "label": "Facilities", "width_frac": 0.25, "align": "left", "wrap": True, "max_lines": 8},
            {"key": "Schools awaiting data", "label": "Schools awaiting data", "width_frac": 0.30, "align": "left", "wrap": True, "max_lines": 8},
        ]

        # Optional highlight: if gap > 0 (still below target)
        def row_highlight(row: pd.Series, r: int):
            try:
                gap = int(row.get("Target - Students", 0) or 0)
                if gap > 0:
                    return "#f4f6ff", "#111111"
            except Exception:
                pass
            return None

        draw_dataframe_table_v2(
            ax,
            df=df_page,
            x=TABLE_X,
            y=TABLE_Y,
            width=TABLE_W,
            height=TABLE_H,
            header_height_frac=0.042,
            columns=columns,
            body_fontsize= 11,
            base_row_facecolor="#ffffff",
            row_color_fn=row_highlight,
            merge_first_col=False,
            merge_key="",
            wrap=True,
            max_wrap_lines=10,
            shift=True,
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

        if preview_fig is None:
            preview_fig = fig
        else:
            import matplotlib.pyplot as plt
            plt.close(fig)

    close_pdf(pdf)
    meta["pages"] = page_count
    return preview_fig, meta

def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, pool_pre_ping=True, fast_executemany=True)

# ------------------------------------------------------------
# Optional CLI runner
# ------------------------------------------------------------
if __name__ == "__main__":
    import os
    from sqlalchemy import create_engine
    from dotenv import load_dotenv

    load_dotenv()

    FUNDER_ID = 20  # Lifesavings
    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)
    print(os.getenv("DB_URL"))
    engine = get_db_engine()
    footer = Path(__file__).parent / "static" / "footer.png"
    pdf_out = OUT_DIR / f"LifeSavingsCampaign_StudentCounts.pdf"

    with engine.begin() as conn:
        preview, meta = build_lifesavings_campaign_counts_pdf(
            conn=conn,
            funder_id=FUNDER_ID,
            out_pdf_path=pdf_out,
            footer_png=footer if footer.exists() else None,
            rows_per_page=26,
            use_school_year_scope=True,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows']} | Scope: {meta['scope']}")
