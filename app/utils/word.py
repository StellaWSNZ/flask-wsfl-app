import os
import re
import subprocess
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from num2words import num2words

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.report_utils.CHT_CircleProportions import circle_plot_png
from app.report_utils.helpers import load_ppmori_fonts
from app.utils.database import get_db_engine


WSFL_BLUE = "1A427D"
WSFL_LIGHT_BLUE = "EDF3FB"


# =============================================================================
# Word helpers
# =============================================================================
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
        p2.paragraph_format.space_after = Pt(8)

        r2 = p2.add_run(subtitle)
        r2.font.name = "Aptos"
        r2.font.size = Pt(10)
        r2.font.color.rgb = RGBColor.from_string("666666")


def add_heading(document, text, level=1):
    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(8 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(4)

    r = p.add_run(text)
    r.bold = True
    r.font.name = "PP Mori"
    r.font.size = Pt(14 if level == 1 else 11)
    r.font.color.rgb = RGBColor.from_string(WSFL_BLUE)
    return document


def add_body_paragraph(document, text, space_after=6):
    p = document.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)

    r = p.add_run(text)
    r.font.name = "Aptos"
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor.from_string("000000")
    return document


def set_up_doc(title, subtitle=None):
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    add_title(document, title, subtitle=subtitle)
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
        re.sub(r"(?<=[a-z])(?=[A-Z])", " ", col).replace("Eqi", "EQI")
        for col in df.columns
    ]
    return df


def draw_table_df(document, df, shading=False, DEBUG=False):
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

            if shading and i % 2 == 1:
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


# =============================================================================
# Chart helpers
# =============================================================================
def render_assessment_circle_chart(
    bucket,
    out_path="assessment_circle_chart.png"
):
    font_family = load_ppmori_fonts("app/static/fonts")

    fig, ax = plt.subplots(figsize=(6.8, 2.7), dpi=300)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    fig.subplots_adjust(left=0.03, right=0.97, top=0.92, bottom=0.08)

    circle_plot_png(
        ax,
        stats=bucket,
        fontfamily=font_family,
        top_y=0.92,
        max_radius=None,
        gap_between=0.03,
        side_margin=0.02,
        label_gap=0.02,
        title_reserved=0.02,
        show_pct=False,
        label_box_height=0.08,
        label_fontsize=10.5,
        bottom_margin=0.03,
        label_box_width=0.22,
    )

    fig.savefig(
        out_path,
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.02,
        facecolor="white"
    )
    plt.close(fig)
    return out_path


def add_assessment_circle_chart(document, bucket, DEBUG=False):
    img_path = render_assessment_circle_chart(bucket)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(img_path, width=Inches(5.2))

    if DEBUG:
        print(f"Inserted circle chart image: {img_path}")

    return document


def render_level_score_chart(
    df_level_total,
    out_path="assessment_score_chart.png"
):
    font_family = load_ppmori_fonts("app/static/fonts")

    df_plot = df_level_total.pivot(
        index="TotalScore",
        columns="LevelName",
        values="AssessmentCount"
    ).fillna(0)

    level_order = [
        "Foundational Level",
        "Intermediate Level",
        "Expert Level"
    ]
    df_plot = df_plot.reindex(columns=level_order, fill_value=0)

    colours = {
        "Foundational Level": "#BBE6E9",
        "Intermediate Level": "#2EBDC2",
        "Expert Level": "#1A427D"
    }

    fig, ax = plt.subplots(figsize=(6.8, 2.5), dpi=300)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    bottom = None
    for level in level_order:
        vals = df_plot[level]
        ax.bar(
            df_plot.index,
            vals,
            bottom=bottom,
            label=level,
            color=colours[level],
            edgecolor="none",
            width=0.8
        )
        bottom = vals if bottom is None else bottom + vals

    ax.set_xlabel("Total Assessment Score", fontname=font_family, fontsize=10)
    ax.set_ylabel("Number of Assessments", fontname=font_family, fontsize=10)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.2)

    ax.tick_params(axis="both", labelsize=9)

    legend = ax.legend(
        frameon=False,
        fontsize=8,
        ncol=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.18)
    )
    for txt in legend.get_texts():
        txt.set_fontname(font_family)

    fig.tight_layout()

    fig.savefig(
        out_path,
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.02,
        facecolor="white"
    )
    plt.close(fig)

    return out_path


def add_level_score_chart(document, df_level_total, DEBUG=False):
    img_path = render_level_score_chart(df_level_total)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(img_path, width=Inches(5.4))

    if DEBUG:
        print(f"Inserted score chart image: {img_path}")

    return document


# =============================================================================
# Data
# =============================================================================
def get_page1_data(DEBUG=False):
    con = get_db_engine()

    df_summary_funder = pd.read_sql(
        "EXEC KaiakoAnalysis @Request = 'KaiakoTargets'",
        con
    )
    df_summary = pd.read_sql(
        "EXEC KaiakoAnalysis @Request = 'OverallSummary'",
        con
    )
    df_level_total = pd.read_sql(
        "EXEC KaiakoAnalysis @Request = 'LevelTotalSchool'",
        con
    )

    if DEBUG:
        print(df_summary)
        print(df_summary_funder)
        print(df_level_total)

    return df_summary, df_summary_funder, df_level_total


