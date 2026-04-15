import os
import re
import subprocess

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from num2words import num2words

from app.utils.database import get_db_engine


WSFL_BLUE = "1A427D"
WSFL_LIGHT_BLUE = "EDF3FB"


def close_word(DEBUG=False):
    if DEBUG:
        subprocess.run(["taskkill", "/f", "/im", "WINWORD.EXE"])
    else:
        subprocess.run(
            ["taskkill", "/f", "/im", "WINWORD.EXE"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_text(
    cell,
    text,
    bold=False,
    font_name="Aptos",
    font_size=9,
    font_color="000000",
    align="left"
):
    cell.text = ""
    p = cell.paragraphs[0]

    if align == "left":
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    elif align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "right":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    run = p.add_run(str(text))
    run.bold = bold
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.font.color.rgb = RGBColor.from_string(font_color)

    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def add_title(document, title, subtitle=None):
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    r = p.add_run(title)
    r.bold = True
    r.font.name = "PP Mori"
    r.font.size = Pt(20)
    r.font.color.rgb = RGBColor.from_string(WSFL_BLUE)

    if subtitle:
        p2 = document.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r2 = p2.add_run(subtitle)
        r2.font.name = "Aptos"
        r2.font.size = Pt(10)
        r2.font.color.rgb = RGBColor.from_string("666666")


def add_heading(document, text, level=1):
    p = document.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.name = "PP Mori"
    r.font.size = Pt(14 if level == 1 else 11)
    r.font.color.rgb = RGBColor.from_string(WSFL_BLUE)
    return document


def add_body_paragraph(document, text):
    p = document.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    r.font.name = "Aptos"
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor.from_string("000000")
    return document


def set_up_doc(title, DEBUG=False):
    document = Document()
    add_title(document, title, "Water Skills for Life")
    return document


def fmt_num(n):
    if n < 10:
        return num2words(n)
    return str(f"{n:,}")


def format_value(val, col_name=None):
    if pd.isna(val):
        return ""

    if col_name and "Percentage" in col_name:
        return f"{float(val):.1f}%"

    if isinstance(val, float):
        if float(val).is_integer():
            return f"{int(val):,}"
        return f"{val:,.1f}"

    if isinstance(val, int):
        return f"{val:,}"

    return str(val)


def prettify_columns(df):
    df = df.copy()
    df.columns = [
        re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', col).replace("Eqi", "EQI")
        for col in df.columns
    ]
    return df


def draw_table_df(document, df, shading=False,DEBUG=False):
    df = prettify_columns(df)

    nrow, ncol = df.shape
    table = document.add_table(rows=nrow + 1, cols=ncol)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"

    if DEBUG:
        print(f"Headers: {list(df.columns)}")

    # Header row
    for j, col in enumerate(df.columns):
        cell = table.cell(0, j)
        set_cell_shading(cell, WSFL_BLUE)
        set_cell_text(
            cell,
            col,
            bold=True,
            font_name="PP Mori",
            font_size=9,
            font_color="FFFFFF",
            align="center"
        )

    # Body
    for i in range(nrow):
        for j in range(ncol):
            val = df.iat[i, j]
            col_name = df.columns[j]
            text = format_value(val, col_name)

            cell = table.cell(i + 1, j)

            if shading and i % 2 == 1 :
                set_cell_shading(cell, WSFL_LIGHT_BLUE)

            align = "left"
            if "Percentage" in col_name:
                align = "right"
            elif isinstance(val, (int, float)) and not pd.isna(val):
                align = "right"

            set_cell_text(
                cell,
                text,
                bold=False,
                font_name="Aptos",
                font_size=9,
                font_color="000000",
                align=align
            )

    return document


def build_region_phrase(region_names):
    region_names = (region_names or "").replace(" Region", "").strip()

    if not region_names:
        return ""

    parts = [x.strip() for x in region_names.split(",") if x.strip()]

    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def summary_paragraph(document, DEBUG=False):
    con = get_db_engine()

    df_summary_funder = pd.read_sql(
        "EXEC KaiakoAnalysis @Request = 'KaiakoTargets'",
        con
    )
    df_summary = pd.read_sql(
        "EXEC KaiakoAnalysis @Request = 'OverallSummary'",
        con
    )

    if DEBUG:
        print(df_summary_funder)
        print(df_summary)

    if df_summary.empty:
        document.add_paragraph("No summary data was returned.")
        return document

    funder_count_raw = int(df_summary.loc[0, "FunderCount"])
    school_count_raw = int(df_summary.loc[0, "SchoolCount"])
    region_count_raw = int(df_summary.loc[0, "RegionCount"])
    school_count_eqi_raw = int(df_summary.loc[0, "SchoolCountEQI446Plus"])
    kaiako_class_count_raw = int(df_summary.loc[0, "KaiakoClassCount"])
    kaiako_assessment_count_raw = int(df_summary.loc[0, "KaiakoAssessmentCount"])

    funder_count = fmt_num(funder_count_raw)
    school_count = fmt_num(school_count_raw)
    region_count = fmt_num(region_count_raw)
    region_names = build_region_phrase(df_summary.loc[0, "RegionNames"])

    if school_count_raw > 0:
        target_school_pct = round((school_count_eqi_raw / school_count_raw) * 100, 1)
    else:
        target_school_pct = 0

    if kaiako_class_count_raw > 0:
        assessment_completion_pct = round(
            (kaiako_assessment_count_raw / kaiako_class_count_raw) * 100, 1
        )
    else:
        assessment_completion_pct = 0

    summary_text = (
        f"In this funding year we have had {funder_count} funded organisations "
        f"delivering Kaiako Led programs. This delivery has taken place at "
        f"{school_count} different schools, with {target_school_pct}% being "
        f"in our target EQI range. We have delivered in {region_count} regions: "
        f"{region_names}. Across all Kaiako Led classes, {assessment_completion_pct}% "
        f"of classes have a teacher assessment recorded."
    )

    add_heading(document, "Summary", level=1)
    add_body_paragraph(document, summary_text)

    df_summary_funder = df_summary_funder.copy()
    df_summary_funder["ClassesMissingAssessment"] = (
        df_summary_funder["KaiakoClassCount"] - df_summary_funder["KaiakoCount"]
    )


    df_table = df_summary_funder[
        [
            "Funder",
            "KaiakoTarget",
            "KaiakoCount",
            "KaiakoClassCount",
            "ClassesMissingAssessment",
            "AssessmentCompletePercentage",
            "TargetPercentage",
        ]
    ]

    document = draw_table_df(document, df_table,DEBUG= DEBUG)
    return document


if __name__ == "__main__":
    DEBUG = True
    title = "Teacher Assessment Analysis"
    filename = f"{title}.docx"

    close_word(DEBUG)

    document = set_up_doc(title, DEBUG)
    document = summary_paragraph(document, DEBUG)

    document.save(filename)
    os.startfile(filename)