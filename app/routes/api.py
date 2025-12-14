from flask import Blueprint, request, session, jsonify
from app.routes.auth import login_required
from sqlalchemy import text

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

        if debug:
            print("\n================ API:get_entities =================")
            print("📥 Request args:")
            print("   entity_type      =", entity_type)
            print("   include_inactive =", include_inactive)
            print("👤 Session:")
            print("   user_id   =", user_id)
            print("   user_role =", user_role)
            print("   desc      =", desc)

        # ----------------------------
        # Validation
        # ----------------------------
        if not entity_type:
            if debug:
                print("❌ Validation failed: missing entity_type")
            return jsonify({"ok": False, "error": "Missing entity_type"}), 400

        if not user_role:
            if debug:
                print("❌ Validation failed: invalid session")
            return jsonify({"ok": False, "error": "Invalid session"}), 401

        if debug:
            print(f"✅ Validation passed → {entity_type} | role={user_role} | id={user_id}")

        # ----------------------------
        # Execute Stored Procedure
        # ----------------------------
        engine = get_db_engine()

        if debug:
            print("🔌 DB engine acquired")
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
                print("🧾 SQL Params:")
                for k, v in params.items():
                    print(f"   {k} = {v}")

            res = conn.execute(sql, params)
            rows = res.mappings().all()

        if debug:
            print(f"📦 Rows returned: {len(rows)}")
            if rows:
                print("🧪 First row sample:", dict(rows[0]))

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
            print(f"✅ Entities formatted: {len(entities)} items")
            if entities:
                print("🧪 First entity:", entities[0])
            print("==================================================\n")

        return jsonify({
            "ok": True,
            "entity_type": entity_type,
            "role": user_role,
            "count": len(entities),
            "entities": entities
        })

    except Exception as e:
        print("🔥 EXCEPTION in /api/get_entities:")
        print(str(e))

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
