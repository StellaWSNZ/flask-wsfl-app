import matplotlib
matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt
import pandas as pd
import os
from sqlalchemy import create_engine, text
import textwrap
from datetime import date, datetime
import pytz

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

TERM = 2
CALENDARYEAR = 2025

# Choose the 3 series (in order) you want to plot + their colors
DEFAULT_VARS_TO_PLOT = [
    "National Rate (YTD)",
    "Funder Rate (YTD)",
    "WSNZ Target",
]

DEFAULT_COLORS_DICT = {
    "Funder Rate (YTD)": "#2EBDC2",
    "WSNZ Target": "#356FB6",
    "National Rate (YTD)": "#BBE6E9",
}

# ===================
# DB HELPERS
# ===================
def get_vars_code(vars_list):
    """Return a short code like NR_FR_FT from a list of var labels."""
    codes = []
    for var in vars_list:
        parts = var.replace("(", "").replace(")", "").split()
        initials = "".join(word[0].upper() for word in parts if word[0].isalnum())
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

def load_competencies(con, calendaryear, term):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC GetRelevantCompetencies :CalendarYear, :Term"),
            {"CalendarYear": calendaryear, "Term": term}
        )
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(data, columns=columns)

def load_funder_name(con, FunderID: int) -> str:
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request"),
            {"Request": "FunderDropdown"}
        )
        data = result.fetchall()
        columns = result.keys()
    df = pd.DataFrame(data, columns=columns)

    match = df.loc[df["FunderID"] == FunderID, "Description"]
    if match.empty:
        raise ValueError(f"FunderID {FunderID} not found in FunderDropdown result")
    return match.iloc[0]

def load_funder_results(con, calendaryear: int, term: int, funder_ids: list[int]) -> pd.DataFrame:
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC GetFunderNationalRates_All :CalendarYear, :Term"),
            {"CalendarYear": calendaryear, "Term": term}
        )
        data = result.fetchall()
        columns = result.keys()

    df = pd.DataFrame(data, columns=columns)

    # Ensure FunderID is numeric (National/Best rows will be NaN)
    if "FunderID" in df.columns:
        df["FunderID"] = pd.to_numeric(df["FunderID"], errors="coerce")

    # Filter by selected funders, keep rows where FunderID is NULL (e.g., National rows)
    ids = set(int(x) for x in funder_ids)
    df = df[df["FunderID"].isin(ids) | df["FunderID"].isna()].copy()

    return df

# ===================
# UTIL
# ===================

def get_nz_datetime_string():
    nz = pytz.timezone("Pacific/Auckland")
    now_nz = datetime.now(nz)
    return now_nz.strftime("%d/%m/%Y %I:%M %p")

def sanitize_filename(s):
    return s.replace(" ", "_").replace("/", "_")

def save_and_open_pdf(fig, filename):
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(filename, format='pdf')
    try:
        os.startfile(filename)
    except Exception:
        pass
    plt.close(fig)

# ===================
# PLOTTING
# ===================

def make_grid(ax, n_cols, n_rows, row_heights, title_space, subtitle_space, df, df_results, debug, vars_to_plot, colors_dict):
    total_height = sum(row_heights)
    subtitles = sorted(df['YearGroupDesc'].unique())

    ax.set_xlim(0, n_cols)
    ax.set_ylim(0, total_height + title_space)

    # Calculate top starting y-coordinate for each row band
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
                    subtitle_space, debug, vars_to_plot, colors_dict
                )
                idx += 1

    for spine in ax.spines.values():
        spine.set_visible(False)

def draw_key(ax, x, y, vars_to_plot, colors_dict):
    box_size = 0.03
    padding = 0.01
    spacing = 0.25
    total_width = len(vars_to_plot) * spacing - (spacing - 1) * 0.01
    start_x = x - total_width / 2

    for i, label in enumerate(vars_to_plot):
        color = colors_dict.get(label, "#CCCCCC")
        box_x = start_x + i * spacing
        ax.add_patch(plt.Rectangle(
            (box_x, y), box_size, box_size * (11.69 / 8.27),
            facecolor=color, edgecolor='black'
        ))
        ax.text(
            box_x + box_size + padding, y + box_size * (11.69 / 8.27) / 2,
            label, va='center', ha='left', fontsize=7
        )

