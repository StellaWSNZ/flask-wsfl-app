# ============================================================
# Blank page (header + footer SVG) + DB call for
# GetFunderYearGroupSummary_StudentWeighted_TY_LY_WithTrend 2026, 2
# Includes a runnable main()
# ============================================================

from __future__ import annotations

from typing import Optional, Tuple, Dict, Any
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from sqlalchemy import create_engine, text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import open_pdf, new_page, save_page, close_pdf
from app.utils.funder_missing_plot import add_full_width_footer_svg


# -------------------------
# DB engine (standalone-friendly)
# -------------------------
def get_db_engine():
    """
    Uses the same env vars you use elsewhere.
    """
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, pool_pre_ping=True, fast_executemany=True)


# -------------------------
# 1) Data loader
# -------------------------
import os
import pandas as pd
from sqlalchemy import text

USE_CSV = True   # 🔁 flip this while debugging
CSV_PATH = "data.csv"
FUNDER_ID = None
FUNDER_NAME = "Dash Swim School"

def load_studentweighted_data(conn, calendaryear: int, term: int, funder_name: str) -> pd.DataFrame:
    """
    Unified loader:
    - CSV when USE_CSV=True
    - Stored procedure otherwise
    """
    if USE_CSV:
        if not os.path.exists(CSV_PATH):
            raise FileNotFoundError(f"CSV not found: {CSV_PATH}")
        df = pd.read_csv(CSV_PATH)
        print(f"[DEBUG] Loaded {len(df)} rows from CSV")
        return df

    # ---- DB path ----
    sql = text(
        """
        SET NOCOUNT ON;
        EXEC dbo.GetFunderYearGroupSummary_StudentWeighted_TY_LY_WithTrend
            @CalendarYear = :CalendarYear,
            @Term         = :Term;
        """
    )
    rows = conn.execute(
        sql,
        {"CalendarYear": calendaryear, "Term": term},
    ).mappings().all()

    df = pd.DataFrame(rows)
    print(f"[DEBUG] Loaded {len(df)} rows from DB")
    return df


