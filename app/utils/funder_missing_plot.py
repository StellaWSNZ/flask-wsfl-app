# app/utils/funder_missing_plot.py

from pathlib import Path
import os
import re
import xml.etree.ElementTree as ET

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import matplotlib.patches as mpatches
import matplotlib.patches as patches
from svgpath2mpl import parse_path

from app.report_utils.TAB_DataframeTable import (
    build_dynamic_columns,
    draw_dataframe_table,
    Block,
    layout_tables_by_rows,
)
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.helpers import get_display_name, load_ppmori_fonts


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
    col_master=None,
) -> None:
    """
    Add a full-width footer from an SVG at the bottom of the figure
    without rasterising it.

    Behaviour:
    - SVG is always scaled to span full figure width
    - If SVG is taller than max_footer_height_frac, it is cropped
      from the bottom (top stays aligned)
    """

    width_in, height_in = fig.get_size_inches()
    if debug:
        print(f"[svg-footer] Figure size: {width_in:.2f} x {height_in:.2f} in")

    # --- SVG viewBox for aspect ratio ---
    min_x, min_y, vb_w, vb_h = _parse_svg_viewbox(footer_svg, debug=debug)
    svg_aspect = vb_h / vb_w

    # Height needed if width fits exactly
    required_footer_height_frac = (svg_aspect * width_in) / height_in

    # Footer axes height is capped, SVG will be cropped if taller
    footer_h = min(required_footer_height_frac, max_footer_height_frac)
    y0 = bottom_margin_frac

    crop_bottom = required_footer_height_frac > max_footer_height_frac
    visible_ratio = (
        max_footer_height_frac / required_footer_height_frac
        if crop_bottom
        else 1.0
    )

    footer_h = min(footer_h, 1.0 - y0)
    if footer_h <= 0:
        if debug:
            print("[svg-footer] Footer height <= 0; skipping footer.")
        return

    if debug:
        print(f"[svg-footer] viewBox: {vb_w} x {vb_h}")
        print(f"[svg-footer] svg_aspect={svg_aspect:.4f}")
        print(f"[svg-footer] required_height_frac={required_footer_height_frac:.4f}")
        print(f"[svg-footer] footer_h={footer_h:.4f}, y0={y0:.4f}")
        print(f"[svg-footer] crop_bottom={crop_bottom}")

    # --- Create footer axes ---
    existing_axes = list(fig.axes)
    ax = fig.add_axes([0.0, y0, 1.0, footer_h], zorder=0)

    for a in existing_axes:
        a.set_zorder(2)
        a.patch.set_alpha(0.0)
        a.set_facecolor("none")

    ax.axis("off")

    # --- Parse SVG paths ---
    tree = ET.parse(footer_svg)
    root = tree.getroot()

    m = re.match(r"\{(.*)\}", root.tag)
    ns = m.group(1) if m else "http://www.w3.org/2000/svg"
    path_tag = f"{{{ns}}}path"

    all_x, all_y = [], []
    path_count = 0

    for path_el in root.iter(path_tag):
        d = path_el.attrib.get("d")
        if not d:
            continue

        path_count += 1
        mpl_path = parse_path(d)

        verts = mpl_path.vertices
        all_x.extend(verts[:, 0])
        all_y.extend(verts[:, 1])

        style = path_el.attrib.get("style", "")
        stroke = path_el.attrib.get("stroke", "#1a427d")
        fill = path_el.attrib.get("fill", "none")

        if "fill:" in style:
            m_fill = re.search(r"fill:([^;]+)", style)
            if m_fill:
                fill = m_fill.group(1).strip()

        if "stroke:" in style:
            m_stroke = re.search(r"stroke:([^;]+)", style)
            if m_stroke:
                stroke = m_stroke.group(1).strip()

        if col_master is not None:
            fill = stroke = col_master

        patch = patches.PathPatch(
            mpl_path,
            facecolor=None if fill in ("none", "transparent") else fill,
            edgecolor=None if stroke in ("none", "transparent") else stroke,
            linewidth=0,
        )
        ax.add_patch(patch)

    if debug:
        print(f"[svg-footer] Total paths drawn: {path_count}")

    if path_count == 0:
        if debug:
            print("[svg-footer] ⚠ No <path> elements found.")
        return

    # --- Set limits: full width, crop bottom if needed ---
    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)

    ax.set_xlim(xmin, xmax)

    if crop_bottom:
        full_h = ymax - ymin
        visible_h = full_h * visible_ratio
        y_bottom_visible = ymin + visible_h
        ax.set_ylim(y_bottom_visible, ymin)  # inverted (SVG y goes down)
    else:
        ax.set_ylim(ymax, ymin)

    ax.margins(0, 0)
    ax.set_aspect("equal", adjustable="box")

