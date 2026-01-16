from __future__ import annotations

from typing import Callable, List, Dict, Optional, Literal
import math
import textwrap
import pandas as pd
from dataclasses import dataclass

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

Align = Literal["left", "center", "right"]
def draw_dataframe_table(
    ax: plt.Axes,
    *,
    df: pd.DataFrame,
    # box position/size in AXES-FRACTION coordinates (0..1 of the axes)
    x: float, y: float, width: float, height: float,
    # column definitions: order + label + relative width + alignment
    columns: List[Dict] | None = None,
    # header styling
    header_height_frac: float = 0.12,            # fraction of total height
    header_facecolor: str = "#1a427d",
    header_textcolor: str = "#ffffff",
    header_fontfamily: Optional[str] = None,     # e.g. "PP Mori"
    header_fontsize: float = 11.0,
    header_fontweight: str = "semibold",
    # body styling
    body_fontfamily: Optional[str] = None,
    body_fontsize: float = 10.0,
    body_textcolor: str = "#111111",
    row_alt_facecolor: Optional[str] = "#f6f8fb",  # zebra; set None to disable
    row_facecolor: Optional[str] = "#ffffff",
    # grid/border styling
    show_grid: bool = True,
    grid_color: str = "#c9d2e3",
    grid_linewidth: float = 0.6,
    border_color: str = "#1f2a44",
    border_linewidth: float = 1.0,
    # cell padding (axes fractions of each cell)
    pad_x_frac: float = 0.01,   # horizontal padding relative to table width
    pad_y_frac: float = 0.005,  # vertical padding relative to table height
    # text alignment per column (default 'left')
    default_align: Align = "left",
    # truncate/wrap behaviour
    wrap: bool = True,
    max_wrap_lines: int = 3,    # hard cap for very small cells

    # --- footer (optional) ---
    footer: Optional[str] = None,
    footer_align: Align = "left",
    footer_fontsize: float = 9.0,
    footer_fontfamily: Optional[str] = None,
    footer_color: str = "#667085",
    footer_gap_frac: float = 0.01,   # vertical gap from table border (axes fraction)

    # debug
    DEBUG: bool = False,
) -> None:
    """
    Draw a dataframe as a styled table inside a fixed box on `ax`.

    Parameters
    ----------
    ax : plt.Axes
        Target axes. Coordinates are in axes-fraction units (0..1).
    df : pd.DataFrame
        Data to render. If `columns` is provided, only those keys are used.
    x, y, width, height : float
        Box position/size in axes fraction coordinates.
    columns : list of dict, optional
        Each dict: {"key": colname, "label": header label, "width_frac": float, "align": "left|center|right"}.
        If None, uses df columns uniformly with label=key and equal widths.
    """

    if df is None or df.empty:
        return

    # ----- Resolve columns -----
    if columns is None:
        keys = list(df.columns)
        k = len(keys)
        columns = [{"key": k_, "label": str(k_), "width_frac": 1.0 / max(1, k), "align": default_align}
                   for k_ in keys]
    else:
        # normalise + fill defaults
        total_w = sum(c.get("width_frac", 0.0) for c in columns)
        if not total_w or abs(total_w - 1.0) > 1e-6:
            s = sum(c.get("width_frac", 1.0) for c in columns) or 1.0
            for c in columns:
                c["width_frac"] = c.get("width_frac", 1.0) / s
        for c in columns:
            c.setdefault("label", str(c["key"]))
            c.setdefault("align", default_align)

    # ----- Basic geometry -----
    n_rows = len(df)
    header_h = height * header_height_frac
    body_h   = max(0.0, height - header_h)
    row_h    = body_h / max(1, n_rows)

    # transforms
    trans = ax.transAxes
    fig = ax.figure

    # ----- Optional debug outer box -----
    if DEBUG:
        ax.add_patch(Rectangle((x, y), width, height, fill=False,
                               edgecolor="red", lw=0.8, transform=trans, zorder=10_000))

    # ----- Table outer border -----
    ax.add_patch(Rectangle((x, y), width, height,
                           facecolor="none",
                           edgecolor=border_color,
                           lw=border_linewidth,
                           transform=trans,
                           zorder=5_000))

    # ----- Header background -----
    ax.add_patch(Rectangle((x, y + height - header_h), width, header_h,
                           facecolor=header_facecolor,
                           edgecolor="none",
                           transform=trans,
                           zorder=4_900))

    # ----- Column x-positions (cumulative) -----
    col_x = [x]
    for c in columns:
        col_x.append(col_x[-1] + width * c["width_frac"])

    # ----- Helpers -----
    def _axes_width_to_pixels(w_axes: float) -> float:
        x0 = ax.transAxes.transform((0, 0))[0]
        x1 = ax.transAxes.transform((w_axes, 0))[0]
        return max(1.0, x1 - x0)

    def _wrap_text_to_width(s: str, col_w_axes: float, fontsize: float, fontfamily: Optional[str]) -> str:
        if not wrap or not s:
            return s
        # available pixel width inside the cell after horizontal padding
        pad_px = _axes_width_to_pixels(pad_x_frac * width)
        avail_px = _axes_width_to_pixels(col_w_axes) - 2 * pad_px
        if avail_px <= 1:
            return s
        # heuristic: avg character width ~ 0.55 * fontsize px
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

    # ----- Header labels -----
    for i, col in enumerate(columns):
        left, right = col_x[i], col_x[i + 1]
        label = col["label"]

        # Header cell vertical separator
        if show_grid and i > 0:
            ax.plot([left, left], [y, y + height],
                    transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

        # Header text (centered)
        ax.text(
            _x_text(left, right, align="center"),
            y + height - 0.5 * header_h,
            label,
            transform=trans,
            ha="center",
            va="center",
            fontsize=header_fontsize,
            fontfamily=header_fontfamily,
            fontweight=header_fontweight,
            color=header_textcolor,
            zorder=5_200,
        )

    # Horizontal line between header and body
    if show_grid:
        ax.plot([x, x + width], [y + height - header_h, y + height - header_h],
                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

    # ----- Body rows -----
    wrapped_cache: Dict[tuple, str] = {}
    for r in range(n_rows):
        row_y0 = y + body_h - (r + 1) * row_h
        row_yc = row_y0 + 0.5 * row_h

        # Row background
        if row_alt_facecolor:
            face = row_alt_facecolor if (r % 2 == 0) else (row_facecolor or "#ffffff")
            ax.add_patch(Rectangle((x, row_y0), width, row_h,
                                   facecolor=face, edgecolor="none",
                                   transform=trans, zorder=4_800))

        # Row horizontal grid
        if show_grid and r < n_rows - 1:
            ax.plot([x, x + width], [row_y0, row_y0],
                    transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

        # Cells
        for i, col in enumerate(columns):
            key = col["key"]
            align = col.get("align", default_align)
            left, right = col_x[i], col_x[i + 1]

            val = df.iloc[r][key]
            s = "" if pd.isna(val) else str(val)

            col_w_axes = (right - left)
            cache_key = (i, r)
            if cache_key not in wrapped_cache:
                wrapped_cache[cache_key] = _wrap_text_to_width(
                    s, col_w_axes=col_w_axes, fontsize=body_fontsize, fontfamily=body_fontfamily
                )
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

    # Right-most vertical border
    if show_grid:
        ax.plot([col_x[-1], col_x[-1]], [y, y + height],
                transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

    # ----- Optional footer (below table if possible; otherwise inside) -----
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
        # if below axes, put it just inside the table instead
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
    print(f"✅ Successfully saved renedered table")


def draw_dataframe_table_v2(
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

    # base row colour (same for all rows by default)
    base_row_facecolor: str = "#ffffff",

    # optional zebra
    row_alt_facecolor: Optional[str] = None,

    # ✅ NEW: conditional row colouring
    # return a facecolor string (e.g. "#ffe8ee") to override, or None
    row_color_fn: Optional[
    Callable[[pd.Series, int], Optional[Tuple[str, str]]]
] = None,

    # grid/border styling
    show_grid: bool = True,
    grid_color: str = "#c9d2e3",
    grid_linewidth: float = 0.6,
    border_color: str = "#1a427d",
    border_linewidth: float = 1.0,

    # cell padding (axes fractions of each cell)
    pad_x_frac: float = 0.01,
    pad_y_frac: float = 0.005,

    # text alignment per column
    default_align: Align = "left",

    # truncate/wrap behaviour
    wrap: bool = True,
    max_wrap_lines: int = 3,

    # ✅ NEW: merge first column cells when consecutive values equal
    merge_first_col: bool = True,
    merge_key: Optional[str] = None,   # default = first column key
    merge_text_align: Align = "left",

    # footer (optional)
    footer: Optional[str] = None,
    footer_align: Align = "left",
    footer_fontsize: float = 9.0,
    footer_fontfamily: Optional[str] = None,
    footer_color: str = "#667085",
    footer_gap_frac: float = 0.01,

    # debug
    DEBUG: bool = False,
    shift: bool = False,
) -> None:
    """
    V2 adds:
      - Conditional row colouring via row_color_fn(row, row_index)
      - Optional merge of the first column cells when consecutive values match
    """

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

    if merge_key is None:
        merge_key = columns[0]["key"]

    n_rows = len(df)
    header_h = height * header_height_frac
    body_h = max(0.0, height - header_h)
    row_h = body_h / max(1, n_rows)

    trans = ax.transAxes

    if DEBUG:
        ax.add_patch(Rectangle((x, y), width, height, fill=False,
                               edgecolor="red", lw=0.8, transform=trans, zorder=10_000))

    # ----- Outer border -----
    ax.add_patch(Rectangle((x, y), width, height,
                           facecolor="none",
                           edgecolor=border_color,
                           lw=border_linewidth,
                           transform=trans,
                           zorder=5_000))

    # ----- Header background -----
    ax.add_patch(Rectangle((x, y + height - header_h), width, header_h,
                           facecolor=header_facecolor,
                           edgecolor="none",
                           transform=trans,
                           zorder=4_900))

    # ----- Column x-positions -----
    col_x = [x]
    for c in columns:
        col_x.append(col_x[-1] + width * c["width_frac"])

    # safe first-column bounds (only if we actually have a first column)
    first_col_left  = col_x[0]
    first_col_right = col_x[1] if len(col_x) >= 2 else x
    for c in columns:
        col_x.append(col_x[-1] + width * c["width_frac"])

    # ----- Helpers -----
    def _axes_width_to_pixels(w_axes: float) -> float:
        x0 = ax.transAxes.transform((0, 0))[0]
        x1 = ax.transAxes.transform((w_axes, 0))[0]
        return max(1.0, x1 - x0)

    def _wrap_text_to_width(s: str, col_w_axes: float, fontsize_pt: float) -> str:
        if not wrap or not s:
            return s

        dpi = float(ax.figure.dpi or 100.0)
        fontsize_px = fontsize_pt * dpi / 72.0  # ✅ points -> pixels

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

    # ----- Merge groups for first column (consecutive equal values) -----
    merge_groups: List[Tuple[int, int]] = []
    row_to_group: Dict[int, Tuple[int, int]] = {}

    merge_col_idx = next((i for i, c in enumerate(columns) if c["key"] == merge_key), None)

    if merge_first_col and merge_col_idx is not None and merge_key in df.columns:
        vals = df[merge_key].tolist()
        start = 0
        while start < n_rows:
            end = start
            while end + 1 < n_rows and vals[end + 1] == vals[start]:
                end += 1
            merge_groups.append((start, end))
            for rr in range(start, end + 1):
                row_to_group[rr] = (start, end)
            start = end + 1

    wrapped_cache: Dict[tuple, str] = {}

    # ----- Body rows -----
    for r in range(n_rows):
        row_y0 = y + body_h - (r + 1) * row_h
        row_yc = row_y0 + 0.5 * row_h

        # base row face
        if row_alt_facecolor:
            face = row_alt_facecolor if (r % 2 == 0) else base_row_facecolor
        else:
            face = base_row_facecolor

        # conditional override
        text = None
        if row_color_fn is not None:
            override = row_color_fn(df.iloc[r], r)
            if override:
                face, text = override

        # row background across full width
        # row background
        if row_alt_facecolor and (r % 2 == 0):
            zebra_face = row_alt_facecolor
        else:
            zebra_face = base_row_facecolor

        # apply conditional override to the "row face" (but we'll still respect shift logic)
        row_face = zebra_face
        if row_color_fn is not None:
            override = row_color_fn(df.iloc[r], r)
            if override:
                row_face, text = override  # row_face is the override background

        if (not shift) and len(col_x) >= 2:
            # ✅ first column stays base; zebra/override only on the rest
            ax.add_patch(Rectangle((first_col_left, row_y0), first_col_right - first_col_left, row_h,
                                facecolor=base_row_facecolor, edgecolor="none",
                                transform=trans, zorder=4_800))
            ax.add_patch(Rectangle((first_col_right, row_y0), (x + width) - first_col_right, row_h,
                                facecolor=row_face, edgecolor="none",
                                transform=trans, zorder=4_800))
        else:
            # normal behaviour (whole row gets zebra/override)
            ax.add_patch(Rectangle((x, row_y0), width, row_h,
                                facecolor=row_face, edgecolor="none",
                                transform=trans, zorder=4_800))

        # horizontal grid (skip through merged first column interior lines)
        if show_grid and r < n_rows - 1:
            if merge_first_col and merge_col_idx == 0 and r in row_to_group:
                gs, ge = row_to_group[r]
                if r < ge:
                    # draw line everywhere except inside first column
                    left0 = col_x[0]
                    right0 = col_x[1]
                    ax.plot([x, left0], [row_y0, row_y0],
                            transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)
                    ax.plot([right0, x + width], [row_y0, row_y0],
                            transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)
                else:
                    ax.plot([x, x + width], [row_y0, row_y0],
                            transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)
            else:
                ax.plot([x, x + width], [row_y0, row_y0],
                        transform=trans, color=grid_color, lw=grid_linewidth, zorder=5_100)

        # cells
        for i, col in enumerate(columns):
            key = col["key"]
            align = col.get("align", default_align)
            left, right = col_x[i], col_x[i + 1]

            # merged first column handling (only draw text on group start)
            if merge_first_col and i == merge_col_idx and r in row_to_group:
                gs, ge = row_to_group[r]
                if r != gs:
                    continue

                gs_y0 = y + body_h - (gs + 1) * row_h
                ge_y0 = y + body_h - (ge + 1) * row_h
                merged_y0 = ge_y0
                merged_h = (gs_y0 + row_h) - ge_y0
                if(shift):
                    merged_yc = merged_y0 + 0.5 * (merged_h) + (row_h*0.5)
                else:
                    merged_yc = merged_y0 + 0.5 * (merged_h) 
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
                    color=text or body_textcolor,
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
                color=text or body_textcolor,
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
               
@dataclass(frozen=True)
class TablePos:
    y: float                 # bottom of the table (axes fraction)
    height: float            # total table height (axes fraction)
    header_h: float          # header band height (axes fraction)
    body_h: float            # body height = height - header_h
    row_h: float             # single row height (axes fraction)
    n_rows: int              # number of rows

    def row_y0(self, r: int) -> float:
        """Bottom y of body row r (0-based) in axes fraction."""
        # rows stacked from top of body downward
        return self.y + self.header_h + self.body_h - (r + 1) * self.row_h

    def row_yc(self, r: int) -> float:
        """Center y of body row r (0-based) in axes fraction."""
        return self.row_y0(r) + 0.5 * self.row_h        

def table_pos(
    df: pd.DataFrame,
    y1: float,
    y2: float,
    header_height_frac: float,
    *,
    # Optional: enforce a minimum per-row height; if it doesn't fit,
    # we shrink rows (i.e., we always fit the table inside [y1, y2]).
    min_row_h: float | None = None,
) -> TablePos:
    """
    Compute table geometry inside vertical span [min(y1,y2), max(y1,y2)] in axes fractions.

    - `header_height_frac` is the same "fraction of TOTAL table height" you pass to draw_dataframe_table.
    - Returns TablePos with y, height, header_h, body_h, row_h, and row helpers.
    """
    if df is None or df.empty:
        # trivial empty table with zero height
        yb, yt = (min(y1, y2), max(y1, y2))
        return TablePos(y=yb, height=0.0, header_h=0.0, body_h=0.0, row_h=0.0, n_rows=0)

    yb, yt = (min(y1, y2), max(y1, y2))
    avail_h = max(0.0, yt - yb)
    n = len(df)

    # If we want to respect a minimum per-row height, compute the needed total height
    # and clamp to the available space (we still return something that fits in [y1,y2]).
    if min_row_h is not None and min_row_h > 0:
        # height = header_frac*height + n*row_h  => height*(1 - header_frac) = n*row_h
        needed_h = (n * min_row_h) / max(1e-9, (1.0 - header_height_frac))
        height = min(avail_h, needed_h)
    else:
        height = avail_h

    header_h = header_height_frac * height
    body_h   = max(0.0, height - header_h)
    row_h    = body_h / n if n > 0 else 0.0

    return TablePos(y=yb, height=height, header_h=header_h, body_h=body_h, row_h=row_h, n_rows=n)  
# ---------- dynamic column width helper (fixed DPI + robust measuring) ----------
from typing import List, Dict, Optional
import numpy as np
from matplotlib.text import Text
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

def _get_renderer(fig):
    """Ensure the figure has a live renderer."""
    rend = fig.canvas.get_renderer()
    if rend is None:
        fig.canvas.draw()                 # force renderer
        rend = fig.canvas.get_renderer()
    return rend

def _text_width_px(ax, s: str, *, fontsize: float, family: Optional[str], weight: str = "normal") -> float:
    """
    Measure text width in screen pixels using the current figure's renderer.
    Attaches the Text artist to the figure so DPI is known (fixes 'NoneType has no dpi').
    Falls back to TextPath if a renderer isn't available for any reason.
    """
    txt = (s or "")
    fig = ax.figure

    # Primary path: real renderer measurement
    try:
        renderer = _get_renderer(fig)
        t = Text(x=0, y=0, text=txt)
        t.set_figure(fig)                 # CRITICAL so DPI exists
        t.set_fontsize(fontsize)
        if family is not None:
            t.set_fontfamily(family)
        if weight is not None:
            t.set_fontweight(weight)
        bb = t.get_window_extent(renderer=renderer)
        return float(bb.width)
    except Exception:
        # Fallback: approximate with TextPath (device-independent, then convert via DPI)
        try:
            fp = FontProperties(family=family, weight=weight, size=fontsize)
            tp = TextPath((0, 0), txt, prop=fp)     # width in font units (points)
            w_pts = tp.get_extents().width          # points (1pt = 1/72 inch)
            dpi = fig.dpi or 72.0
            return float(w_pts / 72.0 * dpi)        # convert pts → inches → pixels
        except Exception:
            # Last resort: simple heuristic
            avg_char_px = 0.55 * fontsize
            return avg_char_px * max(1, len(txt))

def _axes_width_to_pixels(ax, w_axes: float) -> float:
    """Convert an axes-fraction width to pixels for the current axes."""
    x0 = ax.transAxes.transform((0, 0))[0]
    x1 = ax.transAxes.transform((w_axes, 0))[0]
    return max(1.0, x1 - x0)

def build_dynamic_columns(
    ax,
    df,
    *,
    table_x_axes: float,            # kept for symmetry (not used directly)
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
    # Guards/tunables
    min_numeric_col_px: float = 80.0,      # minimum for Classes / Edited
    min_text_col_px: float = 120.0,        # minimum for School / Provider
    max_text_total_frac: float = 0.8,      # cap combined School+Provider share of table width
) -> List[Dict]:
    """
    Compute width_fracs for 4 columns:
      School (text, auto), Provider (text, auto), Classes (numeric), Edited* (numeric).
    Remainder after School+Provider is split 50/50 between the two numeric columns.
    """
    if df is None or df.empty:
        # fallback split
        return [
            {"key": school_key,   "label": "School",   "width_frac": 0.40, "align": "left"},
            {"key": provider_key, "label": "Provider", "width_frac": 0.30, "align": "left"},
            {"key": classes_key,  "label": "Classes",  "width_frac": 0.15, "align": "center"},
            {"key": edited_key,   "label": "Edited*",  "width_frac": 0.15, "align": "center"},
        ]

    # Total table width (px) so we can budget padding and column pixels sanely
    table_w_px = _axes_width_to_pixels(ax, table_width_axes)
    pad_px_each_side = _axes_width_to_pixels(ax, pad_x_frac * table_width_axes)

    # Measure longest School / Provider text in *displayed* data (plus header labels)
    def longest_px(col_key: str, header_label: str) -> float:
        # Header measurement (header font)
        header_px = _text_width_px(
            ax,
            header_label,
            fontsize=header_fontsize,
            family=header_fontfamily,
            weight=header_fontweight,
        )
        # Longest body string (body font). For speed, measure only the top-N longest by length.
        body_strings = df[col_key].astype(str).fillna("")
        candidates = body_strings.loc[body_strings.str.len().nlargest(20).index]
        body_px = 0.0
        for s in candidates:
            body_px = max(body_px, _text_width_px(ax, s, fontsize=body_fontsize, family=body_fontfamily))
        # Include left/right cell padding
        return max(header_px, body_px) + 2 * pad_px_each_side

    w_school_px   = max(min_text_col_px,   longest_px(school_key,   "School"))
    w_provider_px = max(min_text_col_px,   longest_px(provider_key, "Provider"))

    # Keep text columns from eating the whole table
    max_text_total_px = max_text_total_frac * table_w_px
    text_total_px = w_school_px + w_provider_px
    if text_total_px > max_text_total_px:
        scale = max_text_total_px / text_total_px
        w_school_px   *= scale
        w_provider_px *= scale
        text_total_px  = max_text_total_px

    # Remainder for numeric columns
    remainder_px = max(0.0, table_w_px - text_total_px)
    min_numeric_total = 2 * min_numeric_col_px

    # If numeric columns would be too small, steal back from text (down to min_text_col_px)
    if remainder_px < min_numeric_total and table_w_px > 0:
        steal_needed = (min_numeric_total - remainder_px)
        # How much can we still steal without dropping below text minima?
        text_room = (w_school_px - min_text_col_px) + (w_provider_px - min_text_col_px)
        steal_px = min(steal_needed, max(0.0, text_room))
        if steal_px > 0:
            # Proportional reduction
            p_school_room = max(0.0, w_school_px - min_text_col_px)
            p_provider_room = max(0.0, w_provider_px - min_text_col_px)
            total_room = p_school_room + p_provider_room
            if total_room > 0:
                take_school   = steal_px * (p_school_room / total_room)
                take_provider = steal_px - take_school
                w_school_px   -= take_school
                w_provider_px -= take_provider
        remainder_px = max(0.0, table_w_px - (w_school_px + w_provider_px))

    # Split remainder between numeric columns (respecting their minima)
    w_classes_px = max(min_numeric_col_px, remainder_px / 2.0)
    w_edited_px  = max(min_numeric_col_px, remainder_px - w_classes_px)

    # Convert pixel widths to fractions
    total_px = w_school_px + w_provider_px + w_classes_px + w_edited_px
    if total_px <= 0:
        fr_school = fr_provider = fr_classes = fr_edited = 0.25
    else:
        fr_school   = w_school_px   / total_px
        fr_provider = w_provider_px / total_px
        fr_classes  = w_classes_px  / total_px
        fr_edited   = w_edited_px   / total_px

    return [
        {"key": school_key,   "label": "School",   "width_frac": fr_school,   "align": "left"},
        {"key": provider_key, "label": "Provider", "width_frac": fr_provider, "align": "left"},
        {"key": classes_key,  "label": "Classes",  "width_frac": fr_classes,  "align": "center"},
        {"key": edited_key,   "label": "Edited*",  "width_frac": fr_edited,   "align": "center"},
    ]

from dataclasses import dataclass
from typing import List, Tuple

@dataclass(frozen=True)
class Block:
    df: pd.DataFrame
    columns: list[dict]
    header_height_frac: float  # e.g. 0.05
    key: str = ""              # optional label for debugging

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
    y_top: float,             # e.g. 0.90
    y_bottom: float,          # e.g. 0.10
    target_row_h: float = 0.022,   # desired body row height (axes fraction)
    min_row_h: float = 0.012,      # minimum row height (axes fraction)
    gap: float = 0.018            # vertical gap between tables (axes fraction)
) -> List[BlockPos]:
    """
    Compute stacked table positions between y_bottom..y_top based on row counts.
    Each table i gets height_i ≈ (n_i * target_row_h) / (1 - header_frac_i),
    scaled so all fit into the available space; with a minimum row height enforced.
    """
    # Available vertical space for all blocks + gaps
    avail = max(0.0, y_top - y_bottom - gap * max(0, len(blocks) - 1))
    if avail <= 0 or not blocks:
        return [BlockPos(y_bottom, 0, 0, 0, 0, len(b.df)) for b in blocks]

    n_list = [len(b.df) if (b.df is not None and not b.df.empty) else 0 for b in blocks]
    # Raw requested heights for target row height
    raw = [
        ((n * target_row_h) / max(1e-9, (1.0 - b.header_height_frac))) if n > 0 else (0.0 if n == 0 else 0.0)
        for b, n in zip(blocks, n_list)
    ]
    total_raw = sum(raw)

    # If nothing to show, return zero-sized blocks
    if total_raw <= 0:
        return [BlockPos(y_bottom + i * (gap), 0, 0, 0, 0, n_list[i]) for i in range(len(blocks))]

    # First pass: scale to fit
    scale = min(1.0, avail / total_raw)
    heights = [r * scale for r in raw]

    # Enforce minimum row height per block
    min_heights = [
        ((n * min_row_h) / max(1e-9, (1.0 - b.header_height_frac))) if n > 0 else 0.0
        for b, n in zip(blocks, n_list)
    ]

    # If any block dips below min, bump it and rescale the remainder
    deficit = 0.0
    for i in range(len(blocks)):
        if heights[i] < min_heights[i]:
            deficit += (min_heights[i] - heights[i])
            heights[i] = min_heights[i]

    # Redistribute remaining space proportionally among blocks that are above min
    remaining = avail - sum(heights)
    if remaining > 1e-9:
        # Proportion based on their raw requests (only those above min)
        weights = []
        for i in range(len(blocks)):
            want = max(0.0, raw[i] - min_heights[i])
            weights.append(want)
        wsum = sum(weights)
        if wsum > 0:
            for i in range(len(blocks)):
                if weights[i] > 0:
                    heights[i] += remaining * (weights[i] / wsum)

    # Build y positions top-down
    poses: List[BlockPos] = []
    y_cursor = y_top
    for i, b in enumerate(blocks):
        h = heights[i]
        y = y_cursor - h
        header_h = b.header_height_frac * h
        body_h = max(0.0, h - header_h)
        n = n_list[i]
        row_h = (body_h / n) if n > 0 else 0.0
        poses.append(BlockPos(y=y, height=h, header_h=header_h, body_h=body_h, row_h=row_h, n_rows=n))
        y_cursor = y - gap
    return poses