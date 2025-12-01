from __future__ import annotations
import numpy as np
import pandas as pd
import matplotlib.patches as patches
from dataclasses import dataclass
from typing import Optional, Tuple, Literal, Dict, Any


# =========================
# Small data containers
# =========================
@dataclass
class Areas:
    inner: Tuple[float, float, float, float]
    title_band: Tuple[float, float, float, float]
    content: Tuple[float, float, float, float]


@dataclass
class BarStyle:
    mode: Literal["fill", "line", "both"]
    face: str
    edge: str
    lw: float
    facealpha: float


# =========================
# Text utilities
# =========================
def _autosize_text_to_band(
    ax,
    text_str: str,
    center_xy_axes: Tuple[float, float],
    band_w_axes: float,
    band_h_axes: float,
    *,
    base_fontsize: float,
    fontsize_minmax: Tuple[float, float],
    font_family: Optional[str],
    fontweight: str,
    zorder: float,
):
    """Place centered text and shrink font until it fits inside (band_w_axes x band_h_axes)."""
    fig = ax.figure
    txt = ax.text(
        center_xy_axes[0], center_xy_axes[1], text_str,
        ha="center", va="center",
        fontsize=base_fontsize,
        fontweight=fontweight,
        family=font_family,
        transform=ax.transAxes,
        zorder=zorder,
    )

    renderer = fig.canvas.get_renderer()
    if renderer is None:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()

    # Convert allowed band size (axes units) -> display pixels
    (x0_disp, y0_disp) = ax.transAxes.transform((0, 0))
    (xw_disp, _      ) = ax.transAxes.transform((band_w_axes, 0))
    (_      , yh_disp) = ax.transAxes.transform((0, band_h_axes))
    max_w_px = xw_disp - x0_disp
    max_h_px = yh_disp - y0_disp

    fs = min(max(base_fontsize, fontsize_minmax[0]), fontsize_minmax[1])
    txt.set_fontsize(fs)
    while True:
        bbox = txt.get_window_extent(renderer=renderer)
        if bbox.width <= max_w_px and bbox.height <= max_h_px:
            break
        fs -= 0.5
        if fs <= fontsize_minmax[0]:
            fs = fontsize_minmax[0]
            txt.set_fontsize(fs)
            break
        txt.set_fontsize(fs)
    return txt


def _draw_value_label(
    ax,
    *,
    x_axes: float,
    y_axes: float,
    text: str,
    ha: Literal["left","center","right"],
    va: Literal["bottom","center","top"],
    color: str,
    fontsize: float,
    font_family: Optional[str],
    z: float,
):
    ax.text(
        x_axes, y_axes, text,
        ha=ha, va=va,
        fontsize=fontsize,
        color=color,
        family=font_family,
        transform=ax.transAxes,
        zorder=z,
    )


def _text_height_axes(ax, text: str, *, fontsize: float, family: str | None, fontweight: str | None) -> float:
    """
    Return the rendered text height in *axes* units for the given style.
    """
    fig = ax.figure
    t = ax.text(0, 0, text,
                fontsize=fontsize, family=family, fontweight=fontweight,
                transform=ax.transAxes, visible=False)
    renderer = fig.canvas.get_renderer()
    if renderer is None:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()

    bbox = t.get_window_extent(renderer=renderer)
    # pixels per 1.0 in axes-y
    y0 = ax.transAxes.transform((0, 0))[1]
    y1 = ax.transAxes.transform((0, 1))[1]
    px_per_axes_y = (y1 - y0)
    height_axes = bbox.height / px_per_axes_y
    t.remove()
    return float(height_axes)


# =========================
# Geometry & layout
# =========================
def _figure_aspect(ax) -> float:
    w_in, h_in = ax.figure.get_size_inches()
    return w_in / h_in


def _compute_insets(
    *, x: float, y: float, width: float, height: float,
    buffer: float, buffer_units: str, aspect: float
) -> Tuple[float, float, float, float]:
    bx = by = 0.0
    if buffer > 0:
        if buffer_units == "rect":
            by = buffer * height
            bx = by / aspect
        elif buffer_units == "page":
            by = buffer
            bx = buffer / aspect
        else:
            raise ValueError("buffer_units must be 'rect' or 'page'")
        eps = 1e-6
        bx = min(bx, width / 2 - eps)
        by = min(by, height / 2 - eps)

    inner_x, inner_y = x + bx, y + by
    inner_w, inner_h = width - 2 * bx, height - 2 * by
    return inner_x, inner_y, inner_w, inner_h


