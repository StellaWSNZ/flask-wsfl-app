import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
import pandas as pd
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# ========== CONFIGURATION ==========
load_dotenv()
PAGE_SIZE = (8.27, 11.69)  # A4 Portrait
TERM = 2
YEAR = 2025
OUTPUT_FILE = f"FunderStudentCountsYTD_{YEAR}_T{TERM}.pdf"

# ========== DATABASE ==========
def get_db_engine():
    db_url = os.getenv("DB_URL_CUSTOM")
    return create_engine(db_url, fast_executemany=True)

def load_funder_data(engine, year, term):
    with engine.begin() as conn:
        result = conn.execute(
            text("EXEC GetFunderStudentCountsYTD :Year, :Term"),
            {"Year": year, "Term": term}
        )
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
    return df

# ========== DRAW FUNCTION ==========
def draw_double_bars(df, pdf_filename):
    df = df[~df["FunderID"].isin([13, 15, 100])]
    df = df.pivot(index="FunderDesc", columns="Metric", values="Value").fillna(0)
    df = df[["ErrorCount", "StudentCountYTD", "Target"]]
    df["Total"] = df["ErrorCount"] + df["StudentCountYTD"]
    df["Delta"] = df["Total"] - df["Target"]
    df = df.sort_index(ascending=False)  # 🔁 reverse alphabetical

    max_target = df[["Target", "Total"]].max().max()
    n = len(df)

    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Layout constants
    top_margin = 0.06
    bottom_margin = 0.06
    available_height = 1 - top_margin - bottom_margin
    row_spacing = available_height / n
    bar_height = row_spacing * 0.4
    gap = 0.02
    box_w = 0.05
    box_h = 0.018

    left_margin = 0.25
    usable_width = 1 - left_margin - 0.05

    for i, (funder, row) in enumerate(df.iterrows()):
        y_base = bottom_margin + i * row_spacing
        y_bottom = y_base
        y_top = y_base + bar_height + (row_spacing - 2 * bar_height) / 2
        label_y = y_top + bar_height / 2

        # Values
        errors = int(row["ErrorCount"])
        valid = int(row["StudentCountYTD"])
        target = int(row["Target"])
        delta = int(row["Delta"])
        delta_sign = "+" if delta > 0 else ""
        delta_text = f"{delta_sign}{delta}"
        delta_color = "#00800020" if delta >= 0 else "#d0000020"

        # Compute positions
        label_x = left_margin - gap - box_w - gap
        box_x = label_x + gap
        box_y = label_y - 0.031
        bars_x = box_x + box_w + gap

        # Scaled widths
        target_w = target / max_target * usable_width
        error_w = errors / max_target * usable_width
        student_w = valid / max_target * usable_width

        # Bars
        ax.add_patch(Rectangle((bars_x, y_bottom), target_w, bar_height, color="#BBE6E9"))
        ax.add_patch(Rectangle((bars_x, y_top), error_w, bar_height, color="#2EBDC2"))
        ax.add_patch(Rectangle((bars_x + error_w, y_top), student_w, bar_height, color="#356FB6"))

        # Funder label
        ax.text(label_x, label_y,
                funder, ha='right', va='bottom', fontsize=8.5, weight='bold')

        ax.text(label_x, label_y - 0.004,
                f"Errors: {errors}\nValid Records: {valid}\nTotal Records:{errors + valid}\nTarget: {target}",
                ha='right', va='top', fontsize=7.5, linespacing=1.2)

        # Delta box
        ax.add_patch(Rectangle((box_x, box_y), box_w, box_h,
                               color=delta_color, linewidth=0, zorder=2))
        text_color = "#006400" if delta >= 0 else "#8B0000"
        ax.text(box_x + box_w / 2, box_y + box_h / 2,
                delta_text, ha='center', va='center',
                fontsize=7.5, weight='bold', color=text_color, zorder=3)

    # Title
    ax.text(0.5, 0.985, f"Funder Progress vs Target (Term {TERM}, {YEAR})",
            ha='center', va='top', fontsize=14, weight='bold')

    # Legend centered under title
    legend_y = 0.945
    key_w = 0.015
    gap = 0.005
    labels = [("Target", "#BBE6E9"), ("Error Count", "#2EBDC2"), ("Student Count", "#356FB6")]
    label_widths = [0.07, 0.11, 0.13]
    total_legend_width = 3 * key_w + 3 * gap + sum(label_widths)
    start_x = 0.5 - total_legend_width / 2

    current_x = start_x
    for (label, color), text_width in zip(labels, label_widths):
        ax.add_patch(Rectangle((current_x, legend_y), key_w, key_w * (21/29.7), color=color))
        current_x += key_w + gap
        ax.text(current_x, legend_y + (key_w * (21/29.7)) / 2, label, va='center', fontsize=8)
        current_x += text_width

    fig.tight_layout()
    with PdfPages(pdf_filename) as pdf:
        pdf.savefig(fig)
        plt.close(fig)

# ========== MAIN ==========
def main():
    engine = get_db_engine()
    df = load_funder_data(engine, YEAR, TERM)
    draw_double_bars(df, OUTPUT_FILE)
    print(f"✅ PDF saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
