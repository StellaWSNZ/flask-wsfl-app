# app/utils/funder_targets_counts_report.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
import matplotlib.pyplot as plt
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import open_pdf, new_page, save_page, close_pdf
from app.utils.funder_missing_plot import add_full_width_footer_svg


# =========================
# Styles
# =========================
@dataclass
class PanelStyle:
    header_fill: str = "#1a427d"
    header_edge: str = "#1a427d"
    header_text_color: str = "#ffffff"

    panel_bg: str = "#f5f7fb"
    panel_edge: str = "#d5dbe7"

    panel_coords: List[int] = field(default_factory=lambda: [1, 2, 3, 4])
    header_coords: List[int] = field(default_factory=lambda: [1, 2, 3, 4])

    target_edge: str = "#1a427d"
    count_edge: str = "#2EBDC2"
    target_fill: str = "#1a427d80"
    count_fill: str = "#2EBDC280"
    label_color: str = "#1f2d3d"
    small_text_color: str = "#3c4858"


# =========================
# Geometry helpers
# =========================
def match_round_ratio(
    *,
    ref_ratio: float,
    ref_w: float,
    ref_h: float,
    w: float,
    h: float,
    min_ratio: float = 0.02,
    max_ratio: float = 0.45,
) -> float:
    ref_min = max(1e-6, min(ref_w, ref_h))
    r_abs = ref_ratio * ref_min

    box_min = max(1e-6, min(w, h))
    ratio = r_abs / box_min
    return max(min_ratio, min(max_ratio, ratio))


def _add_polygon_header(ax, family, x, y, w, h, ratio, text_str, style: PanelStyle):
    poly = rounded_rect_polygon(
        cx=x + w / 2,
        cy=y + h / 2,
        width=w,
        height=h,
        ratio=ratio,
        corners_round=style.header_coords,
        n_arc=64,
    )

    ax.add_patch(
        mpatches.Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=style.header_fill,
            edgecolor=style.header_edge,
            linewidth=1.5,
            transform=ax.transAxes,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=text_str,
        fontfamily=family,
        fontsize=18,
        fontweight="semibold",
        color=style.header_text_color,
        pad_frac=0.08,
        wrap=True,
        max_lines=None,
        autoshrink=True,
        min_fontsize=10,
        clip_to_polygon=True,
    )


