from __future__ import annotations
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd
import textwrap
from typing import Optional, Tuple

# ------------------ helpers ------------------
def _get_renderer(ax):
    fig = ax.figure
    rend = fig.canvas.get_renderer()
    if rend is None:
        fig.canvas.draw()
        rend = fig.canvas.get_renderer()
    return rend

def _px_per_axes_y(ax):
    y0 = ax.transAxes.transform((0, 0))[1]
    y1 = ax.transAxes.transform((0, 1))[1]
    return (y1 - y0)

def _wrap_to_column_chars(ax, col_w_axes: float, fontsize_pt: float) -> int:
    """
    Estimate characters per line so wrapping happens before hitting the column edge.
    Smaller avg_char_px = more aggressive wrapping.
    """
    dpi = ax.figure.dpi
    x0_px = ax.transAxes.transform((0, 0))[0]
    xw_px = ax.transAxes.transform((col_w_axes, 0))[0]
    col_px = max(1.0, xw_px - x0_px)

    avg_char_px = max(1.0, 0.45 * fontsize_pt * (dpi / 72.0))  # tighter wrap
    width_chars = max(8, int(col_px / avg_char_px))
    return width_chars

def _measure_wrapped_block(
    ax,
    *, text_str: str, x_axes: float, top_y_axes: float, col_w_axes: float,
    fontsize: float, family: Optional[str], indent_spaces: int = 0,
) -> tuple[float, str]:
    """Return (height_in_axes, wrapped_text) for a text block without leaving a visible draw."""
    width_chars = _wrap_to_column_chars(ax, col_w_axes, fontsize)
    wrapped = textwrap.fill(text_str, width=width_chars,
                            subsequent_indent=" " * indent_spaces)
    t = ax.text(x_axes, top_y_axes, wrapped, ha="left", va="top",
                fontsize=fontsize, family=family,
                transform=ax.transAxes, visible=True, alpha=0.0)
    bb = t.get_window_extent(renderer=_get_renderer(ax))
    t.remove()
    h_axes = bb.height / _px_per_axes_y(ax)
    return h_axes, wrapped

def _draw_wrapped_block(
    ax,
    *, text_str: str, x_axes: float, top_y_axes: float, col_w_axes: float,
    fontsize: float, family: Optional[str], color: str, indent_spaces: int = 0,
) -> float:
    """Draw wrapped text and return next cursor_y (axes)."""
    h_axes, wrapped = _measure_wrapped_block(
        ax, text_str=text_str, x_axes=x_axes, top_y_axes=top_y_axes,
        col_w_axes=col_w_axes, fontsize=fontsize, family=family,
        indent_spaces=indent_spaces,
    )
    ax.text(x_axes, top_y_axes, wrapped, ha="left", va="top",
            fontsize=fontsize, family=family, color=color,
            transform=ax.transAxes)
    return top_y_axes - h_axes

# ------------------ main (stacked, no subheads, better wrapping) ------------------
def draw_best_worst_competency(
    ax: plt.Axes,
    *, df: pd.DataFrame,
    x: float, y: float, width: float, height: float,
    title: Optional[str] = None, title_band_frac: float = 0.12,
    family: str = "PP Mori", base_fontsize: float = 14.0,
    fontsize_minmax: Tuple[float, float] = (8.0, 24.0),
    item_fs: int = 9, best_color: str = "#0B7A4B", worst_color: str = "#B3261E",
    percent_fmt: str = "{:.0%}", DEBUG: bool = False,
):
    """
    Stacked 'Best' then 'Worst' lists per YearGroupDesc (no subheadings, wrapped text).
    df must have: YearGroupDesc, RankType ('Best'|'Worst'), CompetencyDesc, Rate (0..100).
    """
    if df.empty:
        return

    yg_order = ["0-2", "3-4", "5-6", "7-8"]
    df = df[df["YearGroupDesc"].isin(yg_order)].copy()

    # Title band
    title_h = height * title_band_frac if title else 0.0
    content_h = height - title_h
    if title:
        ax.text(x + width / 2, y + content_h + title_h / 2, title,
                ha="center", va="center",
                fontsize=max(min(base_fontsize, fontsize_minmax[1]), fontsize_minmax[0]),
                family=family, fontweight="semibold", transform=ax.transAxes)

    if content_h <= 0:
        return

    # spacing
    heading_gap        = 0.01
    row_gap_axes       = 0.005   # tighter vertical gaps between items
    between_lists_gap  = 0.004
    col_pad_frac       = 0.02
    col_inner_pad      = 0.01    # small extra padding to ensure text never touches edge

    txt_x = x 
    col_w = width - 0.02

    # measure heights per group
    group_req = {}
    for yg in yg_order:
        sub = df[df["YearGroupDesc"] == yg]
        if sub.empty:
            group_req[yg] = 0.0
            continue

        best  = sub[sub["RankType"] == "Best"]
        worst = sub[sub["RankType"] == "Worst"]

        h = 0.0
        h_head, _ = _measure_wrapped_block(
            ax, text_str=f"Years {yg}", x_axes=txt_x, top_y_axes=0.5,
            col_w_axes=col_w, fontsize=item_fs + 2, family=family
        )
        h += h_head + heading_gap

        # Best + Worst items
        for group, color in [(best, best_color), (worst, worst_color)]:
            for j, (_, row) in enumerate(group.iterrows()):
                prefix = f"{j+1}. "
                text_str = prefix + f"{row['CompetencyDesc']} ({percent_fmt.format(float(row['Rate'])/100.0)})"
                hh, _ = _measure_wrapped_block(
                    ax, text_str=text_str, x_axes=txt_x, top_y_axes=0.5,
                    col_w_axes=col_w, fontsize=item_fs, family=family,
                    indent_spaces=len(prefix)
                )
                h += hh + row_gap_axes
            h += between_lists_gap

        h += 0.005
        group_req[yg] = h

    total_req = sum(group_req.values())
    if total_req <= 0:
        return

    scale = content_h / total_req

    # draw pass
    sec_top = y + content_h
    for yg in yg_order:
        sub = df[df["YearGroupDesc"] == yg]
        if sub.empty:
            continue

        sec_h = group_req[yg] * scale
        sec_y = sec_top - sec_h
        bottom_floor = sec_y + 0.004

        # heading
        ax.text(x, sec_top - 0.005, f"Years {yg}",
                ha="left", va="top",
                fontsize=item_fs + 2, family=family,
                fontweight="semibold", color="black", transform=ax.transAxes)

        cursor_y = sec_top - 0.005 - heading_gap

        best  = sub[sub["RankType"] == "Best"]
        worst = sub[sub["RankType"] == "Worst"]

        # draw both lists
        for group, color in [(best, best_color), (worst, worst_color)]:
            for j, (_, row) in enumerate(group.iterrows()):
                prefix = f"{j+1}. "
                text_str = prefix + f"{row['CompetencyDesc']} ({percent_fmt.format(float(row['Rate'])/100.0)})"
                new_cursor = _draw_wrapped_block(
                    ax, text_str=text_str, x_axes=txt_x, top_y_axes=cursor_y,
                    col_w_axes=col_w, fontsize=item_fs, family=family,
                    color=color, indent_spaces=len(prefix)
                ) - row_gap_axes
                
                cursor_y = new_cursor
            cursor_y -= between_lists_gap

        if DEBUG:
            ax.add_patch(Rectangle((x, sec_y), width, sec_h,
                                   fill=False, ec="#98C379", lw=0.8, ls="--",
                                   transform=ax.transAxes))

        sec_top = sec_y
