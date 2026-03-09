import textwrap
import matplotlib.patches as mpatches

def _get_renderer(ax):
    # Works across backends
    fig = ax.figure
    try:
        return fig.canvas.get_renderer()
    except Exception:
        fig.canvas.draw()
        return fig.canvas.get_renderer()


def draw_text_in_polygon(
    ax,
    *,
    poly,                          # shapely Polygon in AXES coords
    text: str,
    fontfamily: str = "PP Mori",
    fontsize: float = 18,
    fontweight: str = "semibold",
    color: str = "#ffffff",
    pad_frac: float = 0.06,
    wrap: bool = True,
    max_lines: int | None = 1,
    autoshrink: bool = True,
    min_fontsize: float = 6,
    clip_to_polygon: bool = True,
    zorder: int = 10,
    # --- new ---
    bold_first_line: bool = False,
    first_line_weight: str = "bold",
    rest_weight: str = "normal",
    linespacing: float = 1.10,
):
    """
    Draw centered text inside a Shapely polygon defined in ax.transAxes coordinates.
    If bold_first_line=True, the first line is drawn in bold and the whole block
    is vertically centered as a combined unit.
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

    def apply_wrap(s: str, fs_pt: float) -> str:
        if not wrap:
            return s
        width = chars_per_line(fs_pt)
        wrapped = textwrap.fill(s, width=width)
        if max_lines is not None:
            lines = wrapped.splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                if len(lines[-1]) > 3:
                    lines[-1] = (lines[-1].rstrip()[:-3] + "…") if len(lines[-1].rstrip()) > 3 else "…"
                wrapped = "\n".join(lines)
        return wrapped

    # If you passed explicit newlines and wrap=False, keep them as-is
    def make_text(fs_pt: float) -> str:
        return apply_wrap(text, fs_pt)

    # Create artists lazily so we can update them in autoshrink loop
    txt_single = None
    txt_bold = None
    txt_rest = None

    fs = float(fontsize)

    def measure_artist(t):
        bb = t.get_window_extent(renderer=rend)
        return bb.width, bb.height

    def px_to_axes_dy(dy_px: float) -> float:
        # Convert a pixel delta (vertical) to axes coords delta at current axes transform
        x_ref, y_ref = ax.transAxes.transform((0.0, 0.0))
        x_ref2, y_ref2 = x_ref, y_ref + dy_px
        _, y_axes = ax.transAxes.inverted().transform((x_ref, y_ref))
        _, y_axes2 = ax.transAxes.inverted().transform((x_ref2, y_ref2))
        return (y_axes2 - y_axes)

    while True:
        s = make_text(fs)

        if not bold_first_line:
            if txt_single is None:
                txt_single = ax.text(
                    cx, cy, s,
                    transform=ax.transAxes,
                    ha="center", va="center",
                    color=color, fontsize=fs,
                    fontweight=fontweight, fontfamily=fontfamily,
                    linespacing=linespacing,
                    zorder=zorder + 1,
                    clip_on=False,
                )
            else:
                txt_single.set_text(s)
                txt_single.set_fontsize(fs)

            w_px, h_px = measure_artist(txt_single)
            fits = (w_px <= box_w_px) and (h_px <= box_h_px)

            if fits or not autoshrink or fs <= min_fontsize:
                if clip_to_polygon:
                    txt_single.set_clip_path(poly_patch)
                return txt_single

            fs = max(min_fontsize, fs - 1.0)
            continue

        # ----- bold-first-line mode -----
        lines = s.splitlines() if s else [""]
        first = lines[0] if lines else ""
        rest = "\n".join(lines[1:]) if len(lines) > 1 else ""

        # Create texts with va="top" so we can place them from a known top y
        if txt_bold is None:
            txt_bold = ax.text(
                cx, cy, first,
                transform=ax.transAxes,
                ha="center", va="top",
                color=color, fontsize=fs,
                fontweight=first_line_weight, fontfamily=fontfamily,
                linespacing=linespacing,
                zorder=zorder + 2,
                clip_on=False,
            )
        else:
            txt_bold.set_text(first)
            txt_bold.set_fontsize(fs)

        if txt_rest is None:
            txt_rest = ax.text(
                cx, cy, rest,
                transform=ax.transAxes,
                ha="center", va="top",
                color=color, fontsize=fs,
                fontweight=rest_weight, fontfamily=fontfamily,
                linespacing=linespacing,
                zorder=zorder + 1,
                clip_on=False,
            )
        else:
            txt_rest.set_text(rest)
            txt_rest.set_fontsize(fs)

        # Measure heights in px
        _, h1_px = measure_artist(txt_bold)
        w2_px, h2_px = measure_artist(txt_rest) if rest else (0.0, 0.0)

        # A small vertical gap between first line and rest (as a fraction of line height)
        gap_px = 0.15 * h1_px if rest else 0.0

        total_h_px = h1_px + gap_px + h2_px

        # Position the *combined* block centered at cy
        top_y_axes = cy + px_to_axes_dy(total_h_px / 2.0)

        txt_bold.set_position((cx, top_y_axes))
        if rest:
            y_rest_axes = top_y_axes - px_to_axes_dy(h1_px + gap_px)
            txt_rest.set_position((cx, y_rest_axes))

        # Now measure combined width/height “as drawn”
        # (height is total_h_px by construction; width is max of both)
        w1_px, _ = measure_artist(txt_bold)
        fits = (max(w1_px, w2_px) <= box_w_px) and (total_h_px <= box_h_px)

        if fits or not autoshrink or fs <= min_fontsize:
            if clip_to_polygon:
                txt_bold.set_clip_path(poly_patch)
                if txt_rest is not None:
                    txt_rest.set_clip_path(poly_patch)
            return (txt_bold, txt_rest)

        fs = max(min_fontsize, fs - 1.0)