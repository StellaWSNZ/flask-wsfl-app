# blank_canvas_report.py
from __future__ import annotations

from pathlib import Path
import io
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import pandas as pd
from matplotlib.patches import Rectangle, Polygon
from dotenv import load_dotenv
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.helpers import load_ppmori_fonts
from app.utils.database import get_db_engine
from app.utils.funder_missing_plot import add_full_width_footer_svg

# =========================================================
# Settings
# =========================================================
DEBUG_COL = None  # "#ff8cd9"
ICON_BLUE = "#1359A9"

BRAND_BLUE = "#1a427d"
TITLE_FILL = ICON_BLUE
TITLE_EDGE = ICON_BLUE
TITLE_TEXT = "#ffffff"
SUBTITLE_FILL = "#dbe7f5"
SUBTITLE_EDGE = ICON_BLUE
SUBTITLE_TEXT = ICON_BLUE
LABEL_COL = ICON_BLUE
RATE_COL = ICON_BLUE
UP_COL = "#2e7d32"
DOWN_COL = "#c62828"
SAME_COL = "#6c757d"

GRID_MODE = "full_page"
FILTER_TERM = 4 
FILTER_YEAR = 2025
# =========================================================
# Data
# =========================================================
def load_competency_icons_df(
    conn,
    *,
    filter_entity=None,
    filter_id=None,
    filter_period="YTD",
    filter_year=FILTER_YEAR,
    filter_term=FILTER_TERM,
) -> pd.DataFrame:
    sql = text(
        """
        EXEC dbo.GetCompetencyIcons
            @FilterEntity = :filter_entity,
            @FilterID = :filter_id,
            @FilterPeriod = :filter_period,
            @FilterYear = :filter_year,
            @FilterTerm = :filter_term
        """
    )

    df = pd.read_sql(
        sql,
        conn,
        params={
            "filter_entity": filter_entity,
            "filter_id": filter_id,
            "filter_period": filter_period,
            "filter_year": int(filter_year),
            "filter_term": int(filter_term),
        },
    )
    return df


# =========================================================
# Footer
# =========================================================
def add_footer_behind(
    fig,
    footer_svg_path: Path,
    *,
    bottom_margin_frac: float = 0.0,
    max_footer_height_frac: float = 0.18,
    col_master: str = f"{BRAND_BLUE}80",
):
    n_images_before = len(fig.images)
    n_patches_before = len(fig.patches)
    n_artists_before = len(fig.artists)

    add_full_width_footer_svg(
        fig,
        footer_svg_path,
        bottom_margin_frac=bottom_margin_frac,
        max_footer_height_frac=max_footer_height_frac,
        col_master=col_master,
    )

    for im in fig.images[n_images_before:]:
        try:
            im.set_zorder(0)
        except Exception:
            pass

    for p in fig.patches[n_patches_before:]:
        try:
            p.set_zorder(0)
        except Exception:
            pass

    for a in fig.artists[n_artists_before:]:
        try:
            a.set_zorder(0)
        except Exception:
            pass


