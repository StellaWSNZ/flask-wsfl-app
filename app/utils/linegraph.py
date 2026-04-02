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


def draw_graph(
    ax: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    linewidth: float = 1.5,
):
    buffer_x = 0.05

    bbox = ax.get_position()
    fig = ax.figure
    fig_w, fig_h = fig.get_size_inches()
    ax_w = bbox.width * fig_w
    ax_h = bbox.height * fig_h
    aspect = ax_h / ax_w

    buffer_y = buffer_x * (width / height) / aspect

    rect = Rectangle(
        (x, y),
        width,
        height,
        transform=ax.transAxes,
        edgecolor="red",
        facecolor="white",
        linewidth=linewidth,
    )
    ax.add_patch(rect)

    x_left = x + width * buffer_x
    x_right = x + width * (1 - buffer_x)
    y_bottom = y + height * buffer_y
    y_top = y + height * (1 - buffer_y)

    plot_width = x_right - x_left
    plot_height = y_top - y_bottom

    # 🔥 scaled styling
    axis_linewidth = height_scaled_points(ax, height, 0.008)
    series_linewidth = height_scaled_points(ax, height, 0.006)
    marker_size = height_scaled_points(ax, height, 0.05)

    df = pd.read_csv("student_audit.csv")
    df.columns = df.columns.str.strip()

    print(df.head())
    print(df.columns.tolist())

    if "CumulativeTotal" not in df.columns:
        raise ValueError(f"Missing CumulativeTotal. Columns are: {df.columns.tolist()}")

    y_min = 0 # df["CumulativeTotal"].min()
    y_max = df["CumulativeTotal"].max()

    if y_max == y_min:
        y_coords = pd.Series([y_bottom + plot_height / 2] * len(df))
    else:
        y_coords = y_bottom + (
            (df["CumulativeTotal"] - y_min) / (y_max - y_min)
        ) * plot_height

    if len(df) == 1:
        x_coords = pd.Series([x_left + plot_width / 2])
    else:
        x_coords = x_left + (
            pd.Series(range(len(df))) / (len(df) - 1)
        ) * plot_width

    # axes
    ax.plot(
        [x_left, x_left],
        [y_bottom, y_top],
        transform=ax.transAxes,
        color="black",
        linewidth=axis_linewidth,
    )

    ax.plot(
        [x_left, x_right],
        [y_bottom, y_bottom],
        transform=ax.transAxes,
        color="black",
        linewidth=axis_linewidth,
    )

    # line
    ax.plot(
        x_coords,
        y_coords,
        transform=ax.transAxes,
        color="red",
        linewidth=series_linewidth,
    )

    # end dot
    ax.plot(
        x_coords.iloc[-1],
        y_coords.iloc[-1],
        transform=ax.transAxes,
        color="red",
        marker="o",
        markersize=marker_size,
        zorder=20000
    )

    return rect


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
        ax=ax,
        x=0.10,
        y=0.65,
        width=0.50,
        height=0.30,
    )

    save_pdf(fig, output_path)


if __name__ == "__main__":
    make_example_pdf("blank.pdf")