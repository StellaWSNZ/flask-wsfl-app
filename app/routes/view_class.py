# app/routes/view_class.py
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import qrcode
import base64
from io import BytesIO
from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
from app.utils.database import get_db_engine
from sqlalchemy import text
import pandas as pd
import io, re, ast, urllib.parse, traceback

from app.routes.auth import login_required
import matplotlib
from sqlalchemy.exc import SQLAlchemyError

matplotlib.use('Agg')  # Prevent GUI backend errors in web servers
import matplotlib.pyplot as plt
import io, base64
from datetime import datetime
import sys
from app.utils.fundernationalplot import create_competency_report
from app.utils.competencyplot import load_competency_rates, make_figure
from app.utils.nationalplot import generate_national_report
import traceback
from collections import defaultdict
from datetime import datetime, timedelta,timezone

class_bp = Blueprint("class_bp", __name__)

from datetime import datetime, timedelta
from dateutil.parser import isoparse
from collections import defaultdict


from datetime import datetime, timedelta, timezone
from collections import defaultdict
import traceback
import pandas as pd
from sqlalchemy import text
from dateutil.parser import isoparse

from datetime import datetime, timedelta, timezone
from collections import defaultdict
import traceback
import pandas as pd
from sqlalchemy import text
from dateutil.parser import isoparse

def _build_print_context(engine, class_id: int, term: int, year: int, filter_type: str, order_by: str):
    """
    Build the same context dict that print_class_view uses to render print_view.html.
    Reuses cache when possible; regenerates if needed.
    """
    cache_key = f"{class_id}_{term}_{year}_{filter_type}"
    class_cache = session.get("class_cache", {})
    cache = class_cache.get(cache_key)

    # If missing, rebuild like print_class_view
    if not cache or "student_competencies" not in cache:
        with engine.begin() as conn:
            result = conn.execute(
                text("""EXEC FlaskGetClassStudentAchievement 
                        @ClassID = :class_id, 
                        @Term = :term, 
                        @CalendarYear = :year, 
                        @Email = :email, 
                        @FilterType = :filter"""),
                {"class_id": class_id, "term": term, "year": year,
                 "email": session.get("user_email"), "filter": filter_type}
            )
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
            if df.empty:
                # Return a minimal context; caller can handle “no data”
                return {
                    "grouped": {"0–2": [], "3–4": [], "5–6": [], "7–8": []},
                    "columns_by_range": {"0–2": [], "3–4": [], "5–6": [], "7–8": []},
                    "class_name": "(Unknown)", "teacher_name": "(Unknown)",
                    "filter_type": filter_type, "now": datetime.now,
                    "qr_data_uri": generate_qr_code_png(url_for("auth_bp.login", _external=True))
                }

            comp_df = (
                df[["CompetencyLabel", "CompetencyID", "YearGroupID"]]
                .drop_duplicates()
                .rename(columns={"CompetencyLabel": "label"})
            )
            comp_df["col_order"] = comp_df["YearGroupID"].astype(str).str.zfill(2) + "-" + comp_df["CompetencyID"].astype(str).str.zfill(4)
            comp_df = comp_df.sort_values("col_order")

            meta_cols = [
                "NSN", "FirstName", "LastName", "PreferredName",
                "DateOfBirth", "Ethnicity", "YearLevelID"
            ]
            df_combined = df.pivot_table(
                index=meta_cols,
                columns="label",
                values="CompetencyStatus",
                aggfunc="first"
            ).fillna(0).astype(int).replace({1: "✓", 0: ""}).reset_index()

            expiry_time = datetime.now(timezone.utc) + timedelta(minutes=15)
            class_cache[cache_key] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "expires": expiry_time.isoformat(),
                "students": df_combined.to_dict(),
                "competencies": comp_df.to_dict(orient="records"),
                "filter": filter_type,
                "student_competencies": df_combined.to_dict(orient="records"),
            }
            session["class_cache"] = class_cache

    else:
        df_combined = pd.DataFrame(cache["student_competencies"]).replace({1: "✓", 0: ""})
        comp_df = pd.DataFrame(cache["competencies"])

    # Column groups for the template
    labels = comp_df["label"].tolist() if not comp_df.empty else []
    def _labels_for(yr): return [l for l in labels if f"({yr})" in l]
    columns_by_range = {
        "0–2": _labels_for("0-2"),
        "3–4": _labels_for("3-4"),
        "5–6": _labels_for("5-6"),
        "7–8": _labels_for("7-8"),
    }

    # Grouped rows (template expects same rows per group; columns are filtered per range)
    grouped = {"0–2": [], "3–4": [], "5–6": [], "7–8": []}
    for row in df_combined.to_dict(orient="records"):
        for k in grouped.keys():
            grouped[k].append(row)

    # Class/teacher names
    with engine.connect() as conn:
        class_info = conn.execute(
            text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :class_id"),
            {"Request": "ClassInfoByID", "class_id": class_id}
        ).fetchone()
    class_name   = class_info.ClassName if class_info else "Unknown Class"
    teacher_name = class_info.TeacherName if class_info else "Unknown Teacher"

    # QR for login-to-view
    target_path = url_for("class_bp.view_class", class_id=class_id, term=term, year=year)
    login_url   = url_for("auth_bp.login", next=target_path, _external=True)
    qr_data_uri = generate_qr_code_png(login_url)

    # Sort order for display (optional)
    key_col = "PreferredName" if order_by == "first" else "LastName"
    if grouped["0–2"] and key_col in grouped["0–2"][0]:
        for k in grouped.keys():
            grouped[k] = sorted(grouped[k], key=lambda r: (r.get(key_col) or "").lower())

    return {
        "grouped": grouped,
        "columns_by_range": columns_by_range,
        "class_name": class_name,
        "teacher_name": teacher_name,
        "filter_type": filter_type,
        "now": datetime.now,
        "qr_data_uri": qr_data_uri,
    }


