import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt
import pandas as pd
import os
from sqlalchemy import create_engine, text
import textwrap
from datetime import date

# ===================
# CONFIGURATION
# ===================
PAGE_SIZE = (11.69, 8.27)  # A4 Landscape inches
TITLE_SPACE = 0.2
SUBTITLE_SPACE = 0.05
ROW_HEIGHTS = [1.1, 0.9]  # Top row 1.1, bottom row 0.9
N_COLS = 2
N_ROWS = 2
DEBUG = False
OUTPUT_FILENAME = "Competency_Report.pdf"


TERM = 1
CALENDARYEAR = 2025
funder_id = 5

# ===================
# FUNCTIONS
# ===================

def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, fast_executemany=True)

def load_competencies(con, calendaryear, term):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC GetRelevantCompetencies :CalendarYear, :Term"),
            {"CalendarYear": calendaryear, "Term": term}
        )
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(data, columns=columns)

def load_funder_name(con, FunderID):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request"),
            {"Request": "FunderDropdown"}
        )
        data = result.fetchall()
        columns = result.keys()
    funder = pd.DataFrame(data, columns=columns)
    return funder.loc[FunderID, 'Description']

def load_funder_results(con, calendaryear, term, FunderID):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC GetFunderNationalRates2 :CalendarYear, :Term, :FunderID"),
            {"CalendarYear": calendaryear, "Term": term, "FunderID": FunderID}
        )
        data = result.fetchall()
        columns = result.keys()
        #print(pd.DataFrame(data, columns=columns).head())
    return pd.DataFrame(data, columns=columns)

# graphing.py
def create_competency_report(term, year, funder_id, funder_name=None):
    con = get_db_engine()
    competencies_df = load_competencies(con, year, term)
    competencies_df = competencies_df[competencies_df['WaterBased'] == 1]
    fundersresults = load_funder_results(con, year, term, funder_id)

    if funder_name is None:
        funder_name = load_funder_name(con, funder_id)

    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    make_grid(ax, N_COLS, N_ROWS, ROW_HEIGHTS, TITLE_SPACE, SUBTITLE_SPACE, competencies_df, fundersresults, DEBUG)

    ax.text(
        N_COLS / 2,
        N_ROWS + (TITLE_SPACE / 2),
        "Competency Report for " + funder_name,
        ha='center', va='center',
        fontsize=14, weight='demibold'
    )
    return fig


def get_colour(var):
    colours = {
        'National Rate (LY)': "#2EBDC2",    # Blue
        'Funder Rate (YTD)': "#356FB6",   # Teal
        'Funder Target': "#BBE6E9"        # Light Blue
    }
    return colours.get(var, "#CCCCCC")  # default grey if not found

def make_grid(ax, n_cols, n_rows, row_heights, title_space, subtitle_space, df, df_results, debug=False):
    total_height = sum(row_heights)
    subtitles = sorted(df['YearGroupDesc'].unique())

    ax.set_xlim(0, n_cols)
    ax.set_ylim(0, total_height + title_space)

    # Calculate top starting y-coordinate for each row
    row_start_y = [total_height]
    for height in row_heights[:-1]:
        row_start_y.append(row_start_y[-1] - height)

    ax.set_xticks([])
    ax.set_yticks([])

    idx = 0
    for row in range(n_rows):
        for col in range(n_cols):
            if idx < len(subtitles):
                make_yeargroup_plot(
                    ax, col, row_start_y[row], row_heights[row],
                    subtitles[idx], df, df_results,
                    subtitle_space, debug
                )
                idx += 1

def draw_key(ax, x, y):
    labels = ['National Rate (LY)', 'Funder Rate (YTD)', 'Funder Target']
    colors = ['#2EBDC2', '#356FB6', '#BBE6E9']
    box_size = 0.03
    padding = 0.01
    spacing = 0.25
    # (11.69, 8.27)
    # Calculate total width of the key
    total_width = len(labels) * spacing - (spacing - 1) * 0.01   
    start_x = x - total_width / 2

    for i, (label, color) in enumerate(zip(labels, colors)):
        box_x = start_x + i * spacing
        ax.add_patch(plt.Rectangle(
            (box_x, y), box_size, box_size * ( 11.69/8.27 ),
            facecolor=color, edgecolor='black'
        ))
        ax.text(
            box_x + box_size + padding, y + box_size *  ( 11.69/8.27 ) / 2,
            label, va='center', ha='left', fontsize=7
        )    



