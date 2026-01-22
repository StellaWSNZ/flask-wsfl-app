import os
from sqlalchemy import create_engine, text
import pandas as pd

from app.report_utils.pdf_builder import open_pdf, new_page, save_page, close_pdf
from app.utils.funder_missing_plot import add_full_width_footer_svg  # <- your file

def build_engine():
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("Missing DB_URL")
    return create_engine(db_url, pool_pre_ping=True, fast_executemany=True)

def load_df(engine) -> pd.DataFrame:
    sql = text("EXEC GetFunderTargetsCounts")
    with engine.begin() as conn:
        return pd.read_sql_query(sql, conn)

def main():


    engine = build_engine()
    df = load_df(engine)

    footer_png = "app/static/footer.png"  # your helper is PNG-based
    pdf, w_in, h_in, dpi = open_pdf("out/base_report.pdf", page_size="A4", orientation="portrait", dpi=300)

    fig, ax = new_page(w_in, h_in, dpi=dpi)

    # Title
    ax.text(0.5, 0.965, f"Funder Targets", ha="center", va="top",
            fontsize=16, fontweight="bold")

    # Tiny body placeholder (prove it loaded)
    ax.text(0.06, 0.90, f"Loaded {len(df)} rows from stored procedure.", ha="left", va="top", fontsize=11)
    
    height = 0.8 
    x,y = 0.1,0.9
    width = 
    footer_svg = "app/static/footer.svg"
    add_full_width_footer_svg(
        fig,
        footer_svg,
        bottom_margin_frac=0.0,
        max_footer_height_frac=0.20,
        col_master="#1a427d40"
    )
    # Save with footer
    save_page(
        pdf,
        fig,
        footer_png=None,
        width_in=w_in,
        height_in=h_in,
        footer_bottom_margin_frac=0.0,
        footer_max_height_frac=0.20,
    )

    outpath = close_pdf(pdf)
    print("Wrote:", outpath)

if __name__ == "__main__":
    main()
