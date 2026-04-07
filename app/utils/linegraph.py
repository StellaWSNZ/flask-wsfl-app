from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd
import numpy as np 
from sqlalchemy import text

from app.utils.database import get_db_engine


def get_page_size(page_size: str = "A4", orientation: str = "portrait") -> tuple[float, float]:
    page_size = (page_size or "A4").upper()
    orientation = (orientation or "portrait").lower()

    sizes = {
        "A4": (8.27, 11.69),
        "A5": (5.83, 8.27),
    }

    if page_size not in sizes:
        raise ValueError("page_size must be 'A4' or 'A5'")

    width, height = sizes[page_size]

    if orientation == "landscape":
        return height, width
    if orientation == "portrait":
        return width, height

    raise ValueError("orientation must be 'portrait' or 'landscape'")


def create_pdf_figure(
    page_size: str = "A4",
    orientation: str = "portrait",
) -> tuple[plt.Figure, plt.Axes]:

    width, height = get_page_size(page_size=page_size, orientation=orientation)

    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([0, 0, 1, 1])

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    return fig, ax


# 🔥 helper to scale anything based on graph height
def height_scaled_points(ax: plt.Axes, height_axes: float, frac: float) -> float:
    bbox = ax.get_position()
    fig_h_in = ax.figure.get_size_inches()[1]
    height_pt = height_axes * bbox.height * fig_h_in * 72
    return height_pt * frac

def points_to_axes_y(ax, points):
    bbox = ax.get_position()
    fig = ax.figure
    fig_h_in = fig.get_size_inches()[1]
    ax_h_in = bbox.height * fig_h_in
    return points / (72 * ax_h_in)

def points_to_axes_x(ax, points):
    bbox = ax.get_position()
    fig = ax.figure
    fig_w_in = fig.get_size_inches()[0]
    ax_w_in = bbox.width * fig_w_in
    return points / (72 * ax_w_in)


