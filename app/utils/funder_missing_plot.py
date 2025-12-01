# app/utils/funder_missing_plot.py

from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import matplotlib.patches as mpatches

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
            linewidth=1.5,
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