def make_yeargroup_plot(ax, x, y_top, cell_height, title, df_relcomp, df_results, subtitle_space, debug, vars_to_plot, colors_dict):
    # Title at top-center of the cell
    ax.text(
        x + 0.5,
        y_top - subtitle_space / 2,
        "Competencies Related to Years " + title,
        ha='center', va='center', weight='bold',
        fontsize=11
    )

    if debug:
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

    # Filter matching competencies for this YearGroup
    df_relcomp = df_relcomp[df_relcomp['YearGroupDesc'] == title]
      
    # Merge in the results
    df = pd.merge(
        df_relcomp,
        df_results,
        on=['YearGroupID', 'CompetencyID', 'CompetencyDesc', 'YearGroupDesc'],
        how='inner'
    )

    # Keep only the series the user wants to plot, in their order
    df = df[df['ResultType'].isin(vars_to_plot)]
    # Sort by competency for stable layout
    df = df.sort_values(by=['YearGroupID', 'CompetencyID'])

    cell_left = x
    cell_right = x + 1
    cell_center = (cell_left + cell_right) / 2

    competency_text_offset = 0.08
    bar_start_offset = competency_text_offset

    competency_text_x = cell_center - competency_text_offset
    percent_text_x = cell_center
    bar_start_x = cell_center + bar_start_offset
    bar_max_width = cell_right - bar_start_x - 0.02

    y_start = y_top - subtitle_space - 0.04
    competency_spacing = 0.01
    rate_spacing = 0.035
    y_current = y_start

    

        # 🔹 Only draw bars for series that actually exist for this competency
    for comp_value in df['CompetencyDesc'].unique():
        comp_rows = df[df['CompetencyDesc'] == comp_value]
        center_of_three_rates = y_current - (rate_spacing * (len(vars_to_plot) / 2))

        # Competency text (wrapped), right-aligned
        ax.text(
            competency_text_x,
            center_of_three_rates,
            "\n".join(textwrap.wrap(comp_value, width=35)),
            ha='right', va='center',
            fontsize=8
        )

        for var_value in vars_to_plot:
            # rows for this competency + this intended series
            rate_row = comp_rows[comp_rows['ResultType'] == var_value]
 
            if not rate_row.empty:
                # Use the actual ResultType from the data as the colour key
                result_type = str(rate_row['ResultType'].iloc[0])
                value = float(rate_row['Rate'].iloc[0])
                formatted_value = f"{value * 100:.2f}%"
                colour_key = result_type
            else:
                # No row for this series → treat as 0% but keep its slot
                value = 0.0
                formatted_value = ""
                colour_key = var_value   # fall back to the intended series
         
            # Percentage text (always shown, may be blank)
            ax.text(
                percent_text_x,
                y_current,
                formatted_value,
                ha='center', va='top',
                fontsize=9
            )

            # Bar (width 0 if value is 0)
            bar_height = 0.025
            bar_spacing = 0.005
            ax.add_patch(plt.Rectangle(
                (bar_start_x, y_current - 0.02 - bar_spacing),
                max(0.0, value) * bar_max_width,
                bar_height,
                facecolor=colors_dict.get(colour_key, "#CCCCCC"),
                edgecolor='none'
            ))

            # Move down for the next series
            y_current -= rate_spacing

        y_current -= competency_spacing



    # Legend under the last item in this cell
    draw_key(ax, cell_center, y_current - 0.05, vars_to_plot, colors_dict)

# r3/report_three_bar_landscape.py

def create_competency_report(term, year, funder_id, vars_to_plot, colors_dict,
                             funder_name=None, rows=None):
    con = get_db_engine()
    competencies_df = load_competencies(con, year, term)
    competencies_df = competencies_df[competencies_df['WaterBased'] == 1]

    # use provided rows if present; otherwise fall back to funder-only (old behaviour)
    if rows is not None:
        df_results = pd.DataFrame(rows)
    else:
        df_results = load_funder_results(con, year, term, [funder_id])

    if funder_name is None and funder_id is not None:
        funder_name = load_funder_name(con, funder_id)
        title = "Competency Report for {funder_name}"
    else:
        title = "National Result (LY) vs National Result (YTD) vs WSNZ Target"
    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    ax.set_position([0.0, 0.0, 1.0, 1.0])

    make_grid(
        ax, N_COLS, N_ROWS, ROW_HEIGHTS,
        TITLE_SPACE, SUBTITLE_SPACE,
        competencies_df, df_results,   # <- pass the LONG rows
        DEBUG, vars_to_plot, colors_dict
    )

    ax.text(N_COLS/2, N_ROWS + (TITLE_SPACE/2),
            title,
            ha='center', va='center', fontsize=14, weight='bold')
    ax.text(N_COLS/2, N_ROWS + (TITLE_SPACE/2) - 0.04,
            f"Term {term}, {year}  |  Generated {get_nz_datetime_string()}",
            ha='center', va='top', fontsize=9, color='gray')
    return fig

# ===================
# MAIN
# ===================

def main():
    con = get_db_engine()
    selected_funders = [5, 6]
    output_folder = f"CompetencyReports_Term{TERM}_{CALENDARYEAR}"
    os.makedirs(output_folder, exist_ok=True)

    for funder_id in selected_funders:
        funder_name = load_funder_name(con, funder_id)
        safe_funder = sanitize_filename(funder_name)
        today = date.today().isoformat().replace("-", ".")
        vars_code = get_vars_code(vars_to_plot)

        filename = f"{safe_funder}_Term{TERM}_{CALENDARYEAR}_{vars_code}_CompetencyReport_{today}.pdf"
        filepath = os.path.join(output_folder, filename)

        competencies_df = load_competencies(con, CALENDARYEAR, TERM)
        competencies_df = competencies_df[competencies_df['WaterBased'] == 1]
        fundersresults = load_funder_results(con, CALENDARYEAR, TERM, [funder_id])

        fig, ax = plt.subplots(figsize=PAGE_SIZE)
        make_grid(
            ax, N_COLS, N_ROWS, ROW_HEIGHTS, TITLE_SPACE, SUBTITLE_SPACE,
            competencies_df, fundersresults, DEBUG, vars_to_plot, colors_dict
        )

        # Main heading
        ax.text(
            N_COLS / 2,
            N_ROWS + (TITLE_SPACE / 2),
            f"Competency Report for {funder_name}",
            ha='center', va='center',
            fontsize=14, weight='bold'
        )

        # Subheading: term/year + selected vars + generated time
        vars_str = " | ".join(vars_to_plot)
        ax.text(
            N_COLS / 2,
            N_ROWS + (TITLE_SPACE / 2) - 0.04,
            f"Term {TERM}, {CALENDARYEAR}  •  {vars_str}  •  Generated {get_nz_datetime_string()}",
            ha='center', va='top',
            fontsize=9, color='gray'
        )

        save_and_open_pdf(fig, filepath)

if __name__ == "__main__":
    main()
