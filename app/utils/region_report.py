# app/utils/region_report.py
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.image as mpimg
import matplotlib.colors as mcolors

from sqlalchemy import text
from shapely.geometry import box as shp_box
import geopandas as gpd

from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import open_pdf, new_page, close_pdf, save_page
from app.utils.funder_missing_plot import add_full_width_footer_svg
from app.report_utils.CHT_Comparison import make_difference_df, draw_comparison
from app.utils.one_bar_one_line import provider_portrait_with_target
from app.utils.geo import load_lakes_and_rivers, load_regional_councils, DEFAULT_NAME_FIELD

# ✅ Use the shared chart component + bucket labels
from app.report_utils.CHT_CircleProportions import (
    circle_plot,
    compute_bucket_stats,
    BUCKET_CURRENT,
    BUCKET_PREV,
    BUCKET_NEVER,
)

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.utils.report_two_bar_portrait import make_figure_region

# =============================================================================
# Types
# =============================================================================
AxesRect = Tuple[float, float, float, float]  # (left, bottom, width, height) in 0..1 figure coords
MapBBox = Tuple[float, float, float, float]   # (minx, miny, maxx, maxy) in REGION CRS (e.g., EPSG:2193)


# =============================================================================
# Style
# =============================================================================
C_MASTER = "#1a427d"

EDGE_CURRENT = "#1a427d"  # current funded (keep as is)
EDGE_PREV =  "#556b2f"        # darker previously funded
EDGE_NEVER = "#af3809"    # red/orange-red not funded

COL_REGION = "#6c757d"
EDGE_REGION = "#6c757d"
LW_REGION = 1.5
CONTEXT_EDGE = "#6c757d"
CONTEXT_FILL = "#D9D9D9"
CONTEXT_LW = 0.5
CONTEXT_A = 0.85


