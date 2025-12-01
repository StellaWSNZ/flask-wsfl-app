import pandas as pd

def get_national_yg_rates(df: pd.DataFrame) -> pd.DataFrame:
    """
    National YTD rate per YearGroupDesc (for your bar chart).
    This one can label 'Years ' since the bar title doesn't add it.
    """
    df = df[df["ResultType"] == "National Rate (YTD)"].copy()
    out = (
        df.groupby("YearGroupDesc", as_index=False)["Rate"]
          .mean()
          .rename(columns={"Rate": "AverageRate"})
    )
    
    return out


def get_x_competencies_yg_funder(df: pd.DataFrame, x: int) -> pd.DataFrame:
    """
    Top/bottom x competencies per YearGroupDesc for National YTD.
    Leaves YearGroupDesc as '0-2','3-4','5-6','7-8' for the stacked renderer.
    Returns columns: YearGroupDesc, RankType, Rank, CompetencyID, CompetencyDesc, Rate
    (Rate in 0..100).
    """
    df_rates = df[df["ResultType"] == "National Rate (YTD)"].copy()

    avg_rates = (
        df_rates.groupby(["CompetencyID", "CompetencyDesc", "YearGroupDesc"], as_index=False)["Rate"]
                .mean()
    )

    # Scale once globally if in 0..1
    if avg_rates["Rate"].max() <= 1.0:
        avg_rates["Rate"] = (avg_rates["Rate"] * 100).round(0)

    best_worst = []
    for yg, sub in avg_rates.groupby("YearGroupDesc", sort=False):
        # Ensure we don't request more rows than exist
        k = min(x, len(sub))
        if k == 0:
            continue

        best = (sub.nlargest(k, "Rate")
                    .assign(Rank=range(1, k + 1), RankType="Best"))
        worst = (sub.nsmallest(k, "Rate")
                     .assign(Rank=range(1, k + 1), RankType="Worst"))

        best_worst.append(pd.concat([best, worst], ignore_index=True))

    result = (pd.concat(best_worst, ignore_index=True)
                .sort_values(["YearGroupDesc", "RankType", "Rank"], ignore_index=True))

    return result

import pandas as pd

def provider_missing_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Summarise by Provider:
      - Schools with Classes Yet to Submit Data shown as "x / y (p%)" or "No schools with Classes Yet to Submit Data"
      - Total Classes Yet to Submit Data shown as "x / y (p%)" or "No Classes Yet to Submit Data"
      - Ordered by % Total Classes Yet to Submit Data descending
    """
    if df.empty:
        return pd.DataFrame(columns=[
            "Provider", "Schools with Classes Yet to Submit Data", "Total Classes Yet to Submit Data"
        ])

    df = df.copy()
    df["MissingClasses"] = df["NumClasses"] - df["EditedClasses"]
    df["HasMissing"] = df["MissingClasses"] > 0

    summary = (
        df.groupby("Provider", as_index=False)
        .agg(
            NumSchools=("SchoolName", "nunique"),
            SchoolsWithMissing=("HasMissing", "sum"),
            TotalClasses=("NumClasses", "sum"),
            TotalEdited=("EditedClasses", "sum"),
        )
    )

    summary["ClassesMissing"] = summary["TotalClasses"] - summary["TotalEdited"]

    # Percentages
    summary["PctSchoolsMissing"] = (
        summary["SchoolsWithMissing"] / summary["NumSchools"] * 100
    ).round(1)
    summary["PctClassesMissing"] = (
        summary["ClassesMissing"] / summary["TotalClasses"] * 100
    ).round(1)

    # Formatters
    def fmt_schools(row):
        if row["SchoolsWithMissing"] == 0:
            return "No schools with Classes Yet to Submit Data"
        return f"{row['SchoolsWithMissing']} / {row['NumSchools']} ({row['PctSchoolsMissing']}%)"

    def fmt_classes(row):
        if row["ClassesMissing"] == 0:
            return "No Classes Yet to Submit Data"
        return f"{row['ClassesMissing']} / {row['TotalClasses']} ({row['PctClassesMissing']}%)"

    summary["Schools with Classes Yet to Submit Data"] = summary.apply(fmt_schools, axis=1)
    summary["Total Classes Yet to Submit Data"] = summary.apply(fmt_classes, axis=1)

    # Order by percentage of Classes Yet to Submit Data (descending)
    summary = summary.sort_values("PctClassesMissing", ascending=False)

    return summary[["Provider", "Schools with Classes Yet to Submit Data", "Total Classes Yet to Submit Data"]]
