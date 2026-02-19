# app/routes/instructions.py
from __future__ import annotations

import os
import traceback
from pathlib import Path

import pandas as pd
from flask import (
    Blueprint,
    render_template,
    request,
    url_for,
    abort,
    current_app,
    session,
    redirect,
)

from app.routes.auth import login_required
from app.utils.database import log_alert

# -----------------------------------------------------------------------------
# Blueprint
# -----------------------------------------------------------------------------
instructions_bp = Blueprint("instructions_bp", __name__)

# -----------------------------------------------------------------------------
# Config / constants
# -----------------------------------------------------------------------------
ALLOWED_ROLES = {"PRO", "GRP", "FUN", "MOE"}  # Provider, Provider Group, Funder, School
ADMIN_CODE = "ADM"

ROLE_TO_LABEL = {
    "PRO": "Provider",
    "FUN": "Funder",
    "MOE": "School",
    "GRP": "ProviderGroup",  # URL-friendly
}

VIDEO_EXTS = {".mp4", ".webm", ".mov"}
PDF_EXT = ".pdf"


def _is_render() -> bool:
    # Render sets RENDER=true in the environment
    return os.environ.get("RENDER") == "true" or bool(os.environ.get("RENDER_SERVICE_ID"))


def _label_to_role(label: str) -> str | None:
    if not label:
        return None
    key = "".join(ch for ch in label.lower() if ch.isalnum())
    return {
        "provider": "PRO",
        "funder": "FUN",
        "school": "MOE",
        "providergroup": "GRP",
    }.get(key)


def _log_role_dir_debug(role_code: str, role_dir: Path) -> None:
    """
    Render-only filesystem debug logging. Shows what the deployed container can see.
    """
    if not _is_render():
        return

    try:
        current_app.logger.warning("=== INSTRUCTIONS DEBUG (RENDER) ===")
        current_app.logger.warning("role_code: %s", role_code)
        current_app.logger.warning("cwd: %s", os.getcwd())
        current_app.logger.warning("static_folder: %s", current_app.static_folder)
        current_app.logger.warning("role_dir: %s", str(role_dir))
        current_app.logger.warning("role_dir_exists: %s", role_dir.exists())

        if role_dir.exists():
            # show top-level files and folders
            try:
                children = sorted([p.name for p in role_dir.iterdir()], key=str.lower)
            except Exception as e:
                children = [f"<iterdir failed: {e}>"]
            current_app.logger.warning("role_dir_children: %s", children)

            # show first N discovered files recursively (helps if you use subfolders)
            try:
                all_files = sorted(
                    [str(p.relative_to(role_dir)) for p in role_dir.rglob("*") if p.is_file()],
                    key=str.lower,
                )
                current_app.logger.warning("role_dir_rglob_file_count: %s", len(all_files))
                current_app.logger.warning("role_dir_rglob_files_head: %s", all_files[:60])
            except Exception as e:
                current_app.logger.warning("role_dir_rglob_failed: %s", e)

        current_app.logger.warning("=== END INSTRUCTIONS DEBUG (RENDER) ===")

    except Exception:
        # never let debug logging crash the request
        current_app.logger.exception("Render debug logging failed")


def _discover_items_for_role(role_code: str, user_admin: int | None):
    """
    Scan static/instructions/<ROLE_CODE>/ and pair files by prefix (stem).

    Naming rule:
      - Normal item:     Staff_Maintenance.mp4 / Staff_Maintenance.pdf
      - Admin only item: ADM_Staff_Maintenance.mp4 / ADM_Staff_Maintenance.pdf

    Admin only items (stem starts with ADM_) are only shown if user_admin is truthy.
    The ADM_ prefix is removed from the displayed title.
    """
    static_root = Path(current_app.static_folder)
    role_dir = static_root / "instructions" / role_code

    # Render-only debug logging right where things go wrong
    _log_role_dir_debug(role_code, role_dir)

    if not role_dir.exists():
        # Keep returning [] in production, but this log is critical on Render
        current_app.logger.warning("Instructions folder missing: %s", role_dir)
        return []

    stems: dict[str, list[Path]] = {}
    for p in role_dir.rglob("*"):
        if p.is_file():
            stems.setdefault(p.stem, []).append(p)

    items = []
    for stem, files in stems.items():
        video_path = None
        pdf_path = None

        for f in files:
            suf = f.suffix.lower()
            if suf in VIDEO_EXTS and video_path is None:
                video_path = f
            elif suf == PDF_EXT and pdf_path is None:
                pdf_path = f

        if not (video_path or pdf_path):
            continue

        is_admin_item = stem.upper().startswith("ADM_")
        if is_admin_item and not user_admin:
            continue

        clean_stem = stem[4:] if is_admin_item else stem
        title = clean_stem.replace("_", " ").strip()

        def _to_static_url(p: Path) -> str:
            rel = p.relative_to(static_root).as_posix()
            return url_for("static", filename=rel)

        items.append(
            {
                "title": title,
                "video_url": _to_static_url(video_path) if video_path else None,
                "pdf_url": _to_static_url(pdf_path) if pdf_path else None,
                "admin_only": bool(is_admin_item),
            }
        )

    items.sort(key=lambda x: x["title"].lower())

    if _is_render():
        current_app.logger.warning(
            "RENDER: discovered %d items for role=%s",
            len(items),
            role_code,
        )

    return items


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@instructions_bp.route("/instructions")
@login_required
def instructions_me():
    """
    Redirect the user to their own label URL based on session['user_role'].
    Non-admins go to their role only.
    Admins default to Funder (change DEFAULT_ADMIN_LABEL if you prefer).
    """
    try:
        user_role = (session.get("user_role") or "").upper()

        if user_role in ALLOWED_ROLES:
            label = ROLE_TO_LABEL.get(user_role, user_role)
            return redirect(url_for("instructions_bp.instructions_for_label", label=label))

        if user_role == ADMIN_CODE:
            DEFAULT_ADMIN_LABEL = "Funder"  # or "Provider" / "School" / "ProviderGroup"
            return redirect(
                url_for("instructions_bp.instructions_for_label", label=DEFAULT_ADMIN_LABEL)
            )

        return (
            render_template("error.html", error="You are not authorised to view that page.", code=403),
            403,
        )

    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"instructions_me: {e}",
        )
        current_app.logger.exception("Unhandled error in instructions_me")
        abort(500)


