# app/utils/kmko_counts_onefile_report.py
#
# ONE-FILE version:
# - Connects to Azure SQL
# - Runs: EXEC KMKO_Counts
# - Tidies the dataframe
# - Builds a ONE-PAGE report with ONE TABLE (A4 portrait)
# - Writes PDF and/or PNG
#
# PowerShell examples:
#   python app/utils/kmko_counts_onefile_report.py --out outputs/KMKO_Counts
#   python app/utils/kmko_counts_onefile_report.py --out outputs/KMKO_Counts --subtitle "As at 21 Jan 2026"
#   python app/utils/kmko_counts_onefile_report.py --out outputs/KMKO_Counts --pdf-only
#
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Your existing report utils (same style as your funder_missing_plot)
from app.report_utils.TAB_DataframeTable import draw_dataframe_table
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.helpers import load_ppmori_fonts


# ============================================================
# DB
# ============================================================

def get_db_engine():
    """
    Uses DB_URL_CUSTOM if present, otherwise builds a pyodbc connection from env vars.
    """
    load_dotenv()

    db_url = os.getenv("DB_URL_CUSTOM")
    if db_url:
        return create_engine(db_url, pool_pre_ping=True, future=True)

    server = os.getenv("AZURE_SQL_SERVER") or os.getenv("DB_SERVER") or os.getenv("SQL_SERVER")
    database = os.getenv("AZURE_SQL_DATABASE") or os.getenv("DB_NAME") or os.getenv("SQL_DATABASE")
    username = os.getenv("AZURE_SQL_USER") or os.getenv("DB_USER") or os.getenv("SQL_USER")
    password = os.getenv("AZURE_SQL_PASSWORD") or os.getenv("DB_PASS") or os.getenv("SQL_PASSWORD")

    if not all([server, database, username, password]):
        raise RuntimeError(
            "Missing DB connection env vars. Provide DB_URL_CUSTOM or set:\n"
            "AZURE_SQL_SERVER / AZURE_SQL_DATABASE / AZURE_SQL_USER / AZURE_SQL_PASSWORD\n"
            "(or your DB_SERVER/DB_NAME/DB_USER/DB_PASS equivalents)."
        )

    driver = os.getenv("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    params = quote_plus(
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}", pool_pre_ping=True, future=True)


def fetch_kmko_counts(engine) -> pd.DataFrame:
    with engine.begin() as conn:
        return pd.read_sql(text("EXEC KMKO_Counts"), conn)


# ============================================================
# Data tidy
# ============================================================