def bar_chart_weighted_yeargroup(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    bar1_vals=None,
    bar2_vals=None,
    bar1_name=None,
    bar2_name=None,
    labels=None,
    y_ticks: int = 5,
    max_y: float = 100.0,
    title_buffer: float | None = None,
    x_axis_buffer: float | None = None,
    y_axis_buffer: float | None = None,
    group_gap: float | None = None,
    bar_gap: float | None = None,
):
    # --------------------
    # Defaults (safe)
    # --------------------
    if labels is None:
        labels = ["1–2", "3–4", "5–6", "7–8"]
    n_groups = len(labels)
    n_bars = 2

    if bar1_vals is None:
        bar1_vals = [60, 70, 55, 65][:n_groups]
    if bar1_name is None:
        bar1_name = "Bar 1"
    if bar2_vals is None:
        bar2_vals = [55, 68, 50, 60][:n_groups]
    if bar2_name is None:
        bar2_name = "Bar 2"
  
    # Validate lengths
    if not (len(bar1_vals) == len(bar2_vals) == len(labels)):
        ax.add_patch(
            plt.Rectangle(
                (x, y),
                width,
                height,
                facecolor="red",
                edgecolor="none",
                linewidth=0.0,
                transform=ax.transAxes,
            )
        )
        ax.text(
            x + width / 2,
            y + height / 2,
            "Labels and values are not the same length",
            ha="center",
            va="center",
            fontsize=10,
            color="white",
            transform=ax.transAxes,
        )
        return

    if title_buffer is None:
        title_buffer = height * 0.08
    if x_axis_buffer is None:
        x_axis_buffer = height * 0.08
    if y_axis_buffer is None:
        y_axis_buffer = width * 0.08

    # clamp
    def clamp(v):
        try:
            v = float(v)
        except Exception:
            return 0.0
        return max(0.0, min(max_y, v))

    bar1_vals = [clamp(v) for v in bar1_vals]
    bar2_vals = [clamp(v) for v in bar2_vals]

    # --------------------
    # Plot area
    # --------------------
    plot_x0 = x + y_axis_buffer
    plot_x1 = x + width - (y_axis_buffer * 0.25)
    plot_y0 = y + x_axis_buffer
    plot_y1 = y + height - title_buffer

    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    # axes lines
    ax.plot([plot_x0, plot_x0], [plot_y0, plot_y1], lw=1.2, color="black", transform=ax.transAxes)
    ax.plot([plot_x0, plot_x1], [plot_y0, plot_y0], lw=1.2, color="black", transform=ax.transAxes)

    # --------------------
    # Y ticks
    # --------------------
    tick_length = width * 0.015
    label_offset = width * 0.020

    for i in range(y_ticks + 1):
        frac = i / y_ticks
        value = frac * max_y
        y_tick = plot_y0 + frac * plot_h

        ax.plot(
            [plot_x0 - tick_length, plot_x0],
            [y_tick, y_tick],
            lw=1.0,
            color="black",
            transform=ax.transAxes,
        )
        ax.text(
            plot_x0 - tick_length - label_offset,
            y_tick,
            f"{int(value)}%",
            ha="right",
            va="center",
            fontsize=9,
            color="black",
            transform=ax.transAxes,
        )

    # --------------------
    # Bars layout
    # --------------------
    if group_gap is None:
        group_gap = plot_w * 0.06
    if bar_gap is None:
        bar_gap = plot_w * 0.015

    total_group_gaps = group_gap * (n_groups + 1)
    remaining = plot_w - total_group_gaps
    if remaining <= 0:
        raise ValueError("Not enough plot width after group gaps.")

    group_w = remaining / n_groups
    bar_w = (group_w - bar_gap) / n_bars
    if bar_w <= 0:
        raise ValueError("Bar width <= 0; reduce bar_gap or group_gap.")

    # Draw bars
    for g in range(n_groups):
        group_left = plot_x0 + group_gap + g * (group_w + group_gap)

        x1 = group_left
        x2 = group_left + bar_w + bar_gap

        h1 = (bar1_vals[g] / max_y) * plot_h
        h2 = (bar2_vals[g] / max_y) * plot_h

        # bar1 (filled)
        ax.add_patch(
            plt.Rectangle(
                (x1, plot_y0),
                bar_w,
                h1,
                facecolor="blue",
                edgecolor="blue",
                lw=0.6,
                transform=ax.transAxes,
            )
        )

        # bar2 (outline)
        ax.add_patch(
            plt.Rectangle(
                (x2, plot_y0),
                bar_w,
                h2,
                facecolor="none",
                edgecolor="blue",
                lw=0.6,
                transform=ax.transAxes,
            )
        )

    # X labels
    for g in range(n_groups):
        group_left = plot_x0 + group_gap + g * (group_w + group_gap)
        group_center = group_left + group_w / 2
        ax.text(
            group_center,
            plot_y0 - (x_axis_buffer * 0.55),
            labels[g],
            ha="center",
            va="top",
            fontsize=10,
            color="black",
            transform=ax.transAxes,
        )
def extract_ty_ly_from_rates(df: pd.DataFrame, funder_name: str):
    d = df[(df["Funder"] == funder_name) & (df["YearGroupID"].notna())].copy()

    # Keep one row per YearGroupID (TY/LY rates are duplicated across TY and LY rows in your CSV)
    d = d.sort_values(["YearGroupID", "PeriodLabel"]).groupby("YearGroupID", as_index=False).first()

    ty = pd.to_numeric(d["TY_YG_Rate"], errors="coerce").fillna(0)
    ly = pd.to_numeric(d["LY_YG_Rate"], errors="coerce").fillna(0)

    # Convert to percent if needed
    ty_vals = ((ty * 100) if ty.max() <= 1 else ty).tolist()
    ly_vals = ((ly * 100) if ly.max() <= 1 else ly).tolist()

    # Nice display labels (your IDs 1..4 correspond to 0-2, 3-4, 5-6, 7-8)
    labels = ["0–2", "3–4", "5–6", "7–8"][: len(d)]

    return ty_vals, ly_vals, labels