# =============================================================================
# Page 1 builder
# =============================================================================
def build_page1_content(document, DEBUG=False):
    df_summary, df_summary_funder, df_level_total = get_page1_data(DEBUG=DEBUG)

    if df_summary.empty:
        add_heading(document, "Summary", level=1)
        add_body_paragraph(document, "No summary data was returned.")
        return document

    row = df_summary.loc[0]

    foundational_raw = int(row["FoundationalLevelCount"])
    intermediate_raw = int(row["IntermediateLevelCount"])
    expert_raw = int(row["ExpertLevelCount"])
    total_level_count = foundational_raw + intermediate_raw + expert_raw

    bucket = {
        "total": total_level_count,
        "buckets": {
            "Foundational Level": {
                "count": foundational_raw,
                "colour": "#BBE6E9"
            },
            "Intermediate Level": {
                "count": intermediate_raw,
                "colour": "#2EBDC2"
            },
            "Expert Level": {
                "count": expert_raw,
                "colour": "#1A427D"
            },
        },
    }

    funder_count_raw = int(row["FunderCount"])
    school_count_raw = int(row["SchoolCount"])
    region_count_raw = int(row["RegionCount"])
    school_count_eqi_raw = int(row["SchoolCountEQI446Plus"])
    kaiako_class_count_raw = int(row["KaiakoClassCount"])
    kaiako_assessment_count_raw = int(row["KaiakoAssessmentCount"])

    funder_count = fmt_num(funder_count_raw)
    school_count = fmt_num(school_count_raw)
    region_count = fmt_num(region_count_raw)
    region_names = build_region_phrase(row["RegionNames"])

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

    add_heading(document, "Summary", level=1)

    summary_text = (
        f"In this funding year we have had {funder_count} funded organisations "
        f"delivering Kaiako Led programmes. This delivery has taken place at "
        f"{school_count} different schools, with {target_school_pct}% being "
        f"in our target EQI range. We have delivered in {region_count} regions: "
        f"{region_names}. Across all Kaiako Led classes, {assessment_completion_pct}% "
        f"of classes have a teacher assessment recorded."
    )
    add_body_paragraph(document, summary_text)

    if total_level_count > 0:
        foundational_pct = round((foundational_raw / total_level_count) * 100, 1)
        intermediate_pct = round((intermediate_raw / total_level_count) * 100, 1)
        expert_pct = round((expert_raw / total_level_count) * 100, 1)

        level_map = {
            "Foundational": foundational_pct,
            "Intermediate": intermediate_pct,
            "Expert": expert_pct,
        }
        top_level = max(level_map, key=level_map.get)
        top_pct = level_map[top_level]

        level_text = (
            f"Overall, teacher assessments were most commonly recorded at the "
            f"{top_level} level ({top_pct}%), with the remaining assessments "
            f"split across the other levels."
        )
        add_body_paragraph(document, level_text)

    add_heading(document, "Assessment Level Distribution", level=2)
    add_body_paragraph(
        document,
        "The distribution of assessment levels and scores is shown below."
    )

    document = add_assessment_circle_chart(document, bucket, DEBUG=DEBUG)

    if not df_level_total.empty:
        document = add_level_score_chart(document, df_level_total, DEBUG=DEBUG)

        peaks_text = (
            "The peaks at scores of 4, 8, and 12 are expected, as these totals "
            "occur when a teacher is rated consistently across all four assessment "
            "areas. For example, a score of 8 reflects a teacher being rated "
            "satisfactory in each area, while 12 reflects consistently proficient "
            "ratings across the assessment criteria."
        )
        add_body_paragraph(document, peaks_text)

    add_heading(document, "Funder Overview", level=2)
    add_body_paragraph(
        document,
        "The table below summarises performance across funded organisations."
    )

    df_summary_funder = df_summary_funder.copy()
    df_summary_funder["ClassesMissingAssessment"] = (
        df_summary_funder["KaiakoClassCount"] - df_summary_funder["KaiakoCount"]
    )
    df_summary_funder["ClassesMissingAssessment"] = (
        df_summary_funder["ClassesMissingAssessment"]
        .where(df_summary_funder["ClassesMissingAssessment"] > 0, "")
    )

    df_table = df_summary_funder[
        [
            "Funder",
            "KaiakoTarget",
            "KaiakoCount",
            "KaiakoClassCount",
            "ClassesMissingAssessment",
            "TargetPercentage",
        ]
    ]

    document = draw_table_df(document, df_table, shading=True, DEBUG=DEBUG)
    return document


# =============================================================================
# Main
# =============================================================================
if __name__ == "__main__":
    DEBUG = True

    title = "Teacher Assessment Analysis"
    subtitle = "Kaiako Led Programmes – Page 1 Overview"
    filename = f"{title}.docx"

    close_word(DEBUG)

    document = set_up_doc(title, subtitle=subtitle)
    document = build_page1_content(document, DEBUG=DEBUG)

    document.save(filename)
    os.startfile(filename)