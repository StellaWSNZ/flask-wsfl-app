# ============================================================
# Blank page (header + footer SVG) + DB call for
# GetFunderYearGroupSummary_StudentWeighted_TY_LY_WithTrend
#
# Features:
# - TY vs LY bars (bar1 + bar2)
# - Per-bar text drawn inside/above bars (bar1_bar_text / bar2_bar_text)
# - Centred key (legend) controlled by keypos:
#     "under_title" | "under_axis" | "none"
#   Key labels come from bar1_name / bar2_name
# - Optional reference (dashed) lines with labels (ref_lines)
# - Runnable main()
# ============================================================

from __future__ import annotations

from typing import Optional, Tuple, Dict, Any, List
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
USE_CSV = True   # flip to False for DB
CSV_PATH = "data.csv"
FUNDER_NAME = "Dash Swim School"


def load_studentweighted_data(conn, calendaryear: int, term: int) -> pd.DataFrame:
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

    sql = text(
        """
        SET NOCOUNT ON;
        EXEC dbo.GetFunderYearGroupSummary_StudentWeighted_TY_LY_WithTrend
            @CalendarYear = :CalendarYear,
            @Term         = :Term;
        """
    )
    rows = conn.execute(sql, {"CalendarYear": calendaryear, "Term": term}).mappings().all()
    df = pd.DataFrame(rows)
    print(f"[DEBUG] Loaded {len(df)} rows from DB")
    return df


# -------------------------
# 2) Data extraction
# -------------------------
def extract_ty_ly_and_counts(df: pd.DataFrame, funder_name: str):
    """
    StudentCount must be found by filtering:
      funder name + yeargroupid + periodlabel
    and reading from column StudentCount.

    Returns:
      ty_vals (%), ly_vals (%),
      ty_counts (per yeargroup), ly_counts (per yeargroup),
      ty_all (%), ly_all (%),
      labels (yeargroup labels)
    """
    needed = {
        "Funder", "PeriodLabel", "YearGroupID", "StudentCount",
        "TY_YG_Rate", "LY_YG_Rate",
        "TY_AllYGsRate", "LY_AllYGsRate",
    }
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns in df: {missing}")

    # Keep only rows for this funder (we’ll use both YG and overall from this subset)
    d_funder = df[df["Funder"] == funder_name].copy()
    if d_funder.empty:
        raise ValueError(f"No rows found for funder '{funder_name}'.")

    # Only year-group rows (YearGroupID present)
    d0 = d_funder[d_funder["YearGroupID"].notna()].copy()
    if d0.empty:
        raise ValueError(f"No rows found for funder '{funder_name}' with YearGroupID not null.")

    # stable ordering of yeargroups
    yeargroup_ids = (
        d0[["YearGroupID"]]
        .drop_duplicates()
        .sort_values("YearGroupID")
        .reset_index(drop=True)["YearGroupID"]
        .tolist()
    )

    # rates (repeated across periods; take first row per yeargroup)
    d_rates = (
        d0.sort_values(["YearGroupID", "PeriodLabel"])
          .groupby("YearGroupID", as_index=False)
          .first()
    )

    ty = pd.to_numeric(d_rates["TY_YG_Rate"], errors="coerce").fillna(0)
    ly = pd.to_numeric(d_rates["LY_YG_Rate"], errors="coerce").fillna(0)

    # convert to percent if looks like 0..1
    ty_vals = ((ty * 100) if ty.max() <= 1 else ty).tolist()
    ly_vals = ((ly * 100) if ly.max() <= 1 else ly).tolist()

    # counts by period+yeargroup (max if duplicates)
    counts_by_period = d0[["PeriodLabel", "YearGroupID", "StudentCount"]].copy()
    counts_by_period["StudentCount"] = pd.to_numeric(counts_by_period["StudentCount"], errors="coerce").fillna(0)
    counts_by_period = (
        counts_by_period.groupby(["PeriodLabel", "YearGroupID"], as_index=False)["StudentCount"].max()
    )

    def _counts_for(period: str) -> List[int]:
        m = (
            counts_by_period[counts_by_period["PeriodLabel"] == period]
            .set_index("YearGroupID")["StudentCount"]
            .to_dict()
        )
        return [int(m.get(yg, 0)) for yg in yeargroup_ids]

    ty_counts = _counts_for("TY")
    ly_counts = _counts_for("LY")

    # labels (YearGroupDesc if present)
    if "YearGroupDesc" in d0.columns:
        desc_map = (
            d0[["YearGroupID", "YearGroupDesc"]]
            .dropna()
            .drop_duplicates(subset=["YearGroupID"])
            .set_index("YearGroupID")["YearGroupDesc"]
            .to_dict()
        )

        labels = [f"Year {desc_map.get(yg, yg)}" for yg in yeargroup_ids]
    else:
        default_labels = ["Year 0–2", "Year 3–4", "Year 5–6", "Year 7–8"]

        labels = default_labels[: len(yeargroup_ids)]

    # --- overall (all year groups) ---
    # Often repeated per-row; take first non-null value
    def _first_num(series: pd.Series) -> float:
        s = pd.to_numeric(series, errors="coerce").dropna()
        return float(s.iloc[0]) if not s.empty else 0.0

    ty_all = _first_num(d_funder["TY_AllYGsRate"])
    ly_all = _first_num(d_funder["LY_AllYGsRate"])

    # convert to percent if looks like 0..1
    if ty_all <= 1:
        ty_all *= 100.0
    if ly_all <= 1:
        ly_all *= 100.0

    return ty_vals, ly_vals, ty_counts, ly_counts, ty_all, ly_all, labels


