from pathlib import Path
import re

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
from sqlalchemy import text

from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.utils.database import get_db_engine


# =========================================================
# LOAD SQL INTO DATAFRAME
# =========================================================
def load_funder_yeargroup_summary(engine, calendaryear, term):
    with engine.connect() as connection:
        result = connection.execute(
            text(
                "EXEC GetFunderYearGroupSummary_StudentWeighted_TY_LY_WithTrend "
                ":CalendarYear, :Term"
            ),
            {
                "CalendarYear": calendaryear,
                "Term": term,
            },
        )
        df = pd.DataFrame(result.fetchall(), columns=result.keys())

    df = df.loc[
        df["YearGroupDesc"] == "All Year Groups",
        ["Funder", "TY_AllYGsRate", "LY_AllYGsRate"],
    ].copy()

    df = df.reset_index(drop=True)
    return df


# =========================================================
# HEADER / SUBTITLE
# =========================================================
def make_header(
    ax,
    x,
    y,
    height,
    width,
    title,
    fonts_dir: str | Path = "app/static/fonts",
):
    family = load_ppmori_fonts(str(fonts_dir))

    poly = rounded_rect_polygon(
        cx=x,
        cy=y,
        width=width,
        height=height,
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
        text=title,
        fontfamily=family,
        fontsize=20,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.05,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=None,
    )


def draw_subtitle_box(
    ax,
    x,
    y,
    width,
    height,
    subtitle,
    fonts_dir: str | Path = "app/static/fonts",
):
    family = load_ppmori_fonts(str(fonts_dir))

    poly = rounded_rect_polygon(
        cx=x,
        cy=y,
        width=width,
        height=height,
        ratio=0.45,
        corners_round=[1, 3],
        n_arc=64,
    )

    ax.add_patch(
        mpatches.Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor="#ffffff",
            edgecolor="#1a427d",
            linewidth=1.5,
            transform=ax.transAxes,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=subtitle,
        fontfamily=family,
        fontsize=12,
        fontweight="medium",
        color="#1a427d",
        pad_frac=0.08,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=2,
    )


