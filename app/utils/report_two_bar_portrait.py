import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt
import pandas as pd
import os
import textwrap
from sqlalchemy import create_engine, text
from datetime import datetime
import pytz

# ===================
# CONFIGURATION
# ===================
PAGE_SIZE = (8.27, 11.69)  # A4 Portrait (width, height) in inches
TITLE_SPACE = 0.05
SUBTITLE_SPACE = 0.02
BUFFER = 0.05
DEBUG = False
DB = True

TERM = 2
CALENDARYEAR = 2025

# Choose which series to plot (order matters) + their colors
# These must match the ResultType values returned by your proc.
vars_to_plot = ["National Rate (LY)", "National Rate (YTD)"]
colors_dict = {
    "National Rate (LY)":  "#2EBDC2",
    "National Rate (YTD)": "#BBE6E9",
}

# Include (YTD)/(LY) tokens in the filename code? Set to False for shorter codes.
INCLUDE_SUFFIX_IN_CODE = True

# ===================
# HELPERS
# ===================
def get_vars_code(vars_list: list[str], *, keep_suffix: bool = True) -> str:
    """
    Turn ["National Rate (LY)", "National Rate (YTD)"] into "NR_LY_NR_YTD"
    If keep_suffix=False, becomes "NR_NR".
    """
    codes = []
    for label in vars_list:
        clean = label.strip()
        suffix = ""
        if "(" in clean and ")" in clean:
            # Extract suffix inside parentheses
            suffix = clean[clean.find("(") + 1 : clean.find(")")]
            clean = clean[: clean.find("(")].strip()

        initials = "".join(w[0].upper() for w in clean.split() if w and w[0].isalnum())
        if keep_suffix and suffix:
            codes.append(f"{initials}_{suffix.upper()}")
        else:
            codes.append(initials)
    return "_".join(codes)

def get_db_engine():
    connection_string = (
        "mssql+pyodbc://"
        f"{os.getenv('WSNZDBUSER')}:{os.getenv('WSNZDBPASS')}"
        "@heimatau.database.windows.net:1433/WSFL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    return create_engine(connection_string, fast_executemany=True)

def load_national_results(con, calendaryear: int, term: int, from_db: bool = True) -> pd.DataFrame:
    """
    Loads national rows. Your proc here uses FunderID=200 to represent 'National'.
    If from_db=False, it will try to read 'funder_rates.csv' from disk.
    """
    if from_db:
        with con.connect() as connection:
            result = connection.execute(
                text("EXEC GetFunderNationalRatesSmart :CalendarYear, :Term, :FunderID"),
                {"CalendarYear": calendaryear, "Term": term, "FunderID": 200}
            )
            data = result.fetchall()
            columns = result.keys()
        return pd.DataFrame(data, columns=columns)
    else:
        return pd.read_csv("funder_rates.csv")

def get_nz_datetime_string():
    nz = pytz.timezone("Pacific/Auckland")
    now_nz = datetime.now(nz)
    return now_nz.strftime("Generated on %d/%m/%Y at %I:%M %p")

# ===================
# VISUALIZATION
# ===================
def draw_key(ax, x, y, vars_to_plot, colors_dict):
    box_size = 0.02
    padding = 0.01
    spacing = 0.15

    total_width = len(vars_to_plot) * spacing - (spacing - 1) * 0.01
    start_x = x - total_width / 2

    for i, label in enumerate(vars_to_plot):
        color = colors_dict.get(label, "#CCCCCC")
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

    if DEBUG:
        ax.add_patch(plt.Rectangle(
            (start_x, y),
            total_width,
            box_size * (8.27 / 11.69),
            edgecolor='red', facecolor='none', linestyle='dashed'
        ))

def make_yeargroup(ax, DEBUG, height, y, BUFFER, subtitle_space, df, vars_to_plot, colors_dict):
    """
    Draws one year-group block. Plots each competency with bars for the series in vars_to_plot.
    """
    year_group = df['YearGroupDesc'].unique()[0]
    ax.text(0.5, y - subtitle_space / 2, f"Years {year_group}", ha='center', va='top', weight='demibold')

    # Keep only requested series, deduplicate on (CompetencyDesc, ResultType)
    df = df[df['ResultType'].isin(vars_to_plot)]
    df = df[['CompetencyDesc', 'ResultType', 'Rate']].drop_duplicates()

    # Layout
    rate_space = 0.10            # gap between competency label column and bar column
    bar_height = 0.018
    bar_vgap = 0.006
    comp_vgap = 0.012
    bar_area_left = 0.5 + rate_space / 2
    bar_area_right = 1 - BUFFER
    bar_area_width = bar_area_right - bar_area_left

    competencies = df['CompetencyDesc'].drop_duplicates().tolist()
    if not competencies:
        # Nothing to draw for this year group
        return

    block_height = (len(vars_to_plot) * (bar_height + bar_vgap)) - bar_vgap
    y_cursor = y - subtitle_space  # top of the first competency block

    if DEBUG:
        ax.add_patch(plt.Rectangle(
            (BUFFER, y - height),
            1 - 2 * BUFFER,
            height,
            edgecolor='red', facecolor='none', linestyle='dashed'
        ))

    for comp in competencies:
        comp_rows = df[df['CompetencyDesc'] == comp]
        comp_center = y_cursor - block_height / 2

        # Competency label (right-aligned)
        ax.text(
            0.5 - rate_space / 2,
            comp_center,
            "\n".join(textwrap.wrap(str(comp), width=50)),
            ha='right', va='center', fontsize=8
        )

        # Bars for each requested series, in declared order
        bar_y = y_cursor - bar_height  # top bar baseline
        for series in vars_to_plot:
            row = comp_rows[comp_rows['ResultType'] == series]
            if not row.empty:
                value = float(row['Rate'].iloc[0])  # value in [0,1]
                # value text in the center column
                ax.text(
                    0.5, bar_y + bar_height / 2,
                    f"{value * 100:.2f}%",
                    ha='center', va='center', fontsize=9
                )
                # bar
                ax.add_patch(plt.Rectangle(
                    (bar_area_left, bar_y),
                    max(0.0, value) * bar_area_width,
                    bar_height,
                    edgecolor='none',
                    facecolor=colors_dict.get(series, "#CCCCCC")
                ))
            bar_y -= (bar_height + bar_vgap)

        # next competency block
        y_cursor -= (block_height + comp_vgap)

    # Legend underneath this year-group block
    key_y = y_cursor - 0.015
    draw_key(ax, x=0.5, y=key_y, vars_to_plot=vars_to_plot, colors_dict=colors_dict)

def make_figure(df, DEBUG, PAGE_SIZE, TITLE_SPACE, subtitle_space, row_heights, BUFFER, vars_to_plot, colors_dict, term, calendaryear):
    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1 + TITLE_SPACE)
    ax.set_xticks([]); ax.set_yticks([])

    title_vars = " vs ".join(vars_to_plot)
    ax.text(
        0.5, 1 + (TITLE_SPACE / 2),
        f'{title_vars} | Term {term}, {calendaryear}',
        ha='center', va='center',
        fontsize=14, weight='demibold'
    )

    if DEBUG:
        ax.add_patch(plt.Rectangle(
            (0, 1), 1, TITLE_SPACE,
            edgecolor='red', facecolor='none', linestyle='dashed'
        ))

    start_y = 1.01
    for group in df['YearGroupDesc'].drop_duplicates():
        if group in row_heights:
            make_yeargroup(
                ax, DEBUG, row_heights[group], start_y, BUFFER, subtitle_space,
                df[df['YearGroupDesc'] == group], vars_to_plot, colors_dict
            )
            start_y -= row_heights[group] + 0.01
        else:
            print(f"Missing rowheight for YearGroupDesc {group}")

    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.text(
        0.01, 0.01,
        get_nz_datetime_string(),
        transform=ax.transAxes,
        fontsize=7, ha='left', va='bottom', color='gray'
    )
    return fig

