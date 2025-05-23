# app/utils/processing.py
import pandas as pd
from sqlalchemy import text
from .database import get_db_engine


def process_uploaded_csv(df, term, calendaryear):
    engine = get_db_engine()
    processing_status["current"] = 0
    processing_status["total"] = len(df)
    processing_status["done"] = False
    errors = []
    valid_data = []

    with engine.connect() as connection:
        # Get all competencies
        result = connection.execute(
            text("EXEC GetRelevantCompetencies :CalendarYear, :Term"),
            {"CalendarYear": calendaryear, "Term": term}
        )
        competencies = pd.DataFrame(result.fetchall(), columns=result.keys())

        label_map = (
            competencies.assign(
                label=lambda d: d['CompetencyDesc'].astype(str) + "<br> (" + d['YearGroupDesc'].astype(str) + ")",
                col_order=lambda d: d['YearGroupID'].astype(str).str.zfill(2) + "-" + d['CompetencyID'].astype(str).str.zfill(4)
            )
            [['CompetencyID', 'YearGroupID', 'label', 'col_order']]
            .drop_duplicates()
            .sort_values('col_order')
        )
        labels = label_map['label'].tolist()


    for _, row in df.iterrows():
        processing_status["current"] += 1

        try:
            # Fully fetch the result BEFORE doing another SQL call
            with engine.connect() as connection:
                # Call CheckNSNMatch
                result = connection.execute(
                    text("""EXEC CheckNSNMatch 
                        :NSN, :FirstName, :PreferredName, :LastName,
                        :BirthDate, :Ethnicity, :CalendarYear, :Term
                    """),
                    {
                        "NSN": row['NSN'] or None,
                        "FirstName": row['FirstName'] or None,
                        "PreferredName": row.get('PreferredName') or None,
                        "LastName": row['LastName'] or None,
                        "BirthDate": row['BirthDate'] if pd.notna(row['BirthDate']) else None,
                        "Ethnicity": row.get('Ethnicity') or None,
                        "CalendarYear": calendaryear,
                        "Term": term
                    }
                )
                result_row = dict(result.mappings().first())


        
            



            if 'Error' in result_row and result_row['Error']:
                
                errors.append(result_row)
                
            elif result_row.get('Message') == 'NSN not found in Student table':
                valid_data.append({
                    'NSN': result_row['NSN'],
                    'FirstName': result_row.get('FirstName'),
                    'LastName': result_row.get('LastName'),
                    'PreferredName': result_row.get('PreferredName'),
                    'BirthDate': result_row.get('BirthDate'),
                    'Ethnicity': result_row.get('Ethnicity'),
                    'YearLevel': result_row.get('YearLevel'),
                    **{label: '' for label in labels},
                    "Scenario One - Selected <br> (7-8)": "",
                    "Scenario Two - Selected <br> (7-8)": ""
                })
            else:
                # Now it's safe to query again
                 with engine.connect() as connection:
                    # Fetch Scenario
                    
                    scenario_result = connection.execute(
                        text("EXEC FlaskHelperFunctions :Request, :Number"),
                        {"Request": "StudentScenario", "Number": result_row['NSN']}
                    )

                    scenario_query = pd.DataFrame(scenario_result.fetchall(), columns=scenario_result.keys())

                    # Build dictionary
                    if scenario_query.shape[0] > 0:
                        scenario_data = {
                            "Scenario One - Selected <br> (7-8)": scenario_query.iloc[0].get("Scenario1", ""),
                            "Scenario Two - Selected <br> (7-8)": scenario_query.iloc[0].get("Scenario2", "")
                        }
                    else:
                        scenario_data = {
                            "Scenario One - Selected <br> (7-8)": "",
                            "Scenario Two - Selected <br> (7-8)": ""
                        }
                    # Fetch Competency Status
                    comp_result = connection.execute(
                        text("EXEC GetStudentCompetencyStatus :NSN, :Term, :CalendarYear"),
                        {"NSN": result_row['NSN'], "Term": term, "CalendarYear": calendaryear}
                    )
                    comp = pd.DataFrame(comp_result.fetchall(), columns=comp_result.keys())
                    comp = comp.merge(label_map, on=['CompetencyID', 'YearGroupID'], how='inner')
                    comp_row = comp.set_index('label')['CompetencyStatusID'].reindex(labels).fillna(0).astype(int).to_dict()
                    comp_row = {k: ('Y' if v == 1 else '') for k, v in comp_row.items()}
                    #print(f"NSN {result_row['NSN']} - Competencies columns: {comp.columns.tolist()}")
                    #print(f"Competencies fetched: {len(comp)} rows")

                    if 'label' not in comp.columns:
                        raise ValueError("Competencies not found for NSN")
                   

                    # Add personal info fields up front
                    full_row = {
                        'NSN': result_row.get('NSN'),
                        'FirstName': result_row.get('FirstName'),
                        'LastName': result_row.get('LastName'),
                        'PreferredName': result_row.get('PreferredName'),
                        'BirthDate': result_row.get('BirthDate'),
                        'Ethnicity': result_row.get('Ethnicity'),
                        'YearLevel': result_row.get('YearLevel'),
                        **comp_row,  # Add all competency columns
                        **scenario_data
                    }

                    valid_data.append(full_row)


        except Exception as e:
            errors.append({"NSN": row.get('NSN', None), "Error": str(e)})

   
    df_valid = pd.DataFrame(valid_data)
    if not df_valid.empty:
        cols = df_valid.columns.tolist()
        for col in ["Scenario One - Selected <br> (7-8)", "Scenario Two - Selected <br> (7-8)"]:
            if col in df_valid.columns:
                df_valid[col] = df_valid[col].replace(0, '')

        # Remove scenario columns temporarily
        s1 = cols.pop(cols.index("Scenario One - Selected <br> (7-8)"))
        s2 = cols.pop(cols.index("Scenario Two - Selected <br> (7-8)"))

        # Insert at 4th-to-last and 2nd-to-last
        cols.insert(-2, s1)
        cols.insert(-1, s2)

        df_valid = df_valid[cols]
    if 'YearLevel' in df_valid.columns:
        df_valid['YearLevel'] = df_valid['YearLevel'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    for row in errors:
        if 'YearLevel' in row and row['YearLevel'] is not None:
            row['YearLevel'] = int(row['YearLevel'])

    df_errors = pd.DataFrame(errors)
    processing_status["done"] = True

    return df_valid, df_errors
