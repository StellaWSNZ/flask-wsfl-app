# one_bar_one_line.py
# A4 portrait: per-YearGroup sections, bars = subject (YTD or LY), dashed = WSNZ target

"""
one_bar_one_line.py
====================

Generates an A4-portrait chart showing competency rates vs the WSNZ target,
grouped by YearGroup and Competency. Bars represent a chosen rate series
(e.g. YTD), and a vertical dashed line shows the WSNZ target for each group.

Data can come from:
  • SQL Server stored procedures (GetProviderNationalRates /
    GetFunderNationalRatesSmart)
  • A CSV passed via --csv (bypasses DB)

Output files:
  <outfile>.png
  <outfile>.pdf

---------------------------------------------------------------------------
REQUIRED INPUT COLUMNS (CSV or DB rows)
---------------------------------------------------------------------------
YearGroupDesc   – string, e.g. "0–2", "3–4"
CompetencyDesc  – competency label text
ResultType      – e.g. "Provider Rate (YTD)", "WSNZ Target", "National Rate (YTD)"
Rate            – proportion 0–1 (e.g. 0.73 = 73%)

Any row missing these is ignored.

ResultType values are normalised automatically (case-insensitive, space/underscore/
hyphen tolerant). Student-count rows (e.g. "Provider Student Count (YTD)") are treated
as 0% for plotting so competencies still appear even if the rate is missing.

---------------------------------------------------------------------------
COMMAND-LINE ARGUMENTS
---------------------------------------------------------------------------

--mode               provider | funder | national
                     Selects which stored procedure and which canonical rate
                     labels are used.

--subject-id         Integer provider/funder ID for DB mode.
                     Not required for --mode national.

--csv PATH           Load data from a CSV instead of DB. If set, no DB access
                     happens and --subject-id is ignored.

--subject-name STR   Overrides display name used in the chart title.
                     If omitted, the script attempts to derive the name
                     from DB → stored procs → fallback table lookup.

--year INT           Calendar year (default: 2025)

--term INT           School term (default: 2)

--title STR          Optional custom title. If omitted, a default such as
                     "CLM vs Target • Term 2, 2025" is generated.

--outfile STR        Base filename (default: "one_bar_one_line")
                     Produces <outfile>.png and <outfile>.pdf

--fallback-to-national
                     If set, uses national rates as a fallback when provider/
                     funder rates are missing.

--debug              Draws debug layout lines (used during development).

--bar-series ytd|ly  Which rate series to use for the bars.
                     NOTE: LY is currently treated the same as YTD in provider/
                     funder/national modes.

---------------------------------------------------------------------------
EXAMPLE CALLS
---------------------------------------------------------------------------

# 1. NATIONAL YTD chart (Term 2, 2025)
python one_bar_one_line.py \
    --mode national \
    --year 2025 \
    --term 2 \
    --bar-series ytd \
    --outfile National_LastYear_24_25

# 2. FUNDER chart (Aktive example: funder ID 101)
python one_bar_one_line.py \
    --mode funder \
    --subject-id 101 \
    --year 2025 \
    --term 2 \
    --bar-series ytd \
    --outfile Aktive_T2_2025

# 3. PROVIDER chart with national fallback enabled
python one_bar_one_line.py \
    --mode provider \
    --subject-id 42 \
    --year 2025 \
    --term 1 \
    --bar-series ytd \
    --fallback-to-national \
    --outfile Provider42_T1_2025

# 4. CSV-DRIVEN chart (no DB access)
python one_bar_one_line.py \
    --csv provider_rates.csv \
    --subject-name "CLM (All Funders)" \
    --year 2025 \
    --term 2 \
    --bar-series ytd \
    --outfile CLM_AllFunders_T2_2025

---------------------------------------------------------------------------
FONT NOTES
---------------------------------------------------------------------------
The script loads all .otf/.ttf fonts from app/static/fonts and uses the first
one it finds (typically PP Mori). If the folder is empty, it raises an error.

---------------------------------------------------------------------------
TARGET LOGIC
---------------------------------------------------------------------------
• Each YearGroup uses its own WSNZ Target if present in data.
• If missing, the script defaults to 85%.
• Bars always reflect the chosen canonical series (YTD by default).
• National fallback may provide a bar value where provider/funder rates
  are absent.

---------------------------------------------------------------------------
END OF HEADER
---------------------------------------------------------------------------
"""