# -------------------------
# 3) Centred key helper
# -------------------------
def draw_centered_key(
    ax,
    *,
    x_center: float,
    y: float,
    bar1_name: str,
    bar2_name: str,
    bar1_style: dict,
    bar2_style: dict,
    fontsize: int = 10,
):
    handles = [
        mpatches.Patch(label=bar1_name, **bar1_style),
        mpatches.Patch(label=bar2_name, **bar2_style),
    ]
    ax.legend(
        handles=handles,
        loc="center",
        bbox_to_anchor=(x_center, y),
        ncol=2,
        frameon=False,
        fontsize=fontsize,
        handlelength=1.6,
        columnspacing=2.0,
    )


# -------------------------
# 4) Chart: bars + text + key + optional reference lines
# -------------------------
def bar_chart_weighted_yeargroup(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    chart_title: str | None = None,

    bar1_vals=None,
    bar2_vals=None,
    labels=None,

    # If you want per-bar text (counts, percents, etc.)
    bar1_bar_text=None,
    bar2_bar_text=None,

    # Key labels (legend)
        bar1_name: str = "Last year (EOY)",
    bar2_name: str = "This Year (YTD)",
    keypos: str = "none",   # "under_title" | "under_axis" | "none"

    # If bar text is numeric you can still prefix/suffix
    count_prefix: str = "",
    count_suffix: str = "",

    # Reference line specs (targets/benchmarks)
    ref_lines: list[dict] | None = None,

    y_ticks: int = 5,
    max_y: float = 100.0,
    title_buffer: float | None = None,
    x_axis_buffer: float | None = None,
    y_axis_buffer: float | None = None,
    group_gap: float | None = None,
    bar_gap: float | None = None,
):
    if labels is None:
        labels = ["0–2", "3–4", "5–6", "7–8"]
    n_groups = len(labels)
    n_bars = 2

    if bar1_vals is None:
        bar1_vals = [60, 70, 55, 65][:n_groups]
    if bar2_vals is None:
        bar2_vals = [55, 68, 50, 60][:n_groups]

    if not (len(bar1_vals) == len(bar2_vals) == len(labels)):
        ax.add_patch(
            plt.Rectangle((x, y), width, height, facecolor="red", edgecolor="none", transform=ax.transAxes)
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

    # Styles (keep consistent with key + ref lines)
    bar1_face = "#2EBDC280"   # filled
    bar1_edge = "#2EBDC2"
    bar2_face = "#1a427d80"   # light fill
    bar2_edge = "#1a427d"

    # Title
    title_y = None
    if chart_title:
        title_y = y + height - (title_buffer * 0.45)
        ax.text(
            x + width / 2,
            title_y,
            chart_title,
            ha="center",
            va="center",
            fontsize=12,
            fontweight="semibold",
            color="black",
            transform=ax.transAxes,
        )

    def clamp(v):
        try:
            v = float(v)
        except Exception:
            return 0.0
        return max(0.0, min(max_y, v))

    bar1_vals = [clamp(v) for v in bar1_vals]
    bar2_vals = [clamp(v) for v in bar2_vals]

    # Plot area
    plot_x0 = x + y_axis_buffer
    plot_x1 = x + width - (y_axis_buffer * 0.25)
    plot_y0 = y + x_axis_buffer
    plot_y1 = y + height - title_buffer

    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    # Axes lines
    ax.plot([plot_x0, plot_x0], [plot_y0, plot_y1], lw=1.2, color="black", transform=ax.transAxes)
    ax.plot([plot_x0, plot_x1], [plot_y0, plot_y0], lw=1.2, color="black", transform=ax.transAxes)

    # Y ticks
    tick_length = width * 0.015
    label_offset = width * 0.020
    for i in range(y_ticks + 1):
        frac = i / y_ticks
        value = frac * max_y
        y_tick = plot_y0 + frac * plot_h
        ax.plot([plot_x0 - tick_length, plot_x0], [y_tick, y_tick],
                lw=1.0, color="black", transform=ax.transAxes)
        ax.text(plot_x0 - tick_length - label_offset, y_tick, f"{int(value)}%",
                ha="right", va="center", fontsize=9, color="black", transform=ax.transAxes)

    # Bars layout
    if group_gap is None:
        group_gap = plot_w * 0.03
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

    def _fmt_bar_text(v):
        if v is None:
            return None
        if isinstance(v, str):
            return v
        try:
            fv = float(v)
            if abs(fv - round(fv)) < 1e-9:
                s = f"{int(round(fv)):,}"
            else:
                s = f"{fv:.1f}"
        except Exception:
            return None
        return f"{count_prefix}{s}{count_suffix}"

    min_inside_h = plot_h * 0.12
    pad_y_inside = plot_h * 0.03
    pad_y_above = plot_h * 0.02

    # Draw bars + text + x labels
    for g in range(n_groups):
        group_left = plot_x0 + group_gap + g * (group_w + group_gap)
        group_center = group_left + group_w / 2

        x1 = group_left
        x2 = group_left + bar_w + bar_gap

        h1 = (bar1_vals[g] / max_y) * plot_h
        h2 = (bar2_vals[g] / max_y) * plot_h

        ax.add_patch(
            plt.Rectangle(
                (x1, plot_y0), bar_w, h1,
                facecolor=bar1_face, edgecolor=bar1_edge, lw=0.6, transform=ax.transAxes
            )
        )
        ax.add_patch(
            plt.Rectangle(
                (x2, plot_y0), bar_w, h2,
                facecolor=bar2_face, edgecolor=bar2_edge, lw=0.6, transform=ax.transAxes
            )
        )

        t1 = _fmt_bar_text(bar1_bar_text[g]) if (bar1_bar_text is not None and g < len(bar1_bar_text)) else None
        if t1:
            if h1 >= min_inside_h:
                y_txt = plot_y0 + h1 - pad_y_inside
                va = "top"
                col = "white"
            else:
                y_txt = plot_y0 + h1 + pad_y_above
                va = "bottom"
                col = "black"
            ax.text(x1 + bar_w / 2, y_txt, t1,
                    ha="center", va=va, fontsize=8.5, color=bar1_edge, transform=ax.transAxes)

        t2 = _fmt_bar_text(bar2_bar_text[g]) if (bar2_bar_text is not None and g < len(bar2_bar_text)) else None
        if t2:
            if h2 >= min_inside_h:
                y_txt = plot_y0 + h2 - pad_y_inside
                va = "top"
            else:
                y_txt = plot_y0 + h2 + pad_y_above
                va = "bottom"
            ax.text(x2 + bar_w / 2, y_txt, t2,
                    ha="center", va=va, fontsize=8.5, color=bar2_edge, transform=ax.transAxes)

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

    # Reference (target) lines
    if ref_lines:
        x_pad = plot_w * 0.01
        y_pad = plot_h * 0.02

        def _line_color(spec: dict) -> str:
            if spec.get("color"):
                return str(spec["color"])
            cf = (spec.get("color_from") or "").lower()
            if cf == "bar1":
                return bar1_edge
            if cf == "bar2":
                return bar2_edge
            return "black"

        def _y_to_axes(yval: float) -> float:
            try:
                yval = float(yval)
            except Exception:
                yval = 0.0
            yval = max(0.0, min(max_y, yval))
            return plot_y0 + (yval / max_y) * plot_h

        for spec in ref_lines:
            if "y" not in spec:
                continue

            y_line = _y_to_axes(spec["y"])
            col = _line_color(spec)
            ls = spec.get("linestyle", "--")
            lw = float(spec.get("linewidth", 1.2))

            ax.plot(
                [plot_x0, plot_x1],
                [y_line, y_line],
                linestyle=ls,
                linewidth=lw,
                color=col,
                transform=ax.transAxes,
                zorder=3,
            )

            label = spec.get("label")
            if label:
                pos = (spec.get("label_pos") or "right").lower()
                pad = float(spec.get("label_pad", 1.0))

                if pos == "left":
                    tx, ty = plot_x0 + (x_pad * pad), y_line + (y_pad * pad)
                    ha, va = "left", "bottom"
                elif pos == "right":
                    tx, ty = plot_x1 - (x_pad * pad), y_line + (y_pad * pad)
                    ha, va = "right", "bottom" 
                
                else:
                    tx, ty = plot_x1 + (x_pad * pad), y_line
                    ha, va = "left", "center"

                ax.text(
                    tx, ty, str(label),
                    ha=ha, va=va,
                    fontsize=9,
                    color=col,
                    fontweight = "semibold",
                    transform=ax.transAxes,
                )

    # Key / legend
    title_key_gap = title_buffer * 0.55

    if keypos == "under_title":
        if title_y is None:
            key_y = plot_y0 - x_axis_buffer * 0.95
        else:
            key_y = title_y - title_key_gap
    elif keypos == "under_axis":
        key_y = plot_y0 - x_axis_buffer * 0.95
    else:
        key_y = None

    if keypos != "none" and key_y is not None:
        draw_centered_key(
            ax,
            x_center=x + width / 2,
            y=key_y,
            bar1_name=bar1_name,
            bar2_name=bar2_name,
            bar1_style=dict(facecolor=bar1_face, edgecolor=bar1_edge),
            bar2_style=dict(facecolor=bar2_face, edgecolor=bar2_edge),
            fontsize=10,
        )


# -------------------------
# 5) Builder: blank PDF page + footer, loads data
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
    keypos: str = "under_title",   # "under_title" | "under_axis" | "none"
    bar1_name: str = "Last Year Result (EOY)",
    bar2_name: str = "This Year Result (YTD)",
    ref_lines: list[dict] | None = None,
) -> Tuple[Optional[Any], Dict[str, Any]]:

    df = load_studentweighted_data(conn, calendaryear, term)
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

    # Header (blue rounded rectangle)
    header_poly = rounded_rect_polygon(
        cx=0.5, cy=0.96, width=0.92, height=0.06,
        ratio=0.45, corners_round=[1, 2, 3, 4], n_arc=64
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

    # Extract values + counts + overall rates
    ty_vals, ly_vals, ty_counts, ly_counts, ty_all, ly_all, labels = extract_ty_ly_and_counts(df, funder_name)

    auto_ref_lines = [
        {"y": ty_all, "label": f"Weighted Average ({round(ty_all)}%)", "color_from": "bar2", "label_pos": "left", "linestyle": "--"},
        {"y": ly_all, "label": f"Weighted Average ({round(ly_all)}%)", "color_from": "bar1", "label_pos": "right",  "linestyle": "--"},
    ]

    # Chart:
    # bar1 = LY (filled), bar2 = TY (outlined-ish)
    bar_chart_weighted_yeargroup(
        ax=ax,
        x=0.05,
        y=0.60,
        width=0.92,
        height=0.30,
        chart_title=f"Average Achievement by Year Group (Term {term}, {calendaryear})",
        bar1_vals=ly_vals,
        bar2_vals=ty_vals,
        labels=labels,

        # choose what to show inside bars:
        # counts: bar1_bar_text=ly_counts, bar2_bar_text=ty_counts, count_suffix=""
        # percents:
        bar1_bar_text=ly_vals,
        bar2_bar_text=ty_vals,
        count_suffix="%",  # for percent text

        bar1_name=bar1_name,
        bar2_name=bar2_name,
        keypos=keypos,

        ref_lines=(ref_lines if ref_lines is not None else auto_ref_lines),
    )

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
        "out_pdf_path": str(out_pdf_path),
    }
    return fig, meta


# -------------------------
# MAIN (standalone runner)
# -------------------------
def main():
    CALENDARYEAR = 2026
    TERM = 2

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
            funder_name=FUNDER_NAME,
            keypos="under_title",  # "under_title" | "under_axis" | "none"
            bar1_name="Last year (EOY)",
            bar2_name="This year (YTD)",
        )

    if fig is not None:
        plt.close(fig)

    print("Wrote:", meta["out_pdf_path"])
    print("Rows returned:", meta["rows_returned"])
    print("Columns:", meta["columns"])


if __name__ == "__main__":
    main()