# =============================================================================
# Footer: force behind everything
# =============================================================================
def add_footer_behind(
    fig,
    footer_svg_path: Path,
    *,
    bottom_margin_frac: float = 0.0,
    max_footer_height_frac: float = 0.20,
    col_master: str = f"{C_MASTER}80",
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


# =============================================================================
# Geometry helpers: auto-expand bbox to match axes box (no distortion)
# =============================================================================
def _fit_bbox_to_axes(bbox: MapBBox, ax_aspect: float) -> MapBBox:
    minx, miny, maxx, maxy = map(float, bbox)
    dx = maxx - minx
    dy = maxy - miny
    if dx <= 0 or dy <= 0:
        return (minx, miny, maxx, maxy)

    data_aspect = dx / dy

    if data_aspect < ax_aspect:
        new_dx = ax_aspect * dy
        pad = 0.5 * (new_dx - dx)
        return (minx - pad, miny, maxx + pad, maxy)

    if data_aspect > ax_aspect:
        new_dy = dx / ax_aspect
        pad = 0.5 * (new_dy - dy)
        return (minx, miny - pad, maxx, maxy + pad)

    return (minx, miny, maxx, maxy)


def _axes_aspect(ax) -> float:
    bb = ax.get_position()
    w = float(bb.width)
    h = float(bb.height)
    return (w / h) if h > 0 else 1.0


def _expand_bbox_frac(bbox: MapBBox, frac: float) -> MapBBox:
    minx, miny, maxx, maxy = map(float, bbox)
    dx = maxx - minx
    dy = maxy - miny
    if dx <= 0 or dy <= 0:
        return (minx, miny, maxx, maxy)
    pad = float(frac) * max(dx, dy)
    return (minx - pad, miny - pad, maxx + pad, maxy + pad)


# =============================================================================
# Region name normalization
# =============================================================================
def _detect_region_name_col(gdf, preferred: str | None = None) -> str:
    if preferred and preferred in gdf.columns:
        return preferred

    candidates = [
        preferred,
        DEFAULT_NAME_FIELD,
        "REGC2025_V1_00_NAME",
        "RegionName",
        "REGC2025_2",
        "REGC2025_1",
        "NAME",
        "Name",
        "REGION",
        "REGIONNAME",
        "Description",
        "DESC",
    ]
    for c in candidates:
        if c and c in gdf.columns:
            return c

    non_geom = [c for c in gdf.columns if c.lower() != "geometry"]
    for c in non_geom:
        try:
            if gdf[c].dtype == "object":
                return c
        except Exception:
            pass

    raise RuntimeError(f"Could not detect region name column. Columns: {list(gdf.columns)}")


def _norm_region_name(s: str) -> str:
    s = (s or "").strip().lower()
    for suf in [" regional council", " region", " district council", " city council"]:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    s = " ".join(s.split())
    return s


# =============================================================================
# Data loaders
# =============================================================================
def _load_school_points_by_region(conn, *, region: str, eqi_filter: int = 1) -> pd.DataFrame:
    sql = text(
        """
        EXEC dbo.GetSchoolCoordsByRegion
            @Region = :region
        """
    )
    df = pd.read_sql(sql, conn, params={"region": region})

    needed = {
        "MOENumber",
        "SchoolName",
        "Latitude",
        "Longitude",
        "Funded_2023_2024",
        "Funded_2024_2025",
        "Funded_2025_2026",
        "Funders_2023_2024",
        "Funders_2024_2025",
        "Funders_2025_2026",
        "HasSchoolPool",
    }
    missing = needed - set(df.columns)
    if missing:
        raise RuntimeError(f"GetSchoolCoordsByRegion missing columns: {sorted(missing)}. Got: {list(df.columns)}")

    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")
    df = df.dropna(subset=["Latitude", "Longitude"]).copy()

    for c in ["Funded_2023_2024", "Funded_2024_2025", "Funded_2025_2026"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    for c in ["Funders_2023_2024", "Funders_2024_2025", "Funders_2025_2026"]:
        df[c] = df[c].astype("string").fillna("").str.strip()

    df["HasSchoolPool"] = pd.to_numeric(df["HasSchoolPool"], errors="coerce").fillna(0).astype(int)
    df["HasSchoolPool"] = df["HasSchoolPool"].clip(lower=0, upper=1)

    # NOTE: compute_bucket_stats will create Bucket if missing, but it's OK to keep it too
    # (If you prefer, you can delete these next two lines)
    # df["Bucket"] = df.apply(_bucket, axis=1)

    return df


def _load_region_rates(conn, *, year: int, term: int, region_name: str) -> pd.DataFrame:
    sql = text(
        """
        EXEC dbo.GetRegionalCouncilRates
            @calendaryear = :year,
            @term         = :term,
            @region       = :region_name
        """
    )
    df = pd.read_sql(sql, conn, params={"year": int(year), "term": int(term), "region_name": region_name.strip()})

    needed = {"YearGroupDesc", "CompetencyDesc", "ResultType", "Rate"}
    missing = needed - set(df.columns)
    if missing:
        raise RuntimeError(f"GetRegionalCouncilRates missing columns: {sorted(missing)}. Got: {list(df.columns)}")
    return df


def _load_region_polygon(
    *,
    region_name: str,
    debug_boundaries: bool,
    debug_folder: str | Path | None,
    name_field: str = DEFAULT_NAME_FIELD,
    use_internet: bool = True,
    datafinder_layer_id: int = 120945,
    bbox_4326: Optional[Tuple[float, float, float, float]] = None,
):
    gdf = load_regional_councils(
        debug=bool(debug_boundaries),
        debug_folder=debug_folder,
        simplify_tol_deg=0.002,
        use_internet=bool(use_internet),
        datafinder_layer_id=int(datafinder_layer_id),
        bbox_4326=bbox_4326,
    )

    detected = _detect_region_name_col(gdf, preferred=name_field)
    s = gdf[detected].astype("string")
    target = _norm_region_name(region_name)

    mask = s.map(lambda x: _norm_region_name(str(x))) == target
    if not mask.any():
        mask = s.map(lambda x: target in _norm_region_name(str(x)))

    out = gdf.loc[mask].copy()
    if out.empty:
        sample = sorted(s.dropna().unique().tolist())[:30]
        raise RuntimeError(f"Region '{region_name}' not found in polygons using column '{detected}'. Examples: {sample}")

    out = out.dissolve(by=detected).reset_index()
    return out


# =============================================================================
# Header
# =============================================================================
def _draw_header(ax, *, family: str, title: str):
    poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.955,
        width=0.90,
        height=0.05,
        ratio=1,
        corners_round=[1, 3],
        n_arc=64,
    )

    patch = mpatches.Polygon(
        list(poly.exterior.coords),
        closed=True,
        facecolor=C_MASTER,
        edgecolor=C_MASTER,
        linewidth=1.5,
        transform=ax.transAxes,
        zorder=1000,
    )
    patch.set_clip_on(False)
    ax.add_patch(patch)

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=title,
        fontfamily=family,
        fontsize=18,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.05,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=False,
        max_lines=None,
        zorder=1100,
    )