@class_bp.route('/Class/<int:class_id>/<int:term>/<int:year>')
@login_required
def view_class(class_id, term, year):
    try:
        # ---------- Query params ----------
        filter_type = request.args.get("filter", "all")
        order_by    = request.args.get("order_by", "last")

        # ---------- Cache lookup ----------
        cache_key   = f"{class_id}_{term}_{year}_{filter_type}"
        class_cache = session.get("class_cache", {})
        cached      = class_cache.get(cache_key)

        # ⏳ Cache expiry check
        if cached:
            expires_str = cached.get("expires")
            try:
                if expires_str and datetime.now(timezone.utc) > isoparse(expires_str):
                    print(f"🕒 Cache expired for {cache_key}")
                    class_cache.pop(cache_key, None)
                    cached = None
                    session["class_cache"] = class_cache
            except Exception as e:
                print("⚠️ Failed to parse cache expiry:", e)
                class_cache.pop(cache_key, None)
                cached = None
                session["class_cache"] = class_cache

        # ✅ Serve from cache (if valid)
        if cached and "student_competencies" in cached:
            try:
                print("✅ Using cached student_competencies")
                df_combined = pd.DataFrame(cached["student_competencies"])

                key_col = "PreferredName" if order_by == "first" else "LastName"
                if key_col in df_combined.columns:
                    df_combined = df_combined.sort_values(
                        by=key_col,
                        key=lambda col: col.astype(str).str.lower().fillna('')
                    )

                comp_df = pd.DataFrame(cached.get("competencies", []))
                competency_id_map = {}
                if not comp_df.empty and {"label","CompetencyID"} <= set(comp_df.columns):
                    competency_id_map = comp_df.set_index("label")["CompetencyID"].to_dict()

                return render_template(
                    "student_achievement.html",
                    students=df_combined.to_dict(orient="records"),
                    columns=[c for c in df_combined.columns if c not in ["DateOfBirth", "Ethnicity", "FirstName", "NSN"]],
                    competency_id_map=competency_id_map,
                    scenarios=cached.get("scenarios", []),
                    class_id=class_id,
                    class_name=cached.get("class_name", "(Unknown)"),
                    teacher_name=cached.get("teacher_name", "(Unknown)"),
                    school_name=cached.get("school_name", "(Unknown)"),
                    class_title=f"Class Name: {cached.get('class_name', '')} | Teacher Name: {cached.get('teacher_name', '')} | School Name: {cached.get('school_name', '')}",
                    edit=session.get("user_admin"),
                    autofill_map=cached.get("autofill_map", {}),
                    term=term,
                    year=year,
                    order_by=order_by,
                    filter_type=filter_type
                )
            except Exception:
                print("⚠️ Error while rendering from cache:")
                traceback.print_exc()

        # ❌ No valid cache → fetch from DB
        engine = get_db_engine()
        with engine.begin() as conn:
            # Scenarios
            scenario_result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :request"),
                {"request": "Scenario"}
            )
            scenarios = [dict(row._mapping) for row in scenario_result]

            # Main data
            print(
                text("""
                    EXEC FlaskGetClassStudentAchievement 
                        @ClassID = :class_id, 
                        @Term = :term, 
                        @CalendarYear = :year, 
                        @Email = :email, 
                        @FilterType = :filter
                """),
                {
                    "class_id": class_id,
                    "term": term,
                    "year": year,
                    "email": session.get("user_email"),
                    "filter": filter_type
                }
            )
            result = conn.execute(
                text("""
                    EXEC FlaskGetClassStudentAchievement 
                        @ClassID = :class_id, 
                        @Term = :term, 
                        @CalendarYear = :year, 
                        @Email = :email, 
                        @FilterType = :filter
                """),
                {
                    "class_id": class_id,
                    "term": term,
                    "year": year,
                    "email": session.get("user_email"),
                    "filter": filter_type
                }
            )
            rows = result.fetchall()
            if not rows:
                print("ℹ️ No rows returned for this class/term/year/filter.")
                return render_template(
                    "student_achievement.html",
                    students=[],
                    columns=[],
                    competency_id_map={},
                    scenarios=scenarios,
                    class_id=class_id,
                    class_name="(Unknown)",
                    teacher_name="(Unknown)",
                    school_name="(Unknown)",
                    class_title="No data for this selection",
                    edit=session.get("user_admin"),
                    autofill_map={},
                    term=term,
                    year=year,
                    order_by=order_by,
                    filter_type=filter_type
                )

            df = pd.DataFrame(rows, columns=result.keys())

            # Build comp_df for ordering/map BEFORE dropping cols
            need_cols = ["CompetencyLabel", "CompetencyID", "YearGroupID"]
            have_cols = [c for c in need_cols if c in df.columns]
            comp_df = (
                df[have_cols]
                .drop_duplicates()
                .rename(columns={"CompetencyLabel": "label"})
            ) if have_cols else pd.DataFrame(columns=["label","CompetencyID","YearGroupID"])

            # Normalize labels to avoid invisible mismatches
            if "label" in comp_df.columns:
                comp_df["label"] = comp_df["label"].astype(str).str.strip()

            # Titles (guard if columns missing)
            class_name   = df["ClassName"].dropna().unique()[0]   if "ClassName"   in df.columns and df["ClassName"].notna().any()   else "(Unknown)"
            teacher_name = df["TeacherName"].dropna().unique()[0] if "TeacherName" in df.columns and df["TeacherName"].notna().any() else "(Unknown)"
            school_name  = df["SchoolName"].dropna().unique()[0]  if "SchoolName"  in df.columns and df["SchoolName"].notna().any()  else "(Unknown)"
            title_string = f"Class Name: {class_name} | Teacher Name: {teacher_name} | School Name: {school_name}"

            # Drop meta columns we don't want duplicated post-pivot
            drop_cols = [c for c in ["ClassName", "TeacherName", "SchoolName", "CompetencyID", "YearGroupID"] if c in df.columns]
            df = df.drop(columns=drop_cols)

            # Pivot
            meta_cols = [
                "NSN", "FirstName", "LastName", "PreferredName",
                "Ethnicity", "YearLevelID", "Scenario1", "Scenario2"
            ]
            existing_meta = [c for c in meta_cols if c in df.columns]
            pivot_df = df.pivot_table(
                index=existing_meta,
                columns="CompetencyLabel",
                values="CompetencyStatus",
                aggfunc="first"
            ).reset_index()

            # Normalize pivot column labels too
            pivot_df.columns = [str(c).strip() for c in pivot_df.columns]

            # Desired competency order
            if not comp_df.empty:
                comp_df_sorted = comp_df.sort_values(["YearGroupID", "CompetencyID"])
            else:
                comp_df_sorted = pd.DataFrame(columns=["label","CompetencyID","YearGroupID"])

            #print("🧭 comp_df_sorted (first 10):\n", comp_df_sorted.head(10))
            desired_competencies = comp_df_sorted["label"].tolist()

            # Sort students by requested key if present
            key_col = "PreferredName" if order_by == "first" else "LastName"
            if key_col in pivot_df.columns:
                pivot_df = pivot_df.sort_values(
                    by=key_col,
                    key=lambda col: col.astype(str).str.lower().fillna('')
                )

            # Rename scenario columns (only if present)
            rename_map = {
                "Scenario1": "Scenario One - Selected <br>(7-8)",
                "Scenario2": "Scenario Two - Selected <br>(7-8)"
            }
            rename_applied = {k: v for k, v in rename_map.items() if k in pivot_df.columns}
            if rename_applied:
                pivot_df = pivot_df.rename(columns=rename_applied)

            # Fixed & scenario columns
            existing_cols = set(pivot_df.columns)
            fixed_cols_all = ["NSN", "LastName", "PreferredName", "YearLevelID"]
            fixed_cols_present = [c for c in fixed_cols_all if c in existing_cols]

            scenario_cols_all = [
                "Scenario One - Selected <br>(7-8)",
                "Scenario One - Completed <br>(7-8)",
                "Scenario Two - Selected <br>(7-8)",
                "Scenario Two - Completed <br>(7-8)"
            ]
            existing_scenario_cols = [c for c in scenario_cols_all if c in existing_cols]
            scenario_set = set(existing_scenario_cols)

            # ===== Force-include all competencies (even if no rows) =====
            # Exclude any labels that equal scenario headers
            full_comp_cols = [lbl for lbl in desired_competencies if lbl not in scenario_set]

            forced_added = []
            for lbl in full_comp_cols:
                if lbl not in pivot_df.columns:
                    pivot_df[lbl] = pd.NA
                    forced_added.append(lbl)
            if forced_added:
                print("ℹ️ Forced in empty competency columns (no rows under current filter):", forced_added)

            # Final column order (only columns that exist + forced)
            ordered_cols = fixed_cols_present + full_comp_cols + existing_scenario_cols
            ordered_cols = [c for c in ordered_cols if c in pivot_df.columns]  # safety
            pivot_df = pivot_df[ordered_cols]

            # Build competency_id_map for template
            competency_id_map = {}
            if not comp_df_sorted.empty and {"label","CompetencyID"} <= set(comp_df_sorted.columns):
                competency_id_map = comp_df_sorted.set_index("label")["CompetencyID"].to_dict()

            # Autofill map
            auto_result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = :request"),
                {"request": "AutoMappedCompetencies"}
            )
            header_map = defaultdict(list)
            for row in auto_result:
                header_map[row.HeaderPre].append(row.HeaderPost)

            # Cache it
            expiry_time = datetime.now(timezone.utc) + timedelta(minutes=15)
            class_cache[cache_key] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "expires": expiry_time.isoformat(),
                "students": pivot_df.to_dict(),  # raw table
                "competencies": comp_df_sorted.to_dict(orient="records"),
                "filter": filter_type,
                "student_competencies": pivot_df.to_dict(orient="records"),  # for cached branch
                "class_name": class_name,
                "teacher_name": teacher_name,
                "school_name": school_name,
                "scenarios": scenarios,
                "autofill_map": dict(header_map)
            }
            session["class_cache"] = class_cache
            target = "Basic awareness of potential water-related hazards"
            cols_list = list(pivot_df.columns)
            #print("🧱 Ordered columns (first 20):", cols_list[:20])
            #print("🔎 Has 'Basic awareness...' column? ->", any(c.startswith(target) for c in cols_list))

            # Also log what the template will receive:
            render_cols = [c for c in pivot_df.columns if c not in ["DateOfBirth","Ethnicity","FirstName","NSN"]]
            #print("🧾 Columns passed to template (first 20):", render_cols[:20])
            #print("🔎 In render_cols? ->", any(c.startswith(target) for c in render_cols))
            # Render
            return render_template(
                "student_achievement.html",
                students=pivot_df.to_dict(orient="records"),
                columns=[c for c in pivot_df.columns if c not in ["DateOfBirth", "Ethnicity", "FirstName", "NSN"]],
                competency_id_map=competency_id_map,
                scenarios=scenarios,
                class_id=class_id,
                class_name=class_name,
                teacher_name=teacher_name,
                school_name=school_name,
                class_title=title_string,
                edit=session.get("user_admin"),
                autofill_map=header_map,
                term=term,
                year=year,
                order_by=order_by,
                filter_type=filter_type
            )

    except Exception as e:
        print("❌ An error occurred in view_class:")
        traceback.print_exc()
        return "An internal error occurred. Check logs for details.", 500