def _draw_debug_box(ax, xywh, *, edge, ls="--", lw=2.0, z=1.0):
    x, y, w, h = xywh
    r = patches.Rectangle(
        (x, y), w, h,
        facecolor="none", edgecolor=edge, linestyle=ls, linewidth=lw,
        transform=ax.transAxes, clip_on=False, zorder=z
    )
    ax.add_patch(r)
    return r


def _layout_regions(
    ax,
    *,
    x: float, y: float, width: float, height: float,
    buffer: float, buffer_units: str,
    title: Optional[str], title_band_frac: float,
    DEBUG: bool, zorder: float,
    extra_top_gap_frac: float = 0.0,   # reserve headroom above content
) -> Tuple[Areas, Dict[str, Any]]:
    """Compute inner, title, and content regions; draw debug guides if requested."""
    aspect = _figure_aspect(ax)

    outer_dbg = inner_dbg = title_dbg = None
    if DEBUG:
        outer_dbg = _draw_debug_box(ax, (x, y, width, height), edge="#ee0000", ls="--", lw=2, z=zorder)

    inner_x, inner_y, inner_w, inner_h = _compute_insets(
        x=x, y=y, width=width, height=height,
        buffer=buffer, buffer_units=buffer_units, aspect=aspect
    )

    if DEBUG:
        inner_dbg = _draw_debug_box(ax, (inner_x, inner_y, inner_w, inner_h), edge="#00aa00", ls="--", lw=2, z=zorder+0.1)

    band_h = 0.0
    if title and inner_w > 0 and inner_h > 0:
        band_h = max(1e-6, inner_h * float(title_band_frac))
        if DEBUG:
            title_dbg = _draw_debug_box(
                ax,
                (inner_x, inner_y + inner_h - band_h, inner_w, band_h),
                edge="#00aa00", ls=":", lw=1.5, z=zorder+0.35
            )

    gap_h = inner_h * max(0.0, float(extra_top_gap_frac))

    content_x, content_y = inner_x, inner_y
    content_w, content_h = inner_w, inner_h - band_h - gap_h
    if content_w < 0: content_w = 0.0
    if content_h < 0: content_h = 0.0

    areas = Areas(
        inner=(inner_x, inner_y, inner_w, inner_h),
        title_band=(inner_x, inner_y + inner_h - band_h, inner_w, band_h),
        content=(content_x, content_y, content_w, content_h),
    )
    dbg = {"outer_dbg": outer_dbg, "inner_dbg": inner_dbg, "title_dbg": title_dbg}
    return areas, dbg


def _draw_title(
    ax,
    *,
    title: Optional[str],
    areas: Areas,
    base_fontsize: float,
    fontsize_minmax: Tuple[float, float],
    font_family: Optional[str],
    fontweight: str,
    zorder: float,
):
    if not title:
        return
    x, y, w, h = areas.title_band
    if w <= 0 or h <= 0:
        return
    _autosize_text_to_band(
        ax,
        text_str=title,
        center_xy_axes=(x + w/2, y + h/2),
        band_w_axes=w,
        band_h_axes=h,
        base_fontsize=base_fontsize,
        fontsize_minmax=fontsize_minmax,
        font_family=font_family,
        fontweight=fontweight,
        zorder=zorder + 1.2,   # draw above bars/labels
    )


def _fill_background(ax, *, areas: Areas, colour: Optional[str], zorder: float):
    if colour in (None, "", "none"):
        return
    x, y, w, h = areas.inner
    if w <= 0 or h <= 0:
        return
    r = patches.Rectangle(
        (x, y), w, h,
        facecolor=colour, edgecolor="none", linewidth=0,
        transform=ax.transAxes, clip_on=False, zorder=zorder - 0.1
    )
    ax.add_patch(r)


def _debug_split_guides(
    ax,
    *,
    bar_type: Literal["h","v"],
    label_prop: float,
    areas: Areas,
    DEBUG: bool,
    zorder: float
) -> Tuple[Optional[patches.Rectangle], Optional[patches.Rectangle]]:
    if not DEBUG:
        return None, None

    cx, cy, cw, ch = areas.content
    label_prop = max(0.0, min(1.0, float(label_prop)))
    if bar_type == "h":
        lw_box = cw * label_prop
        labels_rect = _draw_debug_box(ax, (cx, cy, lw_box, ch), edge="#ff0000", ls="-", lw=1.5, z=zorder+0.4)
        values_rect = _draw_debug_box(ax, (cx + lw_box, cy, cw - lw_box, ch), edge="#ff0000", ls="-", lw=1.5, z=zorder+0.4)
    else:
        lh_box = ch * label_prop
        labels_rect = _draw_debug_box(ax, (cx, cy, cw, lh_box), edge="#ff0000", ls="-", lw=1.5, z=zorder+0.4)
        values_rect = _draw_debug_box(ax, (cx, cy + lh_box, cw, ch - lh_box), edge="#ff0000", ls="-", lw=1.5, z=zorder+0.4)
    return labels_rect, values_rect