def _load_region_kaiako_rates(conn, *, year: int, term: int, region_name: str) -> pd.DataFrame:
    sql = text("""
        EXEC dbo.GetRegionalCouncilRates_kaiako
            @calendaryear = :year,
            @term = :term,
            @region = :region
    """)

    df = pd.read_sql(sql, conn, params={
        "year": year,
        "term": term,
        "region": region_name
    })

    return df
# =============================================================================
# Key helpers
# =============================================================================
def _bucket_solid_color(bucket: str) -> str:
    if bucket.startswith("Current"):
        return EDGE_CURRENT
    if bucket.startswith("Previously"):
        return EDGE_PREV
    return EDGE_NEVER


def _bucket_fill_rgba(bucket: str, alpha: float = 0.18):
    rgb = mcolors.to_rgb(_bucket_solid_color(bucket))
    return (rgb[0], rgb[1], rgb[2], float(alpha))


def _bucket_stats(schools_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    # Ensure Bucket exists (use shared helper’s labels)
    if "Bucket" not in schools_df.columns:
        # compute_bucket_stats will create it (via internal _bucket) if missing
        # so we can force it by calling once on a copy:
        _ = compute_bucket_stats(
            schools_df,
            colours={BUCKET_CURRENT: EDGE_CURRENT, BUCKET_PREV: EDGE_PREV, BUCKET_NEVER: EDGE_NEVER},
        )
    total = int(len(schools_df)) or 0
    out: Dict[str, Dict[str, Any]] = {}

    for bucket in (BUCKET_CURRENT, BUCKET_PREV, BUCKET_NEVER):
        sub = schools_df[schools_df["Bucket"] == bucket]
        n = int(len(sub))
        pct = (100.0 * n / total) if total else 0.0

        in_pool = not_in_pool = 0
        if bucket == BUCKET_NEVER and "HasSchoolPool" in sub.columns:
            in_pool = int((sub["HasSchoolPool"] == 1).sum())
            not_in_pool = int((sub["HasSchoolPool"] == 0).sum())

        out[bucket] = {"n": n, "pct": pct, "in_pool": in_pool, "not_in_pool": not_in_pool}
    return out


def _draw_key_stack(
    ax,
    *,
    family: str,
    schools_df: pd.DataFrame,
    x: float,
    y_top: float,
    w: float,
    h_item: float,
    gap: float = 0.010,
    fontsize_title: float = 11,
    fontsize_item: float = 9.2,
    show_pool_breakdown: bool = True,
):
    stats = _bucket_stats(schools_df)
    total = int(len(schools_df)) or 0

    def _bubble(cx, cy, width, height, edge, fill, text, weight="semibold", fs=fontsize_item):
        poly = rounded_rect_polygon(
            cx=cx,
            cy=cy,
            width=width,
            height=height,
            ratio=0.3,
            corners_round=[1, 2, 3, 4],
            n_arc=48,
        )
        patch = mpatches.Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=fill,
            edgecolor=edge,
            linewidth=1.0,
            transform=ax.transAxes,
            zorder=4800,
        )
        patch.set_clip_on(False)
        ax.add_patch(patch)

        draw_text_in_polygon(
            ax,
            poly=poly,
            text=text,
            fontfamily=family,
            fontsize=fs,
            fontweight=weight,
            color=edge,
            pad_frac=0.18,
            wrap=False,
            autoshrink=True,
            min_fontsize=6.0,
            clip_to_polygon=False,
            max_lines=1,
            zorder=4900,
        )

    ax.text(
        x,
        y_top,
        "Key",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontfamily=family,
        fontsize=fontsize_title,
        fontweight="semibold",
        color=C_MASTER,
        zorder=5000,
    )

    y = y_top - 0.018
    cx = x + w / 2.0

    items = [
        (BUCKET_CURRENT, "Current funded"),
        (BUCKET_PREV, "Previously funded"),
        (BUCKET_NEVER, "Not funded"),
    ]

    for bucket, label in items:
        edge = _bucket_solid_color(bucket)
        fill = _bucket_fill_rgba(bucket, alpha=0.16)

        n = int(stats[bucket]["n"])
        pct = float(stats[bucket]["pct"])
        txt = f"{label}: {n} ({pct:.1f}%)"

        _bubble(cx, y, w, h_item, edge, fill, txt, weight="semibold", fs=fontsize_item)
        y -= (h_item + gap)

        if bucket == BUCKET_NEVER and show_pool_breakdown:
            in_pool = int(stats[bucket]["in_pool"])
            not_in_pool = int(stats[bucket]["not_in_pool"])

            in_pool_pct = (100.0 * in_pool / total) if total else 0.0
            not_in_pool_pct = (100.0 * not_in_pool / total) if total else 0.0

            w2 = w * 0.92
            cx2 = x + 0.04 + w2 / 2.0
            h2 = h_item * 0.92

            _bubble(
                cx2,
                y,
                w2,
                h2,
                edge,
                fill,
                f"In school pool: {in_pool} ({in_pool_pct:.1f}%)",
                weight="normal",
                fs=fontsize_item - 0.2,
            )
            y -= (h2 + gap * 0.8)

            _bubble(
                cx2,
                y,
                w2,
                h2,
                edge,
                fill,
                f"Not in pool: {not_in_pool} ({not_in_pool_pct:.1f}%)",
                weight="normal",
                fs=fontsize_item - 0.2,
            )
            y -= (h2 + gap)
