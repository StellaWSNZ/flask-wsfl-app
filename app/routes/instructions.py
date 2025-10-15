# app/routes/instructions.py
from pathlib import Path
from flask import (
    Blueprint,
    render_template,
    url_for,
    abort,
    current_app,
    session,
    redirect,
)
from app.routes.auth import login_required

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


def _discover_items_for_role(role_code: str):
    """
    Scan static/instructions/<ROLE_CODE>/ and pair files by prefix (stem).
    Returns: list[dict(title, video_url|None, pdf_url|None)]
    """
    static_root = Path(current_app.static_folder)  # e.g. .../static
    role_dir = static_root / "instructions" / role_code
    if not role_dir.exists():
        return []

    # group files by stem
    stems = {}
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

        # prettify title from stem
        title = stem.replace("_", " ").strip()

        video_url = (
            url_for("static", filename=str(video_path.relative_to(static_root)).replace("\\", "/"))
            if video_path else None
        )
        pdf_url = (
            url_for("static", filename=str(pdf_path.relative_to(static_root)).replace("\\", "/"))
            if pdf_path else None
        )

        if video_url or pdf_url:
            items.append(
                {
                    "title": title,
                    "video_url": video_url,
                    "pdf_url": pdf_url,
                }
            )

    # stable sort by title
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
    
    print("*")
    user_role = (session.get("user_role") or "").upper()

    if user_role in ALLOWED_ROLES:
        label = ROLE_TO_LABEL.get(user_role, user_role)
        return redirect(url_for("instructions_bp.instructions_for_label", label=label))

    if user_role == ADMIN_CODE:
        DEFAULT_ADMIN_LABEL = "Funder"  # or "Provider" / "School" / "ProviderGroup"
        return redirect(url_for("instructions_bp.instructions_for_label", label=DEFAULT_ADMIN_LABEL))

    abort(403)


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
    role_code = _label_to_role(label)
    if role_code is None or role_code not in ALLOWED_ROLES:
        abort(404)

    # Access control: non-admins can only view their own role
    user_role = (session.get("user_role") or "").upper()
    if user_role != ADMIN_CODE and user_role != role_code:
        abort(403)

    items = _discover_items_for_role(role_code)

    # Display label (optionally add a space for Provider Group)
    display_label = ROLE_TO_LABEL[role_code]
    if display_label == "ProviderGroup":
        display_label = "Provider Group"

    return render_template(
        "instructions.html",   # your template should extend header.html
        role_code=role_code,   # e.g. "PRO"
        role_label=display_label,  # e.g. "Provider"
        items=items,
        user_role=user_role,
    )


# Optional: keep old /instructions/code/PRO working (back-compat)
@instructions_bp.route("/instructions/code/<role_code>")
@login_required
def instructions_for_code(role_code):
    role = (role_code or "").upper()
    if role not in ALLOWED_ROLES:
        abort(404)

    user_role = (session.get("user_role") or "").upper()
    if user_role != ADMIN_CODE and user_role != role:
        abort(403)

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