# =========================================================
# Debug grid
# =========================================================
def draw_debug_grid(
    ax,
    *,
    color: str,
    x0: float = 0.0,
    x1: float = 1.0,
    y0: float = 0.0,
    y1: float = 1.0,
    step: float = 0.1,
    mini_step: float = 0.02,
    lw: float = 0.9,
    lw_mini: float = 0.3,
    show_labels: bool = True,
    label_fs: float = 8.5,
    draw_border: bool = True,
    border_lw: float = 1.6,
):
    if color is None:
        return

    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    if mini_step and mini_step > 0:
        nmini = int(round(1.0 / mini_step))
        for i in range(nmini + 1):
            t = i * mini_step
            x = lerp(x0, x1, t)
            ax.plot([x, x], [y0, y1], color=color, linewidth=lw_mini, zorder=5000, linestyle="--")
        for j in range(nmini + 1):
            t = j * mini_step
            y = lerp(y0, y1, t)
            ax.plot([x0, x1], [y, y], color=color, linewidth=lw_mini, zorder=5000, linestyle="--")

    n = int(round(1.0 / step))
    for i in range(n + 1):
        t = i * step
        x = lerp(x0, x1, t)
        ax.plot([x, x], [y0, y1], color=color, linewidth=lw, zorder=6000)

    for j in range(n + 1):
        t = j * step
        y = lerp(y0, y1, t)
        ax.plot([x0, x1], [y, y], color=color, linewidth=lw, zorder=6000)

    if show_labels:
        for i in range(n + 1):
            t = i * step
            x = lerp(x0, x1, t)
            ax.text(x, y1, f"{t:.1f}", ha="center", va="bottom", fontsize=label_fs, color=color, zorder=7000)
            ax.text(x, y0, f"{t:.1f}", ha="center", va="top", fontsize=label_fs, color=color, zorder=7000)

        for j in range(n + 1):
            t = j * step
            y = lerp(y0, y1, t)
            ax.text(x0, y, f"{t:.1f}", ha="right", va="center", fontsize=label_fs, color=color, zorder=7000)
            ax.text(x1, y, f"{t:.1f}", ha="left", va="center", fontsize=label_fs, color=color, zorder=7000)

    if draw_border:
        ax.plot([x0, x1], [y0, y0], color=color, linewidth=border_lw, zorder=8000)
        ax.plot([x0, x1], [y1, y1], color=color, linewidth=border_lw, zorder=8000)
        ax.plot([x0, x0], [y0, y1], color=color, linewidth=border_lw, zorder=8000)
        ax.plot([x1, x1], [y0, y1], color=color, linewidth=border_lw, zorder=8000)


# =========================================================
# Drawing helpers
# =========================================================
import numpy as np
def trim_white_border(img, threshold=0.99):
    if img.ndim != 3 or img.shape[2] < 3:
        return img

    rgb = img[..., :3]
    mask = np.any(rgb < threshold, axis=2)

    if img.shape[2] == 4:
        alpha = img[..., 3]
        mask |= alpha < threshold

    coords = np.argwhere(mask)
    if coords.size == 0:
        return img

    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    return img[y0:y1, x0:x1]

def draw_image_square(ax, image_bytes, x, y, size=0.1, zorder=20000):
    if image_bytes is None:
        return

    fp = io.BytesIO(image_bytes)
    img = mpimg.imread(fp)
    img = trim_white_border(img)

    fig_w, fig_h = ax.figure.get_size_inches()
    img_h = size * (fig_w / fig_h)

    ax.imshow(
        img,
        extent=(x, x + size, y, y + img_h),
        zorder=zorder,
        aspect = 'auto',
        interpolation="none",
        resample=False,
    )


def draw_title_band(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    fontfamily: str,
):
    poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=0.35,
        corners_round=[1, 2, 3, 4],
        n_arc=64,
    )

    ax.add_patch(
        Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=TITLE_FILL,
            edgecolor=TITLE_EDGE,
            linewidth=1.0,
            transform=ax.transAxes,
            zorder=15000,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=title,
        fontfamily=fontfamily,
        fontsize=24,
        fontweight="semibold",
        color=TITLE_TEXT,
        pad_frac=0.05,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=2,
        zorder=16000,
    )


def draw_subtitle_band(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    subtitle: str,
    fontfamily: str,
):
    poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=0.30,
        corners_round=[1, 2, 3, 4],
        n_arc=64,
    )

    ax.add_patch(
        Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=SUBTITLE_FILL,
            edgecolor=SUBTITLE_EDGE,
            linewidth=1.0,
            transform=ax.transAxes,
            zorder=15000,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=subtitle,
        fontfamily=fontfamily,
        fontsize=11,
        fontweight="bold",
        color=SUBTITLE_TEXT,
        pad_frac=0.04,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=2,
        zorder=16000,
    )


