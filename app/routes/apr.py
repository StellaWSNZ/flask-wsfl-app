# app/routes/apr.py
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify
from datetime import datetime
import json
import pandas as pd
from sqlalchemy import text

from app.utils.database import get_db_engine

apr_bp = Blueprint("apr", __name__)


# -----------------------------
# Small helpers
# -----------------------------
def to_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def to_date(v):
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except Exception:
        return None


def as_int_list(vals):
    out = []
    for v in vals:
        try:
            out.append(int(v))
        except Exception:
            pass
    return out


def safe_json_load(v, default):
    """
    Loads JSON from SQL string columns safely.
    """
    if v is None:
        return default
    s = str(v).strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def normalize_email_list(x):
    """
    Accept:
      - ["a@x.com", "b@y.com"]
      - [{"email":"a@x.com"}]
      - [{"*":"a@x.com"}]  (old accidental SQL shape)
    Return: ["a@x.com", ...] lowercased, trimmed, unique-preserving order
    """
    out = []
    if not x:
        return out

    if isinstance(x, list):
        for item in x:
            email = None
            if isinstance(item, str):
                email = item
            elif isinstance(item, dict):
                email = item.get("email") or item.get("*")
            if email:
                e = str(email).strip().lower()
                if e and e not in out:
                    out.append(e)
    elif isinstance(x, str):
        e = x.strip().lower()
        if e:
            out.append(e)

    return out


