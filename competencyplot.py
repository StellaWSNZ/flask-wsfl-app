import matplotlib.pyplot as plt
import pandas as pd
import os
import textwrap
from sqlalchemy import create_engine, text

# ===================
# CONFIGURATION
# ===================
PAGE_SIZE = (11.69, 8.27)  # A4 Landscape
TITLE_SPACE = 0.2
DEBUG = False
OUTPUT_FILENAME = "Competency_Report.pdf"
TERM = 4
CALENDARYEAR = 2024
COMPETENCYID = 5
YEARGROUPID = 1


# ===================
# DATABASE
# ===================
def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, fast_executemany=True)


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
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(data, columns=columns)


# ===================
# PLOTTING
# ===================
def make_figure(df, title):
    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1 + TITLE_SPACE)
    ax.set_xticks([])
    ax.set_yticks([])

    char_limit = 70  # you might tune this
    wrapped_title = textwrap.wrap(title, width=char_limit)

    # Calculate vertical space per line
    line_spacing = 0.05
    start_y = 1 + TITLE_SPACE / 2 + (len(wrapped_title) - 1) * line_spacing / 2

    for i, line in enumerate(wrapped_title):
        ax.text(
            0.5,
            start_y - i * line_spacing,
            line,
            ha='center',
            va='center',
            fontsize=14,
            weight='demibold'
        )

    buffer = 0.05
    buffer_name = 0.2
    height_per = (1 - (buffer * 2)) / len(df)
    width_per = (1 - (buffer * 2) - buffer_name) / 100

    if DEBUG:
        ax.add_patch(plt.Rectangle((0, 1), 1, TITLE_SPACE, edgecolor='red', facecolor='none', linestyle='dashed'))
        ax.add_patch(plt.Rectangle((buffer, buffer), 1 - buffer * 2, 1 - buffer * 2, edgecolor='red', facecolor='none', linestyle='dashed'))

    df = df.sort_values(by=['FunderDesc'], ascending=False)

    for index, row in df.iterrows():
        funder = row['FunderDesc']
        value = row['Rate']
        formatted_value = f"{value * 100:.2f}%"
        y_pos = buffer + height_per * index

        # Draw bar
        ax.add_patch(plt.Rectangle(
            (buffer + buffer_name, y_pos),
            width_per * value * 100,
            height_per,
            edgecolor='black',
            facecolor='none'
        ))

        # Value label
        value_x = buffer + buffer_name + width_per * value * 100
        value_offset = height_per * 0.2
        ha = 'left' if value < 0.2 else 'right'
        value_x += value_offset if ha == 'left' else -value_offset

        ax.text(value_x, y_pos + height_per / 2, formatted_value, ha=ha, va='center', weight='bold')

        # Provider name
        ax.text(buffer + buffer_name - value_offset, y_pos + height_per / 2, funder, ha='right', va='center', weight='bold')

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


# ===================
# MAIN
# ===================
def main():
    con = get_db_engine()
    df = load_competency_rates(con, CALENDARYEAR, TERM, COMPETENCYID, YEARGROUPID)

    try:
        competency_desc = df['CompetencyDesc'].unique()[0]
    except IndexError:
        competency_desc = "Unknown Competency"
    year_group  = df['YearGroupDesc'].unique()[0]
    title = f"{competency_desc}({year_group})"
    fig = make_figure(df, title)
    save_and_open_pdf(fig, OUTPUT_FILENAME)


if __name__ == "__main__":
    main()