def compute_auto_grid(n_items: int, grid_width: float, grid_height: float) -> tuple[int, int]:
    if n_items <= 0:
        return 0, 0

    target_ratio = grid_width / grid_height
    best_cols = 1
    best_rows = n_items
    best_score = float("inf")

    for cols in range(1, n_items + 1):
        rows = math.ceil(n_items / cols)
        cell_ratio = cols / rows
        empty_cells = cols * rows - n_items
        score = abs(cell_ratio - target_ratio) + (empty_cells * 0.08)

        if score < best_score:
            best_score = score
            best_cols = cols
            best_rows = rows

    return best_cols, best_rows


def draw_image_grid_auto(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    df: pd.DataFrame,
    cell_pad_x: float = 0.004,
    cell_pad_y: float = 0.010,
    show_cell_debug: bool = False,
    draw_labels: bool = True,
):
    work = df.reset_index(drop=True).copy()

    n_items = len(work)
    if n_items == 0:
        return

    ncols, nrows = compute_auto_grid(n_items, width, height)

    fig_w, fig_h = ax.figure.get_size_inches()
    aspect_factor = fig_w / fig_h

    cell_w = width / ncols
    cell_h = height / nrows

    for idx, (_, row) in enumerate(work.iterrows()):
        r = idx // ncols
        c = idx % ncols

        cell_x = x + c * cell_w
        cell_y = y + height - (r + 1) * cell_h

        if show_cell_debug and DEBUG_COL is not None:
            ax.add_patch(
                Rectangle(
                    (cell_x, cell_y),
                    cell_w,
                    cell_h,
                    edgecolor=DEBUG_COL,
                    facecolor="none",
                    linewidth=1.5,
                    zorder=12000,
                )
            )

        text_band_h = 0.32 * cell_h if draw_labels else 0.0
        top_pad = cell_pad_y
        bottom_pad = 0.0 * cell_h

        avail_w = max(0.0, cell_w - 2 * cell_pad_x)
        avail_h = max(0.0, cell_h - text_band_h - top_pad - bottom_pad)

        img_w = min(avail_w, avail_h / aspect_factor)
        img_h = img_w * aspect_factor

        img_x = cell_x + (cell_w - img_w) / 2
        img_y = cell_y + cell_h - top_pad - img_h

        draw_image_square(
            ax,
            row["ImageData"],
            x=img_x,
            y=img_y,
            size=img_w,
        )

        if draw_labels:
            label_top_y = img_y - 0.02 * cell_h

            if "YearGroupDesc" in row.index and pd.notna(row["YearGroupDesc"]):
                ax.text(
                    cell_x + cell_w / 2,
                    label_top_y,
                    f"Years {row['YearGroupDesc']}",
                    ha="center",
                    va="top",
                    fontsize=10,
                    fontweight="bold",
                    color=LABEL_COL,
                    zorder=21000,
                    wrap=True,
                )

            if "RateTY" in row.index and pd.notna(row["RateTY"]):
                rate_y = label_top_y - 0.10 * cell_h
                ax.text(
                    cell_x + cell_w / 2,
                    rate_y,
                    f"{row['RateTY']:.0%}",
                    ha="center",
                    va="top",
                    fontsize=11,
                    fontweight="bold",
                    color=RATE_COL,
                    zorder=21000,
                )

            if "Trend" in row.index and "RateDiff" in row.index and pd.notna(row["RateDiff"]):
                delta_y = label_top_y - 0.18 * cell_h

                if row["Trend"] == "UP":
                    delta_txt = f"↑ {abs(row['RateDiff']) * 100:.0f} pp"
                    delta_col = UP_COL
                elif row["Trend"] == "DOWN":
                    delta_txt = f"↓ {abs(row['RateDiff']) * 100:.0f} pp"
                    delta_col = DOWN_COL
                elif row["Trend"] == "SAME":
                    delta_txt = "—"
                    delta_col = SAME_COL
                elif row["Trend"] == "NEW":
                    delta_txt = "New"
                    delta_col = SAME_COL
                else:
                    delta_txt = ""
                    delta_col = SAME_COL

                if delta_txt:
                    ax.text(
                        cell_x + cell_w / 2,
                        delta_y,
                        delta_txt,
                        ha="center",
                        va="top",
                        fontsize=9,
                        fontweight="bold",
                        color=delta_col,
                        zorder=21000,
                    )


