# weekly_stats_report.py
from __future__ import annotations

from pathlib import Path
import os
import re
from collections import Counter
from datetime import timedelta, datetime, timezone

import git
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import pythoncom
import win32com.client

from app.report_utils.FNT_PolygonText import draw_text_in_polygon
from app.report_utils.SHP_RoundRect import rounded_rect_polygon
from app.report_utils.helpers import load_ppmori_fonts
from app.utils.funder_missing_plot import add_full_width_footer_svg
from app.utils.linegraph import draw_graph

# =========================================================
# Settings
# =========================================================
DEBUG_COL = None  # "#ff8cd9"

BRAND_BLUE = "#1a427d"
DARK_BLUE = "#12345a"
MID_BLUE = "#2e5b8a"
LIGHT_BLUE  = "#dbe7f5"   # darker subtitle / header background
PALE_BLUE   = "#e8f1fb"   # light panel background
SOFT_EDGE   = "#9fb6d4"   # stronger border so boxes show on paper
COMMIT_FILL = "#eef3f9"   # commit table background
COMMIT_EDGE = "#aebed4"   # table border

TITLE_FILL = BRAND_BLUE
TITLE_EDGE = BRAND_BLUE
TITLE_TEXT = "#ffffff"

SUBTITLE_FILL = BRAND_BLUE
SUBTITLE_EDGE = COMMIT_FILL
SUBTITLE_TEXT = COMMIT_FILL

BODY_BG = "#ffffff"

CARD_FILL = PALE_BLUE
CARD_EDGE = MID_BLUE
CARD_LABEL = BRAND_BLUE
CARD_VALUE = BRAND_BLUE

HIGHLIGHT_CARD_FILL = "#ffffff"
HIGHLIGHT_CARD_EDGE = DARK_BLUE
HIGHLIGHT_CARD_LABEL = DARK_BLUE
HIGHLIGHT_CARD_VALUE = DARK_BLUE

ERROR_FILL = DARK_BLUE
ERROR_EDGE = DARK_BLUE
ERROR_TEXT = "#ffffff"

PANEL_BODY_FILL = PALE_BLUE
PANEL_BODY_EDGE = SOFT_EDGE
PANEL_TITLE_FILL = DARK_BLUE
PANEL_TITLE_TEXT = "#ffffff"

COMMIT_TITLE_FILL = DARK_BLUE
COMMIT_TITLE_TEXT = "#ffffff"
COMMIT_HEADER_FILL = MID_BLUE
COMMIT_HEADER_TEXT = "#ffffff"

OUTLOOK_MAILBOX_NAME = "dbadmin@watersafety.org.nz"
TOP_EMAIL_FOLDERS = 6
TOP_EMAIL_SUBJECTS = 6
DEBUG_EMAILS = False
EXCLUDED_EMAIL_FOLDERS = {
    "sent items",
    "azure",
    "drafts",
    "deleted items",
    "junk email",
    "junk e-mail",
    "outbox",
    "rss feeds",
    "conversation history",
    "sync issues",
    "bounce backs",
}


# =========================================================
# DB
# =========================================================
def get_db_engine():
    load_dotenv()
    db_conn = os.getenv("DB_URL")
    if not db_conn:
        raise ValueError("DB_URL not found in environment variables.")
    return create_engine(db_conn, future=True)

def load_linegraph_df(refresh: bool = False) -> pd.DataFrame:
    engine = get_db_engine()

    if refresh:
        with engine.begin() as conn:
            conn.execute(text("EXEC dbo.RefreshDashboardDailyChange"))

    with engine.connect() as conn:
        df = pd.read_sql(
            text("EXEC dbo.GetDashboardLineGraphData"),
            conn
        )

    return df
def load_weekly_stats(as_of_date: str | None = None):
    engine = get_db_engine()

    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()

        if as_of_date is None:
            sql = "EXEC dbo.GetWeeklyDatabaseStats"
            print(sql)
            cursor.execute(sql)
        else:
            sql = "EXEC dbo.GetWeeklyDatabaseStats @AsOfDate = ?"
            print(sql, as_of_date)
            cursor.execute(sql, (as_of_date,))

        dfs = []

        while True:
            if cursor.description is not None:
                cols = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                df = pd.DataFrame.from_records(rows, columns=cols)
                dfs.append(df)

            if not cursor.nextset():
                break

        cursor.close()

    finally:
        raw_conn.close()

    for i, df in enumerate(dfs):
        print(f"\n--- Result set {i} ---")
        print(df.columns)
        print(df.head())

    summary_df = dfs[0] if len(dfs) > 0 else pd.DataFrame()
    users_by_role_df = dfs[1] if len(dfs) > 1 else pd.DataFrame()
    respondents_by_survey_df = dfs[2] if len(dfs) > 2 else pd.DataFrame()

    return summary_df, users_by_role_df, respondents_by_survey_df

def trim_graph_df(df: pd.DataFrame, week_ending_date) -> pd.DataFrame:
    df = df.copy().sort_values("AuditDay").reset_index(drop=True)

    df["AuditDay"] = pd.to_datetime(df["AuditDay"])
    week_ending_date = pd.to_datetime(week_ending_date)

    df = df[df["AuditDay"] <= week_ending_date]

    idx = df.index[df["CumulativeTotal"] != 0].tolist()
    if len(idx) > 0:
        df = df.iloc[idx[0]:].reset_index(drop=True)

    return df
# =========================================================
# Outlook email helpers
# =========================================================
def email_debug(msg: str):
    if DEBUG_EMAILS:
        print(f"[EMAIL DEBUG] {msg}")


