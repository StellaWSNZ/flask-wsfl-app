import textwrap
import matplotlib.patches as mpatches
from matplotlib.text import TextPath

def _get_renderer(ax):
    rend = ax.figure.canvas.get_renderer()
    if rend is None:
        ax.figure.canvas.draw()  # force a draw to create renderer
        rend = ax.figure.canvas.get_renderer()
    return rend

def draw_text_in_polygon(
    ax,
    *,
    poly,                          # shapely Polygon in AXES coords
    text: str,
    fontfamily: str = "PP Mori",
    fontsize: float = 18,
    fontweight: str = "semibold",
    color: str = "#ffffff",
    pad_frac: float = 0.06,        # padding as fraction of polygon height
    wrap: bool = True,
    max_lines: int = 1,
    autoshrink: bool = True,
    min_fontsize: float = 9,
    clip_to_polygon: bool = True,
    zorder: int = 10,
):
    """
    Draw centered text inside a Shapely polygon defined in ax.transAxes coordinates.
    - pad_frac pads all sides relative to polygon height.
    - wrap uses a quick char-per-line estimate; autoshrink reduces font until it fits.
    """
    # polygon -> Matplotlib patch (axes coords)
    xy_axes = list(poly.exterior.coords)
    poly_patch = mpatches.Polygon(
        xy_axes, closed=True, facecolor="none", edgecolor="none",
        transform=ax.transAxes, zorder=zorder
    )
    ax.add_patch(poly_patch)

    # bounding box in axes coords
    xs, ys = zip(*xy_axes)
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    # padded content box (axes coords)
    h_axes = maxy - miny
    pad = pad_frac * h_axes
    box_minx = minx + pad
    box_maxx = maxx - pad
    box_miny = miny + pad
    box_maxy = maxy - pad
    cx = 0.5 * (box_minx + box_maxx)
    cy = 0.5 * (box_miny + box_maxy)

    # convert box width/height to pixels for fitting math
    fig = ax.figure
    rend = _get_renderer(ax)
    (x0_px, y0_px) = ax.transAxes.transform((box_minx, box_miny))
    (x1_px, y1_px) = ax.transAxes.transform((box_maxx, box_maxy))
    box_w_px = max(1.0, x1_px - x0_px)
    box_h_px = max(1.0, y1_px - y0_px)

    # estimated avg character width in pixels (~0.55em heuristic)
    def chars_per_line(fs_pt: float) -> int:
        px_per_pt = fig.dpi / 72.0
        avg_char_px = 0.55 * fs_pt * px_per_pt
        return max(1, int(box_w_px / avg_char_px))

    def make_wrapped(s: str, fs_pt: float) -> str:
        if not wrap:
            return s
        width = chars_per_line(fs_pt)
        wrapped = textwrap.fill(s, width=width)
        if max_lines is not None:
            lines = wrapped.splitlines()
            if len(lines) > max_lines:
                # keep first max_lines; truncate last line with ellipsis
                lines = lines[:max_lines]
                if len(lines[-1]) > 3:
                    lines[-1] = lines[-1].rstrip()
                    lines[-1] = (lines[-1][:max(0, len(lines[-1]) - 3)] + "…")
                wrapped = "\n".join(lines)
        return wrapped

    # place text, shrink if necessary to fit height
    xs, ys = zip(*poly.exterior.coords)
    height_axes = max(ys) - min(ys)
    fig_h_in = ax.figure.get_size_inches()[1]
    dpi = ax.figure.dpi
    height_px = height_axes * fig_h_in * dpi

    # heuristic: 1pt ≈ 1.33 px; we want ~font_height ≈ 0.6 * rect_height
    fontsize = (height_px * 0.6) / 1.33
    fs = fontsize
    txt = None
    while True:
        s = make_wrapped(text, fs)
        if txt is None:
            txt = ax.text(
                cx, cy, s,
                transform=ax.transAxes,
                ha="center", va="center",
                color=color, fontsize=fs,
                fontweight=fontweight, fontfamily=fontfamily,
                linespacing=1.1,
                zorder=zorder+1,
                clip_on=False  # we will optionally set clip_path below
            )
        else:
            txt.set_text(s)
            txt.set_fontsize(fs)

        # measure in pixels
        bbox = txt.get_window_extent(renderer=rend)
        text_w_px = bbox.width
        text_h_px = bbox.height

        fits = (text_w_px <= box_w_px) and (text_h_px <= box_h_px)
        if fits or not autoshrink or fs <= min_fontsize:
            break
        fs = max(min_fontsize, fs - 1)

    if clip_to_polygon:
        # clip text to the same rounded shape
        txt.set_clip_path(poly_patch)

    return txt