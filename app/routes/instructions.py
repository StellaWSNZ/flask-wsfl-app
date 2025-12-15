# app/routes/instructions.py
from pathlib import Path
import traceback
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
import pandas as pd
# Create the blueprint
instructions_bp = Blueprint("instructions_bp", __name__)

# Internal folder/role codes (do NOT change your static folder names)
ALLOWED_ROLES = {"PRO", "GRP", "FUN", "MOE"}   # Provider, Provider Group, Funder, School
ADMIN_CODE = "ADM"

# Human-friendly labels for display + routing
ROLE_TO_LABEL = {
    "PRO": "Provider",
    "FUN": "Funder",
    "MOE": "School",
    "GRP": "ProviderGroup",  # URL-friendly; display text can add a space if you prefer
}

# Reverse lookup for incoming label paths (case/space/dash/underscore-insensitive)
def _label_to_role(label: str):
    if not label:
        return None
    key = "".join(ch for ch in label.lower() if ch.isalnum())  # strip spaces/dashes/underscores
    lut = {
        "provider": "PRO",
        "funder": "FUN",
        "school": "MOE",
        "providergroup": "GRP",
    }
    return lut.get(key)

VIDEO_EXTS = {".mp4", ".webm", ".mov"}
PDF_EXT = ".pdf"



def _discover_items_for_role(role_code: str, user_admin: int):
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
    if not role_dir.exists():
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

        # Admin only flag based on stem prefix
        is_admin_item = stem.upper().startswith("ADM_")

        # Hide admin items for non admin users
        if is_admin_item and not user_admin:
            continue

        # Build clean title (strip ADM_ then replace underscores)
        if is_admin_item:
            clean_stem = stem[4:]  # drop "ADM_"
        else:
            clean_stem = stem

        title = clean_stem.replace("_", " ").strip()

        video_url = (
            url_for(
                "static",
                filename=str(video_path.relative_to(static_root)).replace("\\", "/"),
            )
            if video_path
            else None
        )
        pdf_url = (
            url_for(
                "static",
                filename=str(pdf_path.relative_to(static_root)).replace("\\", "/"),
            )
            if pdf_path
            else None
        )

        items.append(
            {
                "title": title,
                "video_url": video_url,
                "pdf_url": pdf_url,
                "admin_only": bool(is_admin_item),
            }
        )

    items.sort(key=lambda x: x["title"].lower())
    return items

# ---------- Routes ----------
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
            return redirect(url_for("instructions_bp.instructions_for_label", label=DEFAULT_ADMIN_LABEL))

        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    except Exception as e:
        # Log to DB and server logs; never crash the logger
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"instructions_me: {e}"
        )
        current_app.logger.exception("Unhandled error in instructions_me")
        abort(500)


@instructions_bp.route("/instructions/<label>")
@login_required
def instructions_for_label(label):
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

        # Access control: non-admins can only view their own role
        user_role = (session.get("user_role") or "").upper()
        if user_role != ADMIN_CODE and user_role != role_code:
            return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

        items = _discover_items_for_role(role_code, session.get("user_admin"))

        # Display label (optionally add a space for Provider Group)
        display_label = ROLE_TO_LABEL[role_code]
        if display_label == "ProviderGroup":
            display_label = "Provider Group"
            role_code = "PRO"

        return render_template(
            "instructions.html",   # your template should extend header.html
            role_code=role_code,   # e.g. "PRO"
            role_label=display_label,  # e.g. "Provider"
            items=items,
            user_role=user_role,
        )

    except Exception as e:
        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=None,
            link=request.url,
            message=f"instructions_for_label('{label}'): {e}"
        )
        current_app.logger.exception("Unhandled error in instructions_for_label(%s)", label)
        abort(500)


# Optional: keep old /instructions/code/PRO working (back-compat)
@instructions_bp.route("/instructions/code/<role_code>")
@login_required
def instructions_for_code(role_code):
    try:
        role = (role_code or "").upper()
        if role not in ALLOWED_ROLES:
            abort(404)

        user_role = (session.get("user_role") or "").upper()
        if user_role != ADMIN_CODE and user_role != role:
            return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

        items = _discover_items_for_role(role)
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
            message=f"instructions_for_code('{role_code}'): {e}"
        )
        current_app.logger.exception("Unhandled error in instructions_for_code(%s)", role_code)
        abort(500)
        

def load_faq_rows():
    # This always points to the correct static folder
    static_dir = Path(current_app.static_folder)

    xlsx_path = static_dir / "WSFL_FAQs_REVISED.xlsx"

    if not xlsx_path.exists():
        raise FileNotFoundError(f"FAQ Excel file not found at: {xlsx_path}")

    df = pd.read_excel(xlsx_path)

    required_cols = ["Question Type", "Question", "Answer", "Tags"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"FAQ file is missing required columns: {missing}")

    # Drop completely empty rows just in case
    df = df.dropna(how="all")

    # Convert to list of dicts
    records = df.to_dict(orient="records")

    # Group by Question Type for nice headings
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
    except Exception as e:
        current_app.logger.exception("\n=== ERROR IN /FAQ ROUTE ===")
        traceback.print_exc()
        # Re-raise so your global error handler / debug page can still run
        raise