# =========================================================
# Main
# =========================================================
def build_icon_reoprt(
    *,
    out_pdf_path: str | Path,
    footer_svg: str | Path = "app/static/footer.svg",
    dpi: int = 300,
    footer_height_frac: float = 0.10,
    term = FILTER_TERM,
    year = FILTER_YEAR
):
    out_pdf_path = Path(out_pdf_path)
    out_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    engine = get_db_engine()
    with engine.begin() as conn:
        df = load_competency_icons_df(
            conn,
            filter_period="YTD",
            filter_year=year,
            filter_term=term,
        )

    fig = plt.figure(figsize=(8.27, 11.69), dpi=dpi)

    footer_svg_path = Path(footer_svg)
    if not footer_svg_path.is_absolute():
        if not footer_svg_path.exists():
            footer_svg_path = Path.cwd() / footer_svg_path

    if not footer_svg_path.exists():
        raise FileNotFoundError(f"Footer SVG not found at: {footer_svg_path}")

    add_footer_behind(
        fig,
        footer_svg_path,
        bottom_margin_frac=0.00,
        max_footer_height_frac=float(footer_height_frac),
        col_master=f"{BRAND_BLUE}80",
    )

    ax = fig.add_axes([0, 0, 1, 1], zorder=10000)
    ax.set_axis_off()
    ax.patch.set_alpha(0.0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("auto")
    ax.axis("off")
    fonts_dir = "app/static/fonts"

    family = load_ppmori_fonts(str(fonts_dir))

    fig_w, fig_h = fig.get_size_inches()
    aspect = fig_w / fig_h
    margin = 0.02
    page_left = margin
    page_right = 1 - margin
    
    page_bottom = 0.08
    page_top = 1 - (margin * aspect)

    page_w = page_right - page_left
    page_h = page_top - page_bottom



    title_h = 0.04
    subtitle_h = 0.02
    band_gap = 0.009
    content_gap = 0.012

    title_y = page_top - title_h
    subtitle_y = title_y - band_gap - subtitle_h

    draw_title_band(
        ax,
        x=page_left,
        y=title_y,
        width=page_w,
        height=title_h,
        title="National Competency Rates",
        fontfamily=family,
    )
    subtitle_text = (
        f"Funding year-to-date rates (to Term {term}, {year}) "
        f"compared with last year's full year. Changes are shown in percentage points."
    )
    draw_subtitle_band(
        ax,
        x=page_left,
        y=subtitle_y,
        width=page_w,
        height=subtitle_h,
        subtitle=subtitle_text,
        fontfamily=family,
    )

    grid_y = page_bottom
    grid_h = subtitle_y - content_gap - page_bottom

    draw_image_grid_auto(
        ax,
        x=page_left,
        y=grid_y,
        width=page_w,
        height=grid_h,
        df=df,
        cell_pad_x=0.001,
        cell_pad_y=0.00,
        show_cell_debug=False,
        draw_labels=True,
    )

    if DEBUG_COL is not None and GRID_MODE == "full_page":
        draw_debug_grid(
            ax,
            color=DEBUG_COL,
            x0=0.0,
            x1=1.0,
            y0=0.0,
            y1=1.0,
            step=0.1,
            mini_step=0.02,
        )

    fig.savefig(out_pdf_path, format="pdf", dpi=dpi, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print(f"[OK] Wrote PDF: {out_pdf_path.resolve()}")
    return fig


if __name__ == "__main__":
    load_dotenv()

    fonts_dir = "app/static/fonts"
    load_ppmori_fonts(str(fonts_dir))

    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    build_icon_reoprt(
        out_pdf_path=OUT_DIR / "blank_canvas.pdf",
        footer_svg="app/static/footer.svg",
        dpi=300,
        footer_height_frac=0.10,
    )