def make_naive(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.replace(tzinfo=None)
    return dt


def get_outlook_namespace():
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.GetActiveObject("Outlook.Application")
        email_debug("Attached to existing Outlook.Application")
    except Exception:
        outlook = win32com.client.Dispatch("Outlook.Application")
        email_debug("Started Outlook.Application via COM")
    return outlook.GetNamespace("MAPI")


def list_outlook_stores(namespace):
    stores = []
    for store in namespace.Folders:
        try:
            stores.append(str(store.Name))
        except Exception:
            pass
    return stores


def get_outlook_mailbox(namespace, mailbox_name: str):
    stores = list_outlook_stores(namespace)
    email_debug(f"Available Outlook stores: {stores}")

    for store in namespace.Folders:
        try:
            store_name = str(store.Name)
            if mailbox_name.lower() in store_name.lower():
                email_debug(f"Matched mailbox '{mailbox_name}' to store '{store_name}'")
                return store
        except Exception:
            pass

    raise Exception(
        f"Mailbox '{mailbox_name}' not found in Outlook. Available stores: {stores}"
    )


def get_folder_path(folder):
    parts = []
    current = folder

    while current is not None:
        try:
            parts.append(str(current.Name))
            current = current.Parent
        except Exception:
            break

    parts.reverse()
    if len(parts) > 1:
        return " / ".join(parts[1:])
    return " / ".join(parts)


def normalise_folder_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def should_exclude_folder(folder) -> bool:
    try:
        path_parts = [normalise_folder_name(part) for part in get_folder_path(folder).split(" / ")]
        return any(part in EXCLUDED_EMAIL_FOLDERS for part in path_parts)
    except Exception:
        return False


def clean_subject(subject):
    if subject is None:
        return "(No subject)"

    subject = str(subject).strip()
    while True:
        new_subject = re.sub(
            r"^\s*((RE|FW|FWD)\s*:\s*)+",
            "",
            subject,
            flags=re.IGNORECASE,
        ).strip()
        if new_subject == subject:
            break
        subject = new_subject

    return subject if subject else "(No subject)"


def is_mail_item(item):
    try:
        _ = item.Class
        _ = item.Subject
        _ = item.ReceivedTime
        return True
    except Exception:
        return False


def collect_emails_from_folder(folder, rows, min_date):
    folder_path = get_folder_path(folder)

    if should_exclude_folder(folder):
        email_debug(f"Skipping excluded folder: {folder_path}")
        return

    folder_total = 0
    folder_kept = 0
    email_debug(f"Scanning folder: {folder_path}")

    try:
        items = folder.Items
        try:
            items.Sort("[ReceivedTime]", True)
        except Exception as e:
            email_debug(f"Could not sort folder '{folder_path}': {repr(e)}")

        for item in items:
            folder_total += 1
            try:
                if not is_mail_item(item):
                    continue

                received_time = make_naive(item.ReceivedTime)
                if received_time is None or received_time < min_date:
                    continue

                rows.append({
                    "folder_path": folder_path,
                    "subject": clean_subject(item.Subject),
                    "received_time": received_time,
                })
                folder_kept += 1
            except Exception as e:
                email_debug(f"Error reading item in '{folder_path}': {repr(e)}")
    except Exception as e:
        email_debug(f"Could not read folder '{folder_path}': {repr(e)}")

    email_debug(f"Finished folder '{folder_path}' | scanned={folder_total} | kept={folder_kept}")

    for subfolder in folder.Folders:
        collect_emails_from_folder(subfolder, rows, min_date)


def get_email_rows(mailbox_name: str, min_date: datetime):
    namespace = get_outlook_namespace()
    mailbox = get_outlook_mailbox(namespace, mailbox_name)
    rows = []

    for top_folder in mailbox.Folders:
        collect_emails_from_folder(top_folder, rows, min_date)

    email_debug(f"Total email rows collected: {len(rows)}")
    return rows

def get_email_date_ranges(week_ending_date=None):
    """
    Returns date boundaries for:
      - report week: Monday -> Sunday
      - month to date: first day of current month -> week ending Sunday
      - last month: first day of previous month -> first day of current month

    If week_ending_date is supplied, it should be a date representing the
    Sunday that ends the report week.
    """
    if week_ending_date is not None:
        week_ending_date = pd.to_datetime(week_ending_date).date()
    else:
        today = make_naive(datetime.now()).date()
        # find most recent Sunday
        days_since_sunday = (today.weekday() + 1) % 7
        week_ending_date = today - timedelta(days=days_since_sunday)

    week_start_date = week_ending_date - timedelta(days=6)   # Monday
    week_end_exclusive_date = week_ending_date + timedelta(days=1)  # next Monday

    start_this_month = week_ending_date.replace(day=1)
    start_last_month = (start_this_month - timedelta(days=1)).replace(day=1)
    end_last_month = start_this_month

    return {
        "week_ending_date": week_ending_date,
        "week_start_date": week_start_date,
        "week_end_exclusive_date": week_end_exclusive_date,
        "start_this_month": start_this_month,
        "start_last_month": start_last_month,
        "end_last_month": end_last_month,
    }
def summarise_email_rows(rows, week_ending_date=None):
    """
    Summarise email rows using a true Monday->Sunday report week.
    """
    ranges = get_email_date_ranges(week_ending_date=week_ending_date)

    emails_this_week = [
        r for r in rows
        if r["received_time"] is not None
        and ranges["week_start_date"] <= r["received_time"].date() < ranges["week_end_exclusive_date"]
    ]

    emails_month_to_date = [
        r for r in rows
        if r["received_time"] is not None
        and ranges["start_this_month"] <= r["received_time"].date() <= ranges["week_ending_date"]
    ]

    emails_last_month = [
        r for r in rows
        if r["received_time"] is not None
        and ranges["start_last_month"] <= r["received_time"].date() < ranges["end_last_month"]
    ]

    folder_counts_week = Counter(r["folder_path"] for r in emails_this_week)
    subject_counts_week = Counter(r["subject"] for r in emails_this_week)

    return {
        "emails_this_week": len(emails_this_week),
        "emails_month_to_date": len(emails_month_to_date),
        "emails_last_month": len(emails_last_month),
        "folder_counts_week": folder_counts_week,
        "subject_counts_week": subject_counts_week,
    }

def get_email_summary(mailbox_name: str, week_ending_date=None):
    """
    Reads enough email history to support:
      - this report week
      - month to date
      - last month
    """
    try:
        ranges = get_email_date_ranges(week_ending_date=week_ending_date)

        # read from the start of last month so we can calculate both
        # last month and this month-to-date
        min_date = datetime.combine(ranges["start_last_month"], datetime.min.time())

        rows = get_email_rows(mailbox_name, min_date=min_date)
        return summarise_email_rows(rows, week_ending_date=week_ending_date)

    except Exception as e:
        return {
            "emails_this_week": None,
            "emails_month_to_date": None,
            "emails_last_month": None,
            "folder_counts_week": Counter(),
            "subject_counts_week": Counter(),
            "error": str(e),
        }

def build_email_summary_line(email_summary: dict) -> str:
    if email_summary.get("emails_this_week") is None:
        if("exited without properly closing your Outlook data file" in (email_summary.get('error') or "")):
            return f"Old outlook not installed. Change version and try again"
        else:
            return f"Email summary unavailable: {email_summary.get('error', 'Unknown Outlook error')}"

    return "  |  ".join([
        f"Emails last week: {format_value(email_summary.get('emails_this_week'))}",
        f"Emails month to date: {format_value(email_summary.get('emails_month_to_date'))}",
        f"Emails last month: {format_value(email_summary.get('emails_last_month'))}",
    ])


def build_email_folder_lines(email_summary: dict, top_n: int = TOP_EMAIL_FOLDERS) -> list[str]:
    if email_summary.get("emails_this_week") is None:
        return ["Outlook mailbox could not be read"]

    lines = [
        f"{folder}: {count}"
        for folder, count in email_summary["folder_counts_week"].most_common(top_n)
    ]
    
    lines = [
        item.replace("Inbox:", "Inbox (Outstanding Emails):") if "Inbox:" in item else item
        for item in lines
    ]
    return lines or ["No emails found in the last week"]


def build_email_subject_lines(email_summary: dict, top_n: int = TOP_EMAIL_SUBJECTS) -> list[str]:
    if email_summary.get("emails_this_week") is None:
        return ["Outlook mailbox could not be read"]

    lines = [
        f"{subject}: {count}"
        for subject, count in email_summary["subject_counts_week"].most_common(top_n)
    ]
    return lines or ["No email subjects found in the last week"]


# =========================================================
# Footer
# =========================================================
def add_footer_behind(
    fig,
    footer_svg_path: Path,
    *,
    bottom_margin_frac: float = 0.0,
    max_footer_height_frac: float = 0.18,
    col_master: str = f"{BRAND_BLUE}80",
):
    n_images_before = len(fig.images)
    n_patches_before = len(fig.patches)
    n_artists_before = len(fig.artists)

    add_full_width_footer_svg(
        fig,
        footer_svg_path,
        bottom_margin_frac=bottom_margin_frac,
        max_footer_height_frac=max_footer_height_frac,
        col_master=col_master,
    )

    for im in fig.images[n_images_before:]:
        try:
            im.set_zorder(0)
        except Exception:
            pass

    for p in fig.patches[n_patches_before:]:
        try:
            p.set_zorder(0)
        except Exception:
            pass

    for a in fig.artists[n_artists_before:]:
        try:
            a.set_zorder(0)
        except Exception:
            pass


# =========================================================
# Debug grid
# =========================================================
def draw_debug_grid(
    ax,
    *,
    color: str,
    x0: float = 0.0,
    x1: float = 1.0,
    y0: float = 0.0,
    y1: float = 1.0,
    step: float = 0.1,
    mini_step: float = 0.02,
    lw: float = 0.9,
    lw_mini: float = 0.3,
    show_labels: bool = True,
    label_fs: float = 8.5,
    draw_border: bool = True,
    border_lw: float = 1.6,
):
    if color is None:
        return

    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    if mini_step and mini_step > 0:
        nmini = int(round(1.0 / mini_step))
        for i in range(nmini + 1):
            t = i * mini_step
            x = lerp(x0, x1, t)
            ax.plot([x, x], [y0, y1], color=color, linewidth=lw_mini, zorder=5000, linestyle="--")
        for j in range(nmini + 1):
            t = j * mini_step
            y = lerp(y0, y1, t)
            ax.plot([x0, x1], [y, y], color=color, linewidth=lw_mini, zorder=5000, linestyle="--")

    n = int(round(1.0 / step))
    for i in range(n + 1):
        t = i * step
        x = lerp(x0, x1, t)
        ax.plot([x, x], [y0, y1], color=color, linewidth=lw, zorder=6000)

    for j in range(n + 1):
        t = j * step
        y = lerp(y0, y1, t)
        ax.plot([x0, x1], [y, y], color=color, linewidth=lw, zorder=6000)

    if show_labels:
        for i in range(n + 1):
            t = i * step
            x = lerp(x0, x1, t)
            ax.text(x, y1, f"{t:.1f}", ha="center", va="bottom", fontsize=label_fs, color=color, zorder=7000)
            ax.text(x, y0, f"{t:.1f}", ha="center", va="top", fontsize=label_fs, color=color, zorder=7000)

        for j in range(n + 1):
            t = j * step
            y = lerp(y0, y1, t)
            ax.text(x0, y, f"{t:.1f}", ha="right", va="center", fontsize=label_fs, color=color, zorder=7000)
            ax.text(x1, y, f"{t:.1f}", ha="left", va="center", fontsize=label_fs, color=color, zorder=7000)

    if draw_border:
        ax.plot([x0, x1], [y0, y0], color=color, linewidth=border_lw, zorder=8000)
        ax.plot([x0, x1], [y1, y1], color=color, linewidth=border_lw, zorder=8000)
        ax.plot([x0, x0], [y0, y1], color=color, linewidth=border_lw, zorder=8000)
        ax.plot([x1, x1], [y0, y1], color=color, linewidth=border_lw, zorder=8000)


# =========================================================
# Shared helpers
# =========================================================
def draw_card_background(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str = CARD_FILL,
    edge: str = CARD_EDGE,
    linewidth: float = 1.2,
    ratio: float = 0.16,
):
    poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=ratio,
        corners_round=[1, 2, 3, 4],
        n_arc=64,
    )

    patch = Polygon(
        list(poly.exterior.coords),
        closed=True,
        facecolor=fill,
        edgecolor=edge,
        linewidth=linewidth,
        transform=ax.transAxes,
        zorder=110,
    )
    ax.add_patch(patch)
    return poly, patch


