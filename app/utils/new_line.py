from __future__ import annotations

from pathlib import Path
import math

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import pandas as pd
from sqlalchemy import text

from app.utils.database import get_db_engine


def use_ppmori(font_dir="app/static/fonts"):
    font_paths = list(Path(font_dir).glob("*.otf")) + list(Path(font_dir).glob("*.ttf"))
    for p in font_paths:
        fm.fontManager.addfont(str(p))
    if not font_paths:
        raise FileNotFoundError(f"No .otf/.ttf files found in {font_dir}")
    fam_name = fm.FontProperties(fname=str(font_paths[0])).get_name()
    plt.rcParams["font.family"] = [fam_name]
    plt.rcParams["font.sans-serif"] = [fam_name]
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    print(f"✅ Using font family: {fam_name}")


def create_pdf_figure() -> tuple[plt.Figure, plt.Axes]:
    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    return fig, ax


def run_sql(sql: str) -> pd.DataFrame:
    engine = get_db_engine()
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn)


def get_dashboard_users_data() -> pd.DataFrame:
    df = run_sql("EXEC GetDashboardLineGraphData")
    df.columns = df.columns.str.strip()

    required_cols = ["Category", "AuditDay", "CumulativeTotal"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}. Columns are: {df.columns.tolist()}")

    df = df[df["Category"] == "Users"].copy()
    df = df[df["CumulativeTotal"] != 0].copy()

    df["AuditDay"] = pd.to_datetime(df["AuditDay"], errors="coerce")
    df = df.dropna(subset=["AuditDay"])
    df = df.sort_values("AuditDay").reset_index(drop=True)

    if df.empty:
        raise ValueError("No rows found for Category = 'Users' with CumulativeTotal != 0")

    return df


def draw_users_graph(
    df: pd.DataFrame,
    ax: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    wsfl_blue = "#1a427d"
    soft_grey = "#6b7280"
    axis_col = "#111111"

    # layout
    x_left = x + width * 0.18
    x_right = x + width * 0.965
    y_bottom = y + height * 0.20
    y_top = y + height * 0.84

    plot_width = x_right - x_left
    plot_height = y_top - y_bottom

    # title
    # title centred on the actual x-axis span
    ax.text(
        (x_left + x_right) / 2,
        y + height * 0.965,
        "Users Over Time",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=12,
        fontweight="bold",
        color=axis_col,
    )

    # y-axis label
    ax.text(
        x_left - width * 0.12,
        y + height / 2,
        "Users",
        transform=ax.transAxes,
        ha="center",
        va="center",
        rotation=90,
        fontsize=9,
        color=soft_grey,
    )

    y_min_raw = float(df["CumulativeTotal"].min())
    y_max_raw = float(df["CumulativeTotal"].max())

    step = 200
    y_min = math.floor(y_min_raw / step) * step
    y_max = math.ceil(y_max_raw / step) * step

    if y_min == y_max:
        y_max += step

    y_coords = pd.Series(
        y_bottom + ((df["CumulativeTotal"] - y_min) / (y_max - y_min)) * plot_height,
        index=df.index,
    )

    date_num = mdates.date2num(df["AuditDay"])
    if date_num.max() == date_num.min():
        x_coords = pd.Series([x_left + plot_width / 2] * len(df), index=df.index)
    else:
        x_coords = pd.Series(
            x_left + ((date_num - date_num.min()) / (date_num.max() - date_num.min())) * plot_width,
            index=df.index,
        )

    # axes
    ax.plot(
        [x_left, x_left],
        [y_bottom, y_top],
        transform=ax.transAxes,
        color=axis_col,
        linewidth=1.1,
        solid_capstyle="round",
        zorder=2,
    )
    ax.plot(
        [x_left, x_right],
        [y_bottom, y_bottom],
        transform=ax.transAxes,
        color=axis_col,
        linewidth=1.1,
        solid_capstyle="round",
        zorder=2,
    )

    # y ticks
    for val in range(int(y_min), int(y_max + step), step):
        y_tick = y_bottom + ((val - y_min) / (y_max - y_min)) * plot_height

        ax.plot(
            [x_left - width * 0.010, x_left],
            [y_tick, y_tick],
            transform=ax.transAxes,
            color=axis_col,
            linewidth=0.9,
            zorder=2,
        )

        ax.text(
            x_left - width * 0.016,
            y_tick,
            f"{val:,}",
            transform=ax.transAxes,
            ha="right",
            va="center",
            fontsize=7.2,
            color=axis_col,
        )

    # month ticks at the START of each month, only when day 1 exists
    df["MonthStart"] = df["AuditDay"].dt.to_period("M").dt.to_timestamp()
    unique_months = list(df["MonthStart"].drop_duplicates().sort_values())

    month_positions: list[tuple[pd.Timestamp, float]] = []
    for month_start in unique_months:
        month_rows = df[df["MonthStart"] == month_start]

        first_day_rows = month_rows[month_rows["AuditDay"].dt.day == 1]
        if first_day_rows.empty:
            continue

        idx = first_day_rows.index[0]
        month_positions.append((pd.Timestamp(month_start), float(x_coords.loc[idx])))

    tick_height = height * 0.020
    label_y = y_bottom - height * 0.047

    # draw month boundary ticks
    for _, x_pos in month_positions:
        ax.plot(
            [x_pos, x_pos],
            [y_bottom, y_bottom - tick_height],
            transform=ax.transAxes,
            color=axis_col,
            linewidth=0.9,
            zorder=2,
        )

    # draw labels centred on their tick
    for month_start, x_pos in month_positions:
        ax.text(
            x_pos,
            label_y,
            month_start.strftime("%b"),
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=7.5,
            color=soft_grey,
        )

    # line
    ax.plot(
        x_coords.values,
        y_coords.values,
        transform=ax.transAxes,
        color=wsfl_blue,
        linewidth=2.2,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=3,
    )

    # end dot
    ax.plot(
        x_coords.iloc[-1],
        y_coords.iloc[-1],
        transform=ax.transAxes,
        marker="o",
        color=wsfl_blue,
        markersize=4.8,
        linestyle="None",
        zorder=4,
    )

    # final value
    ax.text(
        x_coords.iloc[-1] - width * 0.006,
        y_coords.iloc[-1] + height * 0.02,
        f"{int(df['CumulativeTotal'].iloc[-1]):,}",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=8.2,
        color=wsfl_blue,
        bbox=dict(
            boxstyle="round,pad=0.18",
            facecolor="none",
            edgecolor="none",
            alpha=0.9,
        ),
        zorder=5,
    )


def save_pdf(fig: plt.Figure, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def make_users_graph_pdf(output_path: str | Path = "out/users_graph.pdf") -> None:
    use_ppmori()

    fig, ax = create_pdf_figure()
    df = get_dashboard_users_data()

    draw_users_graph(
        df=df,
        ax=ax,
        x=0.10,
        y=0.65,
        width=0.40,
        height=0.18,
    )

    save_pdf(fig, output_path)


if __name__ == "__main__":
    make_users_graph_pdf()