# ===================
# PUBLIC API
# ===================
def generate_national_report(term: int, calendaryear: int, from_db: bool = True):
    con = get_db_engine()
    df = load_national_results(con, calendaryear, term, from_db)

    # Row heights per year group proportional to number of competencies (plus some headroom)
    df2 = df[['CompetencyDesc', 'YearGroupDesc']].drop_duplicates()
    row_heights = (
        df2['YearGroupDesc'].value_counts().sort_index() /
        (df2['YearGroupDesc'].value_counts().sum() + 2)
    )

    fig = make_figure(
        df, DEBUG, PAGE_SIZE, TITLE_SPACE, SUBTITLE_SPACE,
        row_heights, BUFFER, vars_to_plot, colors_dict,
        term, calendaryear
    )
    return fig

# ===================
# MAIN
# ===================
if __name__ == "__main__":
    con = get_db_engine()
    df = load_national_results(con, CALENDARYEAR, TERM, DB)

    # Row heights per year group proportional to number of competencies
    df2 = df[['CompetencyDesc', 'YearGroupDesc']].drop_duplicates()
    row_heights = (
        df2['YearGroupDesc'].value_counts().sort_index() /
        (df2['YearGroupDesc'].value_counts().sum() + 2)
    )

    fig = make_figure(
        df, DEBUG, PAGE_SIZE, TITLE_SPACE, SUBTITLE_SPACE,
        row_heights, BUFFER, vars_to_plot, colors_dict,
        TERM, CALENDARYEAR
    )

    # Filename with a short code for the variables
    code = get_vars_code(vars_to_plot, keep_suffix=INCLUDE_SUFFIX_IN_CODE)
    output_filename = f"NationalResultReport_{TERM}_{CALENDARYEAR}_{code}.pdf"

    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(output_filename, format='pdf')
    plt.close(fig)
    print(f"✅ PDF saved as {output_filename}")
    try:
        os.startfile(output_filename)
    except Exception:
        pass