def polygon_to_clip_patch(ax, poly, *, zorder: float = 11150):
    verts = list(poly.exterior.coords)
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(verts) - 2) + [MplPath.CLOSEPOLY]
    path = MplPath(verts, codes)
    clip_patch = PathPatch(path, transform=ax.transAxes, facecolor="none", edgecolor="none", zorder=zorder)
    ax.add_patch(clip_patch)
    return clip_patch


def format_value(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        return f"{int(value):,}" if float(value).is_integer() else f"{value:,}"
    return str(value)


def get_summary_value(summary_df: pd.DataFrame, *possible_cols, default=None):
    if summary_df.empty:
        return default

    row = summary_df.iloc[0]
    for col in possible_cols:
        if col in summary_df.columns:
            val = row[col]
            if pd.notna(val):
                return val
    return default


def draw_panel_title_band(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str,
    card_poly=None,
):
    rect = Rectangle(
        (x, y),
        width,
        height,
        transform=ax.transAxes,
        facecolor=fill,
        edgecolor=fill,
        linewidth=0,
        zorder=11100,
    )
    ax.add_patch(rect)

    if card_poly is not None:
        clip_patch = polygon_to_clip_patch(ax, card_poly)
        rect.set_clip_path(clip_patch)

    return rect


# =========================================================
# Band helpers
# =========================================================
def draw_title_band(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    fontfamily: str,
):
    poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=0.35,
        corners_round=[1, 2, 3, 4],
        n_arc=64,
    )

    ax.add_patch(
        Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=TITLE_FILL,
            edgecolor=TITLE_EDGE,
            linewidth=1.0,
            transform=ax.transAxes,
            zorder=15000,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=title,
        fontfamily=fontfamily,
        fontsize=30,
        fontweight="semibold",
        color=TITLE_TEXT,
        pad_frac=0.05,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=2,
        zorder=16000,
    )


def draw_subtitle_band(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    subtitle: str,
    fontfamily: str,
):
    poly = rounded_rect_polygon(
        cx=x + width / 2,
        cy=y + height / 2,
        width=width,
        height=height,
        ratio=0.30,
        corners_round=[1, 2, 3, 4],
        n_arc=64,
    )

    ax.add_patch(
        Polygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor=SUBTITLE_FILL,
            edgecolor=SUBTITLE_EDGE,
            linewidth=1.0,
            transform=ax.transAxes,
            zorder=15000,
        )
    )

    draw_text_in_polygon(
        ax,
        poly=poly,
        text=subtitle,
        fontfamily=fontfamily,
        fontsize=24,
        fontweight="bold",
        color=SUBTITLE_TEXT,
        pad_frac=0.04,
        wrap=True,
        autoshrink=True,
        clip_to_polygon=True,
        max_lines=2,
        zorder=16000,
    )