def make_yeargroup_plot(ax, x, y_top, cell_height, title, df_relcomp, df_results, subtitle_space, debug=False):
    # Title at top-center
    ax.text(
        x + 0.5,
        y_top - subtitle_space / 2,
        "Competencies Related to Years " + title,
        ha='center', va='center', weight='bold',
        fontsize=11
    )

    if debug:
        # Draw debug red box and line
        ax.axhline(
            y_top - subtitle_space,
            xmin=x, xmax=x + 1,
            color='red', linewidth=1, linestyle='dashed'
        )
        ax.add_patch(plt.Rectangle(
            (x, y_top - cell_height),
            1, cell_height,
            linewidth=1,
            edgecolor='red',
            facecolor='none',
            linestyle='dashed'
        ))

    # Filter matching competencies
    df_relcomp = df_relcomp[df_relcomp['YearGroupDesc'] == title]

    df = pd.merge(
        df_relcomp,
        df_results,
        on=['YearGroupID', 'CompetencyID', 'CompetencyDesc','YearGroupDesc'],
        how='inner'
    )
    vars = ['National Rate (LY)', 'Funder Rate (YTD)', 'Funder Target']
    df = df[df['ResultType'].isin(vars)]
    df = df.sort_values(by=['YearGroupID', 'CompetencyID'])
    cell_left = x       # start of this grid cell
    cell_right = x + 1  # end of this grid cell
    cell_center = (cell_left + cell_right) / 2

    competency_text_offset = 0.08
    bar_start_offset = competency_text_offset

    # Where competency text goes
    competency_text_x = cell_center - competency_text_offset
    # Where % text goes
    percent_text_x = cell_center
    # Where blue bar starts
    bar_start_x = cell_center + bar_start_offset
    # How much horizontal space available for the bar
    bar_max_width = cell_right - bar_start_x - 0.02

    # Set starting y position inside the box
    y_start = y_top - subtitle_space - 0.04
    competency_spacing = 0.01
    rate_spacing = 0.035
    y_current = y_start

    for comp_value in df['CompetencyDesc'].unique():
        """
        if debug:
            ax.add_patch(plt.Rectangle(
                (cell_left, y_current ),          # NO offset here! Just y_current
                1,                               # Full width = 1
                rate_spacing * 3,                # Height covering 3 rates
                facecolor='none',
                edgecolor='red',
                linestyle='dashed',
                linewidth=1
            ))
        """
        comp_rows = df[df['CompetencyDesc'] == comp_value]

        center_of_three_rates = y_current - (rate_spacing * 1.5)

        # Draw the competency text (wrapped), right-aligned
        ax.text(
            competency_text_x,
            center_of_three_rates,
            "\n".join(textwrap.wrap(comp_value, width=35)),
            ha='right', va='center',
            fontsize=8
        )

        for var_value in vars:
            rate_row = comp_rows[comp_rows['ResultType'] == var_value]
            if not rate_row.empty:
                value = rate_row['Rate'].iloc[0]
                formatted_value = f"{value * 100:.2f}%"

                # Plot the percentage number
                ax.text(
                    percent_text_x,
                    y_current,
                    formatted_value,
                    ha='center', va='top',
                    fontsize=9
                )


                # Draw the bar
                bar_start_x = cell_center + bar_start_offset
                bar_max_width = cell_right  - bar_start_x - 0.02  # leave a little buffer at the right side
                bar_height = 0.025
                bar_spacing = 0.005

                # Blue bar (starting right of % text)
                ax.add_patch(plt.Rectangle(
                    (bar_start_x, y_current - 0.02 - bar_spacing),
                    value * bar_max_width,
                    bar_height,
                    facecolor=get_colour(var_value),
                    edgecolor='none'
                ))

                y_current -= rate_spacing

        y_current -= competency_spacing
    draw_key(ax, cell_center, y_current-0.05)

def load_all_funders(con):
    with con.connect() as connection:
        result = connection.execute(
            text("Select * from Funder")
        )
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(data, columns=columns).reset_index(drop=True)

def save_and_open_pdf(fig, filename):
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(filename, format='pdf')

    try:
        os.startfile(filename)
        
    except Exception as e:
        print(f"Could not open PDF automatically: {e}")

    plt.close(fig)  # Clean up
    print(f"✅ PDF saved as {filename}")

def sanitize_filename(s):
    return s.replace(" ", "_").replace("/", "_")  # remove problematic characters

funder_name = load_funder_name(get_db_engine(), funder_id)
safe_funder = sanitize_filename(funder_name)
today = date.today().isoformat().replace("-", ".")

OUTPUT_FILENAME = f"{safe_funder}_Term{TERM}_{CALENDARYEAR}_CompetencyReport_{today}.pdf"

# ===================
# MAIN
# ===================

def main():
    con = get_db_engine()
    funders_df = load_all_funders(con)

    # Make output folder
    output_folder = f"CompetencyReports_Term{TERM}_{CALENDARYEAR}"
    os.makedirs(output_folder, exist_ok=True)

    for idx, row in funders_df.iterrows():
        funder_id = row['FunderID']
        funder_name = row['Description']
        safe_funder = sanitize_filename(funder_name)
        today = date.today().isoformat().replace("-", ".")
        filename = f"{safe_funder}_Term{TERM}_{CALENDARYEAR}_CompetencyReport_{today}.pdf"
        filepath = os.path.join(output_folder, filename)

        competencies_df = load_competencies(con, CALENDARYEAR, TERM)
        competencies_df = competencies_df[competencies_df['WaterBased'] == 1]
        fundersresults = load_funder_results(con, CALENDARYEAR, TERM, funder_id)

        fig, ax = plt.subplots(figsize=PAGE_SIZE)
        make_grid(ax, N_COLS, N_ROWS, ROW_HEIGHTS, TITLE_SPACE, SUBTITLE_SPACE, competencies_df, fundersresults, DEBUG)

        ax.text(
            N_COLS / 2,
            N_ROWS + (TITLE_SPACE / 2),
            "Competency Report for " + funder_name,
            ha='center', va='center',
            fontsize=14, weight='demibold'
        )

        save_and_open_pdf(fig, filepath)

if __name__ == "__main__":
    main()