import datetime
import os, argparse, textwrap
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
from matplotlib import font_manager as fm

A4_PORTRAIT = (8.27, 11.69)
load_dotenv()  # expects DB_URL

# ---------- font ----------
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
    plt.rcParams["ps.fonttype"]  = 42
    print(f"✅ Using font family: {fam_name}")

# ---------- canonical keys ----------
TARGET_KEY   = "wsnz target"
NATIONAL_KEY = "national rate (ytd)"
def _canon_factory(mode: str):
    mode = (mode or "provider").strip().lower()
    if mode not in {"provider", "funder", "school", "national", "region"}:
        raise ValueError("mode must be 'provider' or 'funder' or 'school' or 'national' or 'region'")

    if mode == "national":
        rate_ytd = "national rate (ytd)"
        rate_ly  = "national rate (ly)"
        psc_ytd  = "national student count (ytd)"
    else:
        rate_ytd = f"{mode} rate (ytd)"
        rate_ly  = f"{mode} rate (ly)"
        psc_ytd  = f"{mode} student count (ytd)"

    alias_map = {
        # generic mode-specific
        f"{mode} rate ytd": rate_ytd,
        f"{mode} rate (ytd)": rate_ytd,
        f"{mode} rate ly":  rate_ly,
        f"{mode} rate (ly)":  rate_ly,

        # explicit provider/funder aliases (kept for backwards compat)
        "provider rate ytd": "provider rate (ytd)",
        "provider rate ly":  "provider rate (ly)",
        "funder rate ytd":   "funder rate (ytd)",
        "funder rate ly":    "funder rate (ly)",

        # ✅ region aliases
        "region rate ytd":   "region rate (ytd)",
        "region rate (ytd)": "region rate (ytd)",
        "regional council rate (YTD)": "region rate (ytd)",
        "region rate ly":    "region rate (ly)",
        "region rate (ly)":  "region rate (ly)",
        "region student count ytd": "region student count (ytd)",
        "region student count (ytd)": "region student count (ytd)",

        # national aliases
        "national rate ytd": "national rate (ytd)",
        "national rate (ytd)": "national rate (ytd)",
        "national rate ly":  "national rate (ly)",
        "national rate (ly)": "national rate (ly)",

        # target + national canonical forms
        "wsnz target": TARGET_KEY,
        "target": TARGET_KEY,
        "national rate ytd": NATIONAL_KEY,
        "national rate (ytd)": NATIONAL_KEY,

        # student counts
        "provider student count ytd": "provider student count (ytd)",
        "provider student count (ytd)": "provider student count (ytd)",
        "funder student count ytd":   "funder student count (ytd)",
        "funder student count (ytd)": "funder student count (ytd)",
        "national student count ytd": "national student count (ytd)",
        "national student count (ytd)": "national student count (ytd)",
    }

    def _canon(s: str) -> str:
        s = (s or "").strip().lower().replace("_", " ").replace("-", " ")
        s = " ".join(s.split())
        return alias_map.get(s, s)

    return _canon, rate_ytd, rate_ly, psc_ytd

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