# =========================================================
# Generic panels
# =========================================================
def draw_panel(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fontfamily: str,
    title: str | None,
    lines: list[str],
    fill: str = PANEL_BODY_FILL,
    edge: str = PANEL_BODY_EDGE,
    text_color: str = CARD_LABEL,
    title_fill: str = PANEL_TITLE_FILL,
    title_text_color: str = PANEL_TITLE_TEXT,
    title_fontsize: float = 11,
    line_fontsize: float = 12,
    linewidth: float = 1.2,
    single_line: bool = False,
):
    card_poly, _ = draw_card_background(
        ax,
        x=x,
        y=y,
        width=width,
        height=height,
        fill=fill,
        edge=edge,
        linewidth=linewidth,
    )

    has_title = bool(title)

    if has_title:
        title_band_h = height * 0.24
        title_band_y = y + height - title_band_h

        draw_panel_title_band(
            ax,
            x=x,
            y=title_band_y,
            width=width,
            height=title_band_h,
            fill=title_fill,
            card_poly=card_poly,
        )

        title_y = title_band_y + (title_band_h * 0.50)

        ax.text(
            x + width / 2,
            title_y,
            str(title),
            ha="center",
            va="center",
            fontsize=title_fontsize,
            fontweight="bold",
            color=title_text_color,
            transform=ax.transAxes,
            zorder=12050,
            family=fontfamily,
        )

        body_top = title_band_y
    else:
        body_top = y + height

    body_bottom = y
    body_height = body_top - body_bottom

    if not lines:
        return

    if single_line:
        body_mid_y = body_bottom + body_height / 2

        ax.text(
            x + width / 2,
            body_mid_y,
            str(lines[0]),
            ha="center",
            va="center",
            fontsize=line_fontsize,
            fontweight="bold",
            color=text_color,
            transform=ax.transAxes,
            zorder=12000,
            family=fontfamily,
        )
    else:
        line_gap = height * 0.14
        n_lines = len(lines)
        block_height = (n_lines - 1) * line_gap
        first_line_y = body_bottom + (body_height + block_height) / 2

        for i, line in enumerate(lines):
            ax.text(
                x + width / 2,
                first_line_y - i * line_gap,
                str(line),
                ha="center",
                va="center",
                fontsize=line_fontsize,
                color=text_color,
                transform=ax.transAxes,
                zorder=12000,
                family=fontfamily,
            )


# =========================================================
# Metric cards
# =========================================================
def draw_top_graph_cards(
    ax,
    *,
    page_left: float,
    page_right: float,
    top_y: float,
    total_h: float,
    gap_x: float,
    gap_y: float,
    fontfamily: str,
    student_df: pd.DataFrame,
    class_df: pd.DataFrame,
    user_df: pd.DataFrame,
    form_df: pd.DataFrame,
    week_ending_date,
):
    week_ending_date = pd.to_datetime(week_ending_date).normalize()
    week_starting_date = (week_ending_date - timedelta(days=6)).normalize()

    total_w = page_right - page_left
    card_w = (total_w - gap_x) / 2
    card_h = (total_h - gap_y) / 2

    positions = [
        ("Students", page_left, top_y - card_h),
        ("Classes",  page_left + card_w + gap_x, top_y - card_h),
        ("Users",    page_left, top_y - (2 * card_h) - gap_y),
        ("Forms",    page_left + card_w + gap_x, top_y - (2 * card_h) - gap_y),
    ]

    line_cols = {
        "Students": MID_BLUE,
        "Classes": MID_BLUE,
        "Users": MID_BLUE,
        "Forms": MID_BLUE,
    }

    df_map = {
        "Students": student_df,
        "Classes": class_df,
        "Users": user_df,
        "Forms": form_df,
    }

    for base_title, cx, cy in positions:
        src_df = df_map[base_title]

        if src_df is not None and not src_df.empty:
            df = src_df.copy()
            df["AuditDay"] = pd.to_datetime(df["AuditDay"]).dt.normalize()
            df_week = df[
                (df["AuditDay"] >= week_starting_date) &
                (df["AuditDay"] <= week_ending_date)
            ]
            change_val = int(df_week["NetChange"].sum())
        else:
            change_val = 0

        if change_val > 0:
            badge_text = f"↑ {change_val}"
        elif change_val < 0:
            badge_text = f"↓ {abs(change_val)}"
        else:
            badge_text = None

        card_poly, _ = draw_card_background(
            ax,
            x=cx,
            y=cy,
            width=card_w,
            height=card_h,
            fill=PANEL_BODY_FILL,
            edge=PANEL_BODY_EDGE,
            linewidth=1.2,
            ratio=0.10,
        )

        title_band_h = card_h * 0.18
        title_band_y = cy + card_h - title_band_h

        draw_panel_title_band(
            ax,
            x=cx,
            y=title_band_y,
            width=card_w,
            height=title_band_h,
            fill=PANEL_TITLE_FILL,
            card_poly=card_poly,
        )

        # Left-aligned title
        title_x = cx + card_w * 0.035
        title_y = title_band_y + title_band_h / 2

        ax.text(
            title_x,
            title_y,
            base_title,
            ha="left",
            va="center",
            fontsize=12,
            fontweight="bold",
            color=PANEL_TITLE_TEXT,
            transform=ax.transAxes,
            zorder=12050,
            family=fontfamily,
        )

        # Right-aligned white badge
        if badge_text:
            badge_h = title_band_h * 0.62
            badge_w = max(
                card_w * 0.10,
                min(card_w * 0.16, 0.010 * len(badge_text) + card_w * 0.06)
            )
            badge_x = cx + card_w - badge_w - (card_w * 0.035)
            badge_y = title_band_y + (title_band_h - badge_h) / 2

            badge_poly = rounded_rect_polygon(
                cx=badge_x + badge_w / 2,
                cy=badge_y + badge_h / 2,
                width=badge_w,
                height=badge_h,
                ratio=0.5,
                corners_round=[1, 2, 3, 4],
                n_arc=32,
            )

            ax.add_patch(
                Polygon(
                    list(badge_poly.exterior.coords),
                    closed=True,
                    facecolor="#ffffff",
                    edgecolor="none",
                    linewidth=0,
                    transform=ax.transAxes,
                    zorder=12100,
                )
            )

            ax.text(
                badge_x + badge_w / 2,
                badge_y + badge_h / 2,
                badge_text,
                ha="center",
                va="center",
                fontsize=10,
                fontweight="bold",
                color=BRAND_BLUE,
                transform=ax.transAxes,
                zorder=12150,
                family=fontfamily,
            )

        body_x = cx + card_w * 0.035
        body_y = cy + card_h * 0.06
        body_w = card_w * 0.93
        body_h = card_h * 0.70

        graph_df = df_map[base_title]

        if graph_df is not None and not graph_df.empty:
            graph_df = graph_df.copy()
            graph_df["AuditDay"] = pd.to_datetime(graph_df["AuditDay"]).dt.normalize()
            graph_df = graph_df[
                graph_df["AuditDay"] <= week_ending_date
            ].sort_values("AuditDay").reset_index(drop=True)

            draw_graph(
                df=graph_df,
                ax=ax,
                x=body_x,
                y=body_y,
                width=body_w,
                height=body_h,
                box_bg="none",
                box_outline="none",
                axis_col=BRAND_BLUE,
                line_col=line_cols[base_title],
                key_date_fill="#bfd1eb",
            )
        else:
            ax.text(
                cx + card_w / 2,
                cy + card_h * 0.42,
                f"{base_title} graph\ncoming soon",
                ha="center",
                va="center",
                fontsize=11,
                color=CARD_LABEL,
                transform=ax.transAxes,
                zorder=12000,
                family=fontfamily,
            )

    return top_y - (2 * card_h) - gap_y