# =========================
# Panel renderer
# =========================
def draw_target_panel(
    ax,
    df_panel: pd.DataFrame,
    *,
    family: str,
    title: str,
    x: float,
    y: float,
    width: float,
    height: float,
    header_h_abs: float = 0.04,
    header_pad: float = 0.012,
    row_h_abs: Optional[float] = None,
    row_gap_frac: float = 0.18,
    label_col: str = "Description",
    target_col: str = "Target",
    count_col: str = "TargetCount",  # actual count lane (yes, name is legacy)
    sort_by: str = "Description",
    max_rows: Optional[int] = None,
    style: Optional[PanelStyle] = None,
    show_overflow_arrow: bool = True,
    # Counts lane
    show_counts_column: bool = True,
    counts_fontsize: int = 9,
    counts_lane_min_frac: float = 0.10,
    counts_lane_max_frac: float = 0.16,
    counts_lane_frac: float = 0.12,
    counts_lane_gap_frac: float = 0.012,
    format_commas: bool = True,
):
    style = style or PanelStyle()

    # Header geometry
    header_h = min(header_h_abs, max(0.0, height - 2 * header_pad))
    header_w = max(1e-6, width - 2 * header_pad)

    header_ratio = 0.45
    panel_ratio = match_round_ratio(
        ref_ratio=header_ratio,
        ref_w=header_w,
        ref_h=header_h,
        w=width,
        h=height,
        min_ratio=0.02,
        max_ratio=0.20,
    )

    # Panel background
    panel_poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=panel_ratio,
        corners_round=style.panel_coords,
        n_arc=64,
    )
    ax.add_patch(
        mpatches.Polygon(
            list(panel_poly.exterior.coords),
            closed=True,
            facecolor=style.panel_bg,
            edgecolor=style.panel_edge,
            linewidth=1.5,
            transform=ax.transAxes,
        )
    )

    # Header band
    _add_polygon_header(
        ax,
        family=family,
        x=x + header_pad,
        y=y + height - header_h - header_pad,
        w=width - 2 * header_pad,
        h=header_h,
        ratio=header_ratio,
        text_str=title,
        style=style,
    )

    if df_panel is None or df_panel.empty:
        ax.text(
            x + 0.02,
            y + height * 0.5,
            "No data available",
            transform=ax.transAxes,
            ha="left",
            va="center",
            color=style.small_text_color,
            fontfamily=family,
            fontsize=11,
        )
        return

    # Sort / limit
    dfp = df_panel.copy()
    if sort_by in dfp.columns:
        dfp = dfp.sort_values(sort_by, ascending=True, na_position="last")
    if max_rows is not None:
        dfp = dfp.head(max_rows)

    n = len(dfp)
    if n == 0:
        return

    # Body geometry
    body_top = y + height - header_h - 2 * header_pad
    body_bottom = y + header_pad
    body_h = max(1e-6, body_top - body_bottom)

    if row_h_abs is None:
        row_h = body_h / n
        row_gap = row_h * row_gap_frac
        needed = n * row_h + max(0, n - 1) * row_gap
        if needed > body_h:
            row_h = body_h / (n + max(0, n - 1) * row_gap_frac)
            row_gap = row_h * row_gap_frac
    else:
        row_h = row_h_abs
        row_gap = row_h * row_gap_frac
        needed = n * row_h + max(0, n - 1) * row_gap
        if needed > body_h:
            row_h = body_h / (n + max(0, n - 1) * row_gap_frac)
            row_gap = row_h * row_gap_frac

    row_step = row_h + row_gap

    # Within-row bar split
    bar_gap = row_h * 0.10
    bar_h = (row_h - bar_gap) / 2

    def _fmt_int(v: float) -> str:
        v_int = int(round(v))
        return f"{v_int:,}" if format_commas else f"{v_int}"

    # Columns (Label | Counts | Bars)
    max_len = (
        dfp[label_col].astype(str).str.len().max()
        if label_col in dfp.columns
        else 10
    )
    label_w = (width * 0.008) * max_len

    outer_margin = width * 0.02
    col_gap = width * counts_lane_gap_frac

    x_label_r = x + outer_margin + label_w

    if show_counts_column:
        counts_lane_w = width * counts_lane_frac
        counts_lane_w = max(
            width * counts_lane_min_frac,
            min(width * counts_lane_max_frac, counts_lane_w),
        )
    else:
        counts_lane_w = 0.0

    counts_lane_x0 = x_label_r + col_gap
    counts_lane_x1 = counts_lane_x0 + counts_lane_w
    x_counts_c = (counts_lane_x0 + counts_lane_x1) / 2

    bar_x = counts_lane_x1 + col_gap
    bar_right = x + width - outer_margin
    bar_w = max(1e-6, bar_right - bar_x)

    max_target = pd.to_numeric(dfp[target_col], errors="coerce").max()
    if not max_target or pd.isna(max_target) or max_target <= 0:
        max_target = 1.0
    unit_w = bar_w / max_target

    rows_top = body_top
    for i, r in enumerate(dfp.itertuples(index=False)):
        row_y = rows_top - (i + 1) * row_step + row_gap

        label = getattr(r, label_col) if hasattr(r, label_col) else ""
        target_val = getattr(r, target_col) if hasattr(r, target_col) else 0
        count_val = getattr(r, count_col) if hasattr(r, count_col) else 0

        try:
            target_val = float(target_val) if target_val is not None else 0.0
        except Exception:
            target_val = 0.0
        try:
            count_val = float(count_val) if count_val is not None else 0.0
        except Exception:
            count_val = 0.0

        target_val = max(0.0, target_val)
        count_val = max(0.0, count_val)

        # Label
        ax.text(
            x_label_r,
            row_y + row_h / 2,
            str(label),
            transform=ax.transAxes,
            ha="right",
            va="center",
            color=style.label_color,
            fontfamily=family,
            fontsize=10,
        )

        y_target_c = row_y + bar_h + bar_gap + (bar_h / 2)
        y_count_c = row_y + (bar_h / 2)

        # Counts column (centered)
        if show_counts_column:
            ax.text(
                x_counts_c,
                y_target_c,
                _fmt_int(target_val),
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=style.small_text_color,
                fontfamily=family,
                fontsize=counts_fontsize,
            )
            ax.text(
                x_counts_c,
                y_count_c,
                _fmt_int(count_val),
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=style.small_text_color,
                fontfamily=family,
                fontsize=counts_fontsize,
            )

        # Target bar
        target_w = target_val * unit_w
        ax.add_patch(
            Rectangle(
                (bar_x, row_y + bar_h + bar_gap),
                target_w,
                bar_h,
                facecolor=getattr(style, "target_fill", "none"),
                edgecolor=style.target_edge,
                linewidth=1.0,
                transform=ax.transAxes,
            )
        )

        # Actual bar (clamp + overflow arrow)
        overflow = count_val > max_target
        count_w = (max_target if overflow else count_val) * unit_w
        ax.add_patch(
            Rectangle(
                (bar_x, row_y),
                count_w,
                bar_h,
                facecolor=getattr(style, "count_fill", "none"),
                edgecolor=style.count_edge,
                linewidth=1.0,
                transform=ax.transAxes,
            )
        )

        if overflow and show_overflow_arrow:
            ax.text(
                bar_x + bar_w - 0.002,
                y_count_c,
                "▶",
                transform=ax.transAxes,
                ha="right",
                va="center",
                color=style.count_edge,
                fontfamily=family,
                fontsize=10,
            )


