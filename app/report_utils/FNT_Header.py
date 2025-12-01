# utils/FNT_Header.py

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Ellipse
from matplotlib.font_manager import FontProperties
from utils.helpers import choose_text_color


def draw_rounded_header(
    fig: plt.Figure,
    *,
    text: str,
    x_frac: float = 0.5,
    y_frac: float = 0.95,
    hbuffer_frac: float = 0.30,
    vbuffer_frac: float = 0.40,
    fontsize: int = 20,
    fontweight: str = "semibold",
    fontfamily: str = "PP Mori",
    facecolor: str = "#EDF3FA",
    text_color: str | None = None,
    edgecolor: str = "none",
    style: str = "rounded",     # 'rounded' | 'rect' | 'none'
    debug: bool = False,
) -> None:
    """
    Draw a header with text and optional background. If debug=True, show guides.
    """

    if text_color is None:
        text_color = choose_text_color(facecolor)

    # ---- measure text in pixels ----
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    prop = FontProperties(family=fontfamily, weight=fontweight, size=fontsize)
    w_px, h_px, _ = renderer.get_text_width_height_descent(text, prop, ismath=False)

    fig_w_px = fig.get_size_inches()[0] * fig.dpi
    fig_h_px = fig.get_size_inches()[1] * fig.dpi

    # convert text px -> figure fractions
    text_w = w_px / fig_w_px
    text_h = h_px / fig_h_px

    # padded “container” around the text
    hpad = text_w * hbuffer_frac
    vpad = text_h * vbuffer_frac
    box_w = text_w + 2 * hpad
    box_h = text_h + 2 * vpad

    x0 = max(0.0, min(x_frac - box_w / 2, 1.0 - box_w))
    y0 = max(0.0, min(y_frac - box_h / 2, 1.0 - box_h))

    # overlay axes in figure coords
    overlay = fig.add_axes([0, 0, 1, 1], zorder=10000)
    overlay.axis("off")

    # ---- background ----
    if style == "rounded":
        # keep height same in x/y by compensating figure aspect
        dia_y = box_h
        dia_x = box_h * (fig_h_px / fig_w_px)

        if box_w <= dia_x:
            overlay.add_patch(Rectangle((x0, y0), box_w, box_h,
                                        facecolor=facecolor, edgecolor=edgecolor,
                                        linewidth=0, transform=fig.transFigure, zorder=10001))
        else:
            cx_left  = x0 + dia_x / 2
            cx_right = x0 + box_w - dia_x / 2
            cy       = y0 + box_h / 2
            rect_w   = max(0.0, box_w - dia_x)

            overlay.add_patch(Rectangle((cx_left, y0), rect_w, box_h,
                                        facecolor=facecolor, edgecolor=edgecolor,
                                        linewidth=0, transform=fig.transFigure, zorder=10001))
            for cx in (cx_left, cx_right):
                overlay.add_patch(Ellipse((cx, cy), width=dia_x, height=dia_y,
                                          facecolor=facecolor, edgecolor=edgecolor,
                                          linewidth=0, transform=fig.transFigure, zorder=10001))
    elif style == "rect":
        overlay.add_patch(Rectangle((x0, y0), box_w, box_h,
                                    facecolor=facecolor, edgecolor=edgecolor,
                                    linewidth=0, transform=fig.transFigure, zorder=10001))
    # style == "none": no background

    # ---- draw the text (keep handle for bbox) ----
    txt = fig.text(x0 + box_w / 2, y0 + box_h / 2, text,
                   ha="center", va="center", fontsize=fontsize,
                   fontweight=fontweight, family=fontfamily,
                   color=text_color, zorder=10002)

    # ---- DEBUG GUIDES ----
    if debug:
        # container box outline
        overlay.add_patch(Rectangle((x0, y0), box_w, box_h,
                                    facecolor="none", edgecolor="#ff7f0e",
                                    linewidth=1.5, linestyle="--",
                                    transform=fig.transFigure, zorder=10003))

        # center crosshairs of the container
        cx = x0 + box_w / 2
        cy = y0 + box_h / 2
        overlay.plot([cx, cx], [y0, y0 + box_h], transform=fig.transFigure,
                     linestyle=":", linewidth=1.2, color="#1f77b4", zorder=10003)
        overlay.plot([x0, x0 + box_w], [cy, cy], transform=fig.transFigure,
                     linestyle=":", linewidth=1.2, color="#1f77b4", zorder=10003)

        # figure border (useful to see padding vs page)
        overlay.add_patch(Rectangle((0, 0), 1, 1,
                                    facecolor="none", edgecolor="#888",
                                    linewidth=0.8, linestyle=":",
                                    transform=fig.transFigure, zorder=9999))

        # actual rendered text bbox (tight)
        fig.canvas.draw()  # ensure positions are current
        bb_disp = txt.get_window_extent(renderer=renderer)
        inv = fig.transFigure.inverted()
        (bx0, by0) = inv.transform((bb_disp.x0, bb_disp.y0))
        (bx1, by1) = inv.transform((bb_disp.x1, bb_disp.y1))
        overlay.add_patch(Rectangle((bx0, by0), bx1 - bx0, by1 - by0,
                                    facecolor="none", edgecolor="#2ca02c",
                                    linewidth=1.5, linestyle="-.",
                                    transform=fig.transFigure, zorder=10003))

        # handy console readout
    

    # --- message ---
    if style == "rounded":
        print(f"✅ Displayed header '{text}' with rounded background")
    elif style == "rect":
        print(f"✅ Displayed header '{text}' with background")
    else:
        print(f"✅ Displayed header '{text}' (no background)")