def draw_metric_card(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fontfamily: str,
    label: str,
    value,
    fill: str = CARD_FILL,
    edge: str = CARD_EDGE,
    label_color: str = CARD_LABEL,
    value_color: str = CARD_VALUE,
    linewidth: float = 1.2,
):
    draw_card_background(
        ax,
        x=x,
        y=y,
        width=width,
        height=height,
        fill=fill,
        edge=edge,
        linewidth=linewidth,
    )

    ax.text(
        x + width / 2,
        y + height * 0.68,
        str(label),
        ha="center",
        va="center",
        fontsize=12,
        fontweight="bold",
        color=label_color,
        transform=ax.transAxes,
        zorder=12000,
        family=fontfamily,
    )

    value_text = format_value(value)

    ax.text(
        x + width / 2,
        y + height * 0.33,
        value_text,
        ha="center",
        va="center",
        fontsize=23,
        fontweight="normal",
        color=value_color,
        transform=ax.transAxes,
        zorder=12000,
        family=fontfamily,
    )


# =========================================================
# Git helpers
# =========================================================
def get_commit_totals_for_report_week(
    repo,
    week_ending_date,
    tracked_exts: tuple[str, ...] = (".py", ".html"),
):
    if week_ending_date is None:
        return {
            "insertions": 0,
            "deletions": 0,
            "commits": 0,
            "py_insertions": 0,
            "html_insertions": 0,
            "new_files": 0,
        }

    # report week is Monday -> Sunday
    week_start_date = week_ending_date - timedelta(days=6)
    week_end_exclusive_date = week_ending_date + timedelta(days=1)

    total_insertions = 0
    total_deletions = 0
    commit_count = 0

    py_insertions = 0
    html_insertions = 0

    # count each newly-added tracked file only once across the week
    new_tracked_files = set()

    for commit in repo.iter_commits():
        committed_dt = commit.committed_datetime

        # make timezone naive (like your email logic 👀)
        committed_dt = commit.committed_datetime

        if committed_dt.tzinfo is not None:
            committed_dt = committed_dt.replace(tzinfo=None)

        week_start_dt = datetime.combine(week_start_date, datetime.min.time())
        week_end_dt = datetime.combine(week_end_exclusive_date, datetime.min.time())
        print(committed_dt)
        print("Week:", week_start_date, week_end_exclusive_date)
        if committed_dt < week_start_dt:
            break

        if not (week_start_dt <= committed_dt < week_end_dt):
            continue

        stats = commit.stats.total
        insertions = int(stats.get("insertions", 0))
        deletions = int(stats.get("deletions", 0))

        if insertions == 0 and deletions == 0:
            continue

        total_insertions += insertions
        total_deletions += deletions
        commit_count += 1

        # file-level stats for extension-specific insertions
        for file_path, file_stats in commit.stats.files.items():
            ext = Path(file_path).suffix.lower()
            if ext not in tracked_exts:
                continue

            file_insertions = int(file_stats.get("insertions", 0) or 0)

            if ext == ".py":
                py_insertions += file_insertions
            elif ext == ".html":
                html_insertions += file_insertions

        # count newly-added tracked files
        parents = commit.parents

        try:
            if parents:
                parent = parents[0]
                diffs = parent.diff(commit, create_patch=False)

                for diff in diffs:
                    if diff.change_type == "A":
                        new_path = diff.b_path
                        if not new_path:
                            continue

                        ext = Path(new_path).suffix.lower()
                        if ext in tracked_exts:
                            new_tracked_files.add(new_path)

            else:
                # initial commit: treat tracked files in the commit as new
                for file_path in commit.stats.files.keys():
                    ext = Path(file_path).suffix.lower()
                    if ext in tracked_exts:
                        new_tracked_files.add(file_path)

        except Exception:
            # if diff inspection fails, skip file creation counting rather than crashing
            pass

    return {
        "insertions": total_insertions,
        "deletions": total_deletions,
        "commits": commit_count,
        "py_insertions": py_insertions,
        "html_insertions": html_insertions,
        "new_files": len(new_tracked_files),
    }
def get_commit_rows(
    repo,
    max_count=10,
    max_message_len=63,
    search_limit=50,
    week_ending_date=None,
):
    raw_rows = []

    if week_ending_date:
        week_start = week_ending_date - timedelta(days=6)
        week_end_exclusive = week_ending_date + timedelta(days=1)
    else:
        week_start = None
        week_end_exclusive = None

    # -----------------------------
    # STEP 1: collect raw commits
    # -----------------------------
    for commit in repo.iter_commits(max_count=search_limit):
        committed_dt = commit.committed_datetime
        if committed_dt.tzinfo is not None:
            committed_dt = committed_dt.replace(tzinfo=None)

        committed_date = committed_dt.date()

        if week_start and committed_date < week_start:
            break

        if week_start and not (week_start <= committed_date < week_end_exclusive):
            continue

        stats = commit.stats.total
        insertions = int(stats.get("insertions", 0))
        deletions = int(stats.get("deletions", 0))

        if insertions == 0 and deletions == 0:
            continue

        message = commit.message.split("\n")[0].strip()
         
        if len(message) > max_message_len:
            message = message[: max_message_len - 1] + "…"

        raw_rows.append({
            "date": committed_dt,
            "insertions": insertions,
            "deletions": deletions,
            "message": message,
        })

    # -----------------------------
    # STEP 2: group by message
    # -----------------------------
    grouped = {}

    for r in raw_rows:
        key = r["message"]

        if key not in grouped:
            grouped[key] = {
                "date": r["date"],  # keep most recent
                "insertions": 0,
                "deletions": 0,
                "count": 0,
            }

        grouped[key]["insertions"] += r["insertions"]
        grouped[key]["deletions"] += r["deletions"]
        grouped[key]["count"] += 1

        # keep most recent date
        if r["date"] > grouped[key]["date"]:
            grouped[key]["date"] = r["date"]

    # -----------------------------
    # STEP 3: convert back to list
    # -----------------------------
    rows = []
    for msg, data in grouped.items():
        rows.append({
            "date": data["date"].strftime("%d %b"),
            "insertions": data["insertions"],
            "deletions": data["deletions"],
            "message": msg,
            "in_week": True,
        })

    # -----------------------------
    # STEP 4: sort + limit
    # -----------------------------
    rows.sort(key=lambda x: x["date"], reverse=True)

    return rows[:max_count]
