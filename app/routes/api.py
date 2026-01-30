from flask import Blueprint, request, session, jsonify, current_app
from app.routes.auth import login_required
from sqlalchemy import text

from app.routes.overview import get_funders_by_provider
from app.utils.database import get_db_engine, log_alert

api_bp = Blueprint("api_bp", __name__)

def get_terms():
    return list(range(1, 5))

def get_years():
    return list(range(2024, 2027))


@api_bp.route("/get_entities", methods=["GET"])
@login_required
def get_entities():
    debug = False

    try:
        # ----------------------------
        # Inputs from request/session
        # ----------------------------
        entity_type = request.args.get("entity_type")
        include_inactive = int(request.args.get("include_inactive", 0))
        user_id   = session.get("user_id")
        user_role = session.get("user_role")
        desc      = session.get("desc")
        if(user_role == "ADM" or "Example " in desc):
            include_inactive = 1
        if debug:
            current_app.logger.info("================ API:get_entities =================")
            current_app.logger.info("📥 Request args:")
            current_app.logger.info(f"   ➤ entity_type      = {entity_type}")
            current_app.logger.info(f"   ➤ include_inactive = {include_inactive}")
            current_app.logger.info("👤 Session:")
            current_app.logger.info(f"   ➤ user_id   = {user_id}")
            current_app.logger.info(f"   ➤ user_role = {user_role}")
            current_app.logger.info(f"   ➤ desc      = {desc}")

        # ----------------------------
        # Validation
        # ----------------------------
        if not entity_type:
            if debug:
                current_app.logger.error("❌ Validation failed: missing entity_type")
            return jsonify({"ok": False, "error": "Missing entity_type"}), 400

        if not user_role:
            if debug:
                current_app.logger.error("❌ Validation failed: invalid session")
            return jsonify({"ok": False, "error": "Invalid session"}), 401

        if debug:
            current_app.logger.info(f"✅ Validation passed → {entity_type} | role={user_role} | id={user_id}")

        # ----------------------------
        # Execute Stored Procedure
        # ----------------------------
        engine = get_db_engine()
        if entity_type == "Region":
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        EXEC dbo.FlaskHelperFunctions
                            @Request = 'AllRegions'
                    """)
                ).mappings().all()

                return jsonify({"ok": True, "entities": [dict(r) for r in rows]})
        if debug:
            current_app.logger.info("🔌 DB engine acquired")
        user_id = session.get("user_id")
        try:
            user_id = int(user_id) if user_id is not None else None
        except (TypeError, ValueError):
            # If it's something weird, just treat as None and let SP logic decide
            user_id = None
        with engine.begin() as conn:

            sql = text("""
                SET NOCOUNT ON;
                EXEC dbo.FlaskGetEntities
                    @EntityType      = :EntityType,
                    @Role            = :Role,
                    @ID              = :ID,
                    @IncludeInactive = :IncludeInactive;
            """)

            params = {
                "EntityType": entity_type,
                "Role": user_role,
                "ID": user_id,
                "IncludeInactive": include_inactive,
            }

            if debug:
                current_app.logger.info("🧾 SQL Params:")
                for k, v in params.items():
                    current_app.logger.info(f"   {k} = {v}")

            res = conn.execute(sql, params)
            rows = res.mappings().all()

        if debug:
            current_app.logger.info(f"📦 Rows returned: {len(rows)}")
            if rows:
                current_app.logger.info("🧪 First row sample:", dict(rows[0]))

        # ----------------------------
        # Format for frontend
        # ----------------------------
        entities = [
            {
                "id": int(r["ID"]),
                "description": str(r["Description"]),
            }
            for r in rows
        ]

        if debug:
            current_app.logger.info(f"✅ Entities formatted: {len(entities)} items")
            if entities:
                current_app.logger.info("🧪 First entity:", entities[0])
            current_app.logger.info("==================================================\n")

        return jsonify({
            "ok": True,
            "entity_type": entity_type,
            "role": user_role,
            "count": len(entities),
            "entities": entities
        })

    except Exception as e:
        current_app.logger.exception("🔥 EXCEPTION in /api/get_entities")

        log_alert(
            email=session.get("user_email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=request.url,
            message=f"/api/get_entities failed: {str(e)}"
        )

        return jsonify({
            "ok": False,
            "error": "Failed to load entities"
        }), 500


# ---- API: ethnicity dropdown for edit modal ----
@api_bp.route("/ethnicities")
@login_required
def ethnicities():
    engine = get_db_engine()
    with engine.begin() as conn:
        rows = conn.execute(text("EXEC FlaskHelperFunctions @Request='EthnicityDropdown'")).fetchall()
    return jsonify([{"id": r._mapping["EthnicityID"], "desc": r._mapping["Description"]} for r in rows])


@api_bp.route("/provider_funders")
@login_required
def provider_funders_api():
    if session.get("user_role") != "ADM":
        return jsonify({"funders": []}), 403

    provider_id = request.args.get("provider_id", "").strip()
    if not provider_id.isdigit():
        return jsonify({"funders": []})

    engine = get_db_engine()
    funders = get_funders_by_provider(engine, int(provider_id))  # returns [{id, Description}, ...]

    # Always return list of dicts with id/Description
    return jsonify({"funders": funders})