def ensure_bucket_column(df: pd.DataFrame) -> pd.DataFrame:
    if "Bucket" in df.columns:
        return df

    df = df.copy()

    def _bucket_row(row: pd.Series) -> str:
        cur = int(row.get("Funded_2025_2026", 0) or 0) == 1
        prev_any = (int(row.get("Funded_2024_2025", 0) or 0) == 1) or (int(row.get("Funded_2023_2024", 0) or 0) == 1)
        if cur:
            return BUCKET_CURRENT
        if prev_any:
            return BUCKET_PREV
        return BUCKET_NEVER

    df["Bucket"] = df.apply(_bucket_row, axis=1)
    return df

# =============================================================================
# Page 1: map drawing
# =============================================================================
def _draw_region_map_page(
    *,
    ax_map,
    region_poly,
    schools_df: pd.DataFrame,
    map_bbox: Optional[MapBBox] = None,
    pad_frac: float = 0.0,
    draw_water: bool = True,
    water_local_folder: str | Path = "app/static/geodata",
    prefer_local_water: bool = True,
    show_region_outline: bool = True,
    debug_water: bool = False,
    draw_context_councils: bool = True,
    context_debug_boundaries: bool = False,
    context_debug_folder: str | Path | None = None,
    fill_axes_box: bool = True,
    councils_use_internet: bool = True,
    councils_datafinder_layer_id: int = 120945,
    councils_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
):
    region_crs = region_poly.crs
    if region_crs is None:
        raise RuntimeError("region_poly.crs is None — ensure regional council polygons have CRS set.")

    if map_bbox is None:
        bbox_region: MapBBox = tuple(map(float, region_poly.total_bounds))
    else:
        bbox_region = tuple(float(v) for v in map_bbox)

    if fill_axes_box:
        bbox_region = _fit_bbox_to_axes(bbox_region, ax_aspect=_axes_aspect(ax_map))

    if pad_frac and float(pad_frac) > 0:
        bbox_region = _expand_bbox_frac(bbox_region, float(pad_frac))

    bminx, bminy, bmaxx, bmaxy = bbox_region

    # Context councils
    if draw_context_councils:
        councils = load_regional_councils(
            debug=bool(context_debug_boundaries),
            debug_folder=context_debug_folder,
            simplify_tol_deg=0.002,
            use_internet=bool(councils_use_internet),
            datafinder_layer_id=int(councils_datafinder_layer_id),
            bbox_4326=councils_bbox_4326,
        )
        if councils is not None and len(councils):
            if councils.crs is None:
                councils = councils.set_crs(epsg=4326)
            if str(councils.crs) != str(region_crs):
                councils = councils.to_crs(region_crs)

            councils = councils.cx[bminx:bmaxx, bminy:bmaxy]
            councils.plot(
                ax=ax_map,
                facecolor=CONTEXT_FILL,
                edgecolor=CONTEXT_EDGE,
                linewidth=CONTEXT_LW,
                zorder=0,
            )

    # Region fill
    region_poly.plot(
        ax=ax_map,
        facecolor=COL_REGION,
        edgecolor=EDGE_REGION,
        linewidth=LW_REGION,
        alpha=0.35,
        zorder=1,
    )

    # Water layers
    if draw_water:
        try:
            bbox_geom = shp_box(bminx, bminy, bmaxx, bmaxy)
            bbox_series = gpd.GeoSeries([bbox_geom], crs=region_crs).to_crs("EPSG:4326")
            wminx, wminy, wmaxx, wmaxy = bbox_series.total_bounds
            bbox_4326 = (float(wminx), float(wminy), float(wmaxx), float(wmaxy))

            lakes_gdf, rivers_gdf = load_lakes_and_rivers(
                bbox_4326=bbox_4326,
                local_folder=water_local_folder,
                prefer_local=prefer_local_water,
                debug=True,
            )

            if lakes_gdf is None:
                lakes_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
            if rivers_gdf is None:
                rivers_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

            if lakes_gdf.crs is None:
                lakes_gdf = lakes_gdf.set_crs(epsg=4326)
            if rivers_gdf.crs is None:
                rivers_gdf = rivers_gdf.set_crs(epsg=4326)

            if str(lakes_gdf.crs) != str(region_crs):
                lakes_gdf = lakes_gdf.to_crs(region_crs)
            if str(rivers_gdf.crs) != str(region_crs):
                rivers_gdf = rivers_gdf.to_crs(region_crs)

            if len(lakes_gdf):
                lakes_gdf = lakes_gdf.cx[bminx:bmaxx, bminy:bmaxy]
            if len(rivers_gdf):
                rivers_gdf = rivers_gdf.cx[bminx:bmaxx, bminy:bmaxy]

            if len(lakes_gdf):
                lakes_gdf.plot(ax=ax_map, facecolor="#ffffff", edgecolor="none", alpha=1, zorder=2)
            if len(rivers_gdf):
                rivers_gdf.plot(ax=ax_map, facecolor="none", edgecolor="#ffffff", alpha=1, linewidth=0.216, zorder=3)

        except Exception as e:
            print("WATER ERROR:", repr(e))

    # School points
    pts = gpd.GeoDataFrame(
        schools_df.copy(),
        geometry=gpd.points_from_xy(schools_df["Longitude"], schools_df["Latitude"]),
        crs="EPSG:4326",
    ).to_crs(region_crs)

    order = [BUCKET_NEVER, BUCKET_PREV, BUCKET_CURRENT]
    for bucket in order:
        sub = pts[pts["Bucket"] == bucket] if "Bucket" in pts.columns else pts.iloc[0:0]
        if sub.empty:
            continue
        ax_map.scatter(
            sub.geometry.x,
            sub.geometry.y,
            s=22,
            c=_bucket_solid_color(bucket),
            alpha=0.95,
            linewidths=0.5,
            edgecolors="white",
            zorder=10 if bucket.startswith("Current") else 9,
        )

    ax_map.set_aspect("equal", adjustable="datalim")
    ax_map.set_axis_off()
    ax_map.set_xlim(bbox_region[0], bbox_region[2])
    ax_map.set_ylim(bbox_region[1], bbox_region[3])