def get_commit_panel_layout(row_h: float = 0.020) -> dict:
    return {
        "top_pad": 0.0,
        "title_band_h": 0.032,
        "gap_after_title": 0.012,
        "header_h": 0.026,
        "gap_after_header": 0.010,
        "row_gap": row_h,
        "bottom_pad": 0.018,
    }


def get_commit_panel_height(n_rows: int, row_h: float = 0.020) -> float:
    n_rows = max(1, n_rows)
    layout = get_commit_panel_layout(row_h=row_h)

    return (
        layout["top_pad"]
        + layout["title_band_h"]
        + layout["gap_after_title"]
        + layout["header_h"]
        + layout["gap_after_header"]
        + (n_rows * layout["row_gap"])
        + layout["bottom_pad"]
    )


def draw_commit_panel(
    ax,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fontfamily: str,
    title: str,
    commits: list[dict],
    max_rows: int | None = None,
    title_fontsize: float = 11,
    header_fontsize: float = 9,
    row_fontsize: float = 9.5,
    row_h: float = 0.020,
):
    if max_rows is None:
        max_rows = len(commits)

    visible_commits = commits[:max_rows]
    card_poly, _ = draw_card_background(
        ax,
        x=x,
        y=y,
        width=width,
        height=height,
        fill=COMMIT_FILL,
        edge=COMMIT_EDGE,
        ratio=0.06,
    )

    left_pad = width * 0.05
    right_pad = width * 0.04
    inner_x0 = x + left_pad
    inner_x1 = x + width - right_pad
    inner_w = inner_x1 - inner_x0

    date_x = inner_x0
    delta_x = inner_x0 + inner_w * 0.12
    msg_x = inner_x0 + inner_w * 0.29

    layout = get_commit_panel_layout(row_h=row_h)

    top_y = y + height
    title_band_top = top_y - layout["top_pad"]
    title_band_y = title_band_top - layout["title_band_h"]

    draw_panel_title_band(
        ax,
        x=x,
        y=title_band_y,
        width=width,
        height=layout["title_band_h"] + layout["top_pad"],
        fill=COMMIT_TITLE_FILL,
        card_poly=card_poly,
    )

    title_y = title_band_y + layout["title_band_h"] / 2
    header_top = title_band_y - layout["gap_after_title"]
    header_y = header_top - layout["header_h"] / 2
    header_rect_y = header_top - layout["header_h"]
    first_row_y = header_rect_y - layout["gap_after_header"] - row_h / 2

    ax.text(
        x + width / 2,
        title_y,
        str(title),
        ha="center",
        va="center",
        fontsize=title_fontsize,
        fontweight="bold",
        color=COMMIT_TITLE_TEXT,
        transform=ax.transAxes,
        zorder=12050,
        family=fontfamily,
    )

    ax.add_patch(
        Rectangle(
            (inner_x0-0.01, header_rect_y),
            inner_x1 - inner_x0 + 0.02,
            layout["header_h"],
            transform=ax.transAxes,
            facecolor=COMMIT_HEADER_FILL,
            edgecolor=COMMIT_HEADER_FILL,
            linewidth=0,
            zorder=11550,
        )
    )

    ax.text(
        date_x,
        header_y,
        "Date",
        ha="left",
        va="center",
        fontsize=header_fontsize,
        fontweight="bold",
        color=COMMIT_HEADER_TEXT,
        transform=ax.transAxes,
        zorder=12000,
        family=fontfamily,
    )

    ax.text(
        delta_x,
        header_y,
        "Changes",
        ha="left",
        va="center",
        fontsize=header_fontsize,
        fontweight="bold",
        color=COMMIT_HEADER_TEXT,
        transform=ax.transAxes,
        zorder=12000,
        family=fontfamily,
    )

    ax.text(
        msg_x,
        header_y,
        "Message",
        ha="left",
        va="center",
        fontsize=header_fontsize,
        fontweight="bold",
        color=COMMIT_HEADER_TEXT,
        transform=ax.transAxes,
        zorder=12000,
        family=fontfamily,
    )

    for i, commit in enumerate(visible_commits):
        row_y = first_row_y - i * layout["row_gap"]

        date_txt = str(commit.get("date", ""))
        ins = int(commit.get("insertions", 0))
        dele = int(commit.get("deletions", 0))
        msg = str(commit.get("message", ""))

        if not commit.get("in_week", True):
            msg = f"(older) {msg}"

        ax.text(
            date_x,
            row_y,
            date_txt,
            ha="left",
            va="center",
            fontsize=row_fontsize,
            color=CARD_LABEL,
            transform=ax.transAxes,
            zorder=12000,
            family=fontfamily,
        )

        ax.text(
            delta_x,
            row_y,
            f"+{ins}",
            ha="left",
            va="center",
            fontsize=row_fontsize,
            color=CARD_LABEL,
            transform=ax.transAxes,
            zorder=12000,
            family=fontfamily,
        )
        ax.text(
            delta_x+((msg_x-delta_x)/2),
            row_y,
            f"-{dele}",
            ha="left",
            va="center",
            fontsize=row_fontsize,
            color=CARD_LABEL,
            transform=ax.transAxes,
            zorder=12000,
            family=fontfamily,
        )
        ax.text(
            msg_x,
            row_y,
            msg,
            ha="left",
            va="center",
            fontsize=row_fontsize,
            color=CARD_LABEL,
            transform=ax.transAxes,
            zorder=12000,
            family=fontfamily,
        )


# =========================================================
# Error helpers
# =========================================================
def build_error_summary_line(summary_df: pd.DataFrame) -> str:
    errors_last_week = get_summary_value(
        summary_df,
        "NewErrorsThisWeek",
        "NewErrorsLastWeek",
        "ErrorLastWeek",
        "ErrorWeekToDate",
        "ErrorsWeekToDate",
        default=None,
    )
    errors_month_to_date = get_summary_value(
        summary_df,
        "ErrorMonthToDate",
        "ErrorsMonthToDate",
        "ErrorMTD",
        "ErrorsMTD",
        default=None,
    )
    errors_last_month = get_summary_value(
        summary_df,
        "ErrorsLastMonth",
        "ErrorLastMonth",
        default=None,
    )

    parts = []

    if errors_last_week is not None:
        parts.append(f"Errors last week: {format_value(errors_last_week)}")
    if errors_month_to_date is not None:
        parts.append(f"Errors month to date: {format_value(errors_month_to_date)}")
    if errors_last_month is not None:
        parts.append(f"Errors last month: {format_value(errors_last_month)}")

    if not parts:
        return "No error summary available"

    return "  |  ".join(parts)