def tidy_kmko_counts_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Stored proc output expected:
      Provider, ParticipantCount, NewParticipantsThisMonth, LatestDateTimeStampNZ
    Produces display cols:
      Provider, Participants, New this month, Latest update
    """
    if df is None or df.empty:
        return df

    expected = {"Provider", "ParticipantCount", "NewParticipantsThisMonth", "LatestDateTimeStampNZ"}
    missing = expected - set(df.columns)
    if missing:
        raise KeyError(f"KMKO_Counts returned missing columns: {sorted(missing)}. Got: {list(df.columns)}")

    out = df.copy()

    for c in ["ParticipantCount", "NewParticipantsThisMonth"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype(int)

    out["LatestDateTimeStampNZ"] = pd.to_datetime(out["LatestDateTimeStampNZ"], errors="coerce")
    out["Latest update"] = out["LatestDateTimeStampNZ"].dt.strftime("%d %b %Y")

    out = out.rename(
        columns={
            "ParticipantCount": "Participants",
            "NewParticipantsThisMonth": "New this month",
        }
    )

    out = out[["Provider", "Participants", "New this month", "Latest update"]]
    out = out.sort_values(["Provider"]).reset_index(drop=True)
    return out


# ============================================================
# Figure builder (ONE PAGE, ONE TABLE)
# ============================================================

def create_kmko_counts_figure(
    df: pd.DataFrame,
    *,
    title: str = "KMKO Participation Counts",
    subtitle: str | None = None,
    a4_portrait: bool = True,
    debug: bool = False,
):
    """
    A4 portrait, header band, one table block.
    Returns a Matplotlib Figure (no file I/O).
    """
    load_ppmori_fonts("app/static/fonts")

    fig, ax = plt.subplots(figsize=(8.27, 11.69) if a4_portrait else (11.69, 8.27))
    ax.set_axis_off()

    # --- Header band ---
    header_poly = rounded_rect_polygon(
        cx=0.5,
        cy=0.955,
        width=0.88,
        height=0.06 if subtitle else 0.05,
        ratio=0.45,
        corners_round=[1, 3],
        n_arc=64,
    )

    ax.add_patch(
        mpatches.Polygon(
            list(header_poly.exterior.coords),
            closed=True,
            facecolor="#395765",
            edgecolor="#395765",
            linewidth=0.8,
            transform=ax.transAxes,
        )
    )

    header_text = title if not subtitle else f"{title}\n{subtitle}"
    draw_text_in_polygon(
        ax,
        poly=header_poly,
        text=header_text,
        fontfamily="PP Mori",
        fontsize=20 if not subtitle else 18,
        fontweight="semibold",
        color="#ffffff",
        pad_frac=0.05,
        wrap=True,
        max_lines=None,
        autoshrink=True,
        min_fontsize=10,
        clip_to_polygon=True,
        zorder=6,
    )

    # --- Table block area ---
    x = 0.06
    y = 0.06
    w = 0.88
    h = 0.86  # leaves room for header

    if df is None or df.empty:
        ax.add_patch(
            mpatches.Rectangle(
                (x, y),
                w,
                h,
                transform=ax.transAxes,
                facecolor="#ffffff",
                edgecolor="#cdd6e6",
                linewidth=0.8,
            )
        )
        ax.text(
            0.5,
            y + h / 2,
            "No data to display",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=11,
            color="#667085",
            fontfamily="PP Mori",
        )
        fig.tight_layout()
        return fig

    cols = [
        {"key": "Provider",          "label": "Provider",           "width_frac": 0.46, "align": "left"},
        {"key": "Participants",      "label": "Participants",       "width_frac": 0.18, "align": "center"},
        {"key": "New this month",    "label": "New this month",     "width_frac": 0.18, "align": "center"},
        {"key": "Latest update","label": "Latest update", "width_frac": 0.18, "align": "center"},
    ]

    # Use a fixed-ish header height in axes units (like your other report)
    FIXED_HEADER_AXES = 0.045
    header_height_frac = FIXED_HEADER_AXES / max(h, 1e-6)
    header_height_frac = max(0.03, min(header_height_frac, 0.30))

    if debug:
        print("▶ create_kmko_counts_figure")
        print("  df shape:", df.shape)
        print("  df cols:", df.columns.tolist())
        print("  header_height_frac:", header_height_frac)

    draw_dataframe_table(
        ax,
        df=df,
        x=x,
        y=y,
        width=w,
        height=h,
        columns=cols,
        header_height_frac=header_height_frac,
        header_facecolor="#395765",
        header_textcolor="#ffffff",
        header_fontfamily="PP Mori",
        header_fontsize=10,
        header_fontweight="semibold",
        body_fontfamily="PP Mori",
        body_fontsize=10,
        body_textcolor="#395765",
        row_alt_facecolor="#39576520",
        row_facecolor="#ffffff",
        show_grid=True,
        grid_color="#395765",
        grid_linewidth=0.6,
        border_color="#395765",
        border_linewidth=1.0,
        pad_x_frac=0.01,
        pad_y_frac=0.005,
        default_align="left",
        wrap=True,
        max_wrap_lines=2,
        footer=None,
        DEBUG=False,
    )

    fig.tight_layout()
    return fig


# ============================================================
# Save
# ============================================================

def save_report(fig, outbase: Path, *, pdf: bool, png: bool, dpi: int = 300) -> dict[str, str]:
    outbase.parent.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    if pdf:
        p = str(outbase.with_suffix(".pdf"))
        fig.savefig(p, bbox_inches="tight")
        written["pdf"] = p

    if png:
        p = str(outbase.with_suffix(".png"))
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        written["png"] = p

    plt.close(fig)
    return written


# ============================================================
# CLI
# ============================================================

def parse_args():
    p = argparse.ArgumentParser(description="Run KMKO_Counts and generate a one-page (one-table) report.")
    p.add_argument("--out", required=True, help="Output base path WITHOUT extension (e.g., outputs/KMKO_Counts)")
    p.add_argument("--title", default="Kia Maanu Kia Ora Participation", help="Header title")
    p.add_argument("--subtitle", default=None, help="Optional subtitle (e.g., 'As at 21 Jan 2026')")
    p.add_argument("--pdf-only", action="store_true", help="Write only PDF")
    p.add_argument("--png-only", action="store_true", help="Write only PNG")
    p.add_argument("--dpi", type=int, default=300, help="PNG DPI")
    p.add_argument("--debug", action="store_true", help="Print debug info")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    outbase = Path(args.out)

    pdf = True
    png = True
    if args.pdf_only and not args.png_only:
        png = False
    if args.png_only and not args.pdf_only:
        pdf = False

    engine = get_db_engine()

    if args.debug:
        print("▶ Running: EXEC KMKO_Counts")

    df_raw = fetch_kmko_counts(engine)
    df = tidy_kmko_counts_df(df_raw)

    if args.debug:
        print(f"✅ Rows: {0 if df is None else len(df)}")
        if df is not None and not df.empty:
            print(df.head(20).to_string(index=False))

    fig = create_kmko_counts_figure(
        df,
        title=args.title,
        subtitle=args.subtitle,
        a4_portrait=True,
        debug=args.debug,
    )

    written = save_report(fig, outbase, pdf=pdf, png=png, dpi=args.dpi)
    for k, v in written.items():
        print(f"✅ Wrote {k.upper()}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
