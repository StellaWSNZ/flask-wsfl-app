from __future__ import annotations

import textwrap
import pandas as pd
from matplotlib.patches import Rectangle


def make_difference_df(
    df: pd.DataFrame,
    *,
    left_result: str,
    right_result: str,
    label_col: str = "CompetencyDesc",
    group_col: str = "YearGroupDesc",
    result_col: str = "ResultType",
    value_col: str = "Rate",
) -> pd.DataFrame:
    """
    Build a tidy dataframe for a diverging comparison chart.

    Output columns:
      Label
      YearGroupDesc
      LeftRate
      RightRate
      Difference
    """

    needed = {label_col, group_col, result_col, value_col}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    out = (
        df[df[result_col].isin([left_result, right_result])]
        [[label_col, group_col, result_col, value_col]]
        .drop_duplicates()
        .pivot_table(
            index=[label_col, group_col],
            columns=result_col,
            values=value_col,
            aggfunc="first"
        )
        .reset_index()
    )

    if left_result not in out.columns:
        out[left_result] = pd.NA
    if right_result not in out.columns:
        out[right_result] = pd.NA

    out = out.rename(
        columns={
            label_col: "Label",
            group_col: "YearGroupDesc",
            left_result: "LeftRate",
            right_result: "RightRate",
        }
    )

    out["LeftRate"] = pd.to_numeric(out["LeftRate"], errors="coerce")
    out["RightRate"] = pd.to_numeric(out["RightRate"], errors="coerce")
    out["Difference"] = out["LeftRate"] - out["RightRate"]

    return out[["Label", "YearGroupDesc", "LeftRate", "RightRate", "Difference"]]


def draw_comparison(
    ax,
    x: float,
    y: float,
    width: float,
    height: float,
    df: pd.DataFrame,
    *,
    label_col: str = "Label",
    diff_col: str = "Difference",
    group_col: str | None = "YearGroupDesc",
    text_area: float = 0.34,
    left_color: str = "#E76F51",
    right_color: str = "#2EBDC2",
    line_color: str = "black",
    fontsize: float = 8,
    sort_by_abs: bool = False,
    debug: bool = False,
    debug_color: str = "#ff8cd9",
):
    """
    Draw a generic diverging bar chart.

    Expected columns:
      - label_col
      - diff_col
      - optionally group_col

    Difference should usually be on a -1..1 scale.
    Example:
      -0.12 = 12 percentage points below
      +0.08 = 8 percentage points above
    """

    if df.empty:
        if debug:
            ax.add_patch(Rectangle((x, y), width, height, edgecolor=debug_color, facecolor="none"))
        ax.text(x + width / 2, y + height / 2, "No comparison data", ha="center", va="center")
        return

    work = df.copy()

    needed = {label_col, diff_col}
    missing = needed - set(work.columns)
    if missing:
        raise ValueError(f"Missing required columns for draw_comparison: {sorted(missing)}")

    if group_col is not None and group_col not in work.columns:
        raise ValueError(f"group_col '{group_col}' not found in dataframe")

    work[diff_col] = pd.to_numeric(work[diff_col], errors="coerce")
    work = work.dropna(subset=[diff_col]).reset_index(drop=True)

    if work.empty:
        if debug:
            ax.add_patch(Rectangle((x, y), width, height, edgecolor=debug_color, facecolor="none"))
        ax.text(x + width / 2, y + height / 2, "No valid comparison data", ha="center", va="center")
        return

    if group_col is not None:
        if sort_by_abs:
            work = work.assign(_absdiff=work[diff_col].abs()).sort_values(
                [group_col, "_absdiff"], ascending=[True, False]
            ).drop(columns="_absdiff")
        else:
            work = work.sort_values([group_col, diff_col], ascending=[True, True])
    else:
        if sort_by_abs:
            work = work.reindex(work[diff_col].abs().sort_values(ascending=False).index)
        else:
            work = work.sort_values(diff_col)

    work = work.reset_index(drop=True)

    if debug:
        ax.add_patch(Rectangle((x, y), width, height, edgecolor=debug_color, facecolor="none", linewidth=1.5))

    text_w = width * text_area
    chart_x = x + text_w
    chart_w = width - text_w
    zero_x = chart_x + chart_w / 2
    half_chart_w = (chart_w / 2) * 0.8

    display_rows = []
    if group_col is not None:
        for grp, grp_df in work.groupby(group_col, sort=False):
            display_rows.append(("__GROUP__", grp))
            for _, row in grp_df.iterrows():
                display_rows.append(("__ROW__", row))
    else:
        for _, row in work.iterrows():
            display_rows.append(("__ROW__", row))

    n_rows = len(display_rows)
    if n_rows == 0:
        return

    row_h = height / n_rows

    ax.plot([zero_x, zero_x], [y, y + height], color=line_color, linewidth=1)

    if debug:
        ax.add_patch(Rectangle((chart_x, y), chart_w, height, edgecolor="blue", facecolor="none", linewidth=1))
        ax.add_patch(Rectangle((x, y), text_w, height, edgecolor="green", facecolor="none", linewidth=1))

    max_abs = float(work[diff_col].abs().max())
    max_abs = max(max_abs, 0.01)
    scale_max = min(1.0, max_abs) if max_abs <= 1.0 else max_abs

    for i, item in enumerate(display_rows):
        y0 = y + height - row_h * (i + 1)
        yc = y0 + row_h / 2

        kind, payload = item

        if kind == "__GROUP__":
            ax.text(
                x + width / 2,
                yc,
                f"Years {payload}",
                ha="center",
                va="center",
                fontweight="bold",
            )
            continue

        row = payload
        label = str(row[label_col])
        diff = float(row[diff_col])
        chars_per_line = (int(text_w * 95 * 1.5))
        wrapped = "\n".join(textwrap.wrap(label, width=chars_per_line))

        ax.text(
            x + text_w - 0.01,
            yc,
            wrapped,
            ha="right",
            va="center",
            fontsize=fontsize
        )

        draw_w = (abs(diff) / scale_max) * half_chart_w
        draw_w = min(draw_w, half_chart_w)

        bar_h = row_h * 0.65
        bar_y = yc - bar_h / 2

        if diff >= 0:
            rect_x = zero_x
            face = right_color
            txt_x = zero_x + draw_w + 0.006
            txt_ha = "left"
        else:
            rect_x = zero_x - draw_w
            face = left_color
            txt_x = zero_x - draw_w - 0.006
            txt_ha = "right"

        ax.add_patch(
            Rectangle(
                (rect_x, bar_y),
                draw_w,
                bar_h,
                edgecolor="none",
                facecolor=face,
                clip_on=False
            )
        )

        ax.text(
            txt_x,
            yc,
            f"{diff * 100:.1f}%",
            ha=txt_ha,
            va="center",
            fontsize=fontsize
        )