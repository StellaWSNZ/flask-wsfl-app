import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors
import matplotlib.pyplot as plt
import pandas as pd
import textwrap
import os
from sqlalchemy import create_engine, text
from matplotlib.backends.backend_pdf import PdfPages

# ========== CONFIGURATION ==========
PAGE_SIZE = (8.27, 11.69)  # A4 Portrait
TITLE_SPACE = 0.2
DEBUG = False
TERM = 2
CALENDARYEAR = 2025
OUTPUT_FILENAME = f"Competency_Report_{TERM}_{CALENDARYEAR}_w_nationalLY.pdf"

# ========== DATABASE ==============
def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, fast_executemany=True)

def get_all_competencies(con, year, term):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC getrelevantcompetencies :CalendarYear, :Term"),
            {"CalendarYear": year, "Term": term}
        )
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
        return df[df["WaterBased"] == 1].reset_index(drop=True)

def load_competency_rates(con, calendaryear, term, competencyID, yearGroupID):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC getcompetencyrate :CalendarYear, :Term, :CompetencyID, :YearGroupID"),
            {
                "CalendarYear": calendaryear,
                "Term": term,
                "CompetencyID": competencyID,
                "YearGroupID": yearGroupID
            }
        )
        df = pd.DataFrame(result.fetchall(), columns=result.keys())

    # Only filter here, sort later in make_figure
    df = df[~df["FunderID"].isin([13, 15])]
    return df

def load_national_rates(con, calendaryear, term):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC GetFunderNationalRatesSmart :CalendarYear, :Term, :FunderID"),
            {
                "CalendarYear": calendaryear,
                "Term": term,
                "FunderID": 200
            }
        )
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
    return df[df["ResultType"].isin(["National Rate (LY)"])].reset_index(drop=True)

# ========== PLOTTING ==============
def make_figure(ax, df, title, national_rate_ly=None, national_rate_ytd=None):
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1 + TITLE_SPACE)
    ax.set_xticks([])
    ax.set_yticks([])

    # Sort and reset index
    df = df.sort_values("FunderDesc", ascending=False).reset_index(drop=True)

    wrapped_title = textwrap.wrap(title, width=70)
    line_spacing = 0.05
    start_y = 1 + TITLE_SPACE / 2 + (len(wrapped_title) - 1) * line_spacing / 2

    for i, line in enumerate(wrapped_title):
        ax.text(0.5, start_y - i * line_spacing, line, ha='center', va='center', fontsize=12, weight='bold')

    buffer = 0.05
    buffer_name = 0.2
    height_per = (1 - (buffer * 2)) / len(df)
    width_per = (1 - (buffer * 2) - buffer_name) / 100

    for index, row in df.iterrows():
        funder = row['FunderDesc']
        value = row['Rate']
        formatted_value = f"{value * 100:.2f}%"
        y_pos = buffer + height_per * index

        ax.add_patch(plt.Rectangle(
            (buffer + buffer_name, y_pos), width_per * value * 100, height_per,
            edgecolor='black', facecolor='none'
        ))

        value_x = buffer + buffer_name + width_per * value * 100
        value_offset = height_per * 0.2
        ha = 'left' if value < 0.2 else 'right'
        value_x += value_offset if ha == 'left' else -value_offset

        ax.text(value_x, y_pos + height_per / 2, formatted_value, ha=ha, va='center', weight='bold')
        ax.text(buffer + buffer_name - value_offset, y_pos + height_per / 2, funder, ha='right', va='center', weight='bold')

    # Red dashed national line
    if national_rate_ly is not None and pd.notna(national_rate_ly):
        national_x = buffer + buffer_name + width_per * national_rate_ly * 100
        line_bottom = buffer - 0.02
        line_top = 1 - buffer + 0.02

        ax.plot([national_x, national_x], [line_bottom, line_top],
                color='red', linestyle='dashed', linewidth=1.5)

        ax.text(national_x, line_top + 0.015, f"{national_rate_ly * 100:.1f}%",
                color='red', ha='center', va='bottom', fontsize=10, weight='bold')

        ax.text(national_x, line_bottom - 0.015, "National (LY)",
                color='red', ha='right', va='top', fontsize=9, weight='bold')

    # Blue dashed YTD line
    if national_rate_ytd is not None and pd.notna(national_rate_ytd):
        national_x = buffer + buffer_name + width_per * national_rate_ytd * 100
        ax.plot([national_x, national_x], [line_bottom, line_top],
                color='blue', linestyle='dashed', linewidth=1.5)

        ax.text(national_x, line_top + 0.015, f"{national_rate_ytd * 100:.1f}%",
                color='blue', ha='center', va='bottom', fontsize=10, weight='bold')

        ax.text(national_x, line_bottom - 0.015, "National (YTD)",
                color='blue', ha='left', va='top', fontsize=9, weight='bold')

    for spine in ax.spines.values():
        spine.set_visible(False)

# ========== MAIN ===================
def main():
    con = get_db_engine()
    competencies = get_all_competencies(con, CALENDARYEAR, TERM).sort_values(
        by=["YearGroupID", "CompetencyID"]
    ).reset_index(drop=True)
    national_df = load_national_rates(con, CALENDARYEAR, TERM)

    pdf_pages = PdfPages(OUTPUT_FILENAME)

    for i in range(0, len(competencies), 2):
        fig, axs = plt.subplots(2, 1, figsize=PAGE_SIZE)

        for j in range(2):
            if i + j >= len(competencies):
                fig.delaxes(axs[j])
                continue
            
            comp = competencies.iloc[i + j]
            print(comp['CompetencyDesc'])
            print(comp['YearGroupDesc'])
            competency_id = int(comp['CompetencyID'])
            year_group_id = int(comp['YearGroupID'])

            df = load_competency_rates(con, CALENDARYEAR, TERM, competency_id, year_group_id)

            national_match = national_df[
                (national_df["CompetencyID"] == competency_id) &
                (national_df["YearGroupID"] == year_group_id)
            ]
            rate_ly = national_match[national_match["ResultType"] == "National Rate (LY)"]["Rate"]
            rate_ytd = national_match[national_match["ResultType"] == "National Rate (YTD)"]["Rate"]

            national_rate_ly = rate_ly.values[0] if not rate_ly.empty else None
            national_rate_ytd = rate_ytd.values[0] if not rate_ytd.empty else None

            title = f"{comp['CompetencyDesc']} ({comp['YearGroupDesc']})"
            make_figure(axs[j], df, title, national_rate_ly=national_rate_ly, national_rate_ytd=national_rate_ytd)


        plt.tight_layout()
        pdf_pages.savefig(fig)
        plt.close(fig)

    pdf_pages.close()
    print(f"✅ All competency plots saved to {OUTPUT_FILENAME}")

if __name__ == "__main__":
    main()