# =============================================================================
# Public builder: 2-page region PDF
# =============================================================================
def build_region_report_pdf(
    *,
    conn,
    region_name: str,
    calendar_year: int,
    term: int,
    out_pdf_path: str | Path,
    footer_svg: str | Path = "app/static/footer.svg",
    dpi: int = 300,
    page_size: str = "A4",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
    debug_boundaries: bool = False,
    debug_folder: str | Path | None = None,
    local_councils_folder: str | Path | None = None,
    name_field: str = DEFAULT_NAME_FIELD,
    eqi_filter: int = 1,
    bar_series: str = "ytd",
    debug_axes_boxes: bool = False,
    rasterize_page1: bool = True,
    draw_water: bool = True,
    show_region_outline: bool = False,
    water_local_folder: str | Path = "app/static/geodata",
    prefer_local_water: bool = True,
    draw_key: bool = True,
    draw_context_councils: bool = True,
    map_axes_rect: AxesRect =  (0.05, 0.13, 0.90, 0.62),
    map_bbox: Optional[MapBBox] = None,
    map_pad_frac: float = 0.0,
    fill_map_axes_box: bool = True,
    councils_use_internet: bool = True,
    councils_datafinder_layer_id: int = 120945,
    councils_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:

    family = load_ppmori_fonts(str(fonts_dir))

    meta: Dict[str, Any] = {
        "region": region_name,
        "calendar_year": int(calendar_year),
        "term": int(term),
        "pages":  4,
        "schools": 0,
    }

    schools = _load_school_points_by_region(conn, region=region_name, eqi_filter=eqi_filter)
    schools = ensure_bucket_column(schools)
    meta["schools"] = int(len(schools))

    councils_folder = local_councils_folder or debug_folder

    region_poly = _load_region_polygon(
        region_name=region_name,
        debug_boundaries=bool(debug_boundaries),
        debug_folder=councils_folder,
        name_field=name_field,
        use_internet=councils_use_internet,
        datafinder_layer_id=councils_datafinder_layer_id,
        bbox_4326=councils_bbox_4326,
    )

    rates_df = _load_region_rates(conn, year=calendar_year, term=term, region_name=region_name)
    comparison_df = make_difference_df(
        rates_df,
        left_result="Region Rate (YTD)",
        right_result="National Rate (YTD)",
    )
    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    footer_svg_path = Path(footer_svg)
    if not footer_svg_path.is_absolute():
        footer_svg_path = Path(__file__).resolve().parents[1] / "static" / footer_svg_path.name

    preview_fig = None

    # ========================================================= 
    # # PAGE 1 (map) 
    # # ========================================================= 
    fig1, ax_master = new_page(w, h, dpi) 
    ax_master.set_axis_off() 
    ax_master.set_zorder(10_000) 
    ax_master.patch.set_alpha(0.0) 
    add_footer_behind( fig1, footer_svg_path, bottom_margin_frac=0.0, max_footer_height_frac=0.13, col_master=f"{C_MASTER}80", ) 
    ax_map = fig1.add_axes(list(map_axes_rect), zorder=100) 
    ax_map.patch.set_alpha(1.0) 
    ax_map.set_facecolor("white") 
    if debug_axes_boxes: 
        ax_map.patch.set_alpha(0.12) 
        ax_map.patch.set_edgecolor("red") 
        ax_map.patch.set_linewidth(2) 
    _draw_region_map_page( ax_map=ax_map, region_poly=region_poly, schools_df=schools, map_bbox=map_bbox, pad_frac=map_pad_frac, draw_water=draw_water, water_local_folder=water_local_folder, prefer_local_water=prefer_local_water, show_region_outline=show_region_outline, debug_water=True, draw_context_councils=draw_context_councils, context_debug_boundaries=bool(debug_boundaries), context_debug_folder=councils_folder, fill_axes_box=bool(fill_map_axes_box), councils_use_internet=councils_use_internet, councils_datafinder_layer_id=councils_datafinder_layer_id, councils_bbox_4326=councils_bbox_4326, ) 
    title1 = f"{region_name} – School Coverage Map" 
    _draw_header(ax_master, family=family, title=title1) # ✅ NEW: compute bucket stats + draw proportional circles with shared fitted label fontsize 
    stats = compute_bucket_stats( schools, colours={ BUCKET_CURRENT: EDGE_CURRENT, BUCKET_PREV: EDGE_PREV, BUCKET_NEVER: EDGE_NEVER, }, ) 
    circle_plot( ax_master, stats=stats, fontfamily=family, top_y=0.92, height=0.15, gap_between_polygons=0.05, polygon_text_size=None, # auto-fit ONE fontsize using longest label 
                polygon_text_max=18.0, polygon_text_min=8.0, ) 
    if draw_key: 
        _draw_key_stack( ax_master, family=family, schools_df=schools, x=0.06, y_top=0.84, w=0.32, h_item=0.042, gap=0.010, fontsize_title=11, fontsize_item=9.2, show_pool_breakdown=True, ) 
    fig1.canvas.draw() 
    preview_fig = fig1 
    if rasterize_page1: 
        buf1 = io.BytesIO() 
        fig1.savefig(buf1, format="png", dpi=dpi, bbox_inches=None, pad_inches=0, transparent=False) 
        png1 = buf1.getvalue() 
        fig1b, ax1b = new_page(w, h, dpi) 
        ax1b.set_axis_off() 
        ax1b.set_position([0, 0, 1, 1]) 
        try: 
            fig1b.subplots_adjust(left=0, right=1, bottom=0, top=1) 
        except Exception: pass 
        img1 = mpimg.imread(io.BytesIO(png1)) 
        ax1b.imshow(img1, extent=(0, 1, 0, 1), transform=ax1b.transAxes, aspect="auto") 
        fig1b.canvas.draw() 
        save_page(pdf, fig1b, full_bleed=True) 
        plt.close(fig1b) 
    else: 
        save_page(pdf, fig1, full_bleed=True) 
    plt.close(fig1)


    # =========================================================
    # PAGE 2 (chart)
    # =========================================================
    fig2 = provider_portrait_with_target(
        rates_df,
        term=int(term),
        year=int(calendar_year),
        mode="region",
        region_name=region_name,
        bar_series=bar_series,
        debug=False,
        title="",
    )

    ax_master2 = fig2.add_axes([0, 0, 1, 1], zorder=10_000)
    ax_master2.set_axis_off()
    ax_master2.patch.set_alpha(0.0)

    title2 = f"{region_name} Summary"
    _draw_header(ax_master2, family=family, title=title2)

    add_footer_behind(
        fig2,
        footer_svg_path,
        bottom_margin_frac=0.0,
        max_footer_height_frac=0.15,
        col_master=f"{C_MASTER}80",
    )

    fig2.canvas.draw()
    save_page(pdf, fig2, full_bleed=True)
    plt.close(fig2)
    
    # =========================================================
    # PAGE 3 (region vs national difference)
    # =========================================================
    fig3, ax3 = new_page(w, h, dpi)
    ax3.set_axis_off()
    ax3.set_xlim(0, 1)
    ax3.set_ylim(0, 1)

    add_footer_behind(
        fig3,
        footer_svg_path,
        bottom_margin_frac=0.0,
        max_footer_height_frac=0.15,
        col_master=f"{C_MASTER}80",
    )

    draw_comparison(
        ax3,
        x=0.05,
        y=0.13,
        width=0.90,
        height=0.78,
        df=comparison_df,
        text_area=0.5,
        label_col="Label",
        diff_col="Difference",
        group_col="YearGroupDesc",
        left_color  = "#C97A6B",   # worse than national
        right_color = "#2EBDC2",   # better than national
        line_color  = "#6c757d",   # 0 line
        fontsize=8,
        sort_by_abs=False,
        debug=False,
    )

    ax_master3 = fig3.add_axes([0, 0, 1, 1], zorder=10_000)
    ax_master3.set_axis_off()
    ax_master3.patch.set_alpha(0.0)

    title3 = f"{region_name} National Difference"
    _draw_header(ax_master3, family=family, title=title3)
    
    fig3.canvas.draw()
    save_page(pdf, fig3, full_bleed=True)
    plt.close(fig3)
    # =========================================================
    # PAGE 4 (chart)
    # =========================================================
    # Load region kaiako vs instructor dataset
    kaiako_rates_df = _load_region_kaiako_rates(
        conn,
        year=calendar_year,
        term=term,
        region_name=region_name
    )

    # Which series to draw
    vars_to_plot = [
        "Region Instructor-Led Rate (YTD)",
        "Region Kaiako-Led Rate (YTD)"
    ]

    colors_dict = {
        "Region Instructor-Led Rate (YTD)": "#2EBDC2",
        "Region Kaiako-Led Rate (YTD)": "#BBE6E9"
    }

    # Compute row heights per year group (required by make_figure)
    df2 = kaiako_rates_df[['CompetencyDesc', 'YearGroupDesc']].drop_duplicates()

    row_heights = (
        df2['YearGroupDesc'].value_counts().sort_index()
        / (df2['YearGroupDesc'].value_counts().sum() )
    )

    # Create the chart figure
    fig4 = make_figure_region(
    kaiako_rates_df,
        DEBUG=False,
        PAGE_SIZE=(8.27, 11.69),
        HEADER_SPACE=0.08,
        FOOTER_SPACE=0.13,
        subtitle_space=0.05,
        row_heights=row_heights,
        BUFFER=0.0,
        vars_to_plot=vars_to_plot,
        colors_dict=colors_dict
    )

    # Add header overlay
    ax_master4 = fig4.add_axes([0, 0, 1, 1], zorder=10_000)
    ax_master4.set_axis_off()
    ax_master4.patch.set_alpha(0)

    title4 = f"{region_name} – Instructor vs Kaiako Delivery"
    _draw_header(ax_master4, family=family, title=title4)

    # Add footer
    add_footer_behind(
        fig4,
        footer_svg_path,
        bottom_margin_frac=0.0,
        max_footer_height_frac=0.15,
        col_master=f"{C_MASTER}80",
    )

    fig4.canvas.draw()

    # Save page to PDF
    save_page(pdf, fig4, full_bleed=True)
    plt.close(fig4)

    close_pdf(pdf)
    return preview_fig, meta


# =============================================================================
# Local run harness (optional)
# =============================================================================
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv()

    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    db_url = os.getenv("DB_URL") or os.getenv("db_url")
    if not db_url:
        raise RuntimeError("Set DB_URL in .env (DB_URL=...)")

    engine = create_engine(db_url, pool_pre_ping=True, fast_executemany=True)

    pdf_out = OUT_DIR / "Region_Report.pdf"
    map_axes_rect = (0.05, 0.13, 0.90, 0.62)

    with engine.begin() as conn:
        preview, meta = build_region_report_pdf(
            conn=conn,
            region_name="Wellington Region",
            calendar_year=2025,
            term=4,
            out_pdf_path=pdf_out,
            footer_svg="app/static/footer.svg",
            dpi=300,
            fonts_dir="app/static/fonts",
            debug_boundaries=False,
            local_councils_folder=None,
            prefer_local_water=False,
            draw_water=True,
            draw_key=False,
            draw_context_councils=True,
            map_axes_rect=map_axes_rect,
            map_bbox=None,
            map_pad_frac=0,
            fill_map_axes_box=True,
            rasterize_page1=True,
            councils_use_internet=True,
            councils_datafinder_layer_id=120945,
            councils_bbox_4326=(166.0, -48.0, 179.0, -34.0),
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Schools: {meta['schools']} | Region: {meta['region']}")

    if preview:
        preview_png = OUT_DIR / "Region_Report_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")