# -----------------------------
# GET: APR page
# -----------------------------
@apr_bp.route("/ApprovedProviders", methods=["GET"])
def apr_page():
    engine = get_db_engine()

    with engine.begin() as conn:
        # Must return:
        # ContactJSON, SelectedContactsJSON (preferred) OR ContactSelectedJSON (legacy)
        df_approved = pd.read_sql("EXEC dbo.APR_GetEntityApprovalSummary", conn)
        df_dropdowns = pd.read_sql("EXEC dbo.APR_AllDropdowns", conn)

    # -----------------------------
    # Dropdown lists
    # -----------------------------
    approved_statuses = (
        df_dropdowns[df_dropdowns["Type"] == "Approved Status"][["ID", "Description"]]
        .copy()
        .to_dict("records")
    )
    database_statuses = (
        df_dropdowns[df_dropdowns["Type"] == "Database Training Status"][["ID", "Description"]]
        .copy()
        .to_dict("records")
    )
    external_statuses = (
        df_dropdowns[df_dropdowns["Type"] == "External Review Status"][["ID", "Description"]]
        .copy()
        .to_dict("records")
    )
    lesson_statuses = (
        df_dropdowns[df_dropdowns["Type"] == "Lesson Plan Status"][["ID", "Description"]]
        .copy()
        .to_dict("records")
    )

    # Self review filter options
    self_statuses = [
        {"Value": "complete", "Label": "Complete"},
        {"Value": "not complete", "Label": "Not complete"},
        {"Value": "no staff", "Label": "No staff"},
    ]

    # -----------------------------
    # Read filters from querystring
    # -----------------------------
    picked_approved = request.args.getlist("approved[]")
    picked_lesson = request.args.getlist("lesson[]")
    picked_external = request.args.getlist("external[]")
    picked_db = request.args.getlist("db[]")
    picked_self = request.args.getlist("self[]")

    # -----------------------------
    # Ensure numeric columns are numeric (so isin works)
    # -----------------------------
    for col in ["ApprovedStatusID", "LessonPlanStatusID", "ExternalReviewStatusID", "DatabaseTrainingStatusID"]:
        if col in df_approved.columns:
            df_approved[col] = pd.to_numeric(df_approved[col], errors="coerce").fillna(0).astype(int)

    # -----------------------------
    # Apply filters (AND logic)
    # -----------------------------
    if picked_approved:
        ids = as_int_list(picked_approved)
        if ids:
            df_approved = df_approved[df_approved["ApprovedStatusID"].isin(ids)]

    if picked_lesson:
        ids = as_int_list(picked_lesson)
        if ids:
            df_approved = df_approved[df_approved["LessonPlanStatusID"].isin(ids)]

    if picked_external:
        ids = as_int_list(picked_external)
        if ids:
            df_approved = df_approved[df_approved["ExternalReviewStatusID"].isin(ids)]

    if picked_db:
        ids = as_int_list(picked_db)
        if ids:
            df_approved = df_approved[df_approved["DatabaseTrainingStatusID"].isin(ids)]

    if picked_self:
        if "SelfReviewStatus" not in df_approved.columns:
            df_approved["SelfReviewStatus"] = ""

        df_approved["SelfReviewStatus_norm"] = (
            df_approved["SelfReviewStatus"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        picked_self_norm = [str(v).strip().lower() for v in picked_self]
        df_approved = df_approved[df_approved["SelfReviewStatus_norm"].isin(picked_self_norm)]

    # -----------------------------
    # Convert to dicts for Jinja + attach Contacts + SelectedEmails
    # -----------------------------
    df_approved_records = df_approved.to_dict("records")

    for row in df_approved_records:
        # Contacts list for dropdown: [{"email":"...","name":"First Last"}, ...]
        row["Contacts"] = safe_json_load(row.get("ContactJSON"), default=[])
        if not isinstance(row["Contacts"], list):
            row["Contacts"] = []

        # Selected contacts list for auto-select:
        # Prefer new column name SelectedContactsJSON, but support legacy ContactSelectedJSON
        selected_raw = safe_json_load(
            row.get("SelectedContactsJSON") or row.get("ContactSelectedJSON"),
            default=[]
        )
        row["SelectedContactEmails"] = normalize_email_list(selected_raw)

        # If you want "default" auto-selected when no stored selection exists:
        #if not row["SelectedContactEmails"]:
        #    default_email = (row.get("ContactDefaultEmail") or "").strip().lower()
        #    if default_email:
        #        row["SelectedContactEmails"] = [default_email]

    # IDs for “set date to today” behaviour
    APPROVED_SET_TODAY_ID = 1
    LESSON_SET_TODAY_ID = 2
    EXTERNAL_SET_TODAY_ID = 3
    DATABASE_SET_TODAY_ID = 2
    return render_template(
        "apr.html",
        df_approved=df_approved_records,
        approved_statuses=approved_statuses,
        lesson_statuses=lesson_statuses,
        external_statuses=external_statuses,
        database_statuses=database_statuses,
        self_statuses=self_statuses,
        APPROVED_SET_TODAY_ID=APPROVED_SET_TODAY_ID,
        LESSON_SET_TODAY_ID=LESSON_SET_TODAY_ID,
        EXTERNAL_SET_TODAY_ID=EXTERNAL_SET_TODAY_ID,
        DATABASE_SET_TODAY_ID=DATABASE_SET_TODAY_ID,
    )


# -----------------------------
# POST: Update entity (statuses + note + contacts)
# -----------------------------
# app/routes/apr.py
@apr_bp.route("/apr/update_entity", methods=["POST"])
def apr_update_entity():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify(ok=False, error="No data received"), 400

    entity_id = to_int(data.get("EntityID"))
    entity_type = (data.get("Code") or "").upper().strip()

    if entity_id <= 0 or entity_type not in ("PRO", "GRP"):
        return jsonify(ok=False, error="Invalid Entity"), 400

    contact_emails = data.get("ContactEmails") or []
    if not isinstance(contact_emails, list):
        contact_emails = []

    cleaned_emails = []
    for e in contact_emails:
        if not e:
            continue
        s = str(e).strip().lower()
        if not s or s == "__none__":
            continue
        if s not in cleaned_emails:
            cleaned_emails.append(s)

    params = {
        "EntityType": entity_type,
        "EntityID": entity_id,
        "ApprovedStatusID": to_int(data.get("ApprovedStatusID")),
        "ApprovedStatusDate": to_date(data.get("ApprovedStatusDate")),
        "LessonPlanStatusID": to_int(data.get("LessonPlanStatusID")),
        "LessonPlanStatusDate": to_date(data.get("LessonPlanStatusDate")),
        "ExternalReviewStatusID": to_int(data.get("ExternalReviewStatusID")),
        "ExternalReviewStatusDate": to_date(data.get("ExternalReviewStatusDate")),
        "DatabaseTrainingStatusID": to_int(data.get("DatabaseTrainingStatusID")),
        "DatabaseTrainingStatusDate": to_date(data.get("DatabaseTrainingStatusDate")),
        "Note": (data.get("Note") or "").strip(),
        "ContactEmailsJSON": json.dumps(cleaned_emails),
    }

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            # 1) write changes
            conn.execute(
                text(
                    """
                    EXEC dbo.APR_UpsertEntityApprovalEdits
                        @EntityType = :EntityType,
                        @EntityID = :EntityID,
                        @ApprovedStatusID = :ApprovedStatusID,
                        @ApprovedStatusDate = :ApprovedStatusDate,
                        @LessonPlanStatusID = :LessonPlanStatusID,
                        @LessonPlanStatusDate = :LessonPlanStatusDate,
                        @ExternalReviewStatusID = :ExternalReviewStatusID,
                        @ExternalReviewStatusDate = :ExternalReviewStatusDate,
                        @DatabaseTrainingStatusID = :DatabaseTrainingStatusID,
                        @DatabaseTrainingStatusDate = :DatabaseTrainingStatusDate,
                        @Note = :Note,
                        @ContactEmailsJSON = :ContactEmailsJSON
                    """
                ),
                params,
            )

            # 2) re-read single updated row from the summary proc
            df_one = pd.read_sql(
                """
                EXEC dbo.APR_GetEntityApprovalSummary
                """,
                conn,
            )
            # filter to just this entity
            df_one = df_one[(df_one["Code"] == entity_type) & (df_one["EntityID"] == entity_id)]
            if df_one.empty:
                # saved but couldn't re-fetch; still return ok
                return jsonify(ok=True, entity=None)

            row = df_one.iloc[0].to_dict()

        # return the refreshed row so JS can repaint badges
        return jsonify(ok=True, entity=row)

    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
    
@apr_bp.route("/apr/entity_dropdown", methods=["GET"])
def apr_entity_dropdown():
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            df = pd.read_sql("EXEC dbo.APR_EntityDropdown", conn)

        items = []
        for _, r in df.iterrows():
            items.append({
                "Code": str(r.get("Code", "")).strip(),
                "ID": int(r.get("ID", 0)),
                "Description": str(r.get("Description", "")).strip(),
            })

        return jsonify(ok=True, items=items)

    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
  
    
@apr_bp.route("/apr/add_entity", methods=["POST"])
def apr_add_entity():
    data = request.get_json(silent=True) or {}
    code = (data.get("Code") or "").strip().upper()
    entity_id = to_int(data.get("EntityID"))

    if code not in ("PRO", "GRP"):
        return jsonify(ok=False, error="Invalid Code"), 400
    if entity_id <= 0:
        return jsonify(ok=False, error="Invalid EntityID"), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            # ✅ call stored proc (permissions-friendly)
            conn.execute(
                text("EXEC dbo.APR_AddEntityToTracking @EntityType=:EntityType, @EntityID=:EntityID"),
                {"EntityType": code, "EntityID": entity_id},
            )
        return jsonify(ok=True)

    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500