def compute_panel_layout_fixed_row_height(
    n_students: int,
    n_kaiako: int,
    *,
    top_y: float = 0.92,
    bottom_y: float = 0.14,
    gap: float = 0.03,
    header_h_abs: float = 0.04,
    header_pad: float = 0.012,
    row_h_abs: float = 0.028,
    row_gap_frac: float = 0.18,
    min_rows_if_empty: int = 2,
) -> Tuple[float, float, float, float, float, float]:
    usable_h = (top_y - bottom_y) - gap
    if usable_h <= 0:
        raise ValueError("Not enough vertical space for panels (check top_y/bottom_y/gap).")

    ns = n_students if n_students > 0 else min_rows_if_empty
    nk = n_kaiako if n_kaiako > 0 else min_rows_if_empty

    def panel_need(n_rows: int, row_h: float) -> float:
        row_gap = row_h * row_gap_frac
        used_rows = n_rows * row_h + max(0, n_rows - 1) * row_gap
        return header_h_abs + 2 * header_pad + used_rows

    s_need = panel_need(ns, row_h_abs)
    k_need = panel_need(nk, row_h_abs)
    total_need = s_need + k_need

    row_h_used = row_h_abs
    if total_need > usable_h and (ns + nk) > 0:
        fixed_overhead = 2 * (header_h_abs + 2 * header_pad)
        avail_for_rows = max(1e-6, usable_h - fixed_overhead)

        total_rows = ns + nk
        total_gaps = max(0, ns - 1) + max(0, nk - 1)
        denom = total_rows + total_gaps * row_gap_frac
        row_h_used = avail_for_rows / max(1e-6, denom)

        s_need = panel_need(ns, row_h_used)
        k_need = panel_need(nk, row_h_used)

    kaiako_y = bottom_y
    students_y = bottom_y + k_need + gap

    return students_y, s_need, kaiako_y, k_need, row_h_used, row_gap_frac


def compute_row_height_dynamic(
    *,
    n_students: int,
    n_kaiako: int,
    top_y: float,
    bottom_y: float,
    gap: float,
    header_h_abs: float,
    header_pad: float,
    row_gap_frac: float = 0.18,
    min_row_h: float = 0.020,
    max_row_h: float = 0.040,
) -> float:
    total_rows = n_students + n_kaiako
    if total_rows <= 0:
        return max_row_h

    usable_h = top_y - bottom_y - gap
    if usable_h <= 0:
        return min_row_h

    fixed_overhead = 2 * (header_h_abs + 2 * header_pad)
    body_h = usable_h - fixed_overhead
    if body_h <= 0:
        return min_row_h

    n_gaps = max(0, n_students - 1) + max(0, n_kaiako - 1)
    denom = total_rows + n_gaps * row_gap_frac
    row_h = body_h / max(1e-6, denom)

    return max(min_row_h, min(max_row_h, row_h))