# -------------------------
# 3) Builder: blank PDF page + footer, but still loads SP data
# -------------------------
def build_studentweighted_blank_pdf(
    *,
    conn,
    out_pdf_path: str,
    footer_svg: str,
    calendaryear: int = 2026,
    term: int = 2,
    title: str = "Student-weighted summary (TY vs LY)",
    fonts_dir: str = "app/static/fonts",
    dpi: int = 300,
    page_size: str = "A4",
    orientation: str = "portrait",
    funder_name: str,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    df = load_studentweighted_data(conn, calendaryear, term, funder_name)
    # df = df["FunderName"==funder_name]
    family = load_ppmori_fonts(str(fonts_dir))
    
    pdf, w_in, h_in, dpi_used = open_pdf(
        str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    fig, ax = new_page(w_in, h_in, dpi=dpi_used)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    header_poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.96,
        width=0.92,
        height=0.05,
        ratio=0.45,
        corners_round=[1, 2, 3, 4],
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
        text=title,
        fontfamily=family,
        fontsize=20,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.06,
        wrap=False,
        autoshrink=True,
        min_fontsize=12,
        clip_to_polygon=False,
        max_lines=1,
    )

    labels = ["1–2", "3–4", "5–6", "7–8"]

    ty_vals, ly_vals,labels = extract_ty_ly_from_rates(df, FUNDER_NAME)

    print(ty_vals)
    print(ly_vals)
    bar_chart_weighted_yeargroup(ax = ax, width = 0.9, height= 0.3, x = 0.05, y = 0.6, bar1_vals= ly_vals, bar2_vals=ty_vals, labels = labels)
    add_full_width_footer_svg(
        fig,
        footer_svg,
        bottom_margin_frac=0.0,
        max_footer_height_frac=0.20,
        col_master="#1a427d40",
    )

    save_page(
        pdf,
        fig,
        footer_png=None,
        width_in=w_in,
        height_in=h_in,
        footer_bottom_margin_frac=0.0,
        footer_max_height_frac=0.20,
    )
    close_pdf(pdf)

    meta = {
        "calendar_year": int(calendaryear),
        "term": int(term),
        "rows_returned": int(len(df)),
        "columns": list(df.columns),
        "df": df,  # caller can use this later
        "out_pdf_path": str(out_pdf_path),
    }
    return fig, meta


# -------------------------
# MAIN (standalone runner)
# -------------------------
def main():
    CALENDARYEAR = 2026
    TERM = 2

    # Point this at your footer SVG file (the one you already use elsewhere)
    # Example env var pattern; change if you prefer hard-coded.
    footer_svg = os.getenv("WSFL_FOOTER_SVG", "app/static/footer.svg")

    out_dir = os.getenv("WSFL_REPORT_DIR", "out")
    os.makedirs(out_dir, exist_ok=True)
    out_pdf = os.path.join(out_dir, f"studentweighted_blank_Term{TERM}_{CALENDARYEAR}.pdf")
    
    engine = get_db_engine()
    with engine.begin() as conn:
        
        fig, meta = build_studentweighted_blank_pdf(
            conn=conn,
            out_pdf_path=out_pdf,
            footer_svg=footer_svg,
            calendaryear=CALENDARYEAR,
            term=TERM,
            title=f"Weighted Achievement {FUNDER_NAME}",
            fonts_dir="app/static/fonts",
            dpi=300,
            page_size="A4",
            orientation="portrait",
            funder_name = FUNDER_NAME,
        )

    # Clean up fig handle for CLI runs
    if fig is not None:
        plt.close(fig)

    print("Wrote:", meta["out_pdf_path"])
    print("Rows returned from SP:", meta["rows_returned"])
    print("Columns:", meta["columns"])


if __name__ == "__main__":
    main()