def get_codebase_stats(
    repo_root: str | Path,
    *,
    exts: tuple[str, ...] = (".py", ".html"),
    ignore_dirs: tuple[str, ...] = (
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        "out",
    ),
):
    repo_root = Path(repo_root)

    total_files = 0
    total_lines = 0
    lines_by_ext = {ext: 0 for ext in exts}
    files_by_ext = {ext: 0 for ext in exts}

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue

        if any(part in ignore_dirs for part in path.parts):
            continue

        ext = path.suffix.lower()
        if ext not in exts:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        n_lines = len(text.splitlines())

        total_files += 1
        total_lines += n_lines
        lines_by_ext[ext] += n_lines
        files_by_ext[ext] += 1

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "lines_by_ext": lines_by_ext,
        "files_by_ext": files_by_ext,
    }
    
def get_codebase_stats(
    repo_root: str | Path,
    *,
    exts: tuple[str, ...] = (".py", ".html"),
    ignore_dirs: tuple[str, ...] = (
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        "out",
    ),
):
    repo_root = Path(repo_root)

    total_files = 0
    total_lines = 0
    lines_by_ext = {ext: 0 for ext in exts}
    files_by_ext = {ext: 0 for ext in exts}

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue

        if any(part in ignore_dirs for part in path.parts):
            continue

        ext = path.suffix.lower()
        if ext not in exts:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        n_lines = len(text.splitlines())

        total_files += 1
        total_lines += n_lines
        lines_by_ext[ext] += n_lines
        files_by_ext[ext] += 1

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "lines_by_ext": lines_by_ext,
        "files_by_ext": files_by_ext,
    }
def build_codebase_summary_line(
    summary_df: pd.DataFrame,
    repo_root: str | Path,
    weekly_commit_stats: dict | None = None,
) -> str:
    stats = get_codebase_stats(repo_root)

    table_count = get_summary_value(summary_df, "TableCount", default=None)
    new_tables_this_week = get_summary_value(summary_df, "TablesNewThisWeek", default=0)

    py_lines = stats["lines_by_ext"].get(".py", 0)
    html_lines = stats["lines_by_ext"].get(".html", 0)
    total_lines = stats["total_lines"]
    total_files = stats["total_files"]

    total_new_lines = 0
    py_new_lines = 0
    html_new_lines = 0
    new_files_this_week = 0

    if weekly_commit_stats:
        total_new_lines = int(weekly_commit_stats.get("insertions", 0) or 0)

        # only use these if you later add them to weekly_commit_stats
        py_new_lines = int(weekly_commit_stats.get("py_insertions", 0) or 0)
        html_new_lines = int(weekly_commit_stats.get("html_insertions", 0) or 0)
        new_files_this_week = int(weekly_commit_stats.get("new_files", 0) or 0)
    def fmt(base, delta):
        if delta and delta != 0:
            return f"{format_value(base)} (+{format_value(delta)})"
        return f"{format_value(base)}"
    parts = [
        f"Python: {fmt(py_lines, py_new_lines)}",
        f"HTML: {fmt(html_lines, html_new_lines)}",
        f"Total lines: {fmt(total_lines, total_new_lines)}",
        f"Files: {fmt(total_files, new_files_this_week)}",
    ]


    if table_count is not None:
        parts.append(
            f"Tables: {fmt(table_count, new_tables_this_week)}"
        )


    return "  |  ".join(parts)


