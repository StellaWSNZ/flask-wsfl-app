# app/routes/instructions.py
from __future__ import annotations

import os
import traceback
import mimetypes
from pathlib import Path
from typing import Optional

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
    send_file,
    make_response,
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

# Ensure correct MIME types in Linux containers
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("application/pdf", ".pdf")


def _is_render() -> bool:
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


def _is_admin_user(user_admin_val) -> bool:
    # session might store 0/1 as int, bool, or string
    return str(user_admin_val or "0") not in ("0", "", "None", "false", "False")


def _log_role_dir_debug(role_code: str, role_dir: Path) -> None:
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
            try:
                children = sorted([p.name for p in role_dir.iterdir()], key=str.lower)
            except Exception as e:
                children = [f"<iterdir failed: {e}>"]
            current_app.logger.warning("role_dir_children: %s", children)

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
        current_app.logger.exception("Render debug logging failed")


# -----------------------------------------------------------------------------
# ASSET ROUTE (fixes MP4 + inline PDF behaviour)
# -----------------------------------------------------------------------------
@instructions_bp.route("/instructions_asset/<role_code>/<path:filename>")
@login_required
def instructions_asset(role_code: str, filename: str):
    """
    Serve instruction assets from static/instructions/<ROLE>/ with:
      - correct mimetype
      - inline PDF when requested
      - Range-friendly video streaming (send_file supports conditional requests)
    Query params:
      ?inline=1   -> force Content-Disposition: inline
      ?download=1 -> force Content-Disposition: attachment
    """
    role = (role_code or "").upper()
    if role not in ALLOWED_ROLES:
        abort(404)

    user_role = (session.get("user_role") or "").upper()
    if user_role != ADMIN_CODE and user_role != role:
        abort(403)

    base_dir = Path(current_app.static_folder) / "instructions" / role
    if not base_dir.exists():
        abort(404)

    # Prevent path traversal
    safe_path = (base_dir / filename).resolve()
    if not str(safe_path).startswith(str(base_dir.resolve())):
        abort(404)

    if not safe_path.exists() or not safe_path.is_file():
        abort(404)

    ext = safe_path.suffix.lower()
    if ext not in VIDEO_EXTS and ext != PDF_EXT:
        abort(404)

    mime, _ = mimetypes.guess_type(str(safe_path))
    if not mime:
        # fallback
        mime = "application/octet-stream"

    inline = request.args.get("inline") == "1"
    download = request.args.get("download") == "1"

    # If neither specified, default:
    # - PDFs: inline (so “Open PDF” works)
    # - Videos: inline
    if ext == PDF_EXT and not download:
        inline = True

    as_attachment = bool(download) and not inline

    # Key bit: conditional=True enables 206 for Range requests (video tag uses Range)
    resp = send_file(
        safe_path,
        mimetype=mime,
        as_attachment=as_attachment,
        download_name=safe_path.name if as_attachment else None,
        conditional=True,
        max_age=0,
        etag=True,
        last_modified=True,
    )

    # Force Content-Disposition for inline PDFs (browser opens tab instead of downloading)
    if inline and not as_attachment:
        # Werkzeug may omit Content-Disposition for inline; set explicitly
        resp.headers["Content-Disposition"] = f'inline; filename="{safe_path.name}"'

    # Extra: tighten CSP issues (if you have strict CSP in header.html, this helps)
    # (Only if you want; harmless if you don’t set CSP elsewhere)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")

    # Render debugging: log exactly what the browser asked for
    if _is_render():
        current_app.logger.warning(
            "INSTRUCTIONS_ASSET: role=%s file=%s status=%s range=%r mime=%s inline=%s download=%s user_role=%s",
            role,
            filename,
            resp.status_code,
            request.headers.get("Range"),
            mime,
            inline,
            download,
            user_role,
        )

    return resp


def _discover_items_for_role(role_code: str, user_admin: int | None):
    static_root = Path(current_app.static_folder)
    role_dir = static_root / "instructions" / role_code

    _log_role_dir_debug(role_code, role_dir)

    if not role_dir.exists():
        current_app.logger.warning("Instructions folder missing: %s", role_dir)
        return []

    stems: dict[str, list[Path]] = {}
    for p in role_dir.rglob("*"):
        if p.is_file():
            stems.setdefault(p.stem, []).append(p)

    def _asset_url(p: Path, *, inline: Optional[bool] = None, download: Optional[bool] = None) -> str:
        rel = p.relative_to(role_dir).as_posix()
        qs = {}
        if inline is True:
            qs["inline"] = "1"
        if download is True:
            qs["download"] = "1"
        if qs:
            return url_for("instructions_bp.instructions_asset", role_code=role_code, filename=rel, **qs)
        return url_for("instructions_bp.instructions_asset", role_code=role_code, filename=rel)

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
        if is_admin_item and not _is_admin_user(user_admin):
            continue

        clean_stem = stem[4:] if is_admin_item else stem
        title = clean_stem.replace("_", " ").strip()

        items.append(
            {
                "title": title,
                "video_url": _asset_url(video_path) if video_path else None,
                # For “open pdf”, we want inline; for “download pdf”, your HTML can add ?download=1
                "pdf_url": _asset_url(pdf_path, inline=True) if pdf_path else None,
                "pdf_download_url": _asset_url(pdf_path, download=True) if pdf_path else None,
                "admin_only": bool(is_admin_item),
            }
        )

    items.sort(key=lambda x: x["title"].lower())

    if _is_render():
        current_app.logger.warning("RENDER: discovered %d items for role=%s", len(items), role_code)

    return items


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@instructions_bp.route("/instructions")
@login_required
def instructions_me():
    try:
        user_role = (session.get("user_role") or "").upper()

        if user_role in ALLOWED_ROLES:
            label = ROLE_TO_LABEL.get(user_role, user_role)
            return redirect(url_for("instructions_bp.instructions_for_label", label=label))

        if user_role == ADMIN_CODE:
            DEFAULT_ADMIN_LABEL = "Funder"
            return redirect(url_for("instructions_bp.instructions_for_label", label=DEFAULT_ADMIN_LABEL))

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
            debug_info=debug_info,
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
# FAQ
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
    try:
        faq_groups = load_faq_rows()
        current_app.logger.info("FAQ groups: %r", faq_groups)
        return render_template("faq.html", faq_groups=faq_groups)
    except Exception:
        current_app.logger.exception("\n=== ERROR IN /FAQ ROUTE ===")
        traceback.print_exc()
        raise