def draw_graph(
    df: pd.DataFrame,
    ax: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    linewidth: float = 1.5,
    line_col="#000000",
    axis_col="#000000",
    box_bg="none",
    box_outline="red",
    box_outline_w: float = 1.5,
    start_filter_col="START DATE",
    end_filter_col="END DATE",
    key_date_col="KeyDates",
    key_date_col_desc="KeyDatesDescription",
    key_date_fill="#D7E8F8",
):
    # high z-order so graph sits above dashboard card backgrounds
    base_z = 12000

    # buffers
    half_line = box_outline_w / 2

    buffer_x_left = points_to_axes_x(ax, half_line)
    buffer_x_right = points_to_axes_x(ax, half_line * 1.5)   # slightly more on right for point/label
    buffer_y_top = points_to_axes_y(ax, half_line)
    buffer_y_bottom = 0.08   # manual room for term labels

    outer_rect = Rectangle(
        (x, y),
        width,
        height,
        transform=ax.transAxes,
        edgecolor=box_outline,
        facecolor=box_bg,
        linewidth=box_outline_w,
        zorder=base_z,
    )
    ax.add_patch(outer_rect)

    x_left = x + width * buffer_x_left
    x_right = x + width * (1 - buffer_x_right)
    y_bottom = y + height * buffer_y_bottom
    y_top = y + height * (1 - buffer_y_top)

    plot_width = x_right - x_left
    plot_height = y_top - y_bottom

    axis_linewidth = height_scaled_points(ax, height, 0.0015)
    series_linewidth = height_scaled_points(ax, height, 0.0045)
    marker_size = height_scaled_points(ax, height, 0.030)

    value_fontsize = max(8, marker_size * 0.42)
    term_fontsize = max(5, marker_size * 0.30)

    df = df.copy()
    df.columns = df.columns.str.strip()

    required_cols = ["CumulativeTotal", "AuditDay", key_date_col, key_date_col_desc]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}. Columns are: {df.columns.tolist()}")

    df["AuditDay"] = pd.to_datetime(df["AuditDay"], dayfirst=True, errors="coerce")
    if df["AuditDay"].isna().any():
        bad_vals = df.loc[df["AuditDay"].isna(), "AuditDay"]
        raise ValueError(f"Some AuditDay values could not be parsed: {bad_vals.tolist()}")

    df = df.sort_values("AuditDay").reset_index(drop=True)

    y_min = 0
    y_max_raw = df["CumulativeTotal"].max()
    y_range = max(1, y_max_raw - y_min)
    y_max = y_max_raw + y_range * 0.12

    if y_max == y_min:
        y_coords = pd.Series([y_bottom + plot_height / 2] * len(df), index=df.index)
    else:
        y_coords = y_bottom + (
            (df["CumulativeTotal"] - y_min) / (y_max - y_min)
        ) * plot_height

    if len(df) == 1:
        x_coords = pd.Series([x_left + plot_width / 2], index=df.index)
    else:
        point_pad = plot_width * 0.01
        x_coords = pd.Series(
            (x_left + point_pad) +
            (pd.Series(range(len(df)), index=df.index) / (len(df) - 1)) * (plot_width - 2 * point_pad),
            index=df.index
        )

    # shaded term boxes + term labels
    for desc in df[key_date_col_desc].dropna().unique():
        s_idx = df[
            (df[key_date_col] == start_filter_col) &
            (df[key_date_col_desc] == desc)
        ].index.tolist()

        e_idx = df[
            (df[key_date_col] == end_filter_col) &
            (df[key_date_col_desc] == desc)
        ].index.tolist()

        full_term = (len(s_idx) > 0 and len(e_idx) > 0)

        # if no end, skip entirely
        if len(e_idx) == 0:
            continue

        # if no start, allow shading from left edge, but no label
        if len(s_idx) == 0:
            s_pos = x_left
        else:
            s_pos = x_coords.iloc[s_idx[0]]

        e_pos = x_coords.iloc[e_idx[0]]

        if e_pos <= s_pos:
            continue

        shade_rect = Rectangle(
            (s_pos, y_bottom),
            e_pos - s_pos,
            y_top - y_bottom,
            transform=ax.transAxes,
            edgecolor="none",
            facecolor=key_date_fill,
            linewidth=0,
            zorder=base_z + 1,
        )
        ax.add_patch(shade_rect)

        # only label full terms
        if full_term:
            x_mid = (s_pos + e_pos) / 2
            term_y = y_bottom - height * 0.035
            term_label = str(desc).split(",")[0].strip()

            ax.text(
                x_mid,
                term_y,
                term_label,
                transform=ax.transAxes,
                ha="center",
                va="top",
                fontsize=term_fontsize,
                color=axis_col,
                zorder=base_z + 5,
            )

    # axes
    ax.plot(
        [x_left, x_left],
        [y_bottom, y_top],
        transform=ax.transAxes,
        color=axis_col,
        linewidth=axis_linewidth,
        zorder=base_z + 2,
    )

    ax.plot(
        [x_left, x_right],
        [y_bottom, y_bottom],
        transform=ax.transAxes,
        color=axis_col,
        linewidth=axis_linewidth,
        zorder=base_z + 2,
    )

    # main line
    ax.plot(
        x_coords.values,
        y_coords.values,
        transform=ax.transAxes,
        color=line_col,
        linewidth=series_linewidth,
        zorder=base_z + 3,
    )

    # end dot
    ax.plot(
        x_coords.iloc[-1],
        y_coords.iloc[-1],
        transform=ax.transAxes,
        color=line_col,
        marker="o",
        markersize=marker_size,
        linestyle="None",
        zorder=base_z + 4,
    )

    # end value
    ax.annotate(
        str(df["CumulativeTotal"].iloc[-1]),
        xy=(x_coords.iloc[-1], y_coords.iloc[-1]),
        xycoords=ax.transAxes,
        xytext=(-2, marker_size / 2 + 1),
        textcoords="offset points",
        ha="right",
        va="bottom",
        fontsize=value_fontsize,
        color=line_col,
        zorder=base_z + 5,
    )

    return outer_rect

def run_sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    engine = get_db_engine()
    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params or {})
    return df


def save_pdf(fig: plt.Figure, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig.savefig(
        output_path,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0,
    )
    plt.close(fig)


def make_example_pdf(output_path: str | Path = "example_box.pdf") -> None:
    fig, ax = create_pdf_figure(page_size="A4", orientation="portrait")

    draw_graph(
        df = pd.read_csv("student_audit.csv"),
        ax=ax,
        x=0.10,
        y=0.65,
        width=0.25,
        height=0.1,
    )

    save_pdf(fig, output_path)


if __name__ == "__main__":
    make_example_pdf("out/blank.pdf")