# =========================================================
# Main
# =========================================================
def build_weekly_stats_pdf(
    *,
    out_pdf_path: str | Path,
    as_of_date: str | None = None,
    footer_svg: str | Path = "app/static/footer.svg",
    fonts_dir: str | Path = "app/static/fonts",
    dpi: int = 300,
    footer_height_frac: float = 0.10,
    ooo_day: str | None = "Monday",
    outlook_mailbox_name: str = OUTLOOK_MAILBOX_NAME,
):
    summary_df, users_by_role_df, respondents_by_survey_df = load_weekly_stats(as_of_date=as_of_date)

    if not summary_df.empty and "WeekEndingSunday" in summary_df.columns:
        raw_week_ending = summary_df.iloc[0]["WeekEndingSunday"]
    else:
        raw_week_ending = as_of_date if as_of_date else None

    if raw_week_ending is None:
        week_ending_date = None
        week_ending_text = "Current Week"
    else:
        week_ending_date = pd.to_datetime(raw_week_ending).date()
        week_ending_text = week_ending_date.strftime("%d %B %Y")

    title = "WSFL Weekly Database Report"

    if ooo_day is not None and week_ending_date is not None:
        weekday_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }

        ooo_day_num = weekday_map.get(ooo_day.strip().lower())
        week_ending_text = week_ending_date.strftime("%d %B %Y").lstrip("0")
        if ooo_day_num is None:
            subtitle = f"Week ending Sunday {week_ending_text}"
        else:
            days_ahead = (ooo_day_num - week_ending_date.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7

            ooo_date = week_ending_date + timedelta(days=days_ahead)
            ooo_text = ooo_date.strftime("%d %B %Y").lstrip("0")
            subtitle = (
                f"Week ending Sunday {week_ending_text}. "
                f"Supporting document for one-on-one {ooo_text}"
            )
    else:
        subtitle = f"Week ending Sunday {week_ending_text}"

    out_pdf_path = Path(out_pdf_path)
    out_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(8.27, 11.69), dpi=dpi)
    fig.patch.set_facecolor(BODY_BG)

    footer_svg_path = Path(footer_svg)
    if not footer_svg_path.is_absolute():
        footer_svg_path = Path.cwd() / footer_svg_path

    if not footer_svg_path.exists():
        raise FileNotFoundError(f"Footer SVG not found at: {footer_svg_path}")

    add_footer_behind(
        fig,
        footer_svg_path,
        bottom_margin_frac=0.00,
        max_footer_height_frac=float(footer_height_frac),
        col_master=f"{BRAND_BLUE}80",
    )

    ax = fig.add_axes([0, 0, 1, 1], zorder=10000)
    ax.set_axis_off()
    ax.patch.set_alpha(0.0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("auto")

    family = load_ppmori_fonts(str(fonts_dir))
    all_df = load_linegraph_df(refresh = True)
    student_df = trim_graph_df(all_df[all_df["Category"] == "Students"], week_ending_date)
    class_df   = trim_graph_df(all_df[all_df["Category"] == "Classes"], week_ending_date)
    user_df    = trim_graph_df(all_df[all_df["Category"] == "Users"], week_ending_date)
    form_df    = trim_graph_df(all_df[all_df["Category"] == "Forms"], week_ending_date)
    fig_w, fig_h = fig.get_size_inches()
    aspect = fig_w / fig_h

    margin = 0.02
    page_left = margin
    page_right = 1 - margin
    page_top = 1 - (margin * aspect)
    page_w = page_right - page_left

    title_h = 0.055
    subtitle_h = 0.027
    band_gap = 0.008
    content_gap = 0.008

    title_y = page_top - title_h
    subtitle_y = title_y - band_gap - subtitle_h

    draw_title_band(
        ax,
        x=page_left,
        y=title_y,
        width=page_w,
        height=title_h,
        title=title,
        fontfamily=family,
    )

    draw_subtitle_band(
        ax,
        x=page_left, 
        y=subtitle_y,
        width=page_w,
        height=subtitle_h,
        subtitle=subtitle,
        fontfamily=family,
    )

    user_lines = [
        f"{num} {role} logins activated"
        for num, role in zip(users_by_role_df["NewUsersThisWeek"], users_by_role_df["RoleTitle"])
    ]
    survey_lines = [
        f"{num} new {survey_title}s Recorded"
        for num, survey_title in zip(
            respondents_by_survey_df["NewSubmittedThisWeek"],
            respondents_by_survey_df["Title"],
        )
    ]
    error_summary_line = build_error_summary_line(summary_df)

    email_summary = get_email_summary(
        mailbox_name=outlook_mailbox_name,
        week_ending_date=week_ending_date,
    )
    email_summary_line = build_email_summary_line(email_summary)
    email_folder_lines = build_email_folder_lines(email_summary, top_n=TOP_EMAIL_FOLDERS)
    email_subject_lines = build_email_subject_lines(email_summary, top_n=TOP_EMAIL_SUBJECTS)

    repo = git.Repo(os.getcwd())
  
    
    commit_rows = get_commit_rows(
        repo,
        max_count=10,
        week_ending_date=week_ending_date
    )
    weekly_commit_stats = get_commit_totals_for_report_week(repo, week_ending_date)
    codebase_summary_line = build_codebase_summary_line(
        summary_df,
        repo.working_tree_dir,
         weekly_commit_stats=weekly_commit_stats,
    )
    top_cards_top = subtitle_y - content_gap
    top_cards_h = 0.34
    top_cards_gap_x = 0.012
    top_cards_gap_y = 0.012

    top_cards_bottom = draw_top_graph_cards(
        ax,
        page_left=page_left,
        page_right=page_right,
        top_y=top_cards_top,
        total_h=top_cards_h,
        gap_x=top_cards_gap_x,
        gap_y=top_cards_gap_y,
        fontfamily=family,
        student_df=student_df,
        class_df=class_df,
        user_df=user_df,
        form_df=form_df,
        week_ending_date=week_ending_date
    )

    section_gap = 0.010
    error_h = 0.024
    email_h = 0.024
    summary_h = 0.12
    codebase_h = 0.024

    # Error summary bar
    error_top = top_cards_bottom - section_gap
    error_y = error_top - error_h

    draw_panel(
        ax,
        x=page_left,
        y=error_y,
        width=page_w,
        height=error_h,
        fontfamily=family,
        title=None,
        lines=[error_summary_line],
        fill=ERROR_FILL,
        edge=ERROR_EDGE,
        text_color=ERROR_TEXT,
        title_fill=ERROR_FILL,
        title_text_color=ERROR_TEXT,
        title_fontsize=11,
        line_fontsize=11,
        linewidth=1.6,
        single_line=True,
    )

    email_top = error_y - section_gap
    email_y = email_top - email_h

    draw_panel(
        ax,
        x=page_left,
        y=email_y,
        width=page_w,
        height=email_h,
        fontfamily=family,
        title=None,
        lines=[email_summary_line],
        fill=ERROR_TEXT,
        edge=ERROR_FILL,
        text_color=ERROR_FILL,
        title_fill=ERROR_TEXT,
        title_text_color=ERROR_FILL,
        title_fontsize=11,
        line_fontsize=11,
        linewidth=1.6,
        single_line=True,
    )

    # Summary panels

    summary_top = email_y - section_gap
    summary_y = summary_top - summary_h
    
    left_panel_w = (page_w - section_gap) / 2
    right_panel_x = page_left + left_panel_w + section_gap
    right_panel_w = page_right - right_panel_x

    draw_panel(
        ax,
        x=page_left,
        y=summary_y,
        width=left_panel_w,
        height=summary_h,
        fontfamily=family,
        title="New User Summary",
        lines=user_lines,
        fill=PANEL_BODY_FILL,
        edge=PANEL_BODY_EDGE,
        text_color=CARD_LABEL,
        title_fill=PANEL_TITLE_FILL,
        title_text_color=PANEL_TITLE_TEXT,
        title_fontsize=12,
        line_fontsize=12,
        linewidth=1.2,
        single_line=False,
    )

    draw_panel(
        ax,
        x=right_panel_x,
        y=summary_y,
        width=right_panel_w,
        height=summary_h,
        fontfamily=family,
        title="Emails Received Summary",
        lines=email_folder_lines,
        fill=PANEL_BODY_FILL,
        edge=PANEL_BODY_EDGE,
        text_color=CARD_LABEL,
        title_fill=PANEL_TITLE_FILL,
        title_text_color=PANEL_TITLE_TEXT,
        title_fontsize=11,
        line_fontsize=12,
        linewidth=1.2,
        single_line=False,
    )


    # Commit panel
    commit_row_h = 0.017
    commit_h = get_commit_panel_height(len(commit_rows), row_h=commit_row_h)
    commit_top = summary_y - section_gap
    commit_y = commit_top - commit_h

    draw_commit_panel(
        ax,
        x=page_left,
        y=commit_y,
        width=page_w,
        height=commit_h,
        fontfamily=family,
        title="10 Latest Commits",
        commits=commit_rows,
        row_h=commit_row_h,
        header_fontsize=12,
        title_fontsize=12,
        row_fontsize=12,
    )
    codebase_top = commit_y - section_gap
    codebase_y = codebase_top - codebase_h

    draw_panel(
        ax,
        x=page_left,
        y=codebase_y,
        width=page_w,
        height=codebase_h,
        fontfamily=family,
        title=None,
        lines=[codebase_summary_line],
        fill=ERROR_TEXT,
        edge=ERROR_FILL,
        text_color=ERROR_FILL,
        title_fill=ERROR_TEXT,
        title_text_color=ERROR_FILL,
        title_fontsize=11,
        line_fontsize=11,
        linewidth=1.6,
        single_line=True,
    )
    if DEBUG_COL is not None:
        draw_debug_grid(
            ax,
            color=DEBUG_COL,
            x0=0.0,
            x1=1.0,
            y0=0.0,
            y1=1.0,
            step=0.1,
            mini_step=0.02,
        )

    fig.savefig(out_pdf_path, format="pdf", dpi=dpi, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print(f"[OK] Wrote PDF: {out_pdf_path.resolve()}")
    
    return summary_df, users_by_role_df, respondents_by_survey_df


if __name__ == "__main__":
    load_dotenv()
    as_of = '2026-04-20' 
    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)

    summary_df, users_by_role_df, respondents_by_survey_df = build_weekly_stats_pdf(
        out_pdf_path=out_dir / f"weekly_stats_{as_of}.pdf",
        as_of_date=as_of, 
        footer_svg="app/static/footer.svg",
        fonts_dir="app/static/fonts",
        dpi=300,
        footer_height_frac=0.13,
    )