import matplotlib.pyplot as plt
import pandas as pd
import os
import textwrap
from sqlalchemy import create_engine, text

# ===================
# CONFIGURATION
# ===================
PAGE_SIZE = (8.27, 11.69)  # A4 Portrait
TITLE_SPACE = 0.05
SUBTITLE_SPACE = 0.02
BUFFER = 0.05
DEBUG = False
DB = True

TERM = 4
CALENDARYEAR = 2024
OUTPUT_FILENAME = "Competency_Report.pdf"

# ===================
# DATABASE FUNCTIONS
# ===================
def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, fast_executemany=True)

def load_national_results(con, calendaryear, term, from_db=True):
    if from_db:
        with con.connect() as connection:
            result = connection.execute(
                text("EXEC GetNationalRates2 :CalendarYear, :Term"),
                {"CalendarYear": calendaryear, "Term": term}
            )
            data = result.fetchall()
            columns = result.keys()
        return pd.DataFrame(data, columns=columns)
    else:
        return pd.read_csv("provider_rates.csv")

# ===================
# VISUALIZATION FUNCTIONS
# ===================
def draw_key(ax, x, y):
    labels = ['National Rate (LY)', 'National Rate (YTD)']
    colors = ['#2EBDC2', '#BBE6E9']
    box_size = 0.02
    padding = 0.01
    spacing = 0.15

    total_width = len(labels) * spacing - (spacing - 1) * 0.01
    start_x = x - total_width / 2

    for i, (label, color) in enumerate(zip(labels, colors)):
        box_x = start_x + i * spacing
        ax.add_patch(plt.Rectangle(
            (box_x, y), box_size, box_size * (8.27 / 11.69),
            facecolor=color, edgecolor='none'
        ))
        ax.text(
            box_x + box_size + padding,
            y + box_size * (8.27 / 11.69) / 2,
            label, va='center', ha='left', fontsize=7
        )

    if(DEBUG):
        ax.add_patch(plt.Rectangle((start_x, y), 
                                   total_width, 
                                   box_size  * (8.27 / 11.69), 
                                   edgecolor='red', facecolor='none', linestyle='dashed'))

def make_yeargroup(ax, DEBUG, height, y, BUFFER, subtitle_space, df):
    rate_space = 0.1
    year_group = df['YearGroupDesc'].unique()[0]
    ax.text(0.5, y - subtitle_space / 2, f"Years {year_group}", ha='center', va='top', weight='demibold')

    # Filter and deduplicate
    unique_df = df[['CompetencyDesc', 'Rate', 'ResultType']].drop_duplicates()
    unique_df = unique_df[unique_df['ResultType'].isin(['National Rate (LY)', 'National Rate (YTD)'])]
    extra = 0.005
    y = y- extra/2
    height_per = ((height - subtitle_space) / len(unique_df)) - extra
    width_per = round((1 - 0.5 - (rate_space / 2) - BUFFER) / 100, 5)

    if DEBUG:
        ax.add_patch(plt.Rectangle((BUFFER, y - height), 1 - 2 * BUFFER, height, edgecolor='red', facecolor='none', linestyle='dashed'))

    for j, row in enumerate(unique_df.itertuples()):
        center_y = y - subtitle_space - (height_per * j) - (height_per / 2) - (extra*j)
        rate_text = f"{row.Rate * 100:.2f}%"
        ax.text(0.5, center_y, rate_text, ha='center', va='center', fontsize=9)

        if j % 2 == 0:
            ax.text(
                0.5 - rate_space / 2,
                y - subtitle_space - (height_per * (j + 1)) - (extra*j),
                "\n".join(textwrap.wrap(row.CompetencyDesc, width=50)),
                ha='right', va='center', fontsize=8
            )
            col = '#2EBDC2'
        else:
            col = '#BBE6E9'

        ax.add_patch(plt.Rectangle(
            (0.5 + rate_space / 2, center_y - height_per / 2),
            width_per * float(row.Rate) * 100,
            height_per,
            edgecolor='none', facecolor=col
        ))

        if DEBUG:
            ax.add_patch(plt.Rectangle(
                (BUFFER, center_y - height_per / 2),
                1 - 2 * BUFFER,
                height_per,
                edgecolor='red', facecolor='none', linestyle='dotted'
            ))
            ax.add_patch(plt.Rectangle(
                (0.5 - rate_space / 2, center_y - height_per / 2),
                rate_space,
                height_per,
                edgecolor='red', facecolor='none', linestyle='dotted'
            ))

    # Draw key
    key_y = y - subtitle_space - (height_per * len(unique_df)) - 0.015 - (extra * len(unique_df))
    draw_key(ax, x=0.5, y=key_y)

def make_figure(df, DEBUG, TITLE, PAGE_SIZE, TITLE_SPACE, subtitle_space, row_heights, BUFFER):
    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1 + TITLE_SPACE)
    ax.set_xticks([])
    ax.set_yticks([])

    ax.text(
        0.5,
        1 + (TITLE_SPACE / 2),
        TITLE,
        ha='center', va='center',
        fontsize=14, weight='demibold'
    )

    if DEBUG:
        ax.add_patch(plt.Rectangle((0, 1), 1, TITLE_SPACE, edgecolor='red', facecolor='none', linestyle='dashed'))

    start_y = 1.01
    for group in df['YearGroupDesc'].drop_duplicates():
        if group in row_heights:
            make_yeargroup(ax, DEBUG, row_heights[group], start_y, BUFFER, subtitle_space, df[df['YearGroupDesc'] == group])
            start_y -= row_heights[group] + 0.01
        else:
            print(f"Missing rowheight for YearGroupDesc {group}")

    return fig

def save_and_open_pdf(fig, filename):
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(filename, format='pdf')
    plt.close(fig)
    print(f"✅ PDF saved as {filename}")
    try:
        os.startfile(filename)
    except Exception as e:
        print(f"Could not open PDF automatically: {e}")

# nationalreport.py (at the bottom)

def generate_national_report(term, calendaryear, from_db=True):
    con = get_db_engine()
    df = load_national_results(con, calendaryear, term, from_db)

    df2 = df[['CompetencyDesc', 'YearGroupDesc']].drop_duplicates()
    row_heights = (
        df2['YearGroupDesc'].value_counts().sort_index() /
        (df2['YearGroupDesc'].value_counts().sum() + 2)
    )

    title = f'National Rate (LY) vs National Rate (YTD) | Term {term}, {calendaryear}'
    fig = make_figure(df, DEBUG, title, PAGE_SIZE, TITLE_SPACE, SUBTITLE_SPACE, row_heights, BUFFER)
    return fig

# ===================
# MAIN EXECUTION
# ===================
if __name__ == "__main__":
    con = get_db_engine()
    df = load_national_results(con, CALENDARYEAR, TERM, DB)
    
    df2 = df[['CompetencyDesc', 'YearGroupDesc']].drop_duplicates()
    row_heights = (
        df2['YearGroupDesc'].value_counts().sort_index() /
        (df2['YearGroupDesc'].value_counts().sum() + 2)
    )

    title = f'National Rate (LY) vs National Rate (YTD) | Term {TERM}, {CALENDARYEAR}'
    fig = make_figure(df, DEBUG, title, PAGE_SIZE, TITLE_SPACE, SUBTITLE_SPACE, row_heights, BUFFER)

    save_and_open_pdf(fig, OUTPUT_FILENAME)