@class_bp.route("/update_class_info", methods=["POST"])
@login_required
def update_class_info():
    if session.get("user_admin") != 1:
        return jsonify(success=False, error="Unauthorized"), 403

    data = request.get_json()
    class_id = data.get("class_id")
    class_name = data.get("class_name", "").strip()
    teacher_name = data.get("teacher_name", "").strip()

    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                EXEC FlaskHelperFunctionsSpecific 
                    @Request = :request,
                    @ClassID = :class_id,
                    @ClassName = :class_name,
                    @TeacherName = :teacher_name
            """),
            {
                "request": "UpdateClassInfo",
                "class_id": class_id,
                "class_name": class_name,
                "teacher_name": teacher_name
            }
        )
    return jsonify(success=True)



@class_bp.route('/ProviderClasses', methods=['GET', 'POST'])
@login_required
def provider_classes():
    if session.get("user_role") != "PRO":
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))

    engine = get_db_engine()
    classes = []
    students = []
    suggestions = []
    schools = []
    selected_class_id = None

    user_role = session.get("user_role")
    user_id = session.get("user_id")
    
    with engine.connect() as conn:
        if request.method == "POST":
            term = request.form.get("term", "").strip()
            year = request.form.get("calendaryear", "").strip()
            moe_number = request.form.get("moe_number", "").strip()

            if term.isdigit() and year.isdigit():
                term = int(term)
                year = int(year)

                # Get school list
                result = conn.execute(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific 
                            @Request = :request,
                            @Term = :term,
                            @Year = :year,
                            @ProviderID = :provider_id
                    """),
                    {
                        "request": "SchoolsByProviderTermYear",
                        "term": term,
                        "year": year,
                        "provider_id": user_id
                    }
                )
                
                schools = [dict(row._mapping) for row in result]

                # Get classes
                if moe_number:
                    result = conn.execute(
                        text("""
                            EXEC FlaskHelperFunctionsSpecific 
                                @Request = :request,
                                @MOENumber = :moe,
                                @Term = :term,
                                @Year = :year
                        """),
                        {
                            "request": "ClassesBySchoolTermYear",
                            "moe": moe_number,
                            "term": term,
                            "year": year
                        }
                    )
                    classes = [row._mapping for row in result.fetchall()]


                    if not classes:
                        suggestion_result = conn.execute(
                             text("""
                                EXEC FlaskHelperFunctionsSpecific 
                                    @Request = :request, 
                                    @MOENumber = :moe
                            """),
                            {
                                "request": "DistinctTermsForSchool",
                                "moe": moe_number
                            }
                        )
                        suggestions = [row.Label for row in suggestion_result]

    return render_template(
        "funder_classes.html",
        schools=schools,
        classes=classes,
        students=students,
        suggestions=suggestions,
        selected_class_id=selected_class_id,
        TERM=session.get("nearest_term"),
        YEAR=session.get("nearest_year"),
    user_role=session.get("user_role") 
    )
    
