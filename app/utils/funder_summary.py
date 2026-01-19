from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import matplotlib.patches as mpatches
from sqlalchemy import text

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.TAB_DataframeTable import draw_dataframe_table_v2
from app.report_utils.helpers import load_ppmori_fonts
from app.report_utils.pdf_builder import close_pdf, new_page, open_pdf, save_page


# ============================================================
# Config: stored proc + expected column names
# (edit these if your proc output changes)
# ============================================================
PROC_NAME = "dbo.FunderProgressSummary"

COL_FUNDER = "Funder"
COL_CALYEAR = "CalendarYear"
COL_TERM = "Term"

COL_PROVIDER = "Provider"
COL_SCHOOL = "School"
COL_EQI = "EQI"

COL_PROG_RATIO = "Students Progressed (Progressed/Total)"
COL_EDIT_RATIO = "Classes Edited (Edited/Total)"
COL_NO_PROGRESS = "Classes with no progress"

# Term totals (repeated on each row by the stored proc)
COL_TOTAL_SCHOOLS = "TotalSchoolsTerm"
COL_TOTAL_STUDENTS = "TotalStudentsTerm"
COL_TOTAL_PROG = "ProgressedStudentsTerm"
COL_TOTAL_EDITED = "EditedStudentsTerm"


# ------------------------------------------------------------
# Pagination helper: keep Provider blocks together (like Month blocks)
# ------------------------------------------------------------
def paginate_provider_blocks(
    df: pd.DataFrame,
    rows_per_page: int = 28,
    provider_col: str = COL_PROVIDER,
) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []

    if provider_col not in df.columns:
        # nothing to block/merge; return as single page
        return [df.reset_index(drop=True)]

    prov_order = df[provider_col].fillna("").drop_duplicates().tolist()

    pages: List[pd.DataFrame] = []
    current_parts: List[pd.DataFrame] = []
    current_n = 0

    for prov in prov_order:
        g = df[df[provider_col].fillna("") == prov].copy()
        g_n = len(g)

        if current_n + g_n <= rows_per_page:
            current_parts.append(g)
            current_n += g_n
            continue

        if current_parts:
            pages.append(pd.concat(current_parts, ignore_index=True))
            current_parts = []
            current_n = 0

        if g_n > rows_per_page:
            for start in range(0, g_n, rows_per_page):
                pages.append(g.iloc[start : start + rows_per_page].reset_index(drop=True))
            continue

        current_parts.append(g)
        current_n = g_n

    if current_parts:
        pages.append(pd.concat(current_parts, ignore_index=True))

    return [p.reset_index(drop=True) for p in pages]


# ------------------------------------------------------------
# Data builder: call stored proc + shape into per-term dataframes
# ------------------------------------------------------------
def _fetch_progress_df(
    conn,
    funder_id: int,
    from_year: int = 2025,
    threshold: float = 0.2,
) -> Tuple[pd.DataFrame, str]:
    sql = text(
        f"""
        EXEC {PROC_NAME}
            @FunderID = :funder_id,
            @threshold = :threshold,
            @FromYear = :from_year
        """
    )

    df = pd.read_sql(
        sql,
        conn,
        params={
            "funder_id": funder_id,
            "threshold": threshold,
            "from_year": from_year,
        },
    )

    if df is None or df.empty:
        return df, ""

    funder_name = ""
    if COL_FUNDER in df.columns:
        vals = df[COL_FUNDER].dropna().unique().tolist()
        funder_name = str(vals[0]) if vals else ""

    # normalise types (safe)
    if COL_CALYEAR in df.columns:
        df[COL_CALYEAR] = pd.to_numeric(df[COL_CALYEAR], errors="coerce").astype("Int64")
    if COL_TERM in df.columns:
        df[COL_TERM] = pd.to_numeric(df[COL_TERM], errors="coerce").astype("Int64")

    return df, funder_name


