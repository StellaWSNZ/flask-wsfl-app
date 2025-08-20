# one_bar_one_line.py
# A4 portrait: per-YearGroup sections, bars = subject YTD (provider/funder), dashed = WSNZ target

import os, io, base64, textwrap, argparse
from typing import Mapping, Optional, Union, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError, DBAPIError
from dotenv import load_dotenv
from pathlib import Path

A4_PORTRAIT = (8.27, 11.69)

load_dotenv()  # expects DB_URL; optional name lookup envs (see get_subject_name())
from matplotlib import font_manager as fm

def use_ppmori(font_dir="app/static/fonts"):
    # Register all .otf/.ttf in the folder
    font_paths = list(Path(font_dir).glob("*.otf")) + list(Path(font_dir).glob("*.ttf"))
    for p in font_paths:
        fm.fontManager.addfont(str(p))

    # Pick a file to read the canonical *family name* (often "PP Mori")
    if font_paths:
        fam_name = fm.FontProperties(fname=str(font_paths[0])).get_name()
    else:
        raise FileNotFoundError(f"No .otf/.ttf files found in {font_dir}")

    # Make it the default font
    plt.rcParams["font.family"] = [fam_name]      # e.g. ["PP Mori"]
    plt.rcParams["font.sans-serif"] = [fam_name]  # helps when sans-serif is referenced

    # Embed TrueType in PDFs (avoids fallback to Type 3)
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"]  = 42

    print(f"✅ Using font family: {fam_name}")
# ---------- canonical keys ----------
TARGET_KEY   = "wsnz target"
NATIONAL_KEY = "national rate (ytd)"

def _canon_factory(mode: str):
    mode = (mode or "provider").strip().lower()
    if mode not in {"provider", "funder", "school"}:
        raise ValueError("mode must be 'provider' or 'funder' or 'school'")
    SUBJECT_KEY = f"{mode} rate (ytd)"
    PSC_KEY     = f"{mode} student count (ytd)"
    alias_map = {
        f"{mode} rate ytd": SUBJECT_KEY, f"{mode} rate (ytd)": SUBJECT_KEY,
        "provider rate ytd": "provider rate (ytd)", "provider rate (ytd)": "provider rate (ytd)",
        "funder rate ytd": "funder rate (ytd)",   "funder rate (ytd)": "funder rate (ytd)",
        "wsnz target": TARGET_KEY, "target": TARGET_KEY,
        "national rate ytd": NATIONAL_KEY, "national rate (ytd)": NATIONAL_KEY,
        "provider student count ytd": PSC_KEY, "provider student count (ytd)": PSC_KEY,
        "funder student count ytd": PSC_KEY,   "funder student count (ytd)": PSC_KEY,
    }
    def _canon(s: str) -> str:
        s = (s or "").strip().lower().replace("_", " ").replace("-", " ")
        s = " ".join(s.split())
        return alias_map.get(s, s)
    return _canon, SUBJECT_KEY, PSC_KEY

# ---------- utils ----------
def _wrap(txt: str, width: int = 58) -> str:
    return "\n".join(textwrap.wrap(str(txt), width=width))

def _normalize_rows(data: Union[Sequence[Mapping], pd.DataFrame], canon_fn) -> list[dict]:
    rows = data.to_dict(orient="records") if isinstance(data, pd.DataFrame) else [dict(r) for r in data]
    clean = []
    for r in rows:
        if not (r.get("YearGroupDesc") and r.get("CompetencyDesc") and r.get("ResultType") and r.get("Rate") is not None):
            continue
        try:
            r["Rate"] = float(r["Rate"])
        except Exception:
            continue
        r["_CanonResultType"] = canon_fn(r.get("ResultType"))
        clean.append(r)
    return clean
from matplotlib.patches import Rectangle, FancyBboxPatch

