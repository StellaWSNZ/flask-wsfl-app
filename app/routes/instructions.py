# app/routes/instructions.py
from __future__ import annotations

import os
import mimetypes
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
    send_file,
    Response,
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
ALLOWED_ROLES = {"PRO", "GRP", "FUN", "MOE"}
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
    """Render-only filesystem debug logging."""
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
            children = sorted([p.name for p in role_dir.iterdir()], key=str.lower)
            current_app.logger.warning("role_dir_children: %s", children)

            all_files = sorted(
                [str(p.relative_to(role_dir)) for p in role_dir.rglob("*") if p.is_file()],
                key=str.lower,
            )
            current_app.logger.warning("role_dir_rglob_file_count: %s", len(all_files))
            current_app.logger.warning("role_dir_rglob_files_head: %s", all_files[:60])

        current_app.logger.warning("=== END INSTRUCTIONS DEBUG (RENDER) ===")
    except Exception:
        current_app.logger.exception("Render debug logging failed")


def _safe_resolve_under(base_dir: Path, filename: str) -> Path:
    """
    Prevent ../ traversal and ensure resolved path stays under base_dir.
    """
    if not filename or filename.strip() == "":
        abort(404)

    # Block path separators explicitly (we only allow files directly in role folder)
    if "/" in filename or "\\" in filename:
        abort(404)

    full = (base_dir / filename).resolve()
    base = base_dir.resolve()

    if not str(full).startswith(str(base) + os.sep) and full != base:
        abort(404)

    if not full.exists() or not full.is_file():
        abort(404)

    return full


def _user_can_access_role(role_code: str) -> bool:
    user_role = (session.get("user_role") or "").upper()
    if user_role == ADMIN_CODE:
        return True
    return user_role == role_code


def _guess_mime(path: Path) -> str:
    # Ensure MP4 is correct (mimetypes can be flaky in some containers)
    suf = path.suffix.lower()
    if suf == ".mp4":
        return "video/mp4"
    if suf == ".webm":
        return "video/webm"
    if suf == ".mov":
        return "video/quicktime"
    if suf == ".pdf":
        return "application/pdf"
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


# -----------------------------------------------------------------------------
# Asset route (THIS is the important bit for Render)
# -----------------------------------------------------------------------------
@instructions_bp.route("/instructions_asset/<role_code>/<path:filename>")
@login_required
def instructions_asset(role_code: str, filename: str):
    """
    Serves instruction assets with:
      - auth checks
      - safe path resolution
      - conditional=True for Range/206 streaming
      - correct Content-Disposition (inline vs attachment)
    """
    role = (role_code or "").upper()
    if role not in ALLOWED_ROLES:
        abort(404)

    if not _user_can_access_role(role):
        abort(403)

    base_dir = Path(current_app.static_folder) / "instructions" / role
    full_path = _safe_resolve_under(base_dir, filename)

    mime = _guess_mime(full_path)
    is_video = full_path.suffix.lower() in VIDEO_EXTS
    is_pdf = full_path.suffix.lower() == PDF_EXT

    # Query: ?download=1 (only meaningful for PDFs; videos should always be inline)
    download = request.args.get("download", "").strip() in {"1", "true", "yes"}
    inline = True if is_video else (not download)

    # Serve the file (conditional=True enables Range support when possible)
    resp: Response = send_file(
        full_path,
        mimetype=mime,
        as_attachment=(not inline),
        download_name=full_path.name,
        conditional=True,
        max_age=0,
        etag=True,
        last_modified=True,
    )

    # Force headers explicitly (helps browser behavior)
    resp.headers["Content-Type"] = mime
    resp.headers["Accept-Ranges"] = "bytes"

    # Many browsers care about this for inline rendering
    disp = "inline" if inline else "attachment"
    resp.headers["Content-Disposition"] = f'{disp}; filename="{full_path.name}"'

    # Helpful debug
    if _is_render():
        status = getattr(resp, "status_code", None)
        rng = request.headers.get("Range")
        current_app.logger.warning(
            "INSTRUCTIONS_ASSET: role=%s file=%s status=%s range=%r mime=%s inline=%s download=%s user_role=%s",
            role,
            full_path.name,
            status,
            rng,
            mime,
            inline,
            download,
            (session.get("user_role") or ""),
        )

    return resp


def _discover_items_for_role(role_code: str, user_admin: int | None):
    """
    Scan static/instructions/<ROLE_CODE>/ and pair files by stem.
    Admin-only items start with ADM_ and are hidden unless user_admin truthy.
    """
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

        def asset_url(p: Path, *, download: bool = False) -> str:
            return url_for(
                "instructions_bp.instructions_asset",
                role_code=role_code,
                filename=p.name,
                download=("1" if download else None),
            )

        items.append(
            {
                "title": title,
                # videos: ALWAYS inline
                "video_url": asset_url(video_path) if video_path else None,
                # pdf: provide both URLs so template can choose
                "pdf_open_url": asset_url(pdf_path, download=False) if pdf_path else None,
                "pdf_download_url": asset_url(pdf_path, download=True) if pdf_path else None,
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

        return render_template(
            "instructions.html",
            role_code=role_code,
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
    try:
        faq_groups = load_faq_rows()
        current_app.logger.info("FAQ groups: %r", faq_groups)
        return render_template("faq.html", faq_groups=faq_groups)
    except Exception:
        current_app.logger.exception("\n=== ERROR IN /FAQ ROUTE ===")
        traceback.print_exc()
        raise