def draw_key_panel(
    ax,
    *,
    family: str,
    x: float,
    y: float,
    width: float,
    height: float,
    style: PanelStyle,
    header_text: str = "KEY:",
    pad: float = 0.008,
):
    panel_ratio = 0.20
    poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=panel_ratio,
        corners_round=style.panel_coords,
        n_arc=64,
    )
    ax.add_patch(
        mpatches.Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=style.panel_bg,
            edgecolor=style.panel_edge,
            linewidth=1.2,
            transform=ax.transAxes,
        )
    )

    left = x + pad
    top = y + height - pad
    center_x = x + width / 2
    key_y = top - 0.012

    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    ax_bbox = ax.get_window_extent(renderer=renderer)
    ax_w_px = max(1.0, ax_bbox.width)
    ax_h_px = max(1.0, ax_bbox.height)

    sw_h = 0.018
    sw_w = sw_h * (ax_h_px / ax_w_px)

    gap_after_key = 0.010
    gap_swatch_label = 0.008
    gap_item = 0.030

    def _text_w_axes(text_str: str, *, fontsize: int = 10, fontweight=None) -> float:
        t = ax.text(
            0,
            0,
            text_str,
            transform=ax.transAxes,
            fontfamily=family,
            fontsize=fontsize,
            fontweight=fontweight,
            alpha=0.0,
        )
        bb = t.get_window_extent(renderer=renderer)
        t.remove()
        return bb.width / ax_w_px

    w_key = _text_w_axes(header_text, fontsize=10, fontweight="semibold") if header_text else 0.0
    w_target = _text_w_axes("Target", fontsize=10)
    w_actual = _text_w_axes("Actual", fontsize=10)

    total_w = (
        w_key
        + (gap_after_key if header_text else 0.0)
        + sw_w
        + gap_swatch_label
        + w_target
        + gap_item
        + sw_w
        + gap_swatch_label
        + w_actual
    )
    start_x = max(left, center_x - total_w / 2)

    cursor_x = start_x
    if header_text:
        ax.text(
            cursor_x,
            key_y,
            header_text,
            transform=ax.transAxes,
            ha="left",
            va="center",
            fontfamily=family,
            fontsize=10,
            fontweight="semibold",
            color=style.label_color,
        )
        cursor_x += w_key + gap_after_key

    ax.add_patch(
        Rectangle(
            (cursor_x, key_y - sw_h / 2),
            sw_w,
            sw_h,
            facecolor=getattr(style, "target_fill", "none"),
            edgecolor=style.target_edge,
            linewidth=1.0,
            transform=ax.transAxes,
        )
    )
    cursor_x += sw_w + gap_swatch_label
    ax.text(
        cursor_x,
        key_y,
        "Target",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontfamily=family,
        fontsize=10,
        color=style.label_color,
    )
    cursor_x += w_target + gap_item

    ax.add_patch(
        Rectangle(
            (cursor_x, key_y - sw_h / 2),
            sw_w,
            sw_h,
            facecolor=getattr(style, "count_fill", "none"),
            edgecolor=style.count_edge,
            linewidth=1.0,
            transform=ax.transAxes,
        )
    )
    cursor_x += sw_w + gap_swatch_label
    ax.text(
        cursor_x,
        key_y,
        "Actual",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontfamily=family,
        fontsize=10,
        color=style.label_color,
    )

    note = (
        "Note: Target = amount in funding agreements; student actuals = unique students; "
        "kaiako actuals = completed assessments."
    )
    ax.text(
        center_x,
        key_y - 0.012,
        note,
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontfamily=family,
        fontsize=9,
        color=style.small_text_color,
        linespacing=1.25,
    )