# =========================================================
# GRAPH AREA
# =========================================================
def draw_achievement_graph(
    ax,
    x,
    y,
    width,
    height,
    df,
    funder_col="Funder",
    ty_col="TY_AllYGsRate",
    ly_col="LY_AllYGsRate",
    anon=False,
    feature=None,
    labels=True,
    fontsize=7,
    fonts_dir: str | Path = "app/static/fonts",
    ly_colo="#78BFEA",
    ty_colo="#1a427d",
    ly_colo_anon="#d9e3ec",
    ty_colo_anon="#5f6f82",
    text_colo="#ffffff",
    dp=0,
    anon_x_labels=False,
    key_position="center",
    calendaryear=None,
    term=None,
):
    family = load_ppmori_fonts(str(fonts_dir))

    def fmt_pct(val):
        if pd.isna(val):
            return ""
        pct = val * 100
        return f"{int(pct)}%" if dp == 0 else f"{round(pct, dp)}%"

    def draw_key():
        box_w = 0.015
        box_h = 0.015
        text_gap = 0.006
        item_gap = 0.11

        if anon and feature is not None:
            labels_key = ["Featured LY", "Featured YTD", "Other LY", "Other YTD"]
            colors = [ly_colo, ty_colo, ly_colo_anon, ty_colo_anon]
        elif anon:
            labels_key = ["Anonymous LY", "Anonymous YTD"]
            colors = [ly_colo_anon, ty_colo_anon]
            item_gap = 0.14
        else:
            if calendaryear is not None and term is not None:
                labels_key = ["LY Full Year", f"YTD Term {term}, {calendaryear}"]
            else:
                labels_key = ["LY Full Year", "YTD"]
            colors = [ly_colo, ty_colo]
            item_gap = 0.17

        total_width = len(labels_key) * item_gap

        if key_position == "center":
            key_x = x + (width / 2) - (total_width / 2)
        elif key_position == "right":
            key_x = x + width - total_width - 0.015
        else:
            key_x = x + 0.015

        key_y = y + height 

        for i, (label, color) in enumerate(zip(labels_key, colors)):
            xx = key_x + i * item_gap

            ax.add_patch(
                Rectangle(
                    (xx, key_y),
                    box_w,
                    box_h,
                    transform=ax.transAxes,
                    facecolor=color,
                    edgecolor=color,
                )
            )

            ax.text(
                xx + box_w + text_gap,
                key_y + box_h / 2,
                label,
                transform=ax.transAxes,
                va="center",
                ha="left",
                fontfamily=family,
                fontsize=8,
                color="#1a427d",
            )



    df = df.reset_index(drop=True).copy()
    df["OriginalFunder"] = df[funder_col]
    df[ty_col] = pd.to_numeric(df[ty_col], errors="coerce")
    df[ly_col] = pd.to_numeric(df[ly_col], errors="coerce")

    if anon:
        if feature is not None and feature in df["OriginalFunder"].values:
            anon_labels = "Anonymous\nFunder " + (df.index + 1).astype(str)
            mask = df["OriginalFunder"] != feature
            df.loc[mask, funder_col] = anon_labels[mask]
        elif feature is not None:
            print(f"{feature} not in dataframe. Treating all as anonymous")
            df[funder_col] = "Anonymous\nFunder " + (df.index + 1).astype(str)
        else:
            df[funder_col] = "Anonymous\nFunder " + (df.index + 1).astype(str)

    n_funders = len(df)
    if n_funders == 0:
        ax.text(
            x + width / 2,
            y + height / 2,
            "No data available",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontfamily=family,
            fontsize=12,
            color="#1a427d",
        )
        return

    area_per = width / n_funders
    gap_between = area_per * 0.09
    gap_between_mini = 0
    bar_width = (area_per - gap_between - gap_between_mini) / 2

    if not labels:
        max_label_lines = 0
    elif anon and not anon_x_labels:
        if feature is not None and feature in df["OriginalFunder"].values:
            max_label_lines = max(
                df.loc[df["OriginalFunder"] == feature, funder_col]
                .astype(str)
                .str.split()
                .str.len()
                .max(),
                1,
            )
        else:
            max_label_lines = 0
    else:
        max_label_lines = max(
            df[funder_col].astype(str).str.split().str.len().max(),
            1,
        )

    gap_for_text = fontsize * max_label_lines * 0.003

    max_val = pd.concat([df[ly_col], df[ty_col]], ignore_index=True).max(skipna=True)
    if pd.isna(max_val) or max_val <= 0:
        max_val = 1

    usable_height = height - gap_for_text - 0.012
    bar_area_height = usable_height * 0.96
    rect_y = y + gap_for_text
    per = bar_area_height / max_val

    for i, row in df.iterrows():
        rect_x_1 = x + (i * area_per)
        rect_x_2 = rect_x_1 + bar_width + gap_between_mini

        is_feature = (
            feature is not None
            and row["OriginalFunder"] == feature
            and feature in df["OriginalFunder"].values
        )

        current_ly_color = ly_colo if (not anon or is_feature) else ly_colo_anon
        current_ty_color = ty_colo if (not anon or is_feature) else ty_colo_anon

        # LY bar
        if pd.notna(row[ly_col]):
            ly_height = row[ly_col] * per
            ax.add_patch(
                Rectangle(
                    (rect_x_1, rect_y),
                    bar_width,
                    ly_height,
                    transform=ax.transAxes,
                    facecolor=current_ly_color,
                    edgecolor=current_ly_color,
                )
            )
            ax.text(
                rect_x_1 + (bar_width / 2),
                rect_y + ly_height,
                s=fmt_pct(row[ly_col]),
                ha="center",
                va="top",
                transform=ax.transAxes,
                fontfamily=family,
                fontsize=fontsize,
                fontweight="bold",
                color=text_colo if (not anon or is_feature) else "#1a427d",
            )

        # TY / YTD bar
        if pd.notna(row[ty_col]):
            ty_height = row[ty_col] * per
            ax.add_patch(
                Rectangle(
                    (rect_x_2, rect_y),
                    bar_width,
                    ty_height,
                    transform=ax.transAxes,
                    facecolor=current_ty_color,
                    edgecolor=current_ty_color,
                )
            )
            ax.text(
                rect_x_2 + (bar_width / 2),
                rect_y + ty_height,
                s=fmt_pct(row[ty_col]),
                ha="center",
                va="top",
                transform=ax.transAxes,
                fontfamily=family,
                fontsize=fontsize,
                fontweight="semibold",
                color=text_colo if (not anon or is_feature) else "#ffffff",
            )

        # x labels
        if not labels:
            label_text = ""
        elif anon:
            if is_feature:
                label_text = str(row[funder_col])
            elif anon_x_labels:
                label_text = re.sub(" ", "\n", str(row[funder_col]))
            else:
                label_text = ""
        else:
            label_text = re.sub(" ", "\n", str(row[funder_col]))

        if label_text:
            ax.text(
                rect_x_1 + (((bar_width * 2) + gap_between_mini) / 2),
                y + (gap_for_text*0.95),
                s=label_text,
                ha="center",
                va="top",
                transform=ax.transAxes,
                fontfamily=family,
                fontsize=fontsize,
                color="#1a427d" if (not anon or is_feature) else "#5f6f82",
                fontweight="bold" if is_feature else "normal",
            )

    draw_key()