def _draw_legend(
    ax,
    label_subject="Provider",
    *,
    cx=0.5, cy=0.08,        # position
    fs=8,                   # smaller font
    bar_color="#2EBDC2",
    target_color="#2E6F8A",
    show_bg=True
):
    # compact layout (axes coords)
    bg_w, bg_h = 0.52, 0.036
    pad = 0.010
    sw_w, sw_h = 0.035, 0.014
    gap = 0.010

    # soft background, NO border line
    if show_bg:
        ax.add_patch(plt.Rectangle(
            (cx - bg_w/2, cy - bg_h/2), bg_w, bg_h,
            facecolor="white", edgecolor="none", alpha=0.9, zorder=0
        ))

    x = cx - bg_w/2 + pad
    y0 = cy

    # bar swatch + label
    ax.add_patch(plt.Rectangle((x, y0 - sw_h/2), sw_w, sw_h,
                               facecolor=bar_color, edgecolor="none"))
    x += sw_w + gap
    ax.text(x, y0, f"{label_subject} Rate (YTD)", ha="left", va="center", fontsize=fs)

    # second item (leave some room so the two labels don't collide)
    x = cx - bg_w/2 + pad + sw_w + gap + 0.30
    ax.plot([x, x + sw_w], [y0, y0], linestyle=(0, (4, 4)), linewidth=1.6, color=target_color)
    x += sw_w + gap
    ax.text(x, y0, "WSNZ Target", ha="left", va="center", fontsize=fs, color=target_color)