def _draw_legend(
    ax,
    label_subject="Provider",
    *,
    cx=0.5, cy=0.08,
    fs=8,
    bar_color="#2EBDC2",
    target_color="#2E6F8A",
    show_bg=True
):
    bg_w, bg_h = 0.52, 0.036
    pad = 0.010
    sw_w, sw_h = 0.035, 0.014
    gap = 0.010

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
    ax.text(x, y0, label_subject, ha="left", va="center", fontsize=fs)

    # second item
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
    region_name: Optional[str] = None,   # ✅ NEW
    title: Optional[str] = None,
    bar_color: str = "#2EBDC2",
    target_color: str = "#2E6F8A",
    fallback_to_national: bool = False,
    debug: bool = False,
    bar_series: str = "ly",
    caption: str = None,
) -> plt.Figure:
    from itertools import groupby, groupby as _gb
    canon_fn, RATE_YTD_KEY, RATE_LY_KEY, PSC_KEY = _canon_factory(mode)
    use_ppmori("app/static/fonts")

    # pick the bar series key + legend label
    bar_series = (bar_series or "ly").lower()
    if mode in ("provider", "funder", "school", "national") and bar_series == "ly":
        # treat LY as "use latest YTD" for these modes
        bar_series = "ytd"
    BAR_KEY = RATE_LY_KEY if bar_series == "ly" else RATE_YTD_KEY
    legend_label = f"{mode.title()} Rate ({bar_series.upper()})"

    # ---------- Normalize + sort ----------
    rows = _normalize_rows(data, canon_fn)
    rows.sort(key=lambda r: (str(r.get("YearGroupDesc")), str(r.get("CompetencyDesc")), r["_CanonResultType"]))

    # ---------- Figure ----------
    fig = plt.figure(figsize=A4_PORTRAIT)
    ax = fig.add_subplot(111); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
    ax.set_position([0.0, 0.0, 1.0, 0.97])
    # Title
    base_title_fs = 14
    if (mode or "").strip().lower() == "region":
        subject_label = (region_name or subject_name or "Region").strip()
    else:
        subject_label = (subject_name or mode.title()).strip()

    default_title = f"{subject_label} vs Target • Term {term}, {year}"
    ttl = title or default_title
    TITLE_Y = 0.99
    ax.text(0.5, TITLE_Y, ttl, ha="center", va="top", fontsize=base_title_fs, weight="bold")
    import pytz  # at top of file
    from datetime import datetime
    nz = pytz.timezone("Pacific/Auckland")
    generated_str = datetime.now(nz).strftime("%d %b %Y, %I:%M %p")
    if(caption is None):
        caption = f"Term {term}, {year} | Generated {generated_str}"

    CAPTION_Y = TITLE_Y - 0.02  # small gap under the title
    ax.text(
        0.5,
        CAPTION_Y,
        caption,
        ha="center",
        va="top",
        fontsize=9.5,
        color="#555555",
    )
    # ---------- Base layout ----------
    BASE = {
        "LEFT_MARGIN": 0.00,
        "BARS_LEFT_X": 0.50,
        "RIGHT_MARGIN": 0.06,
        "LABEL_PAD": 0.012,

        "BAR_H": 0.08, "BAR_GAP": 0.015, "ITEM_GAP": 0,
        "GROUP_TOP_PAD": 0.025,
        "GROUP_BOT_PAD": 0.00,
        "SUBTITLE_GAP": 0.020,

        "TOP_MARGIN": 0.05 ,"BOTTOM_MARGIN": 0.0,
        "LABEL_FS": 7.0, "TARGET_FS": 7.5, "SUBTITLE_FS": 11.5,
    }

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

    BARS_LEFT_X  = BASE["BARS_LEFT_X"]
    RIGHT_MARGIN = BASE["RIGHT_MARGIN"] * (0.9*scale + 1)
    LABEL_PAD    = BASE["LABEL_PAD"] * (0.9*scale + 0.1)
    BAR_MAX_W    = 1.0 - RIGHT_MARGIN - BARS_LEFT_X

    BAR_H        = BASE["BAR_H"]*scale
    BAR_GAP      = BASE["BAR_GAP"]*scale
    ITEM_GAP     = BASE["ITEM_GAP"]*scale
    GROUP_TOP_PAD = BASE["GROUP_TOP_PAD"]*scale
    GROUP_BOT_PAD = BASE["GROUP_BOT_PAD"]*scale
    SUBTITLE_GAP  = BASE["SUBTITLE_GAP"]*scale

    label_fs   = max(10, BASE["LABEL_FS"]*(0.9*scale+0.1))
    target_fs  = max(8, BASE["TARGET_FS"]*(0.9*scale+0.1))
    subtitle_fs= max(8.0, BASE["SUBTITLE_FS"]*(0.9*scale+0.1))

    LEFT_LABEL_X = BARS_LEFT_X - LABEL_PAD

    fig.canvas.draw()
    px_w = fig.get_size_inches()[0] * fig.dpi
    label_region_px = max(10, (LEFT_LABEL_X - BASE["LEFT_MARGIN"]) * px_w)
    avg_char_px = 0.6 * label_fs * (fig.dpi / 72.0)
    wrap_width = max(20, int(label_region_px / max(4.0, avg_char_px)))

    if debug:
        ax.axvline(BARS_LEFT_X, 0, 1, linewidth=0.4)

    # ---------- Build groups ----------
    groups = []
    for yg, yg_iter in groupby(rows, key=lambda r: str(r.get("YearGroupDesc"))):
        yg_rows = list(yg_iter)
        target_val = None
        comp_rate = {}

        for comp, comp_iter in groupby(yg_rows, key=lambda r: str(r.get("CompetencyDesc"))):
            rs = list(comp_iter)
            vals = {r["_CanonResultType"]: float(r["Rate"]) for r in rs}
            if target_val is None and TARGET_KEY in vals:
                target_val = float(vals[TARGET_KEY])

            val = None
            if BAR_KEY in vals:
                val = float(vals[BAR_KEY])
            elif any(k.endswith("student count (ytd)") for k in vals):
                val = 0.0
            elif fallback_to_national and NATIONAL_KEY in vals:
                val = float(vals[NATIONAL_KEY])

            if val is not None:
                comp_rate[comp] = max(0.0, min(1.0, val))

        # ✅ Fallback WSNZ target if missing (85%)
        if target_val is None and comp_rate:
            target_val = None

        ordered_items = sorted(
            comp_rate.items(),
            key=lambda kv: str(kv[0]).lower()  # kv[0] = CompetencyDesc
        )
        if not ordered_items:
            continue
        groups.append({"yg": yg, "items": ordered_items, "target": target_val})
        all_yeargroups      = set(rows_by_yg.keys())        # all YGs that appeared in raw data
        present_yeargroups  = {g["yg"] for g in groups}     # YGs we actually plotted
        missing_yeargroups  = all_yeargroups - present_yeargroups

        if missing_yeargroups:
            # Slight bump — tweak factor to taste (e.g. 1.2 if you want louder)
            subtitle_fs = subtitle_fs * 1.15
    # ---------- Row grid ----------
    baseline_step = max(2*BAR_H + BAR_GAP, ITEM_GAP)
    gap_rows      = max(0, int(round(GROUP_TOP_PAD / max(1e-6, baseline_step))))
    between_rows  = max(0, int(round(GROUP_BOT_PAD / max(1e-6, baseline_step))))
    subtitle_rows = 1

    total_row_slots = 0
    for i, g in enumerate(groups):
        total_row_slots += subtitle_rows + gap_rows + len(g["items"])
        if i < len(groups) - 1:
            total_row_slots += between_rows

    LEGEND_CY    = 0.055
    LEGEND_BG_H  = 0.032
    LEGEND_CLEAR = 0.006

    top_y    = 1.0 - BASE["TOP_MARGIN"] - (1.0 - TITLE_Y)
    legend_top = LEGEND_CY + LEGEND_BG_H/2
    bottom_y   = max(legend_top + LEGEND_CLEAR, 0.02)

    centers = np.array([top_y]) if total_row_slots < 2 else np.linspace(top_y, bottom_y, total_row_slots)
    row_step = abs(centers[0] - centers[-1]) / max(1, (len(centers)-1))
    BAR_H = min(BAR_H, 0.8 * row_step)

    # ---------- Render ----------
    idx = 0
    for gi, g in enumerate(groups):
        if idx >= len(centers): break

        ax.text(0.5, centers[idx] - SUBTITLE_GAP/2, f"Years {g['yg']}",
                ha="center", va="center", fontsize=subtitle_fs, weight="bold")
        idx += subtitle_rows
        idx += gap_rows

        first_center = last_center = None
        for comp, val in g["items"]:
            if idx >= len(centers): break
            y = centers[idx]

            ax.text(LEFT_LABEL_X, y, _wrap(comp, width=wrap_width),
                    ha="right", va="center", multialignment="right", fontsize=label_fs)

            w = val * BAR_MAX_W
            ax.add_patch(plt.Rectangle((BARS_LEFT_X, y - BAR_H/2), w, BAR_H,
                                       facecolor=bar_color, edgecolor="none"))
            ax.text(BARS_LEFT_X + w + 0.008, y, f"{val*100:.1f}%",
                    ha="left", va="center", fontsize=label_fs)

            first_center = y if first_center is None else first_center
            last_center = y
            idx += 1

        if g["target"] is not None and first_center is not None and last_center is not None:
            x_t = BARS_LEFT_X + max(0.0, min(1.0, g["target"])) * BAR_MAX_W
            ax.plot([x_t, x_t], [first_center + BAR_H/2, last_center - BAR_H/2],
                    linestyle=(0, (4, 4)))
            ax.lines[-1].set_color(target_color)
            ax.lines[-1].set_linewidth(1.8)
            ax.text(x_t, (last_center - BAR_H/2) - 0.004,
                    f"WSNZ Target {round(g['target']*100)}%",
                    ha="center", va="top", fontsize=target_fs, color=target_color, fontweight="bold")

        if gi < len(groups) - 1:
            idx += between_rows

    # Legend with dynamic label (e.g., "Funder Rate (LY)")
    legend_fs = max(8, int(8 * (0.9*scale + 0.1)))
    
    if(1 == 0):
        _draw_legend(ax, label_subject=legend_label, cx=0.5, cy=LEGEND_CY, fs=legend_fs,
                    bar_color=bar_color, target_color=target_color, show_bg=True)

    return fig