# =========================================================
# LANDSCAPE FIGURE WITH HEADER + SUBTITLE
# =========================================================
def make_landscape_header_figure(
    df,
    title,
    subtitle,
    anon=False,
    feature=None,
    labels=True,
    anon_x_labels=False,
    dp=0,
    key_position="center",
    calendaryear=None,
    term=None,
    flask=False,
):
    import numpy as np

    fig, ax = plt.subplots(figsize=(11.69, 8.27))  # A4 landscape
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    
    left = 0.02
    right = 1 - left
    bottom = left * (1 / np.sqrt(2))
    top = 1 - (left * (1 / np.sqrt(2)))

    header_h = 0.085
    subtitle_h = 0.05
    gap = 0.02
    current_top = top

    make_header(
        ax=ax,
        x=0.5,
        y=current_top - header_h / 2,
        height=header_h,
        width=0.82,
        title=title,
    )
    current_top -= header_h + gap

    draw_subtitle_box(
        ax=ax,
        x=0.5,
        y=current_top - subtitle_h / 2,
        width=0.50,
        height=subtitle_h,
        subtitle=subtitle,
    )
    current_top -= subtitle_h + gap

    graph_x = left
    graph_y = bottom
    graph_w = right - left
    graph_h = current_top - bottom - gap

    draw_achievement_graph(
        ax=ax,
        x=graph_x,
        y=graph_y,
        width=graph_w,
        height=graph_h,
        df=df,
        anon=anon,
        feature=feature,
        labels=labels,
        anon_x_labels=anon_x_labels,
        dp=dp,
        key_position=key_position,
        calendaryear=calendaryear,
        term=term,
    )

    return fig


# =========================================================
# SAVE PDF
# =========================================================
def save_figure_to_pdf(fig, output_filename):
    with PdfPages(output_filename) as pdf:
        pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# =========================================================
# MAIN WRAPPER
# =========================================================
def create_funder_yeargroup_summary_pdf(
    calendaryear,
    term,
    output_filename=None,
    anon=False,
    feature=None,
    labels=True,
    anon_x_labels=False,
    dp=0,
    key_position="center",
):
    if output_filename is None:
        output_filename = f"FunderYearGroupSummary_{calendaryear}_T{term}.pdf"

    engine = get_db_engine()
    df = load_funder_yeargroup_summary(engine, calendaryear, term)

    title = "Funder Weighted Achievement Summary"
    subtitle = f"YTD (Term {term}, {calendaryear}) vs LY (Full Year)"

    fig = make_landscape_header_figure(
        df=df,
        title=title,
        subtitle=subtitle,
        anon=anon,
        feature=feature,
        labels=labels,
        anon_x_labels=anon_x_labels,
        dp=dp,
        key_position=key_position,
        calendaryear=calendaryear,
        term=term,
    )
    save_figure_to_pdf(fig, output_filename)

    return df


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    TERM = 4
    CALENDAR_YEAR = 2025

    df = create_funder_yeargroup_summary_pdf(
        calendaryear=CALENDAR_YEAR,
        term=TERM,
        output_filename=f"FunderYearGroupSummary_{CALENDAR_YEAR}_T{TERM}.pdf",
        anon=False,
        feature=None,
        labels=True,
        anon_x_labels=False,
        dp=0,
        key_position="center",
    )