# ---------- chart ----------
def provider_portrait_with_target(
    data: Union[Sequence[Mapping], pd.DataFrame],
    term: int,
    year: int,
    *,
    mode: str = "provider",
    subject_name: Optional[str] = None,
    title: Optional[str] = None,
    bar_color: str = '#2EBDC2',
    target_color: str = "#2E6F8A",
    fallback_to_national: bool = False,
    debug: bool = False,
) -> plt.Figure:
    from itertools import groupby, groupby as _gb
    canon_fn, SUBJECT_KEY, PSC_KEY = _canon_factory(mode)
    use_ppmori("app/static/fonts")

    # ---------- Normalize + sort ----------
    rows = _normalize_rows(data, canon_fn)
    rows.sort(key=lambda r: (str(r.get("YearGroupDesc")), str(r.get("CompetencyDesc")), r["_CanonResultType"]))

    # ---------- Figure ----------
    fig = plt.figure(figsize=A4_PORTRAIT)
    ax = fig.add_subplot(111); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")

    # Title
    base_title_fs = 14
    subject_label = subject_name or mode.title()
    default_title = f"{subject_label} YTD vs Target • Term {term}, {year}"
    ttl = title or default_title
    TITLE_Y = 0.985
    ax.text(0.5, TITLE_Y, ttl, ha="center", va="top", fontsize=base_title_fs, weight="bold")

    # ---------- Base layout ----------
    BASE = {
        "LEFT_MARGIN": 0.02,
        "BARS_LEFT_X": 0.50,     # true 50/50 split
        "RIGHT_MARGIN": 0.06,    # room for % labels
        "LABEL_PAD": 0.012,

        "BAR_H": 0.08, "BAR_GAP": 0.015, "ITEM_GAP": 0,
        "GROUP_TOP_PAD": 0.030,  # subtitle -> first bar gap (axis units)
        "GROUP_BOT_PAD": 0.00,  # after group
        "SUBTITLE_GAP": 0.020,   # around subtitle itself

        "TOP_MARGIN": 0.05, "BOTTOM_MARGIN": 0.0,
        "LABEL_FS": 10.0, "TARGET_FS": 7.5, "SUBTITLE_FS": 11.5,
    }

    # Estimate spacing scale as before (kept for font/bounds tuning)
    # NB: this is only advisory now; final placement uses a row grid.
    # Count estimated total rows and pads in axis units
    # (same math as your original for continuity)
    # First, count rows per YearGroup
    rows_by_yg = {}
    for yg, yg_iter in groupby(rows, key=lambda r: str(r.get("YearGroupDesc"))):
        rows_by_yg[yg] = len({str(r.get("CompetencyDesc")) for r in yg_iter})
    total_groups = len(rows_by_yg)
    total_rows = sum(rows_by_yg.values())

    row_spacing_est = max(2*BASE["BAR_H"] + BASE["BAR_GAP"], BASE["ITEM_GAP"])
    group_block_est = BASE["SUBTITLE_GAP"] + BASE["GROUP_TOP_PAD"] + BASE["GROUP_BOT_PAD"]
    needed_est = total_rows * row_spacing_est + total_groups * group_block_est
    available_est = 1.0 - BASE["BOTTOM_MARGIN"] - BASE["TOP_MARGIN"] - (1.0 - TITLE_Y)
    scale = 1.0 if needed_est <= available_est else max(0.55, available_est/needed_est)

    # Apply scaled sizes
    BARS_LEFT_X  = BASE["BARS_LEFT_X"]
    RIGHT_MARGIN = BASE["RIGHT_MARGIN"] * (0.9*scale + 0.1)
    LABEL_PAD    = BASE["LABEL_PAD"] * (0.9*scale + 0.1)
    BAR_MAX_W    = 1.0 - RIGHT_MARGIN - BARS_LEFT_X

    BAR_H        = BASE["BAR_H"]*scale
    BAR_GAP      = BASE["BAR_GAP"]*scale
    ITEM_GAP     = BASE["ITEM_GAP"]*scale
    GROUP_TOP_PAD = BASE["GROUP_TOP_PAD"]*scale
    GROUP_BOT_PAD = BASE["GROUP_BOT_PAD"]*scale
    SUBTITLE_GAP  = BASE["SUBTITLE_GAP"]*scale

    label_fs   = max(8.0, BASE["LABEL_FS"]*(0.9*scale+0.1))
    target_fs  = max(6.0, BASE["TARGET_FS"]*(0.9*scale+0.1))
    subtitle_fs= max(9.0, BASE["SUBTITLE_FS"]*(0.9*scale+0.1))

    LEFT_LABEL_X = BARS_LEFT_X - LABEL_PAD

    # Dynamic wrap width from label column width (pixels)
    fig.canvas.draw()
    px_w = fig.get_size_inches()[0] * fig.dpi
    label_region_px = max(10, (LEFT_LABEL_X - BASE["LEFT_MARGIN"]) * px_w)
    avg_char_px = 0.6 * label_fs * (fig.dpi / 72.0)
    wrap_width = max(20, int(label_region_px / max(4.0, avg_char_px)))

    if debug:
        ax.axvline(BARS_LEFT_X, 0, 1, linewidth=0.4)

    # ---------- Build groups (compute values + ordering once) ----------
    groups = []  # list of dicts: {"yg": str, "items": [(comp, val)], "target": float|None}
    for yg, yg_iter in _gb(rows, key=lambda r: str(r.get("YearGroupDesc"))):
        yg_rows = list(yg_iter)
        group_has_subject_ytd = any(r["_CanonResultType"] == SUBJECT_KEY for r in yg_rows)
        target_val = None
        comp_rate = {}

        for comp, comp_iter in _gb(yg_rows, key=lambda r: str(r.get("CompetencyDesc"))):
            rs = list(comp_iter)
            vals = {r["_CanonResultType"]: float(r["Rate"]) for r in rs}
            if target_val is None and TARGET_KEY in vals:
                target_val = float(vals[TARGET_KEY])

            val = None
            if SUBJECT_KEY in vals:
                val = float(vals[SUBJECT_KEY])
            elif any(k.endswith("student count (ytd)") for k in vals):
                val = 0.0
            elif not group_has_subject_ytd:
                val = 0.0
            elif fallback_to_national and NATIONAL_KEY in vals:
                val = float(vals[NATIONAL_KEY])

            if val is not None:
                comp_rate[comp] = max(0.0, min(1.0, val))

        ordered_items = sorted(comp_rate.items(), key=lambda kv: kv[1], reverse=True)
        groups.append({"yg": yg, "items": ordered_items, "target": target_val})

    # ---------- ROW GRID (Option B) ----------
    # Express paddings in "row units" by comparing to a baseline step
    baseline_step = max(2*BAR_H + BAR_GAP, ITEM_GAP)  # axis-units guess per row
    gap_rows      = max(0, int(round(GROUP_TOP_PAD / max(1e-6, baseline_step))))
    print(gap_rows)
    between_rows  = max(0, int(round(GROUP_BOT_PAD / max(1e-6, baseline_step))))
    # subtitle itself takes 1 row; each competency 1 row
    subtitle_rows = 1

    # total rows (don't add trailing between_rows after the last group)
    total_row_slots = 0
    for i, g in enumerate(groups):
        total_row_slots += subtitle_rows + gap_rows + len(g["items"])
        if i < len(groups) - 1:
            total_row_slots += between_rows
    LEGEND_CY    = 0.055      # lower the legend (was 0.08)
    LEGEND_BG_H  = 0.032      # legend background height (matches _draw_legend)
    LEGEND_CLEAR = 0.006 
    # vertical range: from below title down to just above legend
    top_y    = 1.0 - BASE["TOP_MARGIN"] - (1.0 - TITLE_Y)
    legend_top = LEGEND_CY + LEGEND_BG_H/2
    bottom_y   = max(legend_top + LEGEND_CLEAR, 0.02)
    # Make sure bars don't clip: shrink BAR_H if needed to fit inside row step
    if total_row_slots < 2:
        centers = np.array([top_y])
    else:
        centers = np.linspace(top_y, bottom_y, total_row_slots)

    row_step = abs(centers[0] - centers[-1]) / max(1, (len(centers)-1))
    # keep bars within row band
    BAR_H = min(BAR_H, 0.8 * row_step)

    # ---------- Render using the grid ----------
    idx = 0
    for gi, g in enumerate(groups):
        if idx >= len(centers):
            break

        # subtitle
        ax.text(0.5, centers[idx] - SUBTITLE_GAP/2, f"Years {g['yg']}",
                ha="center", va="center", fontsize=subtitle_fs, weight="bold")
        idx += subtitle_rows  # consume subtitle row(s)

        # reserved gap between subtitle and first bar
        idx += gap_rows

        first_center = last_center = None
        # bars
        for comp, val in g["items"]:
            if idx >= len(centers):
                break

            y = centers[idx]
            # competency label (right-aligned to bar start)
            ax.text(
                LEFT_LABEL_X, y,
                _wrap(comp, width=wrap_width),
                ha="right", va="center",
                multialignment="right",
                fontsize=label_fs
            )
            # bar + value
            w = val * BAR_MAX_W
            ax.add_patch(plt.Rectangle((BARS_LEFT_X, y - BAR_H/2), w, BAR_H,
                                       facecolor=bar_color, edgecolor="none"))
            ax.text(BARS_LEFT_X + w + 0.008, y, f"{val*100:.1f}%",
                    ha="left", va="center", fontsize=label_fs)

            first_center = y if first_center is None else first_center
            last_center = y
            idx += 1

        # target line for the group
        if g["target"] is not None and first_center is not None and last_center is not None:
            x_t = BARS_LEFT_X + max(0.0, min(1.0, g["target"])) * BAR_MAX_W
            ax.plot([x_t, x_t], [first_center + BAR_H/2, last_center - BAR_H/2],
                    linestyle=(0, (4, 4))),  # default color then:
            ax.lines[-1].set_color(target_color)
            ax.lines[-1].set_linewidth(1.8)
            ax.text(x_t, (last_center - BAR_H/2) - 0.004,
                    f"WSNZ Target {round(g['target']*100)}%",
                    ha="center", va="top", fontsize=target_fs, color=target_color, fontweight="bold")

        # between-group spacing (except after last group)
        if gi < len(groups) - 1:
            idx += between_rows

    # Legend (smaller, no border background as per your latest version)
    legend_fs = max(8, int(8 * (0.9*scale + 0.1)))
    _draw_legend(ax, label_subject=subject_label, cx=0.5, cy=LEGEND_CY, fs=legend_fs,
                 bar_color=bar_color, target_color=target_color, show_bg=True)

    return fig