@instructions_bp.route("/instructions/<label>")
@login_required
def instructions_for_label(label: str):
    """
    Pretty label URLs:
      /instructions/provider
      /instructions/funder
      /instructions/school
      /instructions/providergroup
    Map to internal codes: PRO, FUN, MOE, GRP
    """
    try:
        role_code = _label_to_role(label)
        if role_code is None or role_code not in ALLOWED_ROLES:
            abort(404)

        user_role = (session.get("user_role") or "").upper()
        if user_role != ADMIN_CODE and user_role != role_code:
            return (
                render_template("error.html", error="You are not authorised to view that page.", code=403),
                403,
            )

        items = _discover_items_for_role(role_code, session.get("user_admin"))

        display_label = ROLE_TO_LABEL[role_code]
        if display_label == "ProviderGroup":
            display_label = "Provider Group"

        # OPTIONAL: show debug info on page only on Render and only for admins
        debug_info = None
        if _is_render() and user_role == ADMIN_CODE:
            base_dir = Path(current_app.static_folder) / "instructions" / role_code
            debug_info = {
                "static_folder": str(current_app.static_folder),
                "role_dir": str(base_dir),
                "exists": base_dir.exists(),
                "files": sorted([p.name for p in base_dir.iterdir() if p.is_file()], key=str.lower)
                if base_dir.exists()
                else [],
            }

        return render_template(
            "instructions.html",
            role_code=role_code,
            role_label=display_label,
            items=items,
            user_role=user_role,
            debug_info=debug_info,  # safe to ignore in template if not used
        )

    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"instructions_for_label('{label}'): {e}",
        )
        current_app.logger.exception("Unhandled error in instructions_for_label(%s)", label)
        abort(500)


@instructions_bp.route("/instructions_debug/<label>")
@login_required
def instructions_debug(label: str):
    """
    Debug helper to confirm what files the server can see.
    Visit:
      /instructions_debug/provider
      /instructions_debug/funder
      /instructions_debug/school
      /instructions_debug/providergroup
    """
    role_code = _label_to_role(label)
    if not role_code:
        abort(404)

    base_dir = Path(current_app.static_folder) / "instructions" / role_code
    exists = base_dir.exists()
    files = []
    if exists:
        files = sorted([p.name for p in base_dir.iterdir() if p.is_file()], key=str.lower)

    return {
        "role_code": role_code,
        "static_folder": str(current_app.static_folder),
        "cwd": os.getcwd(),
        "base_dir": str(base_dir),
        "exists": exists,
        "file_count": len(files),
        "files": files[:200],
    }


@instructions_bp.route("/instructions/code/<role_code>")
@login_required
def instructions_for_code(role_code: str):
    """
    Back-compat route.
    """
    try:
        role = (role_code or "").upper()
        if role not in ALLOWED_ROLES:
            abort(404)

        user_role = (session.get("user_role") or "").upper()
        if user_role != ADMIN_CODE and user_role != role:
            return (
                render_template("error.html", error="You are not authorised to view that page.", code=403),
                403,
            )

        items = _discover_items_for_role(role, session.get("user_admin"))

        display_label = ROLE_TO_LABEL[role]
        if display_label == "ProviderGroup":
            display_label = "Provider Group"

        return render_template(
            "instructions.html",
            role_code=role,
            role_label=display_label,
            items=items,
            user_role=user_role,
        )

    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"instructions_for_code('{role_code}'): {e}",
        )
        current_app.logger.exception("Unhandled error in instructions_for_code(%s)", role_code)
        abort(500)


# -----------------------------------------------------------------------------
# FAQ (kept since you had it in this file)
# -----------------------------------------------------------------------------
def load_faq_rows():
    static_dir = Path(current_app.static_folder)
    xlsx_path = static_dir / "WSFL_FAQs_REVISED.xlsx"

    if not xlsx_path.exists():
        raise FileNotFoundError(f"FAQ Excel file not found at: {xlsx_path}")

    df = pd.read_excel(xlsx_path)

    required_cols = ["Question Type", "Question", "Answer", "Tags"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"FAQ file is missing required columns: {missing}")

    df = df.dropna(how="all")

    records = df.to_dict(orient="records")

    grouped = {}
    for row in records:
        qtype = str(row.get("Question Type") or "General")
        grouped.setdefault(qtype, []).append(row)

    return grouped


@instructions_bp.route("/FAQ")
def faq_page():
    """
    FAQ page – reads the Excel each request so changes show as soon as
    you save the file.
    """
    try:
        faq_groups = load_faq_rows()
        current_app.logger.info("FAQ groups: %r", faq_groups)
        return render_template("faq.html", faq_groups=faq_groups)
    except Exception:
        current_app.logger.exception("\n=== ERROR IN /FAQ ROUTE ===")
        traceback.print_exc()
        raise