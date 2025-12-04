# app/utils/funder_missing_plot.py

from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import matplotlib.patches as mpatches
import os
from app.report_utils.DAT_dataframes import provider_missing_data
from app.report_utils.TAB_DataframeTable import (
    build_dynamic_columns,
    draw_dataframe_table,
    Block,
    layout_tables_by_rows,
)
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.helpers import get_display_name, load_ppmori_fonts

import matplotlib.pyplot as plt

def add_full_width_footer(
    fig: plt.Figure,
    footer_png: str,
    *,
    bottom_margin_frac: float = 0.0,
    max_footer_height_frac: float = 0.25,
) -> None:
    """
    Add a full-width footer image at the bottom of the figure without distortion.
    Uses the figure's size in inches to compute a sensible height.
    """
    from matplotlib import image as mpimg

    # Figure size in inches
    width_in, height_in = fig.get_size_inches()

    img = mpimg.imread(footer_png)
    img_h, img_w = img.shape[:2]
    img_aspect = img_h / img_w  # height / width

    # Height fraction needed for full-width footer, based on fig size
    required_footer_height_frac = (img_aspect * width_in) / height_in

    # Clamp to max height, but allow repositioning if the image needs more height
    footer_h = min(required_footer_height_frac, max_footer_height_frac)
    y0 = bottom_margin_frac

    if required_footer_height_frac > max_footer_height_frac:
        extra = required_footer_height_frac - max_footer_height_frac
        y0 = max(0.0, bottom_margin_frac - extra)
        footer_h = max_footer_height_frac

    footer_h = min(footer_h, 1.0 - y0)

    # Axes span full width; aspect='auto' so the image stretches to width
    ax_img = fig.add_axes([0.0, y0, 1.0, footer_h])
    ax_img.imshow(img, aspect="auto", extent=[0, 1, 0, 1])
    ax_img.axis("off")

import re
import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from svgpath2mpl import parse_path



def _parse_svg_viewbox(svg_path: str, debug: bool = False):
    """
    Return (min_x, min_y, width, height) from SVG viewBox or width/height.
    """
    if debug:
        print(f"[svg-footer] Parsing viewBox from: {svg_path}")
        print(f"[svg-footer] Exists? {os.path.exists(svg_path)}")

    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Default namespace handling
    tag = root.tag
    m = re.match(r"\{(.*)\}", tag)
    ns = m.group(1) if m else "http://www.w3.org/2000/svg"

    viewBox = root.attrib.get("viewBox")
    if debug:
        print(f"[svg-footer] root tag: {root.tag}")
        print(f"[svg-footer] viewBox: {viewBox!r}")
        print(f"[svg-footer] width: {root.attrib.get('width')!r}, height: {root.attrib.get('height')!r}")

    if viewBox:
        vals = list(map(float, viewBox.strip().split()))
        if len(vals) == 4:
            if debug:
                print(f"[svg-footer] Using viewBox values: {vals}")
            return tuple(vals)  # (min_x, min_y, vb_w, vb_h)

    # Fallback: try width/height attributes
    def _num(s):
        if s is None:
            return None
        m2 = re.match(r"[\d.]+", s)
        return float(m2.group(0)) if m2 else None

    width_attr = _num(root.attrib.get("width"))
    height_attr = _num(root.attrib.get("height"))

    if width_attr and height_attr:
        if debug:
            print(f"[svg-footer] Using width/height attributes: {width_attr} x {height_attr}")
        return 0.0, 0.0, width_attr, height_attr

    if debug:
        print("[svg-footer] No viewBox or width/height; using fallback 100x20")

    # Final fallback; arbitrary box
    return 0.0, 0.0, 100.0, 20.0