@class_bp.route('/Classes', methods=['GET', 'POST'])
@login_required
def funder_classes():
    if session.get("user_role") not in ["FUN", "ADM", "GRP"]:
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))
    session.pop("class_cache", None) 
    engine = get_db_engine()
    classes, students, suggestions, schools = [], [], [], []
    selected_class_id = None

    user_role = session.get("user_role")
    user_id = session.get("user_id")
    group_entities = session.get("group_entities", {})
    provider_ids = [str(e["id"]) for e in group_entities.get("PRO", [])]
    funder_ids = [str(e["id"]) for e in group_entities.get("FUN", [])]

    default_term = session.get("nearest_term")
    default_year = session.get("nearest_year")

    # ========================
    # Always Load Schools List
    # ========================
    with engine.connect() as conn:
        if user_role == "GRP":
            print(group_entities)
            print(provider_ids)
            print(funder_ids)
            if provider_ids:
                csv_providers = ",".join(provider_ids)
                result = conn.execute(
                    text("EXEC FlaskSchoolsByGroupProviders :ProviderList, :Term, :Year"),
                    {"ProviderList": csv_providers, "Term": default_term, "Year": default_year}
                )
            elif funder_ids:
                csv_funders = ",".join(funder_ids)
                result = conn.execute(
                    text("EXEC FlaskSchoolsByGroupFunders :FunderList, :Term, :Year"),
                    {"FunderList": csv_funders, "Term": default_term, "Year": default_year}
                )
            else:
                result = []
        elif user_role == "FUN":
            result = conn.execute(
                text("""EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request,
                        @Term = :term,
                        @Year = :year,
                        @FunderID = :funder_id"""),
                {"request": "SchoolsByFunderTermYear", "term": default_term, "year": default_year, "funder_id": user_id}
            )
        else:  # ADM
            result = conn.execute(
                text("""EXEC FlaskHelperFunctionsSpecific 
                        @Request = :request, 
                        @Term = :term, 
                        @Year = :year"""),
                {"request": "SchoolsByTermYear", "term": default_term, "year": default_year}
            )
        schools = [dict(row._mapping) for row in result]

        # ======================================
        # Only Load Classes Table After Form POST
        # ======================================
        if request.method == "POST":
            term = request.form.get("term", "").strip()
            year = request.form.get("calendaryear", "").strip()
            moe_number = request.form.get("moe_number", "").strip()

            if term.isdigit() and year.isdigit() and moe_number:
                term, year = int(term), int(year)

                result = conn.execute(
                    text("""EXEC FlaskHelperFunctionsSpecific 
                            @Request = :request,
                            @MOENumber = :moe,
                            @Term = :term,
                            @Year = :year"""),
                    {"request": "ClassesBySchoolTermYear", "moe": moe_number, "term": term, "year": year}
                )
                classes = [row._mapping for row in result.fetchall()]

                if not classes:
                    suggestion_result = conn.execute(
                        text("""EXEC FlaskHelperFunctionsSpecific 
                                @Request = :request, 
                                @MOENumber = :moe"""),
                        {"request": "DistinctTermsForSchool", "moe": moe_number}
                    )
                    suggestions = [row.Label for row in suggestion_result]

    return render_template(
        "funder_classes.html",
        schools=schools,
        classes=classes,
        students=students,
        suggestions=suggestions,
        selected_class_id=selected_class_id,
        TERM=default_term,
        YEAR=default_year,
        user_role=user_role
    )
@class_bp.route('/update_competency', methods=['POST'])
@login_required
def update_competency():
    data = request.json
    nsn = data.get("nsn")
    header_name = data.get("header_name")
    status = data.get("status")
    class_id = data.get("class_id")
    term = data.get("term")
    year = data.get("year")
    debug = 0

    print("📥 Incoming update_competency call")
    print(f"➡️ NSN: {nsn}, Header: {header_name}, Status: {status}, Class ID: {class_id}, Term: {term}, Year: {year}")

    if None in (nsn, header_name, status, class_id, term, year):
        print("❌ Missing one or more required fields")
        return jsonify({"success": False, "message": "Missing data"}), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            print("🔄 Running stored procedure FlaskUpdateAchievement...")
            conn.execute(
                text("EXEC FlaskUpdateAchievement @NSN = :nsn, @Header = :header, @Value = :value, @Email = :email, @Debug = :debug"),
                {
                    "nsn": nsn,
                    "header": header_name,
                    "value": status,
                    "email": session.get("user_email"),
                    "debug": debug
                }
            )
            print("✅ Stored procedure executed")

        class_cache = session.get("class_cache", {})
        updated_keys = 0
        updated_students = 0

        for key, cache in class_cache.items():
            if key.startswith(f"{class_id}_{term}_{year}_"):
                students = cache.get("student_competencies", [])
                for student in students:
                    if str(student.get("NSN")) == str(nsn):
                        print(f"✏️ Updating cache for NSN {nsn}, header {header_name}")
                        student[header_name] = status
                        updated_students += 1
                updated_keys += 1

        session["class_cache"] = class_cache
        print(f"✅ Cache edited for {updated_keys} key(s), {updated_students} student(s)")

        return jsonify({"success": True})
    except Exception as e:
        print("❌ Exception occurred during update_competency:")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@class_bp.route("/update_scenario", methods=["POST"])
@login_required
def update_scenario():
    data = request.get_json()
    nsn = data.get("nsn")
    header = data.get("header")
    value = data.get("value")
    class_id = data.get("class_id")
    term = data.get("term")
    year = data.get("year")
    debug = 0

    print(f"📥 Incoming update_scenario call")
    print(f"➡️ NSN: {nsn}, Header: {header}, Value: {value}, Class ID: {class_id}, Term: {term}, Year: {year}")

    if None in (nsn, header, value, class_id, term, year):
        return jsonify(success=False, error="Missing parameters"), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            print("🔄 Running stored procedure FlaskUpdateAchievement...")
            conn.execute(
                text("EXEC FlaskUpdateAchievement @NSN = :nsn, @Header = :header, @Value = :value, @Email = :email, @Debug = :debug"),
                {
                    "nsn": nsn,
                    "header": header,
                    "value": value,
                    "email": session.get("user_email"),
                    "debug": debug
                }
            )
            print("✅ Stored procedure executed")

        # ✏️ Inline update of session cache
        class_cache = session.get("class_cache", {})
        prefix = f"{class_id}_{term}_{year}_"
        updates = 0

        for key in list(class_cache):
            if key.startswith(prefix):
                entry = class_cache[key]
                students = entry.get("student_competencies", [])
                for student in students:
                    if isinstance(student, dict) and str(student.get("NSN")) == str(nsn):
                        print(f"✏️ Updating scenario cache for NSN {nsn}, header {header} in key {key}")
                        student[header] = str(value)
                        updates += 1
                class_cache[key] = entry  # save updated version

        session["class_cache"] = class_cache
        print(f"✅ Scenario cache updated in {updates} cache keys")

        return jsonify(success=True)

    except Exception as e:
        print("❌ Scenario update failed:", e)
        import traceback
        traceback.print_exc()
        return jsonify(success=False, error=str(e)), 500