# =========================
# Data prep & scaling
# =========================
def _validate_and_prepare(
    df: pd.DataFrame,
    label_col: str,
    value_col: str,
    max_value_col: Optional[str],
    n: Optional[int]
) -> pd.DataFrame:
    needed = [label_col, value_col] + ([max_value_col] if max_value_col else [])
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(f"Missing column(s) {missing} in df. Available: {list(df.columns)}")

    use_cols = [label_col, value_col] + ([max_value_col] if max_value_col else [])
    work = df.loc[:, use_cols].copy()
    if n is not None:
        work = work.head(int(n))
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce").fillna(0.0)
    if max_value_col:
        work[max_value_col] = pd.to_numeric(work[max_value_col], errors="coerce")
    return work


def _resolve_caps(
    work: pd.DataFrame,
    value_col: str,
    max_value_col: Optional[str],
    max_value: Optional[float]
) -> Tuple[bool, float]:
    if max_value_col:
        return True, 1.0  # per-row caps; global cap unused
    if isinstance(max_value, (int, float, np.floating)):
        return False, float(max_value)
    vmax = float(work[value_col].max()) if len(work) else 1.0
    return False, (vmax if vmax > 0 else 1.0)


def _norm_val(v: Any, cap: Any) -> float:
    v = 0.0 if v is None or not np.isfinite(v) else float(v)
    c = 1.0 if cap is None or not np.isfinite(cap) or cap <= 0 else float(cap)
    return max(0.0, min(1.0, v / c))


def _bar_style(mode: Literal["fill","line","both"], face: str, edge: str, lw: float, facealpha: float) -> BarStyle:
    use_face = face if mode in ("fill", "both") else "none"
    use_edge = edge if mode in ("line", "both") else "none"
    use_lw   = lw if mode in ("line", "both") else 0.0
    return BarStyle(mode=mode, face=use_face, edge=use_edge, lw=use_lw, facealpha=facealpha)


# =========================
# Renderers
# =========================
def _render_horizontal(
    ax,
    *,
    work: pd.DataFrame,
    label_col: str,
    value_col: str,
    max_value_col: Optional[str],
    global_cap: float,
    per_row_caps: bool,
    areas: Areas,
    label_prop: float,
    gutter_frac: float,
    barstyle: BarStyle,
    # labels/values
    label_fontsize: float,
    label_color: str,
    font_family: Optional[str],
    show_values: bool,
    value_position: Literal["in","out"],
    value_fontsize: float,
    value_color: str,
    value_format: str,
    zorder: float,
):
    cx, cy, cw, ch = areas.content
    label_prop = max(0.0, min(1.0, float(label_prop)))
    gf = max(0.0, min(float(gutter_frac), 0.45))

    lw_box = cw * label_prop
    slot_h = ch / max(1, len(work))
    bar_x0 = cx + lw_box
    bar_w_max = max(0.0, cw - lw_box)

    for i, (_, row) in enumerate(work.iterrows()):
        # slot with vertical gutter
        y_i = cy + i * slot_h + (slot_h * gf / 2.0)
        h_i = slot_h * (1.0 - gf)

        # label text (right-aligned)
        ax.text(
            cx + lw_box * 0.98, y_i + h_i / 2.0, str(row[label_col]),
            ha="right", va="center", fontsize=label_fontsize, color=label_color,
            family=font_family, transform=ax.transAxes, zorder=zorder + 0.5,
        )

        cap = (row[max_value_col] if per_row_caps else global_cap)
        frac = _norm_val(row[value_col], cap)
        bw = bar_w_max * frac

        bar_rect = patches.Rectangle(
            (bar_x0, y_i), max(0.0, bw), max(0.0, h_i),
            facecolor=barstyle.face, edgecolor=barstyle.edge, linewidth=barstyle.lw,
            alpha=(barstyle.facealpha if barstyle.face != "none" else 1.0),
            transform=ax.transAxes, clip_on=False, zorder=zorder + 0.6,
        )
        ax.add_patch(bar_rect)

        if show_values:
            val_text = value_format.format(row[value_col])
            inside = (value_position == "in")
            if inside and bw < 0.04 * bar_w_max:  # small bar → move outside
                inside = False

            if inside:
                x_text, ha, va, col = bar_x0 + bw * 0.98, "right", "center", ("#ffffff" if barstyle.face != "none" else value_color)
            else:
                x_text, ha, va, col = bar_x0 + bw + 0.01, "left", "center", value_color

            _draw_value_label(
                ax,
                x_axes=x_text, y_axes=y_i + h_i / 4.0,
                text=val_text, ha=ha, va=va, color=col,
                fontsize=value_fontsize, font_family=font_family, z=zorder + 0.7,
            )


