from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Dict, Optional, Literal, Tuple, Set
import textwrap

import pandas as pd
import numpy as np

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.text import Text
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

Align = Literal["left", "center", "right"]


# ============================================================
# V1 (kept as-is, small tidy only)
# ============================================================
def draw_dataframe_table(
    ax: plt.Axes,
    *,
    df: pd.DataFrame,
    # box position/size in AXES-FRACTION coordinates (0..1 of the axes)
    x: float, y: float, width: float, height: float,
    # column definitions: order + label + relative width + alignment
    columns: List[Dict] | None = None,
    # header styling
    header_height_frac: float = 0.12,
    header_facecolor: str = "#1a427d",
    header_textcolor: str = "#ffffff",
    header_fontfamily: Optional[str] = None,
    header_fontsize: float = 11.0,
    header_fontweight: str = "semibold",
    # body styling
    body_fontfamily: Optional[str] = None,
    body_fontsize: float = 10.0,
    body_textcolor: str = "#111111",
    row_alt_facecolor: Optional[str] = "#f6f8fb",
    row_facecolor: Optional[str] = "#ffffff",
    # grid/border styling
    show_grid: bool = True,
    grid_color: str = "#c9d2e3",
    grid_linewidth: float = 0.6,
    border_color: str = "#1f2a44",
    border_linewidth: float = 1.0,
    # cell padding (axes fractions of each cell)
    pad_x_frac: float = 0.01,
    pad_y_frac: float = 0.005,
    # text alignment per column (default 'left')
    default_align: Align = "left",
    # truncate/wrap behaviour
    wrap: bool = True,
    max_wrap_lines: int = 3,

    # footer (optional)
    footer: Optional[str] = None,
    footer_align: Align = "left",
    footer_fontsize: float = 9.0,
    footer_fontfamily: Optional[str] = None,
    footer_color: str = "#667085",
    footer_gap_frac: float = 0.01,

    DEBUG: bool = False,
) -> None:
    if df is None or df.empty:
        return

    # ----- Resolve columns -----
    if columns is None:
        keys = list(df.columns)
        k = len(keys)
        columns = [{"key": k_, "label": str(k_), "width_frac": 1.0 / max(1, k), "align": default_align}
                   for k_ in keys]
    else:
        total_w = sum(c.get("width_frac", 0.0) for c in columns)
        if not total_w or abs(total_w - 1.0) > 1e-6:
            s = sum(c.get("width_frac", 1.0) for c in columns) or 1.0
            for c in columns:
                c["width_frac"] = c.get("width_frac", 1.0) / s
        for c in columns:
            c.setdefault("label", str(c["key"]))
            c.setdefault("align", default_align)

    n_rows = len(df)
    header_h = height * header_height_frac
    body_h = max(0.0, height - header_h)
    row_h = body_h / max(1, n_rows)

    trans = ax.transAxes

    if DEBUG:
        ax.add_patch(Rectangle((x, y), width, height, fill=False,
                               edgecolor="red", lw=0.8, transform=trans, zorder=10_000))

    # border
    ax.add_patch(Rectangle((x, y), width, height,
                           facecolor="none",
                           edgecolor=border_color,
                           lw=border_linewidth,
                           transform=trans,
                           zorder=5_000))

    # header bg
    ax.add_patch(Rectangle((x, y + height - header_h), width, header_h,
                           facecolor=header_facecolor,
                           edgecolor="none",
                           transform=trans,
                           zorder=4_900))

    # col x positions
    col_x = [x]
    for c in columns:
        col_x.append(col_x[-1] + width * c["width_frac"])

    def _axes_width_to_pixels(w_axes: float) -> float:
        x0 = ax.transAxes.transform((0, 0))[0]
        x1 = ax.transAxes.transform((w_axes, 0))[0]
        return max(1.0, x1 - x0)

    def _wrap_text_to_width(s: str, col_w_axes: float, fontsize: float) -> str:
        if not wrap or not s:
            return s
        pad_px = _axes_width_to_pixels(pad_x_frac * width)
        avail_px = _axes_width_to_pixels(col_w_axes) - 2 * pad_px
        if avail_px <= 1:
            return s
        avg_char_px = max(0.1, 0.55 * fontsize)
        max_chars = max(1, int(avail_px / avg_char_px))
        wrapped = textwrap.fill(str(s), width=max_chars)
        if max_wrap_lines and max_wrap_lines > 0:
            lines = wrapped.splitlines()
            if len(lines) > max_wrap_lines:
                kept = lines[:max_wrap_lines]
                kept[-1] = kept[-1].rstrip() + "…"
                return "\n".join(kept)
        return wrapped

    def _ha(align: Align) -> str:
        return {"left": "left", "center": "center", "right": "right"}[align]

    def _x_text(col_left_axes: float, col_right_axes: float, align: Align) -> float:
        if align == "left":
            return col_left_axes + pad_x_frac * width
        if align == "right":
            return col_right_axes - pad_x_frac * width
        return 0.5 * (col_left_axes + col_right_axes)

    # header labels + vertical separators
    for i, col in enumerate(columns):
        left, right = col_x[i], col_x[i + 1]
        if show_grid and i > 0:
            ax.plot([left, left], [y, y + height],
                    transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)
        ax.text(
            _x_text(left, right, align="center"),
            y + height - 0.5 * header_h,
            col["label"],
            transform=trans,
            ha="center",
            va="center",
            fontsize=header_fontsize,
            fontfamily=header_fontfamily,
            fontweight=header_fontweight,
            color=header_textcolor,
            zorder=5_200,
        )

    if show_grid:
        ax.plot([x, x + width], [y + height - header_h, y + height - header_h],
                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

    wrapped_cache: Dict[tuple, str] = {}

    for r in range(n_rows):
        row_y0 = y + body_h - (r + 1) * row_h
        row_yc = row_y0 + 0.5 * row_h

        # zebra background
        if row_alt_facecolor:
            face = row_alt_facecolor if (r % 2 == 0) else (row_facecolor or "#ffffff")
        else:
            face = row_facecolor or "#ffffff"

        ax.add_patch(Rectangle((x, row_y0), width, row_h,
                               facecolor=face, edgecolor="none",
                               transform=trans, zorder=4_800))

        if show_grid and r < n_rows - 1:
            ax.plot([x, x + width], [row_y0, row_y0],
                    transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

        for i, col in enumerate(columns):
            key = col["key"]
            align = col.get("align", default_align)
            left, right = col_x[i], col_x[i + 1]
            val = df.iloc[r][key]
            s = "" if pd.isna(val) else str(val)

            col_w_axes = (right - left)
            cache_key = (i, r)
            if cache_key not in wrapped_cache:
                wrapped_cache[cache_key] = _wrap_text_to_width(s, col_w_axes=col_w_axes, fontsize=body_fontsize)
            s_wrapped = wrapped_cache[cache_key]

            ax.text(
                _x_text(left, right, align=align),
                row_yc,
                s_wrapped,
                transform=trans,
                ha=_ha(align),
                va="center",
                fontsize=body_fontsize,
                fontfamily=body_fontfamily,
                color=body_textcolor,
                linespacing=1.05,
                zorder=5_200,
            )

    if show_grid:
        ax.plot([col_x[-1], col_x[-1]], [y, y + height],
                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

    # footer
    if footer:
        if footer_align == "left":
            x_text = x + pad_x_frac * width
            ha = "left"
        elif footer_align == "right":
            x_text = x + width - pad_x_frac * width
            ha = "right"
        else:
            x_text = x + 0.5 * width
            ha = "center"

        y_text = y - footer_gap_frac
        va = "top"
        if y_text < 0.0:
            y_text = y + footer_gap_frac
            va = "bottom"

        ax.text(
            x_text, y_text, str(footer),
            transform=ax.transAxes,
            ha=ha, va=va,
            fontsize=footer_fontsize,
            fontfamily=footer_fontfamily or body_fontfamily,
            color=footer_color,
            zorder=5_300,
        )

    print("✅ Successfully saved renedered table")


# ============================================================
# V2 (UPDATED): conditional row colouring + MERGE ANY COLUMN(S)
# ============================================================
def draw_dataframe_table_v2(
    ax: plt.Axes,
    *,
    df: pd.DataFrame,
    x: float, y: float, width: float, height: float,
    columns: List[Dict] | None = None,

    # header styling
    header_height_frac: float = 0.12,
    header_facecolor: str = "#1a427d",
    header_textcolor: str = "#ffffff",
    header_fontfamily: Optional[str] = None,
    header_fontsize: float = 11.0,
    header_fontweight: str = "semibold",

    # body styling
    body_fontfamily: Optional[str] = None,
    body_fontsize: float = 10.0,
    body_textcolor: str = "#111111",

    # base row colour
    base_row_facecolor: str = "#ffffff",

    # optional zebra
    row_alt_facecolor: Optional[str] = None,

    # conditional row colouring: return (bg, text) or None
    row_color_fn: Optional[Callable[[pd.Series, int], Optional[Tuple[str, str]]]] = None,

    # grid/border styling
    show_grid: bool = True,
    grid_color: str = "#c9d2e3",
    grid_linewidth: float = 0.6,
    border_color: str = "#1a427d",
    border_linewidth: float = 1.0,

    # padding
    pad_x_frac: float = 0.01,
    pad_y_frac: float = 0.005,

    # alignment
    default_align: Align = "left",

    # wrap behaviour
    wrap: bool = True,
    max_wrap_lines: int = 3,

    # ✅ NEW: merge any column(s) when consecutive values equal
    merge_cols: Optional[List[str]] = None,         # list of column keys to merge (e.g. ["Provider"])
    merge_col_indices: Optional[List[int]] = None,  # list of column indices to merge (e.g. [1] = second col)
    merge_text_align: Align = "left",

    # footer
    footer: Optional[str] = None,
    footer_align: Align = "left",
    footer_fontsize: float = 9.0,
    footer_fontfamily: Optional[str] = None,
    footer_color: str = "#667085",
    footer_gap_frac: float = 0.01,

    # debug
    DEBUG: bool = False,

    # behaviour used in your code
    shift: bool = False,
) -> None:
    if df is None or df.empty:
        return

    # ----- Resolve columns -----
    if columns is None:
        keys = list(df.columns)
        k = len(keys)
        columns = [{"key": k_, "label": str(k_), "width_frac": 1.0 / max(1, k), "align": default_align}
                   for k_ in keys]
    else:
        total_w = sum(c.get("width_frac", 0.0) for c in columns)
        if not total_w or abs(total_w - 1.0) > 1e-6:
            s = sum(c.get("width_frac", 1.0) for c in columns) or 1.0
            for c in columns:
                c["width_frac"] = c.get("width_frac", 1.0) / s
        for c in columns:
            c.setdefault("label", str(c["key"]))
            c.setdefault("align", default_align)

    n_rows = len(df)
    header_h = height * header_height_frac
    body_h = max(0.0, height - header_h)
    row_h = body_h / max(1, n_rows)

    trans = ax.transAxes

    if DEBUG:
        ax.add_patch(Rectangle((x, y), width, height, fill=False,
                               edgecolor="red", lw=0.8, transform=trans, zorder=10_000))

    # border
    ax.add_patch(Rectangle((x, y), width, height,
                           facecolor="none",
                           edgecolor=border_color,
                           lw=border_linewidth,
                           transform=trans,
                           zorder=5_000))

    # header bg
    ax.add_patch(Rectangle((x, y + height - header_h), width, header_h,
                           facecolor=header_facecolor,
                           edgecolor="none",
                           transform=trans,
                           zorder=4_900))

    # column x positions  (✅ FIXED: only build once)
    col_x = [x]
    for c in columns:
        col_x.append(col_x[-1] + width * c["width_frac"])

    # first-col bounds used for your "shift" behaviour
    first_col_left = col_x[0]
    first_col_right = col_x[1] if len(col_x) >= 2 else x

    # ----- helpers -----
    def _axes_width_to_pixels(w_axes: float) -> float:
        x0 = ax.transAxes.transform((0, 0))[0]
        x1 = ax.transAxes.transform((w_axes, 0))[0]
        return max(1.0, x1 - x0)

    def _wrap_text_to_width(s: str, *, col_w_axes: float, fontsize_pt: float) -> str:
        if not wrap or not s:
            return s

        dpi = float(ax.figure.dpi or 100.0)
        fontsize_px = fontsize_pt * dpi / 72.0

        pad_px = _axes_width_to_pixels(pad_x_frac * width)
        avail_px = _axes_width_to_pixels(col_w_axes) - 2 * pad_px
        if avail_px <= 5:
            return s

        avg_char_px = max(1.0, 0.58 * fontsize_px)
        max_chars = max(1, int(avail_px / avg_char_px))

        wrapper = textwrap.TextWrapper(
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=True,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        wrapped = wrapper.fill(str(s))

        if max_wrap_lines and max_wrap_lines > 0:
            lines = wrapped.splitlines()
            if len(lines) > max_wrap_lines:
                kept = lines[:max_wrap_lines]
                kept[-1] = kept[-1].rstrip() + "…"
                return "\n".join(kept)

        return wrapped

    def _ha(align: Align) -> str:
        return {"left": "left", "center": "center", "right": "right"}[align]

    def _x_text(col_left: float, col_right: float, align: Align) -> float:
        if align == "left":
            return col_left + pad_x_frac * width
        if align == "right":
            return col_right - pad_x_frac * width
        return 0.5 * (col_left + col_right)

    # ----- Header labels + vertical separators -----
    for i, col in enumerate(columns):
        left, right = col_x[i], col_x[i + 1]

        if show_grid and i > 0:
            ax.plot([left, left], [y, y + height],
                    transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

        ax.text(
            _x_text(left, right, align="center"),
            y + height - 0.5 * header_h,
            col["label"],
            transform=trans,
            ha="center",
            va="center",
            fontsize=header_fontsize,
            fontfamily=header_fontfamily,
            fontweight=header_fontweight,
            color=header_textcolor,
            zorder=5_200,
        )

    if show_grid:
        ax.plot([x, x + width], [y + height - header_h, y + height - header_h],
                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

    # ============================================================
    # ✅ Merge map for any selected columns (consecutive equal values)
    # merge_map[col_idx] = { row_index -> (group_start, group_end) }
    # ============================================================
    merge_map: Dict[int, Dict[int, Tuple[int, int]]] = {}

    merge_idx_set: Set[int] = set()
    if merge_col_indices:
        merge_idx_set.update(i for i in merge_col_indices if 0 <= i < len(columns))

    if merge_cols:
        key_to_idx = {c["key"]: i for i, c in enumerate(columns)}
        for k in merge_cols:
            if k in key_to_idx:
                merge_idx_set.add(key_to_idx[k])

    for m_idx in sorted(merge_idx_set):
        m_key = columns[m_idx]["key"]
        if m_key not in df.columns:
            continue

        vals = df[m_key].tolist()
        row_to_group: Dict[int, Tuple[int, int]] = {}

        start = 0
        while start < n_rows:
            end = start
            while end + 1 < n_rows and vals[end + 1] == vals[start]:
                end += 1
            for rr in range(start, end + 1):
                row_to_group[rr] = (start, end)
            start = end + 1

        merge_map[m_idx] = row_to_group

    wrapped_cache: Dict[tuple, str] = {}

    # ----- Body rows -----
    for r in range(n_rows):
        row_y0 = y + body_h - (r + 1) * row_h
        row_yc = row_y0 + 0.5 * row_h

        # zebra face
        if row_alt_facecolor and (r % 2 == 0):
            zebra_face = row_alt_facecolor
        else:
            zebra_face = base_row_facecolor

        row_face = zebra_face
        text_override = None

        if row_color_fn is not None:
            override = row_color_fn(df.iloc[r], r)
            if override:
                row_face, text_override = override

        # Background: your "shift" behaviour (first col always base)
        if (not shift) and len(col_x) >= 2:
            ax.add_patch(Rectangle((first_col_left, row_y0), first_col_right - first_col_left, row_h,
                                   facecolor=base_row_facecolor, edgecolor="none",
                                   transform=trans, zorder=4_800))
            ax.add_patch(Rectangle((first_col_right, row_y0), (x + width) - first_col_right, row_h,
                                   facecolor=row_face, edgecolor="none",
                                   transform=trans, zorder=4_800))
        else:
            ax.add_patch(Rectangle((x, row_y0), width, row_h,
                                   facecolor=row_face, edgecolor="none",
                                   transform=trans, zorder=4_800))

        # ----- Horizontal grid (skip inside merged column interiors) -----
        if show_grid and r < n_rows - 1:
            skip_ranges: List[Tuple[float, float]] = []
            for m_idx, row_to_group in merge_map.items():
                if r in row_to_group:
                    gs, ge = row_to_group[r]
                    if r < ge:  # interior
                        skip_ranges.append((col_x[m_idx], col_x[m_idx + 1]))

            if not skip_ranges:
                ax.plot([x, x + width], [row_y0, row_y0],
                        transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)
            else:
                skip_ranges.sort()
                merged: List[Tuple[float, float]] = []
                for a, b in skip_ranges:
                    if not merged or a > merged[-1][1]:
                        merged.append((a, b))
                    else:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], b))

                cursor = x
                for a, b in merged:
                    if a > cursor:
                        ax.plot([cursor, a], [row_y0, row_y0],
                                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)
                    cursor = max(cursor, b)
                if cursor < x + width:
                    ax.plot([cursor, x + width], [row_y0, row_y0],
                            transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

        # ----- Cells -----
        for i, col in enumerate(columns):
            key = col["key"]
            align = col.get("align", default_align)
            left, right = col_x[i], col_x[i + 1]

            # ✅ merged cell handling (only draw text on group start)
            if i in merge_map and r in merge_map[i]:
                gs, ge = merge_map[i][r]
                if r != gs:
                    continue

                gs_y0 = y + body_h - (gs + 1) * row_h
                ge_y0 = y + body_h - (ge + 1) * row_h
                merged_y0 = ge_y0
                merged_h = (gs_y0 + row_h) - ge_y0

                merged_yc = (merged_y0 + 0.5 * merged_h + (row_h * 0.5)) if shift else (merged_y0 + 0.5 * merged_h)

                v = df.iloc[gs][key]
                s = "" if pd.isna(v) else str(v)

                col_w_axes = (right - left)
                s_wrapped = _wrap_text_to_width(s, col_w_axes=col_w_axes, fontsize_pt=body_fontsize)

                ax.text(
                    _x_text(left, right, align=merge_text_align),
                    merged_yc,
                    s_wrapped,
                    transform=trans,
                    ha=_ha(merge_text_align),
                    va="center",
                    fontsize=body_fontsize,
                    fontfamily=body_fontfamily,
                    color=text_override or body_textcolor,
                    linespacing=1.05,
                    zorder=5_200,
                )
                continue

            # normal cell
            val = df.iloc[r][key]
            s = "" if pd.isna(val) else str(val)

            col_w_axes = (right - left)
            cache_key = (i, r)
            if cache_key not in wrapped_cache:
                wrapped_cache[cache_key] = _wrap_text_to_width(s, col_w_axes=col_w_axes, fontsize_pt=body_fontsize)
            s_wrapped = wrapped_cache[cache_key]

            ax.text(
                _x_text(left, right, align=align),
                row_yc,
                s_wrapped,
                transform=trans,
                ha=_ha(align),
                va="center",
                fontsize=body_fontsize,
                fontfamily=body_fontfamily,
                color=text_override or body_textcolor,
                linespacing=1.05,
                zorder=5_200,
            )

    # right-most vertical grid
    if show_grid:
        ax.plot([col_x[-1], col_x[-1]], [y, y + height],
                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

    # footer
    if footer:
        if footer_align == "left":
            x_text = x + pad_x_frac * width
            ha = "left"
        elif footer_align == "right":
            x_text = x + width - pad_x_frac * width
            ha = "right"
        else:
            x_text = x + 0.5 * width
            ha = "center"

        y_text = y - footer_gap_frac
        va = "top"
        if y_text < 0.0:
            y_text = y + footer_gap_frac
            va = "bottom"

        ax.text(
            x_text, y_text, str(footer),
            transform=ax.transAxes,
            ha=ha, va=va,
            fontsize=footer_fontsize,
            fontfamily=footer_fontfamily or body_fontfamily,
            color=footer_color,
            zorder=5_300,
        )


# ============================================================
# Geometry helpers
# ============================================================
@dataclass(frozen=True)
class TablePos:
    y: float
    height: float
    header_h: float
    body_h: float
    row_h: float
    n_rows: int

    def row_y0(self, r: int) -> float:
        return self.y + self.header_h + self.body_h - (r + 1) * self.row_h

    def row_yc(self, r: int) -> float:
        return self.row_y0(r) + 0.5 * self.row_h


def table_pos(
    df: pd.DataFrame,
    y1: float,
    y2: float,
    header_height_frac: float,
    *,
    min_row_h: float | None = None,
) -> TablePos:
    if df is None or df.empty:
        yb, yt = (min(y1, y2), max(y1, y2))
        return TablePos(y=yb, height=0.0, header_h=0.0, body_h=0.0, row_h=0.0, n_rows=0)

    yb, yt = (min(y1, y2), max(y1, y2))
    avail_h = max(0.0, yt - yb)
    n = len(df)

    if min_row_h is not None and min_row_h > 0:
        needed_h = (n * min_row_h) / max(1e-9, (1.0 - header_height_frac))
        height = min(avail_h, needed_h)
    else:
        height = avail_h

    header_h = header_height_frac * height
    body_h = max(0.0, height - header_h)
    row_h = body_h / n if n > 0 else 0.0

    return TablePos(y=yb, height=height, header_h=header_h, body_h=body_h, row_h=row_h, n_rows=n)


# ============================================================
# Dynamic column width helper
# ============================================================
def _get_renderer(fig):
    rend = fig.canvas.get_renderer()
    if rend is None:
        fig.canvas.draw()
        rend = fig.canvas.get_renderer()
    return rend


def _text_width_px(ax, s: str, *, fontsize: float, family: Optional[str], weight: str = "normal") -> float:
    txt = (s or "")
    fig = ax.figure

    try:
        renderer = _get_renderer(fig)
        t = Text(x=0, y=0, text=txt)
        t.set_figure(fig)  # critical so DPI exists
        t.set_fontsize(fontsize)
        if family is not None:
            t.set_fontfamily(family)
        if weight is not None:
            t.set_fontweight(weight)
        bb = t.get_window_extent(renderer=renderer)
        return float(bb.width)
    except Exception:
        try:
            fp = FontProperties(family=family, weight=weight, size=fontsize)
            tp = TextPath((0, 0), txt, prop=fp)
            w_pts = tp.get_extents().width
            dpi = fig.dpi or 72.0
            return float(w_pts / 72.0 * dpi)
        except Exception:
            avg_char_px = 0.55 * fontsize
            return avg_char_px * max(1, len(txt))


def _axes_width_to_pixels(ax, w_axes: float) -> float:
    x0 = ax.transAxes.transform((0, 0))[0]
    x1 = ax.transAxes.transform((w_axes, 0))[0]
    return max(1.0, x1 - x0)


def build_dynamic_columns(
    ax,
    df,
    *,
    table_x_axes: float,
    table_width_axes: float,
    pad_x_frac: float,
    header_fontfamily: Optional[str],
    header_fontsize: float,
    header_fontweight: str,
    body_fontfamily: Optional[str],
    body_fontsize: float,
    school_key: str = "SchoolName",
    provider_key: str = "Provider",
    classes_key: str = "NumClasses",
    edited_key: str = "EditedClasses",
    min_numeric_col_px: float = 80.0,
    min_text_col_px: float = 120.0,
    max_text_total_frac: float = 0.8,
) -> List[Dict]:
    if df is None or df.empty:
        return [
            {"key": school_key,   "label": "School",   "width_frac": 0.40, "align": "left"},
            {"key": provider_key, "label": "Provider", "width_frac": 0.30, "align": "left"},
            {"key": classes_key,  "label": "Classes",  "width_frac": 0.15, "align": "center"},
            {"key": edited_key,   "label": "Edited*",  "width_frac": 0.15, "align": "center"},
        ]

    table_w_px = _axes_width_to_pixels(ax, table_width_axes)
    pad_px_each_side = _axes_width_to_pixels(ax, pad_x_frac * table_width_axes)

    def longest_px(col_key: str, header_label: str) -> float:
        header_px = _text_width_px(
            ax,
            header_label,
            fontsize=header_fontsize,
            family=header_fontfamily,
            weight=header_fontweight,
        )
        body_strings = df[col_key].astype(str).fillna("")
        candidates = body_strings.loc[body_strings.str.len().nlargest(20).index]
        body_px = 0.0
        for s in candidates:
            body_px = max(body_px, _text_width_px(ax, s, fontsize=body_fontsize, family=body_fontfamily))
        return max(header_px, body_px) + 2 * pad_px_each_side

    w_school_px = max(min_text_col_px, longest_px(school_key, "School"))
    w_provider_px = max(min_text_col_px, longest_px(provider_key, "Provider"))

    max_text_total_px = max_text_total_frac * table_w_px
    text_total_px = w_school_px + w_provider_px
    if text_total_px > max_text_total_px and text_total_px > 0:
        scale = max_text_total_px / text_total_px
        w_school_px *= scale
        w_provider_px *= scale
        text_total_px = max_text_total_px

    remainder_px = max(0.0, table_w_px - text_total_px)
    min_numeric_total = 2 * min_numeric_col_px

    if remainder_px < min_numeric_total and table_w_px > 0:
        steal_needed = (min_numeric_total - remainder_px)
        text_room = (w_school_px - min_text_col_px) + (w_provider_px - min_text_col_px)
        steal_px = min(steal_needed, max(0.0, text_room))
        if steal_px > 0:
            p_school_room = max(0.0, w_school_px - min_text_col_px)
            p_provider_room = max(0.0, w_provider_px - min_text_col_px)
            total_room = p_school_room + p_provider_room
            if total_room > 0:
                take_school = steal_px * (p_school_room / total_room)
                take_provider = steal_px - take_school
                w_school_px -= take_school
                w_provider_px -= take_provider
        remainder_px = max(0.0, table_w_px - (w_school_px + w_provider_px))

    w_classes_px = max(min_numeric_col_px, remainder_px / 2.0)
    w_edited_px = max(min_numeric_col_px, remainder_px - w_classes_px)

    total_px = w_school_px + w_provider_px + w_classes_px + w_edited_px
    if total_px <= 0:
        fr_school = fr_provider = fr_classes = fr_edited = 0.25
    else:
        fr_school = w_school_px / total_px
        fr_provider = w_provider_px / total_px
        fr_classes = w_classes_px / total_px
        fr_edited = w_edited_px / total_px

    return [
        {"key": school_key,   "label": "School",   "width_frac": fr_school,   "align": "left"},
        {"key": provider_key, "label": "Provider", "width_frac": fr_provider, "align": "left"},
        {"key": classes_key,  "label": "Classes",  "width_frac": fr_classes,  "align": "center"},
        {"key": edited_key,   "label": "Edited*",  "width_frac": fr_edited,   "align": "center"},
    ]


# ============================================================
# Layout helpers for stacking tables
# ============================================================
@dataclass(frozen=True)
class Block:
    df: pd.DataFrame
    columns: list[dict]
    header_height_frac: float
    key: str = ""


@dataclass(frozen=True)
class BlockPos:
    y: float
    height: float
    header_h: float
    body_h: float
    row_h: float
    n_rows: int


def layout_tables_by_rows(
    blocks: List[Block],
    *,
    y_top: float,
    y_bottom: float,
    target_row_h: float = 0.022,
    min_row_h: float = 0.012,
    gap: float = 0.018
) -> List[BlockPos]:
    avail = max(0.0, y_top - y_bottom - gap * max(0, len(blocks) - 1))
    if avail <= 0 or not blocks:
        return [BlockPos(y_bottom, 0, 0, 0, 0, len(b.df)) for b in blocks]

    n_list = [len(b.df) if (b.df is not None and not b.df.empty) else 0 for b in blocks]
    raw = [
        ((n * target_row_h) / max(1e-9, (1.0 - b.header_height_frac))) if n > 0 else 0.0
        for b, n in zip(blocks, n_list)
    ]
    total_raw = sum(raw)
    if total_raw <= 0:
        return [BlockPos(y_bottom + i * gap, 0, 0, 0, 0, n_list[i]) for i in range(len(blocks))]

    scale = min(1.0, avail / total_raw)
    heights = [r * scale for r in raw]

    min_heights = [
        ((n * min_row_h) / max(1e-9, (1.0 - b.header_height_frac))) if n > 0 else 0.0
        for b, n in zip(blocks, n_list)
    ]

    for i in range(len(blocks)):
        if heights[i] < min_heights[i]:
            heights[i] = min_heights[i]

    remaining = avail - sum(heights)
    if remaining > 1e-9:
        weights = [max(0.0, raw[i] - min_heights[i]) for i in range(len(blocks))]
        wsum = sum(weights)
        if wsum > 0:
            for i in range(len(blocks)):
                if weights[i] > 0:
                    heights[i] += remaining * (weights[i] / wsum)

    poses: List[BlockPos] = []
    y_cursor = y_top
    for i, b in enumerate(blocks):
        h = heights[i]
        y0 = y_cursor - h
        header_h = b.header_height_frac * h
        body_h = max(0.0, h - header_h)
        n = n_list[i]
        row_h = (body_h / n) if n > 0 else 0.0
        poses.append(BlockPos(y=y0, height=h, header_h=header_h, body_h=body_h, row_h=row_h, n_rows=n))
        y_cursor = y0 - gap

    return poses


# ============================================================
# Autoheight wrapper (uses table_pos)
# ============================================================
def draw_dataframe_table_v2_autoheight(
    ax,
    *,
    df: pd.DataFrame,
    x: float,
    y_top: float,
    width: float,
    max_height: float,
    header_height_frac: float = 0.12,
    min_row_h: float = 0.018,
    **kwargs,
) -> TablePos:
    if df is None or df.empty:
        return table_pos(df, y1=y_top, y2=y_top, header_height_frac=header_height_frac)

    y2 = y_top
    y1 = max(0.0, y_top - max_height)

    pos = table_pos(
        df,
        y1=y1,
        y2=y2,
        header_height_frac=header_height_frac,
        min_row_h=min_row_h,
    )

    # anchor to top
    y = y_top - pos.height

    draw_dataframe_table_v2(
        ax,
        df=df,
        x=x,
        y=y,
        width=width,
        height=pos.height,
        header_height_frac=header_height_frac,
        **kwargs,
    )

    return TablePos(
        y=y,
        height=pos.height,
        header_h=pos.header_h,
        body_h=pos.body_h,
        row_h=pos.row_h,
        n_rows=pos.n_rows,
    )


# ============================================================
# HOW TO MERGE SECOND COLUMN (examples)
# ============================================================
# 1) Merge by index (second column is index 1):
# draw_dataframe_table_v2(ax, df=df, x=..., y=..., width=..., height=..., merge_col_indices=[1])
#
# 2) Merge by key (safer):
# draw_dataframe_table_v2(ax, df=df, x=..., y=..., width=..., height=..., merge_cols=["Provider"])
#
# 3) Merge first + second:
# merge_col_indices=[0, 1]   OR   merge_cols=["SchoolName","Provider"]