# ---------- DB ----------
def build_engine():
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("Missing DB_URL in .env")
    return create_engine(db_url, pool_pre_ping=True, fast_executemany=True)

def get_rates(engine, year: int, term: int, subject_id: int, mode: str, *, region_name: str | None = None) -> pd.DataFrame:
    mode = (mode or "").strip().lower()
    with engine.begin() as conn:
        if mode == "national":
            # 200 = "National" sentinel in GetFunderNationalRatesSmart
            sql_stmt = text("EXEC GetFunderNationalRatesSmart :year, :term, :funder_id")
            df = pd.read_sql_query(
                sql_stmt,
                conn,
                params={"year": year, "term": term, "funder_id": 200}
            )
        elif mode == "funder":
            sql_stmt = text("EXEC GetFunderNationalRatesSmart :year, :term, :funder_id")
            df = pd.read_sql_query(sql_stmt, conn, params={"year": year, "term": term, "funder_id": subject_id})
        elif mode == "provider":
            sql_stmt = text("EXEC GetProviderNationalRates :year, :term, :provider_id")
            df = pd.read_sql_query(sql_stmt, conn, params={"year": year, "term": term, "provider_id": subject_id})
        elif mode == "region":
            if not region_name or not str(region_name).strip():
                raise ValueError("mode='region' requires region_name")
            sql_stmt = text("EXEC GetRegionalCouncilRates :year, :term, :region_name")
            return pd.read_sql_query(
                sql_stmt,
                conn,
                params={"year": year, "term": term, "region_name": region_name.strip()},
            )
        else:
            raise ValueError("mode must be 'provider', 'funder', 'region', or 'national'")
    return df