@class_bp.route('/Reporting', methods=["GET", "POST"])
@login_required
def reporting():
    global last_pdf_generated, last_pdf_filename, last_png_generated, last_png_filename

    engine = get_db_engine()
    role = session.get("user_role")
    user_id = session.get("user_id")

    funders = []
    competencies = []
    img_data = None
    report_type = None
    term = None
    year = None
    funder_name = None
    dropdown_string = None

    with engine.connect() as conn:
        if role == "ADM":
            result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "FunderDropdown"})
            funders = [row.Description for row in result]

        result = conn.execute(text("EXEC FlaskHelperFunctions :Request"), {"Request": "CompetencyDropdown"})
        competencies = [row.Competency for row in result]
    #print("Session dump:", dict(session), file=sys.stderr)

    if request.method == "POST":
        report_type = request.form.get("report_type")
        term = int(request.form.get("term"))
        year = int(request.form.get("year"))
        funder_name = request.form.get("funder") or session.get("desc")
        #print(request.form.get("funder"))
        #print(session.get("desc"))
        if report_type == "Funder":
            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC FlaskHelperFunction @Request =:Request, Text = :Description"),
                    {"Request":"FunderIDDescription","Description": funder_name}
                )
                row = result.fetchone()

            if not row:
                flash("Funder not found.", "danger")
                return redirect(url_for("class_bp.reporting"))

            funder_id = int(row.FunderID)
            fig = create_competency_report(term, year, funder_id, funder_name)

            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            img_data = base64.b64encode(png_buf.getvalue()).decode("utf-8")

            last_png_generated = io.BytesIO(png_buf.getvalue())

            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)
            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{funder_name}_{term}_{year}.pdf"

            plt.close(fig)

        elif report_type == "Competency":
            dropdown_string = request.form.get("competency")

            with engine.connect() as conn:
                result = conn.execute(
                    text("EXEC GetCompetencyIDsFromDropdown :DropdownValue"),
                    {"DropdownValue": dropdown_string}
                )
                row = result.fetchone()

            if not row:
                flash("Invalid competency selected.", "danger")
                return redirect(url_for("class_bp.reporting"))

            competency_id = row.CompetencyID
            year_group_id = row.YearGroupID

            df = load_competency_rates(engine, year, term, competency_id, year_group_id)
            if df.empty:
                flash("No data found.", "warning")
                return redirect(url_for("class_bp.reporting"))

            title = f"{df['CompetencyDesc'].iloc[0]} ({df['YearGroupDesc'].iloc[0]})"
            fig = make_figure(df, title)

            png_buf = io.BytesIO()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(png_buf, format="png")
            png_buf.seek(0)
            img_data = base64.b64encode(png_buf.read()).decode("utf-8")

            last_png_generated = io.BytesIO(png_buf.getvalue())
            last_png_filename = f"{report_type}_Report_{dropdown_string.replace(' ', '_')}_{term}_{year}.png"

            pdf_buf = io.BytesIO()
            fig.savefig(pdf_buf, format="pdf")
            pdf_buf.seek(0)
            last_pdf_generated = pdf_buf
            last_pdf_filename = f"{report_type}_Report_{dropdown_string.replace(' ', '_')}_{term}_{year}.pdf"

            plt.close(fig)

    return render_template(
        "reporting.html",
        funders=funders,
        competencies=competencies,
        selected_report_type=report_type,
        selected_term=term,
        selected_year=year,
        selected_funder=funder_name,
        selected_competency=dropdown_string,
        img_data=img_data,
        user_role=role
    )

@class_bp.route("/comingsoon")
@login_required
def comingsoon():
    return render_template("comingsoon.html")

@class_bp.route("/get_schools_by_group")
@login_required
def get_schools_by_group():
    user_role = session.get("user_role")
    if user_role != "GRP":
        return jsonify([])

    group_entities = session.get("group_entities", {})
    provider_ids = [str(e["id"]) for e in group_entities.get("PRO", [])]
    funder_ids = [str(e["id"]) for e in group_entities.get("FUN", [])]
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)

    engine = get_db_engine()
    with engine.connect() as conn:
        if provider_ids:
            csv_providers = ",".join(provider_ids)
            result = conn.execute(
                text("EXEC FlaskSchoolsByGroupProviders :ProviderList, :Term, :Year"),
                {"ProviderList": csv_providers, "Term": term, "Year": year}
            )
        elif funder_ids:
            csv_funders = ",".join(funder_ids)
            result = conn.execute(
                text("EXEC FlaskSchoolsByGroupFunders :FunderList, :Term, :Year"),
                {"FunderList": csv_funders, "Term": term, "Year": year}
            )
        else:
            return jsonify([])

        schools = [{"MOENumber": row.MOENumber, "School": row.School} for row in result]
        return jsonify(schools)

@class_bp.route("/get_schools_for_term_year")
@login_required
def get_schools_for_term_year():
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)
    user_role = session.get("user_role")
    funder_id = session.get("user_id")

    if not term or not year:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        if user_role == "ADM":
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :Request,
                        @Term = :Term,
                        @Year = :Year
                """),
                {
                    "Request": "DistinctSchoolsByTermYear",
                    "Term": term,
                    "Year": year
                }
            )
        else:
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :Request,
                        @Term = :Term,
                        @Year = :Year,
                        @FunderID = :FunderID
                """),
                {
                    "Request": "DistinctSchoolsByTermYear",
                    "Term": term,
                    "Year": year,
                    "FunderID": funder_id
                }
            )

        rows = result.fetchall()

        return jsonify([
            {"MOENumber": row.MOENumber, "School": row.SchoolName}
            for row in rows
        ])