def create_funder_missing_figure(
    df_all: pd.DataFrame,
    funder_name: str,
    term: int,
    calendaryear: int,
    threshold: float = 0.5,
    debug: bool = False,
):
    """
    Build a single-page portrait figure for 'funder_missing_data'.
    Returns a Matplotlib Figure (no file I/O).
    """
    if debug:
        print("▶ create_funder_missing_figure called")
        print(f"  funder_name={funder_name!r}, term={term}, calendaryear={calendaryear}, threshold={threshold}")
        print(f"  df_all shape={df_all.shape}")
        print(f"  df_all columns={df_all.columns.tolist()}")

    load_ppmori_fonts("app/static/fonts")

    # ---- Filter to this funder (base df) ----
    df_funder = df_all.loc[df_all["FunderName"] == funder_name].copy()
    if debug:
        print("  ▶ after funder filter")
        print(f"    df_funder shape={df_funder.shape}")
        print(f"    df_funder columns={df_funder.columns.tolist()}")

    if df_funder.empty:
        if debug:
            print("  ⚠ df_funder is empty for this funder – returning None")
        return None  # Caller decides how to message "no data"

    # ------------------------------------------------------------------
    # 1) FIRST TABLE (school-level view)
    # ------------------------------------------------------------------
    try:
        if debug:
            print("  ▶ building schools_df from df_funder")

        schools_df = df_funder[
            ["Provider", "SchoolName", "NumClasses", "EditedClasses", "TotalStudentsUnedited"]
        ].copy()

        if debug:
            print("    schools_df shape:", schools_df.shape)
            print("    schools_df columns:", schools_df.columns.tolist())

        # Classes edited (edited/total)
        schools_df["Classes edited (edited/total)"] = (
            schools_df["EditedClasses"].fillna(0).astype(int).astype(str)
            + " / "
            + schools_df["NumClasses"].fillna(0).astype(int).astype(str)
        )

        # Students in unedited classes
        schools_df["Students in unedited classes"] = (
            schools_df["TotalStudentsUnedited"].fillna(0).astype(int)
        )

        # Only keep display columns for the first table
        dfd_schools = (
            schools_df[
                [
                    "Provider",
                    "SchoolName",
                    "Classes edited (edited/total)",
                    "Students in unedited classes",
                ]
            ]
            .sort_values(["Provider", "SchoolName"])
            .reset_index(drop=True)
        )

        if debug:
            print("  ▶ dfd_schools ready")
            print("    dfd_schools shape:", dfd_schools.shape)
            print("    dfd_schools columns:", dfd_schools.columns.tolist())

    except KeyError as ke:
        print("❌ ERROR building school-level table (dfd_schools)")
        print("   Missing column:", ke)
        print("   df_funder columns were:", df_funder.columns.tolist())
        raise
    except Exception as e:
        print("❌ Unexpected error while building dfd_schools:", repr(e))
        print("   df_funder shape/cols:", df_funder.shape, df_funder.columns.tolist())
        raise

    # ------------------------------------------------------------------
    # 2) SECOND TABLE (provider-level summary)
    # ------------------------------------------------------------------
    try:
        if debug:
            print("  ▶ building provider_df summary")

        tmp = df_funder.copy()
        tmp["MissingClasses"] = (
            tmp["NumClasses"].fillna(0) - tmp["EditedClasses"].fillna(0)
        ).clip(lower=0)

        if debug:
            print("    tmp shape:", tmp.shape)
            print("    tmp columns:", tmp.columns.tolist())
            print("    sample MissingClasses:", tmp["MissingClasses"].head().tolist())

        provider_df = (
            tmp.groupby("Provider", as_index=False)
            .agg(
                **{
                    "Schools with Classes Yet to Submit Data": (
                        "MissingClasses",
                        lambda s: int((s > 0).sum()),
                    ),
                    "Total Classes Yet to Submit Data": ("MissingClasses", "sum"),
                }
            )
            .sort_values("Provider")
            .reset_index(drop=True)
        )

        if debug:
            print("  ▶ provider_df ready")
            print("    provider_df shape:", provider_df.shape)
            print("    provider_df columns:", provider_df.columns.tolist())

    except KeyError as ke:
        print("❌ ERROR building provider-level table (provider_df)")
        print("   Missing column:", ke)
        print("   df_funder columns were:", df_funder.columns.tolist())
        raise
    except Exception as e:
        print("❌ Unexpected error while building provider_df:", repr(e))
        print("   df_funder shape/cols:", df_funder.shape, df_funder.columns.tolist())
        raise

    # ------------------------------------------------------------------
    # 3) Build the figure + header
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8.27, 11.69))  # A4 portrait
    ax.set_axis_off()

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

    
    # ------------------------------------------------------------------
    # 4) Column definitions (FIXED to match dfd_schools columns)
    # ------------------------------------------------------------------
    cols_school = [
        {
            "key": "SchoolName",
            "label": "School",
            "width_frac": 0.35,
            "align": "left",
        },
        {
            "key": "Provider",
            "label": "Provider",
            "width_frac": 0.25,
            "align": "left",
        },
        {
            "key": "Classes edited (edited/total)",
            "label": "Classes edited\n(edited/total)",
            "width_frac": 0.20,
            "align": "center",
        },
        {
            "key": "Students in unedited classes",
            "label": "Students in\nunedited classes",
            "width_frac": 0.20,
            "align": "center",
        },
    ]

    cols_provider = [
        {
            "key": "Provider",
            "label": "Provider",
            "width_frac": 0.40,
            "align": "left",
        },
        {
            "key": "Schools with Classes Yet to Submit Data",
            "label": "Schools with Classes\nYet to Submit Data",
            "width_frac": 0.30,
            "align": "center",
        },
        {
            "key": "Total Classes Yet to Submit Data",
            "label": "Total Classes\nYet to Submit Data",
            "width_frac": 0.30,
            "align": "center",
        },
    ]

    blocks = [
        Block(df=dfd_schools, columns=cols_school, header_height_frac=0.08, key="schools"),
        Block(df=provider_df, columns=cols_provider, header_height_frac=0.12, key="providers"),
    ]

    if debug:
        print("  ▶ layout_tables_by_rows")
        print("    blocks:", [(b.key, None if b.df is None else b.df.shape) for b in blocks])

    poses = layout_tables_by_rows(
        blocks,
        y_top=0.92,
        y_bottom=0.02,
        target_row_h=0.022,
        min_row_h=0.012,
        gap=0.020,
    )

    FIXED_HEADER_AXES = 0.045  # absolute header height in axes units

    for b, p in zip(blocks, poses):
        if debug:
            print(f"  ▶ drawing block {b.key!r}")
            print(f"    height={p.height}, y={p.y}")
            if b.df is not None:
                print(f"    df shape={b.df.shape}, df columns={b.df.columns.tolist()}")
                print(f"    column keys={[c['key'] for c in b.columns]}")

        if b.df is None or b.df.empty or p.height <= 0:
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

        try:
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
                    f"Edited = classes with over {threshold*100:.0f}% of students changed. Unedited students = total students in those classes."
                    if b.key == "schools"
                    else None
                ),
                footer_align="left",
                footer_fontsize=9,
                footer_color="#667085",
                footer_gap_frac=0.005,
                DEBUG=False,
            )
        except KeyError as ke:
            print(f"❌ KeyError inside draw_dataframe_table for block={b.key!r}: {ke}")
            print("   df columns:", list(b.df.columns))
            print("   column keys:", [c["key"] for c in b.columns])
            raise
        except Exception as e:
            print(f"❌ Unexpected error drawing block {b.key!r}: {repr(e)}")
            print("   df shape/cols:", b.df.shape, list(b.df.columns))
            print("   column keys:", [c["key"] for c in b.columns])
            raise

    fig.tight_layout()
    if debug:
        print("✅ create_funder_missing_figure completed successfully")
    return fig