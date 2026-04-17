# app/report_utils/CHT_CircleProportions.py
from __future__ import annotations

import math
from typing import Dict, Any, List

import pandas as pd
from matplotlib.patches import Ellipse, Polygon
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Ellipse, Polygon, Circle
from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon


# =============================================================================
# Bucket labels + default colours
# =============================================================================
BUCKET_CURRENT = "Current funded (2025–2026)"
BUCKET_PREV    = "Previously funded (2023–2025)"
BUCKET_NEVER   = "Not funded (2023–2026)"

DEFAULT_EDGE_CURRENT = "#1a427d"
DEFAULT_EDGE_PREV    = "#2EBDC2"
DEFAULT_EDGE_NEVER   = "#BBE6E9"


# =============================================================================
# Bucketing + stats
# =============================================================================
def _bucket(row: pd.Series) -> str:
    cur = int(row.get("Funded_2025_2026", 0) or 0) == 1
    prev_any = (int(row.get("Funded_2024_2025", 0) or 0) == 1) or (int(row.get("Funded_2023_2024", 0) or 0) == 1)
    if cur:
        return BUCKET_CURRENT
    if prev_any:
        return BUCKET_PREV
    return BUCKET_NEVER


def compute_bucket_stats(
    df: pd.DataFrame,
    *,
    col_bucket: str = "Bucket",
    colours: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "total": <int>,
        "buckets": {
           BUCKET_CURRENT: {"count": int, "colour": str, "radius": float?},
           BUCKET_PREV:    {"count": int, "colour": str, "radius": float?},
           BUCKET_NEVER:   {"count": int, "colour": str, "radius": float?},
        }
      }
    """
    if colours is None:
        colours = {
            BUCKET_CURRENT: DEFAULT_EDGE_CURRENT,
            BUCKET_PREV: DEFAULT_EDGE_PREV,
            BUCKET_NEVER: DEFAULT_EDGE_NEVER,
        }

    if col_bucket not in df.columns:
        df = df.copy()
        df[col_bucket] = df.apply(_bucket, axis=1)

    counts = df[col_bucket].value_counts().to_dict()
    total = int(len(df)) or 0

    return {
        "total": total,
        "buckets": {
            BUCKET_CURRENT: {"count": int(counts.get(BUCKET_CURRENT, 0)), "colour": colours[BUCKET_CURRENT]},
            BUCKET_PREV:    {"count": int(counts.get(BUCKET_PREV, 0)),    "colour": colours[BUCKET_PREV]},
            BUCKET_NEVER:   {"count": int(counts.get(BUCKET_NEVER, 0)),   "colour": colours[BUCKET_NEVER]},
        },
    }


def add_circle_radii(stats: Dict[str, Any], *, max_radius: float) -> None:
    """
    Writes b["radius"] into stats["buckets"][...], scaling circle AREAS by counts.
    max_radius applies to the largest-count bucket.
    """
    max_count = max(int(b.get("count", 0)) for b in stats["buckets"].values()) if stats.get("buckets") else 0
    max_area = math.pi * (max_radius ** 2)

    for b in stats["buckets"].values():
        count = int(b.get("count", 0))
        area = (count / max_count) * max_area if max_count else 0.0
        radius = math.sqrt(area / math.pi) if area > 0 else 0.0
        b["radius"] = float(radius)


# =============================================================================
# Text fitting (one-line, shared fontsize)
# =============================================================================
def _fit_fontsize_one_line(
    *,
    ax,
    texts: List[str],
    fontfamily: str,
    fontweight: str,
    box_width_axes: float,
    pad_frac: float,
    max_fs: float,
    min_fs: float = 6.0,
) -> float:
    """
    Find a single fontsize (points) that makes ALL texts fit on one line
    inside a box of width `box_width_axes` (axes coords), with padding `pad_frac`.

    Uses TextPath widths (points) + converts axes-width -> points via figure dpi.
    """
    fig = ax.figure

    # Need a renderer so transAxes is fully resolved
    fig.canvas.draw()

    # axes coords -> pixels
    p0 = ax.transAxes.transform((0.0, 0.0))
    p1 = ax.transAxes.transform((box_width_axes, 0.0))
    width_px = float(p1[0] - p0[0])

    # pixels -> points
    dpi = float(fig.dpi)
    width_pt = width_px * 72.0 / dpi

    # padding on both sides
    avail_pt = width_pt * max(0.0, 1.0 - 2.0 * float(pad_frac))

    fp = FontProperties(family=fontfamily, weight=fontweight)

    def max_text_width_pt(fs: float) -> float:
        wmax = 0.0
        for t in texts:
            if not t:
                continue
            tp = TextPath((0, 0), t, size=fs, prop=fp)
            w = tp.get_extents().width  # points
            if w > wmax:
                wmax = w
        return wmax

    lo, hi = float(min_fs), float(max_fs)
    for _ in range(20):  # binary search
        mid = (lo + hi) / 2.0
        if max_text_width_pt(mid) <= avail_pt:
            lo = mid
        else:
            hi = mid

    return lo


# =============================================================================
# Plot
# =============================================================================
def circle_plot(
    ax,
    *,
    stats: Dict[str, Any],
    fontfamily: str,
    top_y: float = 0.90,
    height: float = 0.20,
    leave_buffer_label: float = 0.15,
    gap_between_polygons: float = 0.05,
    polygon_height_scale: float = 0.80,      # polygon height = height * leave_buffer_label * this
    polygon_text_size: float | None = None,  # None => auto-fit ONE shared fontsize
    polygon_text_max: float = 18.0,
    polygon_text_min: float = 8.0,
    polygon_text_pad_frac: float = 0.08,
    show_circle_pct: bool = True,
    pct_min_circle_diam: float = 0.06,       # gating for % inside circles (based on 2*r)
    pct_min_value: float = 2.0,              # gating for % inside circles
) -> None:
    """
    Layout:
    - All polygons same width = x-diameter of the largest circle (in ellipse-x units)
    - Constant gap BETWEEN polygon edges (gap_between_polygons)
    - Each circle centered with its matching polygon (same x)
    - Polygons anchored: bottom = top_y - height
      polygon height = height * leave_buffer_label * polygon_height_scale
    - Circle % label (optional) gated by size & percent
    - Polygon label is 1-line. If polygon_text_size=None, compute ONE fontsize
      that fits the longest label and use it for all polygons.
    """
    # Ensure radii exist
    add_circle_radii(stats, max_radius=height - (leave_buffer_label * height))

    buckets = stats["buckets"]
    total = int(stats.get("total", 0) or 0)

    order: List[str] = list(stats["buckets"].keys())

    # ---- vertical layout ----
    y_bottom = top_y - height
    y_usable_top = top_y - height * leave_buffer_label
    cy = 0.5 * (y_bottom + y_usable_top) + (height * leave_buffer_label)  # keep your CY behaviour

    # ---- polygon geometry (anchored bottom) ----
    poly_h = height * leave_buffer_label * polygon_height_scale
    poly_bottom = top_y - height
    poly_cy = poly_bottom + (poly_h / 2.0)

    # ---- circle x-diameters (Ellipse width = r/(1/√2) == r*√2) ----
    radii = [float(buckets[k].get("radius", 0.0)) for k in order]
    circle_wx = [r / (1 / math.sqrt(2)) for r in radii]  # x-"diameters" in your ellipse-x units

    # ---- polygon width = largest circle x-diameter ----
    poly_w = max(circle_wx) if circle_wx else 0.0

    # ---- slot xs from polygon width + constant gap between polygon edges ----
    gap = float(gap_between_polygons)
    total_w = (len(order) * poly_w) + (gap * (len(order) - 1))
    x_left = 0.5 - total_w / 2.0

    xs: List[float] = []
    cursor = x_left
    for _ in order:
        xs.append(cursor + poly_w / 2.0)   # center of polygon (and circle)
        cursor += poly_w + gap

    # ---- choose ONE fontsize for all polygon labels (fit longest) ----
    pad_frac = float(polygon_text_pad_frac)
    if polygon_text_size is None:
        polygon_text_size = _fit_fontsize_one_line(
            ax=ax,
            texts=order,
            fontfamily=fontfamily,
            fontweight="semibold",
            box_width_axes=poly_w,
            pad_frac=pad_frac,
            max_fs=float(polygon_text_max),
            min_fs=float(polygon_text_min),
        )

    # ---- draw ----
    for x, key in zip(xs, order):
        d = buckets[key]
        r = float(d.get("radius", 0.0))
        c = str(d.get("colour", "#cccccc"))
        n = int(d.get("count", 0))

        # Circle (ellipse)
        ax.add_patch(
            Ellipse(
                (x, cy),
                r / (1 / math.sqrt(2)),  # x-width
                r,                       # y-width
                facecolor=c,
                edgecolor="white",
                lw=0,
                zorder=2000,
            )
        )

        # Optional % inside circle (gated)
        if show_circle_pct:
            pct = (100.0 * n / total) if total else 0.0
            diameter = 2.0 * r
            if (diameter >= pct_min_circle_diam) and (pct >= pct_min_value):
                fs = max(min(diameter * 120.0, 16.0), 8.0)
                ax.text(
                    x, cy, f"{pct:.1f}%",
                    ha="center", va="center",
                    fontsize=fs, color="white",
                    zorder=3000, fontweight="bold",
                )
        else:
            diameter = 2.0 * r
            pct = (100.0 * n / total) if total else 0.0
            if (diameter >= pct_min_circle_diam) and (pct >= pct_min_value):
                fs = max(min(diameter * 120.0, 16.0), 8.0)
                ax.text(
                    x, cy, n,
                    ha="center", va="center",
                    fontsize=fs, color="white",
                    zorder=3000, fontweight="bold",
                )
        # Label polygon
        poly = rounded_rect_polygon(
            cx=x,
            cy=poly_cy,
            width=poly_w,
            height=poly_h,
            ratio=1,
            corners_round=[1, 3],
            n_arc=64,
        )

        patch = Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=c,
            edgecolor=c,
            linewidth=1.5,
            transform=ax.transAxes,
            zorder=1000,
        )
        patch.set_clip_on(False)
        ax.add_patch(patch)

        # 1-line label inside polygon (shared fitted size)
        draw_text_in_polygon(
            ax,
            poly=poly,
            text=key,
            fontfamily=fontfamily,
            fontsize=float(polygon_text_size)*0.9,
            fontweight="semibold",
            color="#ffffff",
            pad_frac=pad_frac,
            wrap=False,
            autoshrink=False,  # we already computed a size that fits
            clip_to_polygon=False,
            max_lines=1,
            zorder=1100,
        )
        
        
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
def circle_plot_png(
    ax,
    *,
    stats: Dict[str, Any],
    fontfamily: str,
    top_y: float = 0.80,
    max_radius: float | None = None,
    gap_between: float = 0.03,
    side_margin: float = 0.02,
    label_gap: float = 0.02,
    title_reserved: float = 0.02,
    show_pct: bool = True,
    label_box_height: float = 0.08,
    label_box_pad_x: float = 0.015,
    label_fontsize: float = 10.5,
    bottom_margin: float = 0.03,
    label_box_width: float = 0.3,
) -> None:
    """
    PNG/Word-safe circle chart.

    - visually true circles
    - bottom-aligned coloured label boxes
    - largest circle fills the available height as much as possible
    - horizontal fitting uses actual displayed circle widths
    """
    buckets = stats["buckets"]
    total = int(stats.get("total", 0) or 0)
    order = list(buckets.keys())

    if not order:
        return

    # ----------------------------------------------------------
    # Vertical layout
    # ----------------------------------------------------------
    label_y = bottom_margin + (label_box_height / 2.0)
    label_top = bottom_margin + label_box_height

    circle_bottom = label_top + label_gap
    circle_top = 1.0 - title_reserved - side_margin

    available_circle_height = circle_top - circle_bottom
    if available_circle_height <= 0:
        return

    # Largest possible radius from vertical space
    fitted_max_radius = available_circle_height / 2.0

    if max_radius is None:
        max_radius_use = fitted_max_radius
    else:
        max_radius_use = min(max_radius, fitted_max_radius)

    # Build radii from counts
    add_circle_radii(stats, max_radius=max_radius_use)
    radii = [float(buckets[k].get("radius", 0.0)) for k in order]

    # ----------------------------------------------------------
    # True-circle correction
    # ----------------------------------------------------------
    fig = ax.figure
    fig.canvas.draw()
    bbox = ax.get_window_extent()
    ax_w = float(bbox.width)
    ax_h = float(bbox.height)

    if ax_w <= 0 or ax_h <= 0:
        return

    # Width in axes coords needed for a visually true circle
    wh_ratio = ax_h / ax_w

    # ----------------------------------------------------------
    # Horizontal fit using ACTUAL displayed widths
    # ----------------------------------------------------------
    n = len(radii)
    raw_total_width = sum(2.0 * r * wh_ratio for r in radii) + gap_between * (n - 1)
    available_width = 1.0 - 2.0 * side_margin

    if raw_total_width > 0:
        scale = available_width / raw_total_width
        radii = [r * scale for r in radii]

    total_width = sum(2.0 * r * wh_ratio for r in radii) + gap_between * (n - 1)

    x_left = 0.5 - total_width / 2.0

    xs = []
    cursor = x_left
    for r in radii:
        display_w = 2.0 * r * wh_ratio
        xs.append(cursor + display_w / 2.0)
        cursor += display_w + gap_between

    # ----------------------------------------------------------
    # Shared centre line:
    # largest circle touches the top exactly
    # ----------------------------------------------------------
    max_r = max(radii) if radii else 0.0
    cy = circle_top - max_r

    # Optional safeguard:
    # if labels/height/rounding ever push things too low, keep inside bounds
    if cy - max_r < circle_bottom:
        cy = circle_bottom + max_r

    # ----------------------------------------------------------
    # Draw circles + label boxes
    # ----------------------------------------------------------
    for x, r, key in zip(xs, radii, order):
        d = buckets[key]
        colour = str(d.get("colour", "#cccccc"))
        count = int(d.get("count", 0))
        pct = (100.0 * count / total) if total else 0.0

        # True visual circle via ellipse correction
        circ = Ellipse(
            (x, cy),
            width=2.0 * r * wh_ratio,
            height=2.0 * r,
            transform=ax.transAxes,
            facecolor=colour,
            edgecolor="none",
            zorder=2000,
        )
        circ.set_clip_on(False)
        ax.add_patch(circ)

        # Inner text
        if r >= 0.045:
            fs = max(10, min(16, r * 90))
            inner_text = f"{pct:.0f}%" if show_pct else f"{count}"
            ax.text(
                x,
                cy,
                inner_text,
                ha="center",
                va="center",
                fontsize=fs,
                color="white",
                fontweight="bold",
                fontfamily=fontfamily,
                transform=ax.transAxes,
                zorder=3000,
            )

        # Bottom label box
        poly = rounded_rect_polygon(
            cx=x,
            cy=label_y,
            width=label_box_width,
            height=label_box_height,
            ratio=0.25,
            corners_round=[1, 3],
            n_arc=64,
        )

        patch = Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=colour,
            edgecolor=colour,
            linewidth=1.0,
            transform=ax.transAxes,
            zorder=1000,
        )
        patch.set_clip_on(False)
        ax.add_patch(patch)

        draw_text_in_polygon(
            ax,
            poly=poly,
            text=key,
            fontfamily=fontfamily,
            fontsize=label_fontsize,
            fontweight="semibold",
            color="#ffffff",
            pad_frac=label_box_pad_x,
            wrap=False,
            autoshrink=True,
            clip_to_polygon=False,
            max_lines=1,
            zorder=1100,
        )
def save_circle_plot_image(
    df: pd.DataFrame,
    out_path: str | Path,
    *,
    fontfamily: str = "PP Mori",
    figsize: tuple[float, float] = (7.2, 2.4),
    dpi: int = 300,
    transparent: bool = False,
    facecolor: str = "white",
    top_y: float = 0.90,
    height: float = 0.42,
    leave_buffer_label: float = 0.22,
    gap_between_polygons: float = 0.05,
    polygon_height_scale: float = 0.78,
    polygon_text_size: float | None = None,
    polygon_text_max: float = 16.0,
    polygon_text_min: float = 8.0,
    polygon_text_pad_frac: float = 0.08,
    show_circle_pct: bool = True,
    pct_min_circle_diam: float = 0.06,
    pct_min_value: float = 2.0,
    colours: dict[str, str] | None = None,
) -> Path:
    """
    Create a Word-friendly PNG image of the circle proportions chart.

    Parameters
    ----------
    df : pd.DataFrame
        Input dataframe containing funding columns, or already containing a Bucket column.
    out_path : str | Path
        Output image path, usually .png.
    figsize : tuple
        Figure size in inches. Good Word option: (7.2, 2.4)
    dpi : int
        Image resolution. 300 is good for Word.
    transparent : bool
        Whether to save with transparent background.
    facecolor : str
        Figure/axes background when transparent=False.
    """

    out_path = Path(out_path)

    stats = compute_bucket_stats(df, colours=colours)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor(facecolor)
    ax.set_facecolor(facecolor)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    circle_plot(
        ax,
        stats=stats,
        fontfamily=fontfamily,
        top_y=top_y,
        height=height,
        leave_buffer_label=leave_buffer_label,
        gap_between_polygons=gap_between_polygons,
        polygon_height_scale=polygon_height_scale,
        polygon_text_size=polygon_text_size,
        polygon_text_max=polygon_text_max,
        polygon_text_min=polygon_text_min,
        polygon_text_pad_frac=polygon_text_pad_frac,
        show_circle_pct=show_circle_pct,
        pct_min_circle_diam=pct_min_circle_diam,
        pct_min_value=pct_min_value,
    )

    fig.savefig(
        out_path,
        format=out_path.suffix.lower().replace(".", "") if out_path.suffix else "png",
        dpi=dpi,
        bbox_inches="tight",
        pad_inches=0.02,
        transparent=transparent,
        facecolor="none" if transparent else facecolor,
    )
    plt.close(fig)

    return out_path