@class_bp.route('/get_schools_by_provider')
@login_required
def get_schools_by_provider():
    if session.get("user_role") != "PRO":
        return jsonify([])

    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)
    provider_id = session.get("user_id")

    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                EXEC FlaskHelperFunctionsSpecific
                    @Request = 'SchoolsByProviderTermYear',
                    @Term = :term,
                    @Year = :year,
                    @ProviderID = :provider_id
            """), {
                "term": term,
                "year": year,
                "provider_id": provider_id
            }
        )
        schools = [dict(row._mapping) for row in result]
    return jsonify(schools)


@class_bp.route('/get_schools_by_funder')
@login_required
def get_schools_by_funder():
    user_role = session.get("user_role")
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)

    if not term or not year:
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        if user_role == "FUN":
            funder_id = session.get("user_id")
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific
                        @Request = 'SchoolsByFunderTermYear',
                        @Term = :term,
                        @Year = :year,
                        @FunderID = :funder_id
                """), {
                    "term": term,
                    "year": year,
                    "funder_id": funder_id
                }
            )
        else:
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific
                        @Request = 'SchoolsByTermYear',
                        @Term = :term,
                        @Year = :year
                """), {
                    "term": term,
                    "year": year
                }
            )
        schools = [dict(row._mapping) for row in result]
    return jsonify(schools)

@class_bp.route('/SchoolClasses', methods=['GET', 'POST'])
@login_required
def moe_classes():
    if session.get("user_role") != "MOE":
        flash("Unauthorized access", "danger")
        return redirect(url_for("home_bp.home"))

    engine = get_db_engine()
    classes = []
    students = []
    suggestions = []

    moe_number = session.get("user_id")

    if request.method == "POST":
        term = request.form.get("term")
        year = request.form.get("calendaryear")

        with engine.connect() as conn:
            # Attempt to find classes
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctionsSpecific 
                        @Request = :Request,
                        @MOENumber = :moe,
                        @Term = :term,
                        @Year = :year
                """),
                {"Request": "ClassesBySchoolTermYear", "moe": moe_number, "term": term, "year": year}
            )
            classes = [row._mapping for row in result.fetchall()]

            if not classes:
                result = conn.execute(
                    text("""
                        EXEC FlaskHelperFunctionsSpecific 
                            @Request = :Request,
                            @MOENumber = :moe
                    """),
                    {"Request": "DistinctTermsForSchool", "moe": moe_number}
                )
                suggestions = [f"{row.CalendarYear} Term {row.Term}" for row in result.fetchall()]

                flash("No classes found for your school in this term and year.", "warning")

    return render_template(
        "moe_classes.html",
        classes=classes,
        students=students,
        suggestions=suggestions,
        TERM=session.get("nearest_term"),
        YEAR=session.get("nearest_year")
    )
    
    
def generate_qr_code_png(data, box_size=2):
    qr = qrcode.QRCode(box_size=box_size, border=1)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"
@class_bp.route("/Class/print/<int:class_id>/<int:term>/<int:year>")
@login_required
def print_class_view(class_id, term, year):
    try:
        filter_type = request.args.get("filter") or session.get("last_filter_used", "all")
        order_by    = request.args.get("order_by", "last")

        engine = get_db_engine()
        ctx = _build_print_context(engine, class_id, term, year, filter_type, order_by)

        # If no data, you can redirect or render a minimal page:
        return render_template("print_view.html", **ctx)

    except Exception as e:
        print("❌ Unhandled error in print_class_view:", e)
        traceback.print_exc()
        return "Internal Server Error (print view)", 500

# =========================
# ⭐ Add to app/routes/class.py (where class_bp is defined)
# =========================
import io
import re
from datetime import datetime

import pandas as pd
from sqlalchemy import text
from flask import request, send_file, render_template, render_template_string, session, abort

from app.utils.database import get_db_engine

# ---- helpers ----
SAFE_FN = re.compile(r"[^-_.() a-zA-Z0-9]+")

def safe_filename(s: str) -> str:
    s = (s or "").strip() or "export"
    s = SAFE_FN.sub("_", s)
    return s[:140]
def excel_bytes_writer(df: pd.DataFrame, sheet_name: str = "Sheet1"):
    """
    Writes a compact, readable Excel:
    - Wrapped headers (supports \n in header text)
    - Narrow default widths (12), slightly wider for name columns
    - Centered numbers/booleans, wrapped text for others
    """
    bio = io.BytesIO()
    sheet = (sheet_name or "Sheet1")[:31]

    try:
        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet)

            wb = writer.book
            ws = writer.sheets[sheet]

            # Formats
            header_fmt = wb.add_format({
                "bold": True, "valign": "top", "text_wrap": True,
                "border": 1, "bg_color": "#F2F2F2"
            })
            text_fmt   = wb.add_format({"valign": "top", "text_wrap": True})
            num_fmt    = wb.add_format({"valign": "vcenter", "align": "center"})

            # Re-write headers with wrapping (supports \n inserted earlier)
            for j, col in enumerate(df.columns):
                ws.write(0, j, str(col), header_fmt)

            # Make header row a bit taller for wraps
            ws.set_row(0, 32)

            # Column width plan
            default_width = 12
            width_map = {
                "NSN": 8,
                "YearLevelID": 8,
                "LastName": 16,
                "Surname": 16,
                "FirstName": 14,
                "PreferredName": 14,
                "DateOfBirth": 11,
            }

            # Apply widths + sensible default cell formats
            for j, col in enumerate(df.columns):
                col_name = str(col)
                width = width_map.get(col_name, default_width)

                # Choose a default format for the column
                series = df[col]
                if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
                    col_fmt = num_fmt
                else:
                    col_fmt = text_fmt

                ws.set_column(j, j, width, col_fmt)

            # Freeze header
            ws.freeze_panes(1, 0)

        bio.seek(0)
        return bio

    except Exception:
        # Fallback (no styling) if xlsxwriter is missing
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet)
            # Optional: set simple widths in openpyxl
            try:
                from openpyxl.utils import get_column_letter
                ws = writer.sheets[sheet]
                for j, col in enumerate(df.columns, start=1):
                    col_name = str(col)
                    width = width_map.get(col_name, default_width)
                    ws.column_dimensions[get_column_letter(j)].width = width
            except Exception:
                pass
        bio.seek(0)
        return bio

def _get_class_meta(engine, class_id: int):
    """Fetch class/teacher/school names. Falls back if SELECT is denied."""
    # TRY a proc first (if you add one later, this will just start working)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("EXEC FlaskGetClassMeta @ClassID = :cid"),
                {"cid": class_id}
            ).mappings().first()
        if row:
            return {
                "ClassName":   row.get("ClassName")  or f"Class {class_id}",
                "TeacherName": row.get("TeacherName") or "",
                "SchoolName":  row.get("SchoolName") or "",
                "MOENumber":   row.get("MOENumber"),
            }
    except Exception:
        pass  # proc doesn't exist or not permitted—fall through to SELECT attempt

    
    except SQLAlchemyError:
        # SELECT permission denied or other error—fallback
        pass

    # FINAL FALLBACK: no metadata available
    return {
        "ClassName":   f"Class {class_id}",
        "TeacherName": "",
        "SchoolName":  "",
        "MOENumber":   None,
    }