def _render_vertical(
    ax,
    *,
    work: pd.DataFrame,
    label_col: str,
    value_col: str,
    max_value_col: Optional[str],
    global_cap: float,
    per_row_caps: bool,
    areas: Areas,
    label_prop: float,
    gutter_frac: float,
    barstyle: BarStyle,
    # labels/values
    label_fontsize: float,
    label_color: str,
    font_family: Optional[str],
    show_values: bool,
    value_position: Literal["in","out"],
    value_fontsize: float,
    value_color: str,
    value_format: str,
    zorder: float,
):
    cx, cy, cw, ch = areas.content
    label_prop = max(0.0, min(1.0, float(label_prop)))
    gf = max(0.0, min(float(gutter_frac), 0.45))

    lh_box = ch * label_prop
    slot_w = cw / max(1, len(work))
    bar_y0 = cy + lh_box
    bar_h_max = max(0.0, ch - lh_box)

    for i, (_, row) in enumerate(work.iterrows()):
        x_i = cx + i * slot_w + (slot_w * gf / 2.0)
        w_i = slot_w * (1.0 - gf)

        # label centered under each column
        ax.text(
            cx + i * slot_w + slot_w / 2.0, cy + lh_box * 0.5, str(row[label_col]),
            ha="center", va="center", fontsize=label_fontsize, color=label_color,
            family=font_family, transform=ax.transAxes, zorder=zorder + 0.5,
        )

        cap = (row[max_value_col] if per_row_caps else global_cap)
        frac = _norm_val(row[value_col], cap)
        bh = bar_h_max * frac

        bar_rect = patches.Rectangle(
            (x_i, bar_y0), max(0.0, w_i), max(0.0, bh),
            facecolor=barstyle.face, edgecolor=barstyle.edge, linewidth=barstyle.lw,
            alpha=(barstyle.facealpha if barstyle.face != "none" else 1.0),
            transform=ax.transAxes, clip_on=False, zorder=zorder + 0.6,
        )
        ax.add_patch(bar_rect)

        if show_values:
            val_text = value_format.format(row[value_col])
            inside = (value_position == "in")
            if inside and bh < 0.04 * bar_h_max:
                inside = False

            if inside:
                y_text, va, ha, col = bar_y0 + bh * 0.98, "top", "center", ("#ffffff" if barstyle.face != "none" else value_color)
            else:
                y_text, va, ha, col = bar_y0 + bh + 0.001, "bottom", "center", value_color

            _draw_value_label(
                ax,
                x_axes=x_i + w_i / 2.0, y_axes=y_text,
                text=val_text, ha=ha, va=va, color=col,
                fontsize=value_fontsize, font_family=font_family, z=zorder + 0.7,
            )


