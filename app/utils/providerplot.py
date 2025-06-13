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
ROW_HEIGHTS = [1.1, 0.9]
N_COLS = 2
N_ROWS = 2
DEBUG = False

TERM = 1
CALENDARYEAR = 2025

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

def load_provider_name(con, ProviderID):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC FlaskHelperFunctions :Request"),
            {"Request": "ProviderDropdown"}
        )
        data = result.fetchall()
        columns = result.keys()
    provider = pd.DataFrame(data, columns=columns)
    return provider.loc[provider['ProviderID'] == ProviderID, 'Description'].values[0]

def load_provider_results(con, calendaryear, term, ProviderID):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC [GetProviderNationalRatesSmart] :CalendarYear, :Term, :ProviderID"),
            {"CalendarYear": calendaryear, "Term": term, "ProviderID": ProviderID}
        )
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(data, columns=columns)

def get_colour(var):
    colours = {
        'Funder Rate (YTD)': "#2EBDC2",
        'Provider Rate (YTD)': "#356FB6",
        'Funder Target': "#BBE6E9"
    }
    return colours.get(var, "#CCCCCC")

def sanitize_filename(s):
    return s.replace(" ", "_").replace("/", "_")

def draw_key(ax, x, y):
    labels = ['Funder Rate (YTD)', 'Provider Rate (YTD)', 'Funder Target']
    colors = ['#2EBDC2', '#356FB6', '#BBE6E9']
    box_size = 0.03
    padding = 0.01
    spacing = 0.25
    total_width = len(labels) * spacing - (spacing - 1) * 0.01
    start_x = x - total_width / 2

    for i, (label, color) in enumerate(zip(labels, colors)):
        box_x = start_x + i * spacing
        ax.add_patch(plt.Rectangle(
            (box_x, y), box_size, box_size * (11.69/8.27),
            facecolor=color, edgecolor='black'
        ))
        ax.text(
            box_x + box_size + padding, y + box_size * (11.69/8.27) / 2,
            label, va='center', ha='left', fontsize=7
        )

def make_yeargroup_plot(ax, x, y_top, cell_height, title, df_relcomp, df_results, subtitle_space, debug=False):
    ax.text(
        x + 0.5, y_top - subtitle_space / 2,
        "Competencies Related to Years " + title,
        ha='center', va='center', weight='bold', fontsize=11
    )

    df_relcomp = df_relcomp[df_relcomp['YearGroupDesc'] == title]
    df = pd.merge(
        df_relcomp, df_results,
        on=['YearGroupID', 'CompetencyID', 'CompetencyDesc', 'YearGroupDesc'],
        how='inner'
    )
    vars = ['Funder Rate (YTD)', 'Provider Rate (YTD)', 'Funder Target']
    df = df[df['ResultType'].isin(vars)].sort_values(by=['YearGroupID', 'CompetencyID'])

    competency_text_offset = 0.08
    bar_start_offset = competency_text_offset
    cell_left = x
    cell_right = x + 1
    cell_center = (cell_left + cell_right) / 2
    competency_text_x = cell_center - competency_text_offset
    percent_text_x = cell_center
    bar_start_x = cell_center + bar_start_offset
    bar_max_width = cell_right - bar_start_x - 0.02

    y_start = y_top - subtitle_space - 0.04
    rate_spacing = 0.035
    competency_spacing = 0.01
    y_current = y_start

    for comp_value in df['CompetencyDesc'].unique():
        comp_rows = df[df['CompetencyDesc'] == comp_value]
        center_of_three_rates = y_current - (rate_spacing * 1.5)

        ax.text(
            competency_text_x, center_of_three_rates,
            "\n".join(textwrap.wrap(comp_value, width=35)),
            ha='right', va='center', fontsize=8
        )

        for var_value in vars:
            rate_row = comp_rows[comp_rows['ResultType'] == var_value]
            if not rate_row.empty:
                value = rate_row['Rate'].iloc[0]
                formatted_value = f"{value * 100:.2f}%"
                ax.text(percent_text_x, y_current, formatted_value, ha='center', va='top', fontsize=9)
                bar_height = 0.025
                bar_spacing = 0.005
                ax.add_patch(plt.Rectangle(
                    (bar_start_x, y_current - 0.02 - bar_spacing),
                    value * bar_max_width, bar_height,
                    facecolor=get_colour(var_value), edgecolor='none'
                ))
                y_current -= rate_spacing

        y_current -= competency_spacing

    draw_key(ax, cell_center, y_current - 0.05)

def make_grid(ax, n_cols, n_rows, row_heights, title_space, subtitle_space, df, df_results, debug=False):
    total_height = sum(row_heights)
    subtitles = sorted(df['YearGroupDesc'].unique())

    ax.set_xlim(0, n_cols)
    ax.set_ylim(0, total_height + title_space)
    row_start_y = [total_height]
    for height in row_heights[:-1]:
        row_start_y.append(row_start_y[-1] - height)

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    idx = 0
    for row in range(n_rows):
        for col in range(n_cols):
            if idx < len(subtitles):
                make_yeargroup_plot(
                    ax, col, row_start_y[row], row_heights[row],
                    subtitles[idx], df, df_results, subtitle_space, debug
                )
                idx += 1

def save_and_open_pdf(fig, filename):
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(filename, format='pdf')
    try:
        os.startfile(filename)
    except Exception as e:
        print(f"Could not open PDF automatically: {e}")
    plt.close(fig)
    print(f"✅ PDF saved as {filename}")

def load_all_providers(con):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC FlaskHelperFunctions @Request = :Request"),
            {"Request": "Providers"}
        )
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame.from_records(data, columns=columns).reset_index(drop=True)

def create_competency_report(term, year, provider_id, provider_name=None):
    con = get_db_engine()

    competencies_df = load_competencies(con, year, term)
    competencies_df = competencies_df[competencies_df['WaterBased'] == 1]

    providersresults = load_provider_results(con, year, term, provider_id)

    if not provider_name:
        print("🔎 Looking up provider name...")
        provider_name = load_provider_name(con, provider_id)
        print(f"📛 Provider name resolved: {provider_name}")

    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    make_grid(ax, N_COLS, N_ROWS, ROW_HEIGHTS, TITLE_SPACE, SUBTITLE_SPACE, competencies_df, providersresults, DEBUG)

    ax.text(
        N_COLS / 2,
        N_ROWS + (TITLE_SPACE / 2),
        f"Competency Report for {provider_name}",
        ha='center', va='center',
        fontsize=14, weight='demibold'
    )

    return fig


# ===================
# MAIN
# ===================

def main():
    con = get_db_engine()

    providers_df = load_all_providers(con)

    output_folder = f"CompetencyReports_Term{TERM}_{CALENDARYEAR}"
    os.makedirs(output_folder, exist_ok=True)

    for idx, row in providers_df.iterrows():
        print(f"\n➡️ Processing index {idx}")
        

        provider_id = row['ProviderID']
        provider_name = row['Description']
        print(f"🏷️  Provider ID: {provider_id}, Name: {provider_name}")

        safe_provider = sanitize_filename(provider_name)
        today = date.today().isoformat().replace("-", ".")
        filename = f"{safe_provider}_Term{TERM}_{CALENDARYEAR}_CompetencyReport_{today}.pdf"
        filepath = os.path.join(output_folder, filename)

        fig = create_competency_report(TERM, CALENDARYEAR, provider_id, provider_name)

        save_and_open_pdf(fig, filepath)

    print("✅ All done.")


if __name__ == "__main__":
    print("👟 Running script...")
    try:
        main()
        print("🏁 Done.")
    except Exception as e:
        print("❌ Error:", e)