def _load_class_list_df(engine, class_id: int, term: int, year: int) -> pd.DataFrame:
    """Replace this EXEC with your real exporter for class list."""
    print(session.get("user_role"))
    with engine.begin() as conn:
        df = pd.read_sql(
            text("EXEC FlaskExportClassList @ClassID=:cid, @Term=:t, @CalendarYear=:y, @Role=:r")
,
            conn, params={"cid": class_id, "t": term, "y": year, "r":session.get("user_role")}
        )
    # Optional: preferred ordering
    #lead = [c for c in ["NSN","LastName","FirstName","PreferredName","YearLevelID","DateOfBirth"] if c in df.columns]
    #rest = [c for c in df.columns if c not in lead]
    return  df

def _load_achievements_df(engine, class_id: int, term: int, year: int) -> pd.DataFrame:
    """Replace this EXEC with your real exporter for achievements table (one row per student)."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text("EXEC FlaskExportAchievements @ClassID=:cid, @Term=:t, @Year=:y"),
            conn, params={"cid": class_id, "t": term, "y": year}
        )
    # Bring identity columns to the front if present
    lead = [c for c in ["NSN","LastName","PreferredName","YearLevelID"] if c in df.columns]
    rest = [c for c in df.columns if c not in lead]
    return df[lead + rest] if lead else df

def _ensure_authorised_for_class(engine, class_id: int):
    """
    If you need to restrict access (e.g., PRO only their classes, MOE only their school),
    add checks here using session role/id.
    """
    role = session.get("user_role")
    # Example (commented): require login at least
    if role is None:
        abort(403)
    # Add stricter checks if needed:
    # - for PRO: verify provider owns the class
    # - for MOE: verify class MOENumber matches session user_id
    # meta = _get_class_meta(engine, class_id)
    # if role == "MOE" and meta["MOENumber"] != session.get("user_id"):
    #     abort(403)
import traceback
# ---- Routes used by your Export modal ----
@class_bp.route("/export_class_excel")
def export_class_excel():
    try:
        engine  = get_db_engine()
        class_id = int(request.args.get("class_id"))
        term     = int(request.args.get("term"))
        year     = int(request.args.get("year"))

        _ensure_authorised_for_class(engine, class_id)
         
        meta = _get_class_meta(engine, class_id)
         

        df = _load_class_list_df(engine, class_id, term, year)
         

        if df.empty:
            df = pd.DataFrame(columns=["No results"])

        bio = excel_bytes_writer(df, sheet_name="Class List")
        fname = safe_filename(f"{meta['SchoolName']} - {meta['ClassName']} - Class List (T{term} {year}).xlsx")
        return send_file(
            bio,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=fname
        )
    except Exception:
        print("❌ export_class_excel failed:\n" + traceback.format_exc())
        # Return something visible in the browser while you’re debugging
        return jsonify({"success": False, "error": "Export failed. See server logs for details."}), 500


from flask import request, send_file, jsonify
import pandas as pd
import io, ast, re, traceback
from sqlalchemy import text
@class_bp.route("/export_achievements_excel", methods=["GET", "POST"])
@login_required
def export_achievements_excel():
    try:
        engine = get_db_engine()

        # Prefer JSON body; fall back to querystring/form for ids only
        payload = request.get_json(silent=True) or {}
        class_id = int(payload.get("class_id") or (request.values.get("class_id") or 0))
        term     = int(payload.get("term")     or (request.values.get("term")     or 0))
        year     = int(payload.get("year")     or (request.values.get("year")     or 0))

        _ensure_authorised_for_class(engine, class_id)
        meta = _get_class_meta(engine, class_id)

        # ---------- Build dataframe ----------
        df = None

        # POST JSON: { rows: [...] }  or { data: [...] }
        if request.method == "POST" and request.is_json:
            rows = payload.get("rows") or payload.get("data") or []
            if rows:
                # Optional: lightweight guardrails
                if not isinstance(rows, list):
                    return jsonify({"success": False, "error": "rows must be a list"}), 400
                if len(rows) > 5000:
                    return jsonify({"success": False, "error": "Too many rows"}), 413
                df = pd.DataFrame(rows)

        # Disallow giant/legacy GET with &df=... for privacy + CF limits
        if request.method == "GET" and request.args.getlist("df"):
            return jsonify({
                "success": False,
                "error": "Large GET payloads are not supported. POST a JSON body with { rows: [...] } instead."
            }), 413

        # Fallback to DB exporter (works for both GET and POST when rows weren’t provided)
        if df is None:
            df = _load_achievements_df(engine, class_id, term, year)

        if df.empty:
            df = pd.DataFrame(columns=["No results"])

        # ---------- Clean & shape ----------
        # Remove NSN
        df.drop(columns=["NSN"], errors="ignore", inplace=True)

        # Clean headers: <br> → space
        def _clean_col(c: str) -> str:
            s = str(c)
            s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
            return s.strip()

        df.rename(columns={c: _clean_col(c) for c in df.columns}, inplace=True)

        # YearLevelID → YearLevel
        if "YearLevelID" in df.columns:
            df.rename(columns={"YearLevelID": "YearLevel"}, inplace=True)

        # --- PATCH: Force column order to match UI ---
        ui_order_raw = []
        if request.method == "POST" and request.is_json:
            ui_order_raw = (payload.get("column_order") or [])

        def _norm_header_for_match(s: str) -> str:
            s = str(s)
            s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
            return s.strip()

        df_norm_map = {str(c).strip(): c for c in df.columns}

        desired_ach_cols = []
        for ui_col in ui_order_raw:
            norm = _norm_header_for_match(ui_col)
            if norm in df_norm_map:
                desired_ach_cols.append(df_norm_map[norm])

        id_cols = [c for c in ["LastName", "PreferredName", "YearLevel"] if c in df.columns]
        remaining = [c for c in df.columns if c not in id_cols + desired_ach_cols]
        df = df[id_cols + desired_ach_cols + remaining]
        # --- END PATCH ---

        # Identity first (redundant now but safe)
        id_cols = [c for c in ["LastName", "PreferredName", "YearLevel"] if c in df.columns]
        rest_cols = [c for c in df.columns if c not in id_cols]
        if id_cols:
            df = df[id_cols + rest_cols]

        # 1 → "Y", 0/NaN → "" for binary columns (non-identity)
        def _is_binary(series: pd.Series) -> bool:
            uniq = set(series.dropna().astype(str).str.strip().unique())
            return uniq.issubset({"0", "1", "0.0", "1.0"})
        for col in rest_cols:
            s = df[col]
            if _is_binary(s):
                df[col] = (
                    s.replace({1: "Y", 1.0: "Y", "1": "Y", "1.0": "Y",
                               0: "", 0.0: "", "0": "", "0.0": ""})
                     .fillna("")
                )

        # ---------- Write Excel with 2-row header ----------
        bio = io.BytesIO()
        sheet = "Achievements"

        def split_header(col_name: str) -> tuple[str, str]:
            s = str(col_name).strip()
            m = re.match(r"^(.*?)\s*(?:\((.*?)\))?\s*$", s)
            base = (m.group(1) if m else s).strip()
            in_parens = (m.group(2) if m else "").strip()
            return base, in_parens

        DATA_START_COL = 3  # D; identity are A..C

        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, header=False, sheet_name=sheet, startrow=2)
            wb = writer.book
            ws = writer.sheets[sheet]

            last_col = max(0, len(df.columns) - 1)
            school = meta.get("SchoolName", "")
            klass  = meta.get("ClassName", "")
            teach  = meta.get("TeacherName", "")
            title_lines = [
                f"{school} — {klass}".strip(" —"),
                f"Teacher: {teach}" if teach else "",
                f"Term {term}, {year}",
            ]
            title_text = "\n".join([ln for ln in title_lines if ln])

            title_fmt = wb.add_format({
                "bold": True, "font_size": 12, "align": "left",
                "valign": "top", "text_wrap": True,
            })
            ws.merge_range(0, 0, 0, min(2, last_col), title_text, title_fmt)

            # Formats
            header_row1_rot = wb.add_format({
                "bold": True, "valign": "top", "align": "center",
                "text_wrap": True, "border": 1, "bg_color": "#F2F2F2",
                "rotation": 90
            })
            header_row2_h = wb.add_format({
                "bold": True, "valign": "top", "align": "center",
                "text_wrap": True, "border": 1, "bg_color": "#F2F2F2"
            })
            id_header_fmt = wb.add_format({
                "bold": True, "valign": "vcenter", "align": "left",
                "text_wrap": False, "border": 1, "bg_color": "#F2F2F2"
            })
            cell_text_fmt = wb.add_format({"valign": "bottom", "text_wrap": True})
            cell_center_fmt = wb.add_format({"valign": "vcenter", "align": "center"})

            # Header row heights
            ws.set_row(0, 120)
            ws.set_row(1, 17)
            ws.set_default_row(17)

            # A2:C2 identity headers
            for j, name in enumerate(["LastName", "PreferredName", "YearLevel"]):
                if j <= last_col:
                    ws.write(1, j, name, id_header_fmt)

            # D1.. base headers; D2.. subheaders
            for j in range(DATA_START_COL, last_col + 1):
                base, sub = split_header(df.columns[j])
                ws.write(0, j, base, header_row1_rot)
                ws.write(1, j, sub, header_row2_h)

            # Column widths
            width_map = {"LastName": 16, "PreferredName": 14, "YearLevel": 10}
            narrow_width = 6
            default_identity_width = 12
            for j, col in enumerate(df.columns):
                series = df[col]
                col_fmt = (cell_center_fmt
                           if (pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series))
                           else cell_text_fmt)
                width = width_map.get(col, default_identity_width if col in ["LastName","PreferredName","YearLevel"] else narrow_width)
                ws.set_column(j, j, width, col_fmt)

            # Freeze panes
            ws.freeze_panes(2, DATA_START_COL)

        bio.seek(0)
        fname = _safe_filename(
            f"{meta.get('SchoolName','')} - {meta.get('ClassName','')} - Achievements (T{term} {year}).xlsx"
        )
        return send_file(
            bio,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=fname
        )

    except Exception:
        print("❌ export_achievements_excel failed:\n" + traceback.format_exc())
        return jsonify({"success": False, "error": "Export failed. See server logs for details."}), 500


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]+', "-", str(name))
    name = re.sub(r"\s+", " ", name).strip()
    return name[:200]

# ---- PDF via Playwright (Chromium) ----
def _html_to_pdf_bytes_with_playwright(html: str, base_url: str) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None  # Playwright not installed

    # Ensure relative /static/... works by injecting a <base href="...">
    import re
    def inject_base(h: str, base: str) -> str:
        # insert right after <head> … keep simple & robust
        return re.sub(r"<head(\s*)>", f"<head\\1><base href=\"{base}\">", h, count=1, flags=re.I)

    html = inject_base(html, base_url.rstrip("/") + "/")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page()
            # Load the HTML directly; assets resolve thanks to <base href>
            page.set_content(html, wait_until="networkidle")
            pdf = page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "18mm", "right": "15mm", "bottom": "18mm", "left": "15mm"},
            )
            return pdf
        finally:
            browser.close()

def _render_print_html(engine, class_id: int, term: int, year: int, filter_type: str, order_by: str) -> str:
    # Reuse your existing context builder; you already had this idea earlier
    # If you don't have a _build_print_context yet, we can synthesize a tiny wrapper
    ctx = _build_print_context(engine, class_id, term, year, filter_type, order_by)
    return render_template("print_view.html", **ctx)

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

from flask import request, jsonify, send_file, current_app as app


def _html_to_pdf_bytes_with_playwright(html: str, base_url: str | None = None) -> bytes | None:
    """
    Returns PDF bytes using Playwright/Chromium, or None if Playwright isn't available
    or PDF rendering fails for any reason.
    """
    if not sync_playwright:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context()
            page = context.new_page()
            # set_content supports base_url so relative assets resolve
            page.set_content(html, base_url=base_url, wait_until="load")
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "10mm", "right": "10mm", "bottom": "10mm", "left": "10mm"},
            )
            browser.close()
            return pdf_bytes
    except Exception:
        app.logger.exception("Playwright PDF generation failed")
        return None


@class_bp.route("/export_achievements_pdf", methods=["GET"])
@login_required
def export_achievements_pdf():
    try:
        engine    = get_db_engine()
        class_id  = int(request.args.get("class_id", 0))
        term      = int(request.args.get("term", 0))
        year      = int(request.args.get("year", 0))
        filter_by = request.args.get("filter", "all")
        order_by  = request.args.get("order_by", "last")

        if not class_id or not term or not year:
            return jsonify({"success": False, "error": "Missing class_id/term/year"}), 400

        _ensure_authorised_for_class(engine, class_id)
        meta = _get_class_meta(engine, class_id) or {}

        # 1) Render the same HTML you use for “Print”
        html = _render_print_html(engine, class_id, term, year, filter_by, order_by)
        if not isinstance(html, str):
            # in case your renderer returns a (template, ctx) or Response
            html = str(html)

        # 2) Try Playwright (Chromium) first
        pdf_bytes = _html_to_pdf_bytes_with_playwright(html, base_url=request.url_root)

        # 3) If Playwright isn't available, fall back to returning HTML
        if pdf_bytes is None:
            # You can change this to 503 if you want to signal “PDF not available”
            return html

        # 4) Download nicely named PDF
        school = meta.get("SchoolName", "")
        klass  = meta.get("ClassName", "")
        fname = _safe_filename(f"{school} - {klass} - Achievements (T{term} {year}) - Print.pdf")

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=fname,
        )

    except Exception:
        app.logger.exception("export_achievements_pdf failed")
        return jsonify({"success": False, "error": "Export failed. See server logs for details."}), 500