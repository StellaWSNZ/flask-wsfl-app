"""
Render region charts using one_bar_one_line.

Toggle behavior at the top:

MULTIPAGE = True
    → All regions in ONE multi-page PDF

MULTIPAGE = False
    → Separate PDF per region

Run:
    python scripts/render_regions_toggle.py
"""

# ==============================
# 🔁 TOGGLE HERE
# ==============================
MULTIPAGE = True
YEAR = 2025
TERM = 4
BAR_SERIES = "ytd"  # "ytd" or "ly"
# ==============================


import os
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from app.utils.one_bar_one_line import (
    build_engine,
    get_rates,
    provider_portrait_with_target,
)

REGIONS = [
    "Area Outside Region",
    "Auckland Region",
    "Bay of Plenty Region",
    "Canterbury Region",
    "Gisborne Region",
    "Hawke's Bay Region",
    "Manawatū-Whanganui Region",
    "Marlborough Region",
    "Nelson Region",
    "Northland Region",
    "Otago Region",
    "Southland Region",
    "Taranaki Region",
    "Tasman Region",
    "Waikato Region",
    "Wellington Region",
    "West Coast Region",
]


def slug(s: str) -> str:
    return (
        s.replace(" ", "_")
         .replace("'", "")
         .replace("ū", "u")
         .replace("–", "-")
    )


def main():

    if not os.getenv("DB_URL"):
        raise RuntimeError("Missing DB_URL in .env")

    engine = build_engine()

    out_dir = Path("reports") / "regions" / f"T{TERM}_{YEAR}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"📁 Output folder: {out_dir.resolve()}")
    print(f"📅 Term {TERM}, {YEAR}")
    print(f"📊 Bar series: {BAR_SERIES.upper()}")
    print(f"📄 Multi-page mode: {MULTIPAGE}")

    # ===================================
    # MULTI-PAGE MODE
    # ===================================
    if MULTIPAGE:
        pdf_path = out_dir / f"All_Regions_T{TERM}_{YEAR}.pdf"
        print(f"\n📘 Writing multi-page PDF: {pdf_path.name}")

        with PdfPages(pdf_path) as pdf:

            for region in REGIONS:
                print(f"📊 Rendering {region}")

                df = get_rates(
                    engine,
                    year=YEAR,
                    term=TERM,
                    subject_id=0,
                    mode="region",
                    region_name=region,
                )

                fig = provider_portrait_with_target(
                    df,
                    term=TERM,
                    year=YEAR,
                    mode="region",
                    subject_name=region,
                    region_name=region,
                    title=f"{region} vs Target • Term {TERM}, {YEAR}",
                    bar_series=BAR_SERIES,
                )

                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)

        print("✅ Multi-page PDF complete.")

    # ===================================
    # SEPARATE FILE MODE
    # ===================================
    else:
        for region in REGIONS:
            print(f"📊 Rendering {region}")

            df = get_rates(
                engine,
                year=YEAR,
                term=TERM,
                subject_id=0,
                mode="region",
                region_name=region,
            )

            fig = provider_portrait_with_target(
                df,
                term=TERM,
                year=YEAR,
                mode="region",
                subject_name=region,
                region_name=region,
                title=f"{region} vs Target • Term {TERM}, {YEAR}",
                bar_series=BAR_SERIES,
            )

            base = f"{slug(region)}_T{TERM}_{YEAR}"
            pdf_path = out_dir / f"{base}.pdf"

            fig.savefig(pdf_path, bbox_inches="tight")
            plt.close(fig)

            print(f"✅ Wrote {pdf_path.name}")

    print("\n🎉 Done.")


if __name__ == "__main__":
    main()