def _split_by_term(df: pd.DataFrame) -> List[Tuple[int, int, pd.DataFrame]]:
    if df is None or df.empty:
        return []

    if COL_CALYEAR not in df.columns or COL_TERM not in df.columns:
        raise ValueError(f"Expected columns {COL_CALYEAR} and {COL_TERM} in stored proc output.")

    out: List[Tuple[int, int, pd.DataFrame]] = []
    keys = (
        df[[COL_CALYEAR, COL_TERM]]
        .drop_duplicates()
        .sort_values([COL_CALYEAR, COL_TERM], ascending=[True, True])
        .itertuples(index=False, name=None)
    )

    for y, t in keys:
        d = df[(df[COL_CALYEAR] == y) & (df[COL_TERM] == t)].copy()

        # drop meta columns that shouldn't show in table
        for col in [COL_CALYEAR, COL_TERM, COL_FUNDER]:
            if col in d.columns:
                d = d.drop(columns=[col])

        # stable sort
        sort_cols = [c for c in [COL_PROVIDER, COL_SCHOOL] if c in d.columns]
        if sort_cols:
            d = d.sort_values(sort_cols, ascending=True)

        out.append((int(y), int(t), d.reset_index(drop=True)))

    return out


# ------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------
def _parse_ratio_cell(x: Any) -> Tuple[int, int]:
    """
    Parses cells like:
      "12/30"
      "12 / 30"
      "12/30 (40%)"
      "12/30 something"
    Returns (numerator, denominator) or (0,0) if missing.
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0, 0
    s = str(x).strip()
    if not s:
        return 0, 0

    token = None
    for part in s.replace("(", " ").replace(")", " ").split():
        if "/" in part:
            token = part
            break
    if token is None:
        token = s

    cleaned = "".join(ch for ch in token if (ch.isdigit() or ch == "/"))
    if "/" not in cleaned:
        return 0, 0

    left, right = cleaned.split("/", 1)
    try:
        a = int(left) if left else 0
        b = int(right) if right else 0
        return a, b
    except Exception:
        return 0, 0


def _get_term_totals_from_df(df_term: pd.DataFrame) -> Dict[str, int]:
    """
    Uses stored proc term-total columns (repeated on each row) to avoid double counting.
    Falls back gracefully if columns are missing.
    """
    if df_term is None or df_term.empty:
        return {"schools": 0, "students": 0, "progressed": 0, "edited": 0}

    row0 = df_term.iloc[0]

    def _as_int(x) -> int:
        try:
            if pd.isna(x):
                return 0
            return int(float(x))
        except Exception:
            return 0

    return {
        "schools": _as_int(row0.get(COL_TOTAL_SCHOOLS)),
        "students": _as_int(row0.get(COL_TOTAL_STUDENTS)),
        "progressed": _as_int(row0.get(COL_TOTAL_PROG)),
        "edited": _as_int(row0.get(COL_TOTAL_EDITED)),
    }


def _overall_totals_from_terms(term_blocks: List[Tuple[int, int, pd.DataFrame]]) -> Dict[str, int]:
    """
    Overall = sum of term totals across terms (NOT across rows).
    """
    out = {"schools": 0, "students": 0, "progressed": 0, "edited": 0}
    for (_y, _t, df_term) in term_blocks:
        tt = _get_term_totals_from_df(df_term)
        out["schools"] += tt["schools"]
        out["students"] += tt["students"]
        out["progressed"] += tt["progressed"]
        out["edited"] += tt["edited"]
    return out


# ------------------------------------------------------------
# Public PDF builder
# ------------------------------------------------------------
def build_funder_progress_summary_pdf(
    *,
    conn,
    funder_id: int,
    from_year: int,
    threshold: float,
    out_pdf_path: str | Path,
    footer_png: str | Path | None,
    rows_per_page: int = 28,
    dpi: int = 300,
    page_size: str = "A3",
    orientation: str = "portrait",
    fonts_dir: str | Path = "app/static/fonts",
) -> Tuple[Optional["matplotlib.figure.Figure"], Dict[str, Any]]:
    """
    Multi-page PDF: one section per term (new page for each term).
    Provider cells are merged down like Month in your other report.

    Adds:
      - Per-page term summary bar below the table
      - Definition note bar near the bottom
      - On the very last rendered page only: an overall summary (all terms) under the note

    Layout goal:
      Make the gaps consistent:
        table -> term summary == term summary -> note == note -> overall
    """
    family = load_ppmori_fonts(str(fonts_dir))

    df_all, funder_name = _fetch_progress_df(
        conn,
        funder_id=funder_id,
        from_year=from_year,
        threshold=threshold,
    )

    meta: Dict[str, Any] = {
        "funder_name": funder_name,
        "rows": int(0 if df_all is None else len(df_all)),
        "pages": 0,
        "terms": [],
    }

    if df_all is None or df_all.empty:
        return None, meta

    term_blocks = _split_by_term(df_all)
    meta["terms"] = [f"{y}T{t}" for (y, t, _d) in term_blocks]

    # Overall totals across terms
    overall = _overall_totals_from_terms(term_blocks)
    overall_pct = (overall["progressed"] / overall["students"]) if overall["students"] else 0.0
    overall_summary_text = (
        f"Overall Summary (All Terms): Total schools: {overall['schools']}   |   "
        f"Total students: {overall['students']}   |   "
        f"Progressed: {overall['progressed']} ({overall_pct*100:.1f}%)"
    )

    # Precompute last rendered page (fixes empty-term bug)
    render_plan: List[Tuple[int, int, int]] = []
    term_pages_cache: Dict[Tuple[int, int], List[pd.DataFrame]] = {}

    for (y, t, df_term) in term_blocks:
        if df_term is None or df_term.empty:
            continue

        provider_col = COL_PROVIDER if COL_PROVIDER in df_term.columns else None
        pages_tmp = paginate_provider_blocks(
            df_term,
            rows_per_page=rows_per_page,
            provider_col=provider_col or COL_PROVIDER,
        )
        term_pages_cache[(y, t)] = pages_tmp

        for page_idx in range(1, len(pages_tmp) + 1):
            render_plan.append((y, t, page_idx))

    last_key = render_plan[-1] if render_plan else None

    pdf, w, h, _dpi = open_pdf(
        filename=str(out_pdf_path),
        page_size=page_size,
        orientation=orientation,
        dpi=dpi,
    )

    preview_fig = None
    page_count = 0

    table_columns = [
        {"key": COL_PROVIDER, "label": "Provider", "width_frac": 0.22, "align": "left", "wrap": True, "max_lines": 3},
        {"key": COL_SCHOOL, "label": "School", "width_frac": 0.22, "align": "left", "wrap": True, "max_lines": 3},
        {"key": COL_EQI, "label": "EQI", "width_frac": 0.06, "align": "center", "wrap": True},
        {
            "key": COL_PROG_RATIO,
            "label": "Students Count\n(Progressed/Total)",
            "width_frac": 0.18,
            "align": "center",
        },
        {
            "key": COL_EDIT_RATIO,
            "label": "Classes Count\n(Edited/Total)",
            "width_frac": 0.18,
            "align": "center",
        },
        {"key": COL_NO_PROGRESS, "label": "Classes With\nNo progress", "width_frac": 0.18, "align": "center"},
    ]

    def _filter_table_columns(df: pd.DataFrame) -> List[Dict[str, Any]]:
        keys = set(df.columns)
        return [c for c in table_columns if c["key"] in keys]

    def row_highlight(row: pd.Series, r: int) -> Optional[Tuple[str, str]]:
        try:
            if COL_NO_PROGRESS in row and pd.notna(row[COL_NO_PROGRESS]):
                if int(row[COL_NO_PROGRESS]) > 0:
                    return "#f4f6ff", "#111111"
        except Exception:
            pass
        return None

    # ---- Consistent geometry for bottom section (SINGLE source of truth)
    # These are AXES FRACTIONS (0..1)
    TABLE_X = 0.02
    TABLE_W = 0.96
    TABLE_Y = 0.12
    TABLE_H = 0.80

    BAR_W = 0.96
    GAP = 0.010  # ✅ this controls ALL the gaps

    BAR_H_TERM = 0.022
    BAR_H_NOTE = 0.025
    BAR_H_OVER = 0.022

    for (year, term, df_term) in term_blocks:
        if df_term is None or df_term.empty:
            continue

        pages = term_pages_cache.get((year, term))
        if pages is None:
            provider_col = COL_PROVIDER if COL_PROVIDER in df_term.columns else None
            pages = paginate_provider_blocks(
                df_term,
                rows_per_page=rows_per_page,
                provider_col=provider_col or COL_PROVIDER,
            )

        tt = _get_term_totals_from_df(df_term)
        pct = (tt["progressed"] / tt["students"]) if tt["students"] else 0.0

        term_summary_text = (
            f"Term Summary: Total schools: {tt['schools']}   |   "
            f"Total students: {tt['students']}   |   "
            f"Progressed: {tt['progressed']} ({pct*100:.1f}%)"
        )

        provider_col = COL_PROVIDER if COL_PROVIDER in df_term.columns else None

        for term_page_idx, df_page in enumerate(pages, start=1):
            page_count += 1
            is_last_rendered_page = (last_key == (year, term, term_page_idx))

            fig, ax = new_page(w, h, dpi)

            # ---- Header bar
            header_poly = rounded_rect_polygon(
                cx=0.5,
                cy=0.955,
                width=0.96,
                height=0.055,
                ratio=0.45,
                corners_round=[1, 3],
                n_arc=64,
            )
            ax.add_patch(
                mpatches.Polygon(
                    list(header_poly.exterior.coords),
                    closed=True,
                    facecolor="#1a427d",
                    edgecolor="#1a427d",
                    linewidth=1.5,
                    transform=ax.transAxes,
                )
            )

            title = f"{funder_name} - {year} Term {term}"
            if len(pages) > 1:
                title = f"{title} (Page {term_page_idx} of {len(pages)})"

            draw_text_in_polygon(
                ax,
                poly=header_poly,
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

            # ---- Table
            cols = _filter_table_columns(df_page)

            merge_first = bool(provider_col) and (provider_col in df_page.columns)
            merge_key = provider_col or ""

            draw_dataframe_table_v2(
                ax,
                df=df_page,
                x=TABLE_X,
                y=TABLE_Y,
                width=TABLE_W,
                height=TABLE_H,
                header_height_frac=0.042,
                columns=cols,
                base_row_facecolor="#ffffff",
                row_color_fn=row_highlight,
                merge_first_col=merge_first,
                merge_key=merge_key,
                shift=False,
            )

            # ============================================================
            # Bottom section with CONSISTENT gaps
            # table -> term summary == term summary -> note == note -> overall
            # ============================================================
            table_bottom_y = TABLE_Y

            # Term summary sits GAP below table
            term_top = table_bottom_y - GAP
            term_cy = term_top - (BAR_H_TERM / 2)

            # Note sits GAP below term summary
            note_top = (term_cy - BAR_H_TERM / 2) - GAP
            note_cy = note_top - (BAR_H_NOTE / 2)

            # Overall sits GAP below note
            over_top = (note_cy - BAR_H_NOTE / 2) - GAP
            over_cy = over_top - (BAR_H_OVER / 2)

            # ---- Term summary bar
            term_sum_poly = rounded_rect_polygon(
                cx=0.5,
                cy=term_cy,
                width=BAR_W,
                height=BAR_H_TERM,
                ratio=0.45,
                corners_round=[1, 3],
                n_arc=64,
            )
            ax.add_patch(
                mpatches.Polygon(
                    list(term_sum_poly.exterior.coords),
                    closed=True,
                    facecolor="#eef2ff",
                    edgecolor="#1a427d",
                    linewidth=1.2,
                    transform=ax.transAxes,
                )
            )
            draw_text_in_polygon(
                ax,
                poly=term_sum_poly,
                text=term_summary_text,
                fontfamily=family,
                fontsize=12,
                fontweight="semibold",
                color="#1a427d",
                pad_frac=0.06,
                wrap=False,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=1,
            )

            # ---- Definition note bar
            note_poly = rounded_rect_polygon(
                cx=0.5,
                cy=note_cy,
                width=BAR_W,
                height=BAR_H_NOTE,
                ratio=0.45,
                corners_round=[1, 3],
                n_arc=64,
            )
            ax.add_patch(
                mpatches.Polygon(
                    list(note_poly.exterior.coords),
                    closed=True,
                    facecolor="#1a427d",
                    edgecolor="#1a427d",
                    linewidth=1,
                    transform=ax.transAxes,
                )
            )
            note_text = (
                "For a student to progress they need to have at least one new competency marked as achieved. "
                f"For a class to be edited {threshold * 100:.0f}% of students must be marked as complete. "
                "For a class to have no progress no students have had a new competency marked as achieved."
            )
            draw_text_in_polygon(
                ax,
                poly=note_poly,
                text=note_text,
                fontfamily=family,
                fontsize=10,
                fontweight="semibold",
                color="#ffffff",
                pad_frac=0.10,
                wrap=True,
                autoshrink=True,
                clip_to_polygon=True,
                max_lines=2,
            )

            # ---- Overall summary (LAST RENDERED PAGE ONLY)
            if is_last_rendered_page:
                overall_poly = rounded_rect_polygon(
                    cx=0.5,
                    cy=over_cy,
                    width=BAR_W,
                    height=BAR_H_OVER,
                    ratio=0.45,
                    corners_round=[1, 3],
                    n_arc=64,
                )
                ax.add_patch(
                    mpatches.Polygon(
                        list(overall_poly.exterior.coords),
                        closed=True,
                        facecolor="#eef2ff",
                        edgecolor="#1a427d",
                        linewidth=1.2,
                        transform=ax.transAxes,
                    )
                )
                draw_text_in_polygon(
                    ax,
                    poly=overall_poly,
                    text=overall_summary_text,
                    fontfamily=family,
                    fontsize=12,
                    fontweight="semibold",
                    color="#1a427d",
                    pad_frac=0.06,
                    wrap=False,
                    autoshrink=True,
                    clip_to_polygon=True,
                    max_lines=1,
                )

            save_page(
                pdf,
                fig,
                footer_png=str(footer_png) if footer_png else None,
                width_in=w,
                height_in=h,
                footer_bottom_margin_frac=0.0,
                footer_max_height_frac=0.20,
            )

            if preview_fig is None:
                preview_fig = fig
            else:
                import matplotlib.pyplot as plt
                plt.close(fig)

    close_pdf(pdf)
    meta["pages"] = page_count
    return preview_fig, meta


# ------------------------------------------------------------
# Optional CLI runner
# ------------------------------------------------------------
if __name__ == "__main__":
    import os
    from sqlalchemy import create_engine
    from dotenv import load_dotenv

    load_dotenv()

    FUNDER_ID = 6
    FROM_YEAR = 2025
    THRESHOLD = 0.1

    OUT_DIR = Path("out")
    OUT_DIR.mkdir(exist_ok=True)

    engine = create_engine(os.getenv("db_url"))
    footer = Path(__file__).parent / "static" / "footer.png"
    pdf_out = OUT_DIR / f"WSFL_FunderProgressSummary_{FUNDER_ID}_{FROM_YEAR}.pdf"

    with engine.begin() as conn:
        preview, meta = build_funder_progress_summary_pdf(
            conn=conn,
            funder_id=FUNDER_ID,
            from_year=FROM_YEAR,
            threshold=THRESHOLD,
            out_pdf_path=pdf_out,
            footer_png=footer if footer.exists() else None,
            rows_per_page=28,
        )

    print(f"✅ PDF written: {pdf_out}")
    print(f"📄 Pages: {meta['pages']} | Rows: {meta['rows']} | Terms: {meta['terms']}")

    if preview:
        preview_png = OUT_DIR / f"WSFL_FunderProgressSummary_{FUNDER_ID}_{FROM_YEAR}_preview.png"
        preview.savefig(preview_png, dpi=200)
        print(f"🖼 Preview written: {preview_png}")
