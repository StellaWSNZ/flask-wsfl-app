import matplotlib.pyplot as plt
import pandas as pd
import os
from sqlalchemy import create_engine, text
import textwrap

# ===================
# CONFIGURATION
# ===================
PAGE_SIZE = (11.69, 8.27)  # A4 Landscape inches
TITLE_SPACE = 0.2
DEBUG = True
OUTPUT_FILENAME = "Competency_Report.pdf"

TERM = 4
CALENDARYEAR = 2024
COMPETENCYID = 5
YEARGROUPID = 1
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

def load_competencies(con, calendaryear, term, competencyID, yearGroupID):
    with con.connect() as connection:
        result = connection.execute(
            text("EXEC getcompetencyrate :CalendarYear, :Term, :CompetencyID, :YearGroupID"),
            {"CalendarYear": calendaryear, "Term": term, "CompetencyID":competencyID,"YearGroupID":yearGroupID}
        )
        data = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(data, columns=columns)



def get_colour(var):
    colours = {
        'National Rate (LY)': "#2EBDC2",    # Blue
        'Provider Rate (YTD)': "#356FB6",   # Teal
        'Provider Target': "#BBE6E9"        # Light Blue
    }
    return colours.get(var, "#CCCCCC")  # default grey if not found



def save_and_open_pdf(fig, filename):
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(filename, format='pdf')

    try:
        os.startfile(filename)
        
    except Exception as e:
        print(f"Could not open PDF automatically: {e}")

    plt.close(fig)  # Clean up
    print(f"✅ PDF saved as {filename}")

# ===================
# MAIN
# ===================

def main():
    con = get_db_engine()
    TITLE_SPACE = 0.1
    fig, ax = plt.subplots(figsize=PAGE_SIZE)
    
    competencies_df = load_competencies(con, CALENDARYEAR, TERM, COMPETENCYID, YEARGROUPID)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1 + TITLE_SPACE)

    # Calculate top starting y-coordinate for each row
    row_start_y = [1]

    ax.set_xticks([])
    ax.set_yticks([])
    
    ax.text(
        1 / 2,
        1 + (TITLE_SPACE / 2),
        "Achivement Rates by Provider for " + competencies_df.loc[
            (competencies_df['YearGroupID'] == YEARGROUPID) &
            (competencies_df['CompetencyID'] == COMPETENCYID),
            'CompetencyDesc'
        ].unique()[0],
        ha='center', va='center',
        fontsize=14, weight='demibold'
    )
    buffer = 0.05 
    buffer_name = 0.2
    if(DEBUG):
        ax.add_patch(plt.Rectangle(
            (0, 1 ),
            1, TITLE_SPACE,
            linewidth=1,
            edgecolor='red',
            facecolor='none',
            linestyle='dashed'
        ))

        ax.add_patch(plt.Rectangle(
            (0+buffer, buffer),
            1-buffer*2, 1-buffer*2,
            linewidth=1,
            edgecolor='red',
            facecolor='none',
            linestyle='dashed'
        ))
    
    height_per = (1-(buffer*2))/len(competencies_df)
    width_per =( 1 - (buffer*2) - buffer_name)/ 100
    competencies_df=competencies_df.sort_values(by=['ProviderDesc'], ascending=False)
    for index, row in competencies_df.iterrows():
        
            
        # print(row)
        provider = row['ProviderDesc']
        value = row['Rate']
        formatted_value = f"{value * 100:.2f}%"
        ax.add_patch(plt.Rectangle(
            (buffer+buffer_name, 0+buffer + (height_per*(index))),
             width_per * value * 100, height_per,
            linewidth=1,
            edgecolor='black',
            facecolor='none'
        ))
        if(value < 0.2):
            ax.text(
                buffer+buffer_name + width_per * value * 100 + height_per * 0.2, 0+buffer + (height_per*(index + 0.5)) , 
                formatted_value, ha='left', va='center',  weight='bold'
                )
        else:
            ax.text(
                buffer+buffer_name + width_per * value * 100 - height_per * 0.2, 0+buffer + (height_per*(index + 0.5)) , 
                formatted_value, ha='right', va='center',  weight='bold'
                )
        ax.text(
           buffer+buffer_name - height_per * 0.2, 0+buffer + (height_per*(index + 0.5)) , 
           provider, ha='right', va='center',  weight='bold'
        )

    save_and_open_pdf(fig, OUTPUT_FILENAME)

if __name__ == "__main__":
    main()