def _first_nonempty(*vals):
    for v in vals:
        if v and str(v).strip():
            return str(v).strip()
    return None

def get_subject_name(engine, mode: str, subject_id: int, df_from_proc: Optional[pd.DataFrame] = None) -> Optional[str]:
    mode = mode.strip().lower()

    if mode == "national":
        return "National"
    # (1) Try in-DF columns
    if isinstance(df_from_proc, pd.DataFrame) and len(df_from_proc):
        col = "ProviderName" if mode == "provider" else "FunderName"
        name = _first_nonempty(
            (df_from_proc[col].iloc[0] if col in df_from_proc.columns else None),
            (df_from_proc.get("SubjectName", pd.Series(dtype=object)).iloc[0] if "SubjectName" in df_from_proc.columns else None),
        )
        if name: return name

    with engine.begin() as conn:
        # (2) Name proc
        try:
            proc = "GetProviderName" if mode == "provider" else "GetFunderName"
            row = conn.execute(text(f"EXEC {proc} :id"), {"id": subject_id}).fetchone()
            if row:
                if hasattr(row, "_mapping"):
                    m = row._mapping
                    cand = _first_nonempty(m.get("Name"), m.get("ProviderName"), m.get("FunderName"))
                    if cand: return cand
                return str(row[0])
        except (ProgrammingError, DBAPIError):
            pass

        # (3) Table fallback via env
        table   = os.getenv("PROVIDER_TABLE" if mode=="provider" else "FUNDER_TABLE")
        id_col  = os.getenv("PROVIDER_ID_COL" if mode=="provider" else "FUNDER_ID_COL", "ID")
        namecol = os.getenv("PROVIDER_NAME_COL" if mode=="provider" else "FUNDER_NAME_COL", "Name")
        if table:
            try:
                row = conn.execute(text(f"SELECT TOP 1 {namecol} AS Name FROM {table} WHERE {id_col} = :id"),
                                   {"id": subject_id}).fetchone()
                if row:
                    if hasattr(row, "_mapping") and "Name" in row._mapping:
                        return str(row._mapping["Name"])
                    return str(row[0])
            except (ProgrammingError, DBAPIError):
                pass

    return None

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Render portrait chart (bars vs WSNZ target).")
    ap.add_argument("--mode", choices=["provider", "funder", "national", "region"], default="provider")
    ap.add_argument("--region-name", type=str, default=None, help="Region name (only for --mode region)")    
    ap.add_argument("--subject-id", type=int, help="Provider/Funder ID (if pulling from DB)")
    ap.add_argument("--csv", type=str, help="Path to CSV file instead of DB query")
    ap.add_argument("--subject-name", type=str, default=None)
    
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--term", type=int, default=2)
    ap.add_argument("--title", type=str, default=None)
    ap.add_argument("--outfile", type=str, default="one_bar_one_line")
    ap.add_argument("--fallback-to-national", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--bar-series", choices=["ytd", "ly"], default="ly")
    args = ap.parse_args()

    if args.csv:
        df = pd.read_csv(args.csv)
        print(f"📥 Loaded {len(df)} rows from CSV {args.csv}")
        subject_name = args.subject_name or "CLM (All Funders)"
    else:
        engine = build_engine()

        if args.mode == "national":
            df = get_rates(engine, args.year, args.term, subject_id=0, mode="national")
            subject_name = args.subject_name or "National"

        elif args.mode == "region":
            df = get_rates(engine, args.year, args.term, subject_id=0, mode="region", region_name=args.region_name)
            subject_name = args.subject_name or (args.region_name.strip() if args.region_name else "Region")

        else:
            if args.subject_id is None:
                raise ValueError("--subject-id is required for provider/funder mode")
            df = get_rates(engine, args.year, args.term, args.subject_id, args.mode)
            subject_name = args.subject_name or get_subject_name(engine, args.mode, args.subject_id, df)

        print(f"📥 Loaded {len(df)} rows from DB")

    fig = provider_portrait_with_target(
        df,
        term=args.term,
        year=args.year,
        mode=args.mode,
        subject_name=subject_name,
        title=args.title,
        fallback_to_national=args.fallback_to_national,
        debug=args.debug,
        bar_series=args.bar_series,
    )

    png_path = f"{args.outfile}.png"
    pdf_path = f"{args.outfile}.pdf"
    fig.savefig(png_path, dpi=200, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    print(f"✅ Wrote {png_path} and {pdf_path}")
if __name__ == "__main__":
    main()