# =========================
# Public API
# =========================
def draw_bar_chart(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    # panel/background & layout
    bgcolour: str = "none",
    buffer: float = 0.0,                 # fraction of RECT HEIGHT
    buffer_units: str = "rect",          # "rect" or "page"
    title: Optional[str] = None,
    title_band_frac: float = 0.12,
    base_fontsize: float = 16.0,
    fontsize_minmax: Tuple[float, float] = (8.0, 28.0),
    font_family: Optional[str] = "PP Mori",
    fontweight: str = "bold",
    # content split & bars
    bar_type: Literal["h","v"] = "h",
    label_prop: float = 0.25,
    gutter_frac: float = 0.06,
    # data
    df: Optional[pd.DataFrame] = None,
    label_col: Optional[str] = None,
    value_col: Optional[str] = None,
    n: Optional[int] = None,
    max_value: Optional[float] = None,
    max_value_col: Optional[str] = None,
    # label styling
    label_fontsize: float = 10.0,
    label_color: str = "#222222",
    # bar styling
    bar_style: Literal["fill","line","both"] = "line",
    bar_facecolor: str = "#1a427d",
    bar_facealpha: float = 1.0,
    bar_edgecolor: str = "#1a427d",
    bar_linewidth: float = 1.2,
    # value labels
    show_values: bool = False,
    value_position: Literal["in","out"] = "in",
    value_fontsize: float = 9.0,
    value_color: str = "#000000",
    value_format: str = "{:.0%}",
    # layout tweak for vertical/outside value labels
    value_headroom_frac: Optional[float] = None,  # explicit headroom override
    # z / debug
    zorder: float = 1.0,
    DEBUG: bool = False,
):
    """
    Orchestrator: computes regions, draws background & title, prepares data, and renders bars.
    Keeps the original API intact.
    """
    # --- Prepare data early if available (so we can size headroom using real labels) ---
    work = None
    if df is not None and label_col and value_col:
        try:
            work = _validate_and_prepare(df, label_col, value_col, max_value_col, n)
        except Exception:
            work = None  # draw only panel/title if invalid

    # --- Compute optional headroom for vertical bars with outside value labels ---
    if value_headroom_frac is not None:
        reserve_headroom = max(0.0, float(value_headroom_frac))
    elif (bar_type == "v" and show_values and value_position == "out" and work is not None and len(work) > 0):
        sample_val = float(work[value_col].max())
        sample_text = value_format.format(sample_val)
        text_h_axes = _text_height_axes(
            ax, sample_text,
            fontsize=value_fontsize,
            family=font_family,
            fontweight=None  # set if you use a specific weight for value labels
        )
        reserve_headroom = min(text_h_axes + 0.01, 0.15)  # padding + clamp
    else:
        reserve_headroom = 0.0

    # --- Regions & guides (now with headroom) ---
    areas, dbg = _layout_regions(
        ax,
        x=x, y=y, width=width, height=height,
        buffer=buffer, buffer_units=buffer_units,
        title=title, title_band_frac=title_band_frac,
        DEBUG=DEBUG, zorder=zorder,
        extra_top_gap_frac=reserve_headroom,
    )

    # Title & background
    _draw_title(
        ax,
        title=title,
        areas=areas,
        base_fontsize=base_fontsize,
        fontsize_minmax=fontsize_minmax,
        font_family=font_family,
        fontweight=fontweight,
        zorder=zorder,
    )
    _fill_background(ax, areas=areas, colour=bgcolour, zorder=zorder)

    # Split debug boxes
    labels_rect, values_rect = _debug_split_guides(
        ax,
        bar_type=bar_type,
        label_prop=label_prop,
        areas=areas,
        DEBUG=DEBUG,
        zorder=zorder
    )
    dbg.update({"labels_rect": labels_rect, "values_rect": values_rect})

    # Nothing further if no drawable content region
    cx, cy, cw, ch = areas.content
    if cw <= 0 or ch <= 0:
        print(f"✅ Displayed bar graph ({title or ''})")
        return {**dbg, "areas": areas.__dict__}

    # If no data to draw, bail gracefully after panel/title
    if work is None:
        print(f"✅ Displayed bar graph ({title or ''})")
        return {**dbg, "areas": areas.__dict__}

    # --- Caps & style
    per_row_caps, global_cap = _resolve_caps(work, value_col, max_value_col, max_value)
    style = _bar_style(bar_style, bar_facecolor, bar_edgecolor, bar_linewidth, bar_facealpha)

    # --- Render bars
    if bar_type == "h":
        _render_horizontal(
            ax,
            work=work.iloc[::-1],
            label_col=label_col,
            value_col=value_col,
            max_value_col=max_value_col,
            global_cap=global_cap,
            per_row_caps=per_row_caps,
            areas=areas,
            label_prop=label_prop,
            gutter_frac=gutter_frac,
            barstyle=style,
            label_fontsize=label_fontsize,
            label_color=label_color,
            font_family=font_family,
            show_values=show_values,
            value_position=value_position,
            value_fontsize=value_fontsize,
            value_color=value_color,
            value_format=value_format,
            zorder=zorder,
        )
    else:
        _render_vertical(
            ax,
            work=work,
            label_col=label_col,
            value_col=value_col,
            max_value_col=max_value_col,
            global_cap=global_cap,
            per_row_caps=per_row_caps,
            areas=areas,
            label_prop=label_prop,
            gutter_frac=gutter_frac,
            barstyle=style,
            label_fontsize=label_fontsize,
            label_color=label_color,
            font_family=font_family,
            show_values=show_values,
            value_position=value_position,
            value_fontsize=value_fontsize,
            value_color=value_color,
            value_format=value_format,
            zorder=zorder,
        )

    print(f"✅ Displayed bar graph ({title or ''})")
    return {**dbg, "areas": areas.__dict__}