def add_full_width_footer_svg(
    fig: plt.Figure,
    footer_svg: str,
    *,
    bottom_margin_frac: float = 0.0,
    max_footer_height_frac: float = 0.25,
    debug: bool = False,
) -> None:
    """
    Add a full-width footer from an SVG at the bottom of the figure,
    without rasterising it.
    """
    width_in, height_in = fig.get_size_inches()
    if debug:
        print(f"[svg-footer] Figure size: {width_in:.2f} x {height_in:.2f} in")

    # You can still use viewBox just for aspect ratio
    min_x, min_y, vb_w, vb_h = _parse_svg_viewbox(footer_svg, debug=debug)
    svg_aspect = vb_h / vb_w
    required_footer_height_frac = (svg_aspect * width_in) / height_in

    footer_h = min(required_footer_height_frac, max_footer_height_frac)
    y0 = bottom_margin_frac

    if required_footer_height_frac > max_footer_height_frac:
        extra = required_footer_height_frac - max_footer_height_frac
        y0 = max(0.0, bottom_margin_frac - extra)
        footer_h = max_footer_height_frac

    footer_h = min(footer_h, 1.0 - y0)

    if debug:
        print(f"[svg-footer] viewBox: min_x={min_x}, min_y={min_y}, vb_w={vb_w}, vb_h={vb_h}")
        print(f"[svg-footer] svg_aspect={svg_aspect:.4f}")
        print(f"[svg-footer] required_footer_height_frac={required_footer_height_frac:.4f}")
        print(f"[svg-footer] Using footer_h={footer_h:.4f}, y0={y0:.4f}")

    if footer_h <= 0:
        if debug:
            print("[svg-footer] Footer height <= 0; not adding footer.")
        return

    # 1️⃣ Axes for footer (position on the figure)
    ax = fig.add_axes([0.0, y0, 1.0, footer_h])
    ax.axis("off")

    # 2️⃣ Draw all paths
    tree = ET.parse(footer_svg)
    root = tree.getroot()

    m = re.match(r"\{(.*)\}", root.tag)
    ns = m.group(1) if m else "http://www.w3.org/2000/svg"
    path_tag = f"{{{ns}}}path"

    path_count = 0
    all_x = []
    all_y = []

    for path_el in root.iter(path_tag):
        d = path_el.attrib.get("d")
        if not d:
            continue

        path_count += 1
        if debug and path_count <= 3:
            print(f"[svg-footer] Found path #{path_count}, d[:60]={d[:60]!r}...")

        mpl_path = parse_path(d)

        # collect vertices for tight x/y limits later
        verts = mpl_path.vertices
        all_x.extend(verts[:, 0])
        all_y.extend(verts[:, 1])

        style = path_el.attrib.get("style", "")
        stroke = path_el.attrib.get("stroke", "#1a427d80")
        fill = path_el.attrib.get("fill", "none")

        if "fill:" in style:
            m_fill = re.search(r"fill:([^;]+)", style)
            if m_fill:
                fill = m_fill.group(1).strip()
        if "stroke:" in style:
            m_stroke = re.search(r"stroke:([^;]+)", style)
            if m_stroke:
                stroke = m_stroke.group(1).strip()

        patch = patches.PathPatch(
            mpl_path,
            facecolor=None if fill in ("none", "transparent") else fill,
            edgecolor=None if stroke in ("none", "transparent") else stroke,
            linewidth=0.7,
        )
        ax.add_patch(patch)

    if debug:
        print(f"[svg-footer] Total <path> elements drawn: {path_count}")

    if path_count > 0:
        # 🔹 Tighten limits exactly to the drawn paths
        xmin, xmax = min(all_x), max(all_x)
        ymin, ymax = min(all_y), max(all_y)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymax, ymin)  # invert y

        # 🔹 No extra padding
        ax.margins(0, 0)

        # keep shapes from getting weirdly stretched vertically
        ax.set_aspect("equal", adjustable="box")
    else:
        if debug:
            print("[svg-footer] ⚠ No <path> elements found in SVG.")
        