# ---------- DB ----------
def build_engine():
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("Missing DB_URL in .env")
    return create_engine(db_url, pool_pre_ping=True, fast_executemany=True)

def get_rates(engine, year: int, term: int, subject_id: int, mode: str) -> pd.DataFrame:
    mode = mode.strip().lower()
    proc = "GetProviderNationalRates" if mode == "provider" else "GetFunderNationalRates"
    sql_stmt = text(f"EXEC {proc} :year, :term, :id, :dummy")
    with engine.begin() as conn:
        df = pd.read_sql_query(sql_stmt, conn, params={"year": year, "term": term, "id": subject_id, "dummy": None})
    return df

def _first_nonempty(*vals):
    for v in vals:
        if v and str(v).strip():
            return str(v).strip()
    return None

def get_subject_name(engine, mode: str, subject_id: int, df_from_proc: Optional[pd.DataFrame] = None) -> Optional[str]:
    """
    Attempts to get Provider/Funder name using:
      1) Columns in df_from_proc (ProviderName/FunderName/SubjectName)
      2) Name procs: GetProviderName / GetFunderName (single NVARCHAR result as 'Name')
      3) Direct table query (configurable via .env):
         PROVIDER_TABLE / FUNDER_TABLE, *_ID_COL, *_NAME_COL
    """
    mode = mode.strip().lower()
    # (1) Try columns in DF
    if isinstance(df_from_proc, pd.DataFrame):
        col = "ProviderName" if mode == "provider" else "FunderName"
        name = _first_nonempty(
            df_from_proc.get(col, pd.Series(dtype=object)).iloc[0] if col in df_from_proc.columns and len(df_from_proc) else None,
            df_from_proc.get("SubjectName", pd.Series(dtype=object)).iloc[0] if "SubjectName" in df_from_proc.columns and len(df_from_proc) else None,
        )
        if name: return name

    with engine.begin() as conn:
        # (2) Try name proc
        try:
            proc = "GetProviderName" if mode == "provider" else "GetFunderName"
            res = conn.execute(text(f"EXEC {proc} :id"), {"id": subject_id})
            row = res.fetchone()
            if row:
                if hasattr(row, "_mapping"):
                    m = row._mapping
                    cand = _first_nonempty(m.get("Name"), m.get("ProviderName"), m.get("FunderName"))
                    if cand: return cand
                return str(row[0])
        except (ProgrammingError, DBAPIError):
            pass  # proc may not exist

        # (3) Fallback to table lookup
        table   = os.getenv("PROVIDER_TABLE" if mode=="provider" else "FUNDER_TABLE")
        id_col  = os.getenv("PROVIDER_ID_COL" if mode=="provider" else "FUNDER_ID_COL", "ID")
        namecol = os.getenv("PROVIDER_NAME_COL" if mode=="provider" else "FUNDER_NAME_COL", "Name")
        if table:
            try:
                row = conn.execute(text(f"SELECT TOP 1 {namecol} AS Name FROM {table} WHERE {id_col} = :id"), {"id": subject_id}).fetchone()
                if row:
                    if hasattr(row, "_mapping") and "Name" in row._mapping:
                        return str(row._mapping["Name"])
                    return str(row[0])
            except (ProgrammingError, DBAPIError):
                pass

    return None  # give up

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Render YTD vs Target portrait chart from DB.")
    ap.add_argument("--mode", choices=["provider", "funder"], default="provider")
    ap.add_argument("--subject-id", type=int, required=True)
    ap.add_argument("--subject-name", type=str, default=None, help="(Optional) override; otherwise fetched from DB")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--term", type=int, default=3)
    ap.add_argument("--title", type=str, default=None)
    ap.add_argument("--outfile", type=str, default="one_bar_one_line")
    ap.add_argument("--fallback-to-national", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    engine = build_engine()
    df = get_rates(engine, args.year, args.term, args.subject_id, args.mode)
    print(f"📥 Loaded {len(df)} rows from DB")

    subject_name = args.subject_name or get_subject_name(engine, args.mode, args.subject_id, df)
    if not subject_name:
        subject_name = f"{args.mode.title()} {args.subject_id}"
    else:
        print(f"ℹ️ Using {args.mode} name from DB: {subject_name}")

    fig = provider_portrait_with_target(
        df,
        term=args.term,
        year=args.year,
        mode=args.mode,
        subject_name=subject_name,
        title=args.title,
        fallback_to_national=args.fallback_to_national,
        debug=args.debug,
    )

    png_path = f"{args.outfile}.png"
    pdf_path = f"{args.outfile}.pdf"
    fig.savefig(png_path, dpi=200, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    print(f"✅ Wrote {png_path} and {pdf_path}")

if __name__ == "__main__":
    main()