# =========================
# Data loader (uses existing conn)
# =========================
def load_targets_counts_df(conn) -> pd.DataFrame:
    sql = text("SET NOCOUNT ON; EXEC GetFunderTargetsCounts;")
    rows = conn.execute(sql).mappings().all()
    df = pd.DataFrame(rows)

    if df.empty:
        return df

    for c in ["Target", "StudentCount", "KaiakoCount", "TargetCount", "Percentage"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if "TargetType" in df.columns:
        df["TargetType"] = df["TargetType"].fillna("").astype(str).str.strip()

    # keep only rows that actually have a target number
    if "Target" in df.columns:
        df = df[df["Target"].notna()].copy()

    return df

def build_funder_targets_counts_figure(
    results: List[Dict[str, Any]],
    *,
    footer_svg: Optional[str] = None,
    fonts_dir: str = "app/static/fonts",
    title: str = "Funder Counts (Target vs Actual)",
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Build a 1-page Matplotlib figure from already-fetched result rows.
    Does NOT write a PDF. Safe to call from report.py preview flow.

    Returns (fig_or_none, meta).
    """
    df = pd.DataFrame(results or [])

    family = load_ppmori_fonts(str(fonts_dir))

    # If empty or missing expected columns -> return None (no preview)
    if df.empty:
        meta = {"rows_students": 0, "rows_kaiako": 0, "empty": True}
        return None, meta

    # Coerce numeric columns if present
    for c in ["Target", "StudentCount", "KaiakoCount", "TargetCount", "Percentage"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Need Target to exist to draw anything meaningful
    if "Target" in df.columns:
        df = df[df["Target"].notna()].copy()

    if df.empty:
        meta = {"rows_students": 0, "rows_kaiako": 0, "empty": True}
        return None, meta

    # Ensure TargetType exists
    df["TargetType"] = df.get("TargetType", "").fillna("").astype(str).str.strip()

    # Split
    df_students = df[df["TargetType"].str.lower() == "student"].copy()
    df_kaiako = df[df["TargetType"].str.lower() == "kaiako"].copy()

    # "TargetCount" is the lane your draw_target_panel expects as the ACTUAL count bar
    if not df_students.empty:
        df_students["TargetCount"] = pd.to_numeric(df_students.get("StudentCount", 0), errors="coerce").fillna(0)
    if not df_kaiako.empty:
        df_kaiako["TargetCount"] = pd.to_numeric(df_kaiako.get("KaiakoCount", 0), errors="coerce").fillna(0)

    # ------------------------------------------------------------
    # Create figure (same layout as PDF page)
    # ------------------------------------------------------------
    # A4 portrait-ish preview (match what your pdf_builder produces)
    fig = plt.figure(figsize=(8.27, 11.69), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Header
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
        clip_to_polygon=False,
        max_lines=1,
    )

    # Layout
    panel_x = 0.04
    panel_w = 0.92
    header_h_abs = 0.04
    header_pad = 0.012
    row_gap_frac = 0.18
    bottom_y, top_y, gap = 0.08, 0.92, 0.02

    row_h_abs = compute_row_height_dynamic(
        n_students=len(df_students),
        n_kaiako=len(df_kaiako),
        top_y=top_y,
        bottom_y=bottom_y,
        gap=gap,
        header_h_abs=header_h_abs,
        header_pad=header_pad,
        row_gap_frac=row_gap_frac,
        min_row_h=0.020,
        max_row_h=0.5,
    )

    students_y, students_h, kaiako_y, kaiako_h, row_h_used, row_gap_frac_used = compute_panel_layout_fixed_row_height(
        n_students=len(df_students),
        n_kaiako=len(df_kaiako),
        top_y=top_y,
        bottom_y=bottom_y,
        gap=gap,
        header_h_abs=header_h_abs,
        header_pad=header_pad,
        row_h_abs=row_h_abs,
        row_gap_frac=row_gap_frac,
        min_rows_if_empty=2,
    )

    if students_h > 0:
        draw_target_panel(
            ax,
            df_students,
            family=family,
            title="Students: Targets vs Actuals",
            x=panel_x,
            y=students_y,
            width=panel_w,
            height=students_h,
            header_h_abs=header_h_abs,
            header_pad=header_pad,
            row_h_abs=row_h_used,
            row_gap_frac=row_gap_frac_used,
            sort_by="Description",
        )

    if kaiako_h > 0:
        draw_target_panel(
            ax,
            df_kaiako,
            family=family,
            title="Kaiako: Targets vs Actuals",
            x=panel_x,
            y=kaiako_y,
            width=panel_w,
            height=kaiako_h,
            header_h_abs=header_h_abs,
            header_pad=header_pad,
            row_h_abs=row_h_used,
            row_gap_frac=row_gap_frac_used,
            sort_by="Description",
        )

    # Key panel
    style = PanelStyle()
    draw_key_panel(
        ax,
        family=family,
        x=0.04,
        y=0.015,
        width=0.92,
        height=0.05,
        style=style,
        header_text="",
    )

    # Optional footer (if caller passes it)
    if footer_svg:
        add_full_width_footer_svg(
            fig,
            footer_svg,
            bottom_margin_frac=0.0,
            max_footer_height_frac=0.20,
            col_master="#1a427d40",
        )

    meta = {
        "rows_students": int(len(df_students)),
        "rows_kaiako": int(len(df_kaiako)),
        "empty": False,
    }
    return fig, meta
# =========================
# Public builder: writes PDF + returns preview fig (or None)
# =========================
def build_funder_targets_counts_pdf(
    *,
    conn,
    out_pdf_path,
    footer_svg: str,
    fonts_dir: str = "app/static/fonts",
    dpi: int = 300,
    page_size: str = "A4",
    orientation: str = "portrait",
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Creates a 1-page PDF showing Students + Kaiako targets vs actual counts.
    Returns (preview_fig, meta_dict).

    IMPORTANT behaviour:
    - If there is NO DATA, it still writes a 1-page "No data" PDF
      but returns preview_fig=None (so your /Reports UI doesn't show a preview image).
    """
    df = load_targets_counts_df(conn)
    family = load_ppmori_fonts(str(fonts_dir))

    # ------------------------------------------------------------
    # ✅ No data: write a tiny PDF, return fig=None (no preview)
    # ------------------------------------------------------------
    if df is None or df.empty:
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

        ax.text(
            0.5,
            0.6,
            "Funder Counts (Target vs Actual)",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontfamily=family,
            fontsize=20,
            fontweight="semibold",
            color="#1a427d",
        )
        ax.text(
            0.5,
            0.45,
            "No target/count data available.",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontfamily=family,
            fontsize=14,
            color="#1f2d3d",
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
        plt.close(fig)

        meta = {"rows_students": 0, "rows_kaiako": 0, "empty": True}
        return None, meta

    # ------------------------------------------------------------
    # Normal render (data exists)
    # ------------------------------------------------------------
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

    # Page header
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
        text="Funder Counts (Target vs Actual)",
        fontfamily=family,
        fontsize=20,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.06,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=1,
    )

    # Split data
    df["TargetType"] = df.get("TargetType", "").fillna("").astype(str).str.strip()
    df_students = df[df["TargetType"].str.lower() == "student"].copy()
    df_kaiako = df[df["TargetType"].str.lower() == "kaiako"].copy()

    # "TargetCount" is what the renderer expects for the ACTUAL count bar lane
    if not df_students.empty:
        df_students["TargetCount"] = df_students.get("StudentCount", 0)
        df_students["TargetCount"] = pd.to_numeric(df_students["TargetCount"], errors="coerce").fillna(0)
    if not df_kaiako.empty:
        df_kaiako["TargetCount"] = df_kaiako.get("KaiakoCount", 0)
        df_kaiako["TargetCount"] = pd.to_numeric(df_kaiako["TargetCount"], errors="coerce").fillna(0)

    # Layout
    panel_x = 0.04
    panel_w = 0.92
    header_h_abs = 0.04
    header_pad = 0.012
    row_gap_frac = 0.18
    bottom_y, top_y, gap = 0.08, 0.92, 0.02

    row_h_abs = compute_row_height_dynamic(
        n_students=len(df_students),
        n_kaiako=len(df_kaiako),
        top_y=top_y,
        bottom_y=bottom_y,
        gap=gap,
        header_h_abs=header_h_abs,
        header_pad=header_pad,
        row_gap_frac=row_gap_frac,
        min_row_h=0.020,
        max_row_h=0.5,
    )

    students_y, students_h, kaiako_y, kaiako_h, row_h_used, row_gap_frac_used = compute_panel_layout_fixed_row_height(
        n_students=len(df_students),
        n_kaiako=len(df_kaiako),
        top_y=top_y,
        bottom_y=bottom_y,
        gap=gap,
        header_h_abs=header_h_abs,
        header_pad=header_pad,
        row_h_abs=row_h_abs,
        row_gap_frac=row_gap_frac,
        min_rows_if_empty=2,
    )

    if students_h > 0:
        draw_target_panel(
            ax,
            df_students,
            family=family,
            title="Students: Targets vs Actuals",
            x=panel_x,
            y=students_y,
            width=panel_w,
            height=students_h,
            header_h_abs=header_h_abs,
            header_pad=header_pad,
            row_h_abs=row_h_used,
            row_gap_frac=row_gap_frac_used,
            sort_by="Description",
        )

    if kaiako_h > 0:
        draw_target_panel(
            ax,
            df_kaiako,
            family=family,
            title="Kaiako: Targets vs Actuals",
            x=panel_x,
            y=kaiako_y,
            width=panel_w,
            height=kaiako_h,
            header_h_abs=header_h_abs,
            header_pad=header_pad,
            row_h_abs=row_h_used,
            row_gap_frac=row_gap_frac_used,
            sort_by="Description",
        )

    # Key panel (above footer)
    style = PanelStyle()
    draw_key_panel(
        ax,
        family=family,
        x=0.04,
        y=0.015,
        width=0.92,
        height=0.05,
        style=style,
        header_text="",  # your latest version uses blank here
    )

    # Footer on fig
    add_full_width_footer_svg(
        fig,
        footer_svg,
        bottom_margin_frac=0.0,
        max_footer_height_frac=0.20,
        col_master="#1a427d40",
    )

    # Write page to PDF
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
        "rows_students": int(len(df_students)),
        "rows_kaiako": int(len(df_kaiako)),
        "empty": False,
    }

    # ✅ Return fig for preview PNG creation in report.py
    return fig, meta