def create_funder_missing_figure(
    df_all: pd.DataFrame,
    funder_name: str,
    term: int,
    calendaryear: int,
    threshold: float = 0.5,
):
    """
    Build a single-page portrait figure for 'funder_missing_data'.
    Returns a Matplotlib Figure (no file I/O).
    """
    load_ppmori_fonts("app/static/fonts")
    # ---- Filter to this funder ----
    dfd = (
        df_all.loc[
            df_all["FunderName"] == funder_name,
            ["Provider", "SchoolName", "NumClasses", "EditedClasses"],
        ]
        .sort_values(["Provider", "SchoolName"])
        .reset_index(drop=True)
    )
    if dfd.empty:
        return None  # Caller decides how to message "no data"

    provider_df = provider_missing_data(dfd)

    # ---- Create a portrait A4-style figure ----
    # A4 in inches ~ (8.27, 11.69)
    fig, ax = plt.subplots(figsize=(8.27, 11.69))
    ax.set_axis_off()

    # ---- Header band (polygon) ----
    poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.955,       # near top
        width=0.88,
        height=0.05,
        ratio=0.45,
        corners_round=[1, 3],
        n_arc=64,
    )

    ax.add_patch(
        mpatches.Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor="#1a427d",
            edgecolor="#1a427d",
            linewidth=0.8,
            transform=ax.transAxes,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=f"{get_display_name(funder_name)} Data Overview (Term {term}, {calendaryear})",
        fontfamily="PP Mori",
        fontsize=20,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.05,
        wrap=True,
        max_lines=None,
        autoshrink=True,
        min_fontsize=10,
        clip_to_polygon=True,
        zorder=6,
    )

    # ---- Column layouts ----
    cols_school = build_dynamic_columns(
        ax,
        dfd,
        table_x_axes=0.06,
        table_width_axes=0.88,
        pad_x_frac=0.01,
        header_fontfamily="PP Mori",
        header_fontsize=10,
        header_fontweight="semibold",
        body_fontfamily="PP Mori",
        body_fontsize=10,
        min_numeric_col_px=84,
        min_text_col_px=140,
        max_text_total_frac=0.82,
    )

    cols_provider = [
        {"key": "Provider",                   "label": "Provider",                   "width_frac": 0.40, "align": "left"},
        {"key": "Schools with Classes Yet to Submit Data", "label": "Schools with Classes Yet to Submit Data", "width_frac": 0.30, "align": "center"},
        {"key": "Total Classes Yet to Submit Data",      "label": "Total Classes Yet to Submit Data",      "width_frac": 0.30, "align": "center"},
    ]

    blocks = [
        Block(df=dfd,         columns=cols_school,   header_height_frac=0.05, key="schools"),
        Block(df=provider_df, columns=cols_provider, header_height_frac=0.10, key="providers"),
    ]

    poses = layout_tables_by_rows(
        blocks,
        y_top=0.92,
        y_bottom=0.125,
        target_row_h=0.022,
        min_row_h=0.012,
        gap=0.020,
    )

    FIXED_HEADER_AXES = 0.045  # absolute header height in axes units

    for b, p in zip(blocks, poses):
        if b.df is None or b.df.empty or p.height <= 0:
            # Placeholder if no data for this block
            ax.add_patch(
                Rectangle(
                    (0.06, p.y),
                    0.88,
                    max(0.08, p.height),
                    transform=ax.transAxes,
                    facecolor="#ffffff",
                    edgecolor="#cdd6e6",
                    lw=0.8,
                )
            )
            ax.text(
                0.50,
                p.y + max(0.08, p.height) / 2,
                "No data to display",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=10,
                color="#667085",
                fontfamily="PP Mori",
            )
            continue

        header_height_frac = FIXED_HEADER_AXES / max(p.height, 1e-6)
        header_height_frac = max(0.02, min(header_height_frac, 0.40))

        draw_dataframe_table(
            ax,
            df=b.df,
            x=0.06,
            y=p.y,
            width=0.88,
            height=p.height,
            columns=b.columns,
            header_height_frac=header_height_frac,
            header_facecolor="#1a427d",
            header_textcolor="#ffffff",
            header_fontfamily="PP Mori",
            header_fontsize=10,
            header_fontweight="semibold",
            body_fontfamily="PP Mori",
            body_fontsize=10,
            body_textcolor="#101828",
            row_alt_facecolor="#f2f5fb",
            row_facecolor="#ffffff",
            show_grid=True,
            grid_color="#cdd6e6",
            grid_linewidth=0.6,
            border_color="#1a427d",
            border_linewidth=1.0,
            pad_x_frac=0.01,
            pad_y_frac=0.005,
            default_align="left",
            wrap=True,
            max_wrap_lines=3,
            footer=(
                f"* refers to class lists with more than {threshold*100:.0f}% of students changed"
                if b.key == "schools"
                else None
            ),
            footer_align="left",
            footer_fontsize=9,
            footer_color="#667085",
            footer_gap_frac=0.005,
            DEBUG=False,
        )

    fig.tight_layout()
    return fig
