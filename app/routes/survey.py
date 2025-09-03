## Survey.py
from datetime import timezone
from zoneinfo import ZoneInfo
from flask import Blueprint, jsonify, render_template, request, redirect, flash, session, url_for, current_app
from sqlalchemy import text
from app.utils.database import get_db_engine
from collections import namedtuple
from app.routes.auth import login_required
import traceback
from app.extensions import mail
from itsdangerous import URLSafeTimedSerializer,BadSignature, SignatureExpired
import re
survey_bp = Blueprint("survey_bp", __name__)

@survey_bp.route("/Form/<string:routename>")
def survey_by_routename(routename):
    engine = get_db_engine()
    Label = namedtuple("Label", ["pos", "text"])
    questions = []
    seen_ids = {}

    try:
        with engine.connect() as conn:
            # 1) Resolve survey id
            res = conn.execute(
                text("EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename"),
                {"routename": routename}
            )
            row = res.fetchone()
            res.close()

            if not row:
                flash(f"Survey '{routename}' not found.", "danger")
                return redirect("/Profile")

            survey_id = row.SurveyID

            # 2) Access control
            user_role = session.get("user_role")
            user_id = session.get("user_id")

            if survey_id == 3:
                # Teacher Assessment — funders only
                if user_role not in  ["FUN","ADM"]:
                    flash("This assessment is restricted to funders and WSNZ Administrators.", "warning")
                    return redirect("/MyForms")
                

                # IMPORTANT: do NOT fetch schools here; the template loads them via AJAX
                # from /get_entities?entity_type=School (scoped server-side).

            if survey_id == 4:
                # Admin-only survey
                if user_role != "ADM":
                    flash("This assessment is restricted to WSNZ Admins.", "warning")
                    return redirect("/MyForms")

            # 3) Load survey questions
            qrows = conn.execute(
                text("EXEC SVY_GetSurveyQuestions @SurveyID = :survey_id"),
                {"survey_id": survey_id}
            ).fetchall()

        # 4) Build question objects
        for qid, qtext, qcode, pos, label in qrows:
            if qid not in seen_ids:
                seen_ids[qid] = {"id": qid, "text": qtext, "type": qcode, "labels": []}
                questions.append(seen_ids[qid])
            if qcode == "LIK" and label is not None:
                seen_ids[qid]["labels"].append(Label(pos, label))

        # Ensure likert labels are ordered by pos, if provided
        for q in questions:
            if q["labels"]:
                q["labels"].sort(key=lambda L: (L.pos is None, L.pos))

        # 5) Render — no 'schools' passed; template for survey 3 fetches via AJAX
        ctx = {
            "questions": questions,
            "route_name": routename,
            "survey_id": survey_id,  # template may branch on this
        }
        return render_template(f"survey_form_{survey_id}.html", **ctx)

    except Exception:
        traceback.print_exc()
        return "Internal Server Error: Failed to load survey", 500

@survey_bp.route("/api/teachers")
def api_teachers():
    school_id = request.args.get("school_id", "").strip()
    if not school_id:
        return jsonify([])

    try:
        uid = int(school_id)
    except ValueError:
        # Bad id → empty list instead of 500
        return jsonify([])

    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("EXEC FlaskHelperFunctions @Request='SchoolStaffFunder', @Number=:uid"),
            {"uid": uid},
        ).mappings()

        # Convert RowMapping → plain dicts
        rows = [dict(r) for r in result]

    # Optional: enforce only the fields your frontend expects
    payload = [{"Name": r.get("Name"), "Email": r.get("Email")} for r in rows]
    return jsonify(payload)

@survey_bp.route("/api/surveys/<int:survey_id>/questions/<int:question_id>/options")
def api_dropdown_options(survey_id, question_id):
    with get_db_engine().connect() as conn:
        result = conn.execute(
            text("EXEC dbo.SVY_GetOptions @SurveyID=:sid, @QuestionID=:qid"),
            {"sid": survey_id, "qid": question_id}
        ).mappings()
        rows = [dict(r) for r in result]

    payload = [{"id": r["OptionID"], "value": r["OptionValue"], "label": r["Label"]} for r in rows]
    return jsonify(payload)

@survey_bp.route("/submit/<string:routename>", methods=["POST"])
def submit_survey(routename):
    try:
        email = session.get("user_email")
        if not email:
            return "Email required", 400

        form = request.form  # MultiDict

        # ---- Parse qN + qN_id pairs from the form into a dict
        # answers = { "3": {"value": "Lead Classroom Teacher", "id": "1"}, ... }
        answers = {}
        for key in form.keys():
            m = re.fullmatch(r"q(\d+)(?:_id)?", key)
            if not m:
                continue
            qid = m.group(1)
            answers.setdefault(qid, {"value": None, "id": None})
            if key.endswith("_id"):
                answers[qid]["id"] = form.get(key) or None
            else:
                answers[qid]["value"] = (form.get(key) or "").strip()

        engine = get_db_engine()
        with engine.begin() as conn:
            # ---- Resolve SurveyID from route
            if routename.startswith("guest/") and routename.split("/", 1)[1].isdigit():
                survey_id = int(routename.split("/", 1)[1])
            else:
                res = conn.execute(
                    text("EXEC SVY_GetSurveyIDByRouteName @RouteName = :r"),
                    {"r": routename},
                )
                row = res.fetchone()
                if not row:
                    return f"Survey '{routename}' not found", 400
                # support tuple-like or mappings
                survey_id = row[0] if hasattr(row, "__getitem__") else row.SurveyID

            # ---- Upsert respondent + fetch RespondentID
            conn.execute(
                text("""
                    EXEC SVY_InsertRespondent 
                        @SurveyID = :sid,
                        @Email    = :email,
                        @RespondentID = NULL;
                """),
                {"sid": survey_id, "email": email},
            )

            respondent_id = conn.execute(
                text("EXEC SVY_GetRespondentID @SurveyID=:sid, @Email=:email;"),
                {"sid": survey_id, "email": email},
            ).scalar()
            if not respondent_id:
                raise RuntimeError("Could not retrieve RespondentID")

            # ---- Map QuestionID -> QuestionCode (e.g., LIK, DDL, TEXT, BOOL)
            qtypes = conn.execute(
                text("EXEC SVY_GetQuestionTypesBySurveyID @SurveyID=:sid"),
                {"sid": survey_id},
            ).mappings().all()
            qtype_map = {str(r["QuestionID"]): r["QuestionCode"] for r in qtypes}

            # ---- Truthy/falsy sets for boolean-like questions
            truthy = {"1", "true", "t", "yes", "y", "on"}
            falsy  = {"0", "false", "f", "no", "n", "off"}

            # ---- Insert answers
            for qid_str, payload in answers.items():
                qtype = qtype_map.get(qid_str)
                if not qtype:
                    continue  # unknown qid (ignore)
                qid = int(qid_str)
                val = payload.get("value")
                opt_id_raw = payload.get("id")
                opt_id = int(opt_id_raw) if (opt_id_raw and opt_id_raw.isdigit()) else None

                if qtype == "LIK":
                    if val:
                        conn.execute(
                            text("""
                                EXEC SVY_InsertAnswer2 
                                    @RespondentID = :rid, 
                                    @QuestionID   = :qid, 
                                    @AnswerLikert = :val;
                            """),
                            {"rid": respondent_id, "qid": qid, "val": int(val)},
                        )

                elif qtype == "DDL":
                    # Use resolver proc to validate/resolve OptionID from (opt_id, val)
                    row = conn.execute(
                        text("""
                            DECLARE @Resolved INT, @IsValid BIT;
                            EXEC dbo.SVY_ResolveAndValidateOption
                                @SurveyID=:sid,
                                @QuestionID=:qid,
                                @PostedOptionID=:opt,
                                @PostedValue=:v,
                                @ResolvedOptionID=@Resolved OUTPUT,
                                @IsValid=@IsValid OUTPUT;
                            SELECT Resolved=@Resolved, IsValid=@IsValid;
                        """),
                        {"sid": survey_id, "qid": qid, "opt": opt_id, "v": val},
                    ).mappings().first()

                    resolved_opt = row["Resolved"] if row else None

                    if resolved_opt is not None:
                        # Store OptionID, and optionally keep the human text too
                        conn.execute(
                            text("""
                                EXEC SVY_InsertAnswer2
                                    @RespondentID    = :rid,
                                    @QuestionID      = :qid,
                                    @AnswerOptionID  = :opt,
                                    @AnswerText      = :val;
                            """),
                            {"rid": respondent_id, "qid": qid, "opt": int(resolved_opt), "val": (val or None)},
                        )
                    elif val:
                        # Fall back to storing text if we couldn’t resolve an OptionID
                        conn.execute(
                            text("""
                                EXEC SVY_InsertAnswer2
                                    @RespondentID = :rid,
                                    @QuestionID   = :qid,
                                    @AnswerText   = :val;
                            """),
                            {"rid": respondent_id, "qid": qid, "val": val},
                        )

                elif qtype in ("BOOL", "YN", "CHK"):
                    # Map checkbox/radio booleans
                    if val is not None:
                        v = str(val).strip().lower()
                        if v in truthy:
                            b = 1
                        elif v in falsy:
                            b = 0
                        else:
                            b = 1 if v else 0
                        conn.execute(
                            text("""
                                EXEC SVY_InsertAnswer2
                                    @RespondentID  = :rid,
                                    @QuestionID    = :qid,
                                    @AnswerBoolean = :b;
                            """),
                            {"rid": respondent_id, "qid": qid, "b": b},
                        )

                else:
                    # Default: store as text if non-empty
                    if val:
                        conn.execute(
                            text("""
                                EXEC SVY_InsertAnswer2
                                    @RespondentID = :rid, 
                                    @QuestionID   = :qid, 
                                    @AnswerText   = :val;
                            """),
                            {"rid": respondent_id, "qid": qid, "val": val},
                        )

        flash("✅ Survey submitted successfully!", "success")

        # Guest flows go to a thank-you page
        rn_lower = routename.lower()
        if rn_lower.startswith("form/guest") or rn_lower.startswith("guest/"):
            return redirect("/thankyou")
        return redirect(url_for("survey_bp.list_my_surveys"))

    except Exception as e:
        # Log e as appropriate for your app
        flash(f"❌ Error submitting survey: {e}", "danger")
        rn_lower = routename.lower()
        if rn_lower.startswith("form/guest") or rn_lower.startswith("guest/"):
            return redirect("/thankyou")  # or to a safer fallback
        return redirect(url_for("survey_bp.list_my_surveys"))


@survey_bp.route("/submit/guest/<int:survey_id>", methods=["POST"])
def submit_guest_survey(survey_id):
    try:
        engine = get_db_engine()
        form_data = request.form.to_dict()
        email = session.get("user_email")
        if not email:
            return "Email required", 400

        responses = {k[1:]: v for k, v in form_data.items() if k.startswith("q")}

        with engine.begin() as conn:
            conn.execute(text("""
                EXEC SVY_InsertRespondent 
                    @SurveyID = :survey_id,
                    @Email = :email,
                    @RespondentID = NULL;
            """), {"survey_id": survey_id, "email": email})

            respondent_id = conn.execute(text("""
                EXEC SVY_GetRespondentID 
                    @SurveyID = :survey_id,
                    @Email = :email;
            """), {"survey_id": survey_id, "email": email}).scalar()

            if not respondent_id:
                raise Exception("❌ Could not retrieve RespondentID")

            for qid_str, value in responses.items():
                qid = int(qid_str)
                if value.isdigit():
                    conn.execute(text("""
                        EXEC SVY_InsertAnswer2 
                            @RespondentID = :rid, 
                            @QuestionID = :qid, 
                            @AnswerLikert = :val;
                    """), {"rid": respondent_id, "qid": qid, "val": int(value)})
                else:
                    conn.execute(text("""
                        EXEC SVY_InsertAnswer2 
                            @RespondentID = :rid, 
                            @QuestionID = :qid, 
                            @AnswerText = :val;
                    """), {"rid": respondent_id, "qid": qid, "val": value})

        flash("✅ Survey submitted successfully!")
        return redirect("/thankyou")

    except Exception:
        traceback.print_exc()
        return "Internal Server Error", 500


# 🔹 For users to view their own surveys
@survey_bp.route("/MyForms")
@login_required
def list_my_surveys():
    email = session.get("user_email")
    return _load_survey_list(email)


# 🔹 For admins to view surveys for someone else
@survey_bp.route("/StaffForms")
@login_required
def list_target_surveys():
    if session.get("user_admin") != 1:
        return "Unauthorized", 403

    email = session.get("survey_target_email")
    if not email:
        flash("No survey target set", "warning")
        return redirect(url_for("survey_bp.list_my_surveys"))

    return _load_survey_list(email)


@survey_bp.route("/set_survey_target", methods=["POST"])
@login_required
def set_survey_target():
    if session.get("user_admin") != 1:
        return "Unauthorized", 403

    session["survey_target_email"] = request.form.get("email")
    session["survey_target_firstname"] = request.form.get("firstname")
    session["survey_target_lastname"] = request.form.get("lastname")

    return redirect(url_for("survey_bp.list_target_surveys"))


# 🔹 Internal function to load surveys by email
def _load_survey_list(email):
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                EXEC SVY_GetSurveysCompletedByUser @Email = :email
            """), {"email": email}).fetchall()

        return render_template("survey_list.html", surveys=rows, target_email=email)

    except Exception:
        traceback.print_exc()
        return "Internal Server Error", 500

from flask import render_template, session, redirect, url_for, flash, request
from sqlalchemy import text
from datetime import timezone
from zoneinfo import ZoneInfo
import traceback


@survey_bp.get("/api/classes")
def api_classes():
    school_id = request.args.get("school_id", type=str)
    term = request.args.get("term", type=int)
    year = request.args.get("year", type=int)
    if not school_id:
        return jsonify({"error": "school_id required"}), 400

    engine = get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                EXEC dbo.FlaskHelperFunctionsSpecific
                    @Request       = 'ClassesBySchool',
                    @MOENumber     = :moe,
                    @Term          = :term,
                    @CalendarYear  = :year
            """),
            {"moe": school_id, "term": term, "year": year},
        ).mappings().all()

    data = [
        {
            "ClassID": r.get("ClassID"),
            "ClassName": r.get("ClassName") or r.get("Name"),
            "Term": r.get("Term"),
            "CalendarYear": r.get("CalendarYear"),
        }
        for r in rows
    ]
    return jsonify(data)

@survey_bp.route("/MyForms/<int:respondent_id>")
@login_required
def view_my_survey_response(respondent_id):
    session_email = session.get("user_email")
    is_admin = session.get("user_role") == "ADM"

    try:
        engine = get_db_engine()
        print("🔌 Got engine")

        with engine.begin() as conn:
            print(f"📥 Executing SVY_GetSurveyResponseByRespondentID for {respondent_id}")
            rows = conn.execute(text("""
                EXEC SVY_GetSurveyResponseByRespondentID @RespondentID = :rid
            """), {"rid": respondent_id}).mappings().all()
            print(f"✅ Rows fetched: {len(rows)}")

            if not rows:
                flash("Survey response not found.", "warning")
                return redirect(url_for("survey_bp.list_my_surveys"))

            questions = {}
            for row in rows:
                sid = row["SurveyID"]
                qid = row["QuestionID"]
                qtext = row["QuestionText"]
                qcode = row["QuestionCode"]
                answer_likert = row["AnswerLikert"]
                answer_text = row["AnswerText"]

                if qid not in questions:
                    question = {
                        "id": qid,
                        "text": qtext,
                        "type": qcode,
                        "answer_likert": answer_likert,
                        "answer_text": answer_text,
                        "labels": []
                    }

                    if qcode == "LIK":
                        print(f"🔍 Loading labels for QuestionID={qid}, SurveyID={sid}")
                        label_rows = conn.execute(text("""
                            EXEC SVY_GetLikertLabelsByQuestionID @QuestionID = :qid, @SurveyID = :sid
                        """), {"qid": qid, "sid": sid}).fetchall()
                        question["labels"] = [(pos, label) for pos, label in label_rows]

                    questions[qid] = question
                else:
                    if answer_likert and not questions[qid]["answer_likert"]:
                        questions[qid]["answer_likert"] = answer_likert
                    if answer_text and not questions[qid]["answer_text"]:
                        questions[qid]["answer_text"] = answer_text

            email = rows[0]["Email"]
            submitted_raw = rows[0]["SubmittedDate"]
            title = rows[0]["Title"]
            if submitted_raw:
                submitted = submitted_raw
                #submitted_utc = submitted_raw.replace(tzinfo=timezone.utc)
                #submitted = submitted_utc.astimezone(ZoneInfo("Pacific/Auckland"))
            else:
                submitted = "Not submitted yet"

            role_code = rows[0]["Role"]
            role_mapping = {
                "PRO": "Provider Staff",
                "FUN": "Funder Staff",
                "MOE": "School Staff",
                "GRP": "Swim School Staff",
                "ADM": "WSNZ Admin"
            }
            role = role_mapping.get(role_code, role_code)
            entity = rows[0]["EntityDescription"]
            if role == "WSNZ Admin":
                entity = "WSNZ"
            fullname = f"{rows[0]['FirstName'] or ''} {rows[0]['Surname'] or ''}".strip()
            if not fullname:
                fullname = None
            return render_template(
                "survey_view.html",
                questions=list(questions.values()),
                email=email,
                role=role,
                submitted=submitted,
                entity=entity,
                fullname=fullname,
                title = title 
            )

    except Exception:
        tb_str = traceback.format_exc()
        print("🔥 FULL TRACEBACK:")
        print(tb_str)

        # Flash minimal message
        flash("Something went wrong loading the survey form.", "danger")

        # Render full traceback in browser (optional: disable after debugging)
        return f"<pre>{tb_str}</pre>", 500

@survey_bp.route("/Form/invite/<token>")
def survey_invite_token(token):
    try:
        s = URLSafeTimedSerializer(current_app.secret_key)
        data = s.loads(token, max_age=259200)  # 3 days
        session.clear( )
        session["logged_in"] = False 
        session["guest_user"] = True
        session["user_email"] = data["email"]
        session["user_firstname"] = data["firstname"]
        session["user_lastname"] = data["lastname"]
        session["user_role"] = data["role"]
        session["user_id"] = data["user_id"]
        session["desc"] = data.get("desc") or data.get("user_org", "Guest User")


        return redirect(url_for("survey_bp.guest_survey_by_id", survey_id=data["survey_id"]))

    except SignatureExpired:
        return render_template("error.html", message="This survey link has expired."), 403
    except BadSignature:
        return render_template("error.html", message="Invalid or tampered survey link."), 403
    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template("error.html", message="Something went wrong loading your survey."), 500

        
@survey_bp.route("/Form/guest/<int:survey_id>")
def guest_survey_by_id(survey_id):
    if not session.get("guest_user"):
        return redirect(url_for("auth_bp.login"))

    engine = get_db_engine()
    Label = namedtuple("Label", ["pos", "text"])
    questions = []
    seen_ids = {}

    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                EXEC SVY_GetSurveyQuestions @SurveyID = :survey_id
            """), {"survey_id": survey_id}).fetchall()

        for qid, qtext, qcode, pos, label in rows:
            if qid not in seen_ids:
                question = {
                    "id": qid,
                    "text": qtext,
                    "type": qcode,
                    "labels": []
                }
                questions.append(question)
                seen_ids[qid] = question

            if qcode == "LIK" and label:
                question["labels"].append(Label(pos, label))

        return render_template(
            f"survey_form_{survey_id}.html",
            questions=questions,
            route_name=f"guest/{survey_id}",
            impersonated_name=f"{session.get('user_firstname')} from {session.get('desc', 'your organisation')}"
        )

    except Exception:
        traceback.print_exc()
        return "Internal Server Error", 500

    
from flask import request, redirect, url_for, flash
from app.utils.custom_email import send_survey_invite_email, send_elearning_reminder_email

@survey_bp.route("/send_invite", methods=["POST"])
@login_required
def send_survey_invite():
    if session.get("user_admin") != 1:
        return "Unauthorized", 403

    # These would typically come from a form in your admin panel
    recipient_email = request.form.get("email")
    first_name = request.form.get("firstname")
    role = request.form.get("role")
    user_id = request.form.get("userid")  # optional if you track it
    survey_id = request.form.get("survey_id", 1)  # default to survey 1

    if not recipient_email or not first_name:
        flash("Missing email or name", "danger")
        return redirect(request.referrer or "/")

    send_survey_invite_email(
        mail=mail,
        recipient_email=recipient_email,
        first_name=first_name,
        role=role,
        user_id=user_id,
        survey_id=survey_id,
        invited_by_name = session["user_firstname"] + session["user_surname"]
    )

    flash(f"📧 Invitation sent to {recipient_email}", "success")
    return redirect(request.referrer or "/")


from flask import request, session, redirect, url_for, flash
from app.utils.custom_email import send_survey_invite_email, send_survey_reminder_email, send_survey_invitation_email
from app.extensions import mail
from app.routes.survey import survey_bp
@survey_bp.route("/send_survey_link", methods=["POST"])
@login_required
def email_survey_link():
    try:
        email = request.form["email"]
        firstname = request.form["firstname"]
        lastname = request.form["lastname"]
        role = request.form["role"]
        user_id = int(request.form["user_id"])
        survey_id = 1
        requested_by = request.form["requested_by"]
        from_org = request.form["from_org"]

        send_survey_invitation_email(
            mail, email, firstname, lastname, role, user_id, survey_id, requested_by, from_org
        )
        flash(f"📧 Invitation sent to {firstname}.", "info")
    except Exception as e:
        print("❌ Exception occurred in email_survey_link():")
        traceback.print_exc()
        flash("❌ Failed to send invitation email.", "danger")

    return redirect(url_for("staff_bp.staff_maintenance"))


@survey_bp.route("/send_survey_reminder", methods=["POST"])
@login_required
def send_survey_reminder():
    try:
        email = request.form["email"]
        firstname = request.form["firstname"]
        requested_by = request.form["requested_by"]  # Should be full name
        from_org = request.form["from_org"]
        print(from_org)
        print(requested_by)
        send_survey_reminder_email(mail, email, firstname, requested_by, from_org)
        flash(f"📧 Reminder sent to {firstname}.", "info")
    except Exception as e:
        print("❌ Exception occurred:")
        traceback.print_exc()
        flash("❌ Failed to send reminder.", "danger")

    return redirect(url_for("staff_bp.staff_maintenance"))

@survey_bp.route("/thankyou")
def thank_you():
    return render_template("thankyou.html")


def _badge_class(title: str) -> str:
    # Map survey title to a stable blue shade (matches CSS in the template)
    shades = ["badge-blue-1","badge-blue-2","badge-blue-3","badge-blue-4","badge-blue-5","badge-blue-6"]
    return shades[hash((title or "").lower()) % len(shades)]

def _normalize_entity_type(entity_type: str) -> str:
    """
    Map UI values to your proc's expected codes.
    Adjust as needed ('PRO'/'FUN'/'PRV' etc).
    """
    et = (entity_type or "").strip().lower()
    if et.startswith("fun"):   # "Funder"
        return "FUN"
    if et.startswith("pro"):   # "Provider"
        return "PRO"
    return entity_type  # fallback to whatever was passed

# Map UI value -> DB proc code
ET_CODE = {"Funder": "FUN", "Provider": "PRO", "Group": "GRP", "School": "MOE"}

def _has_groups(engine, funder_id: int) -> bool:
    try:
        with engine.begin() as conn:
            rows = list(conn.execute(
                text("EXEC FlaskGetGroupsByFunder @FunderID = :fid"),
                {"fid": funder_id}
            ))
        return len(rows) > 0
    except Exception:
        return False

def _allowed_entity_types(user_role: str, engine, user_id: int):
    role = (user_role or "").upper()
    allowed = []
    # School: everyone
    allowed.append({"value": "School", "label": "School"})
    # Funder: FUN or ADM
    if role in {"FUN", "ADM"}:
        allowed.append({"value": "Funder", "label": "Funder"})
    # Provider: PRO, FUN, ADM, GRP
    if role in {"PRO", "FUN", "ADM", "GRP"}:
        allowed.append({"value": "Provider", "label": "Provider"})
    # Group: ADM or FUN with groups
    if role == "ADM" or (role == "FUN" and _has_groups(engine, user_id)):
        allowed.append({"value": "Group", "label": "Group"})
    # Keep a stable order like: Funder, Provider, Group, School
    order = {"Funder": 0, "Provider": 1, "Group": 2, "School": 3}
    allowed.sort(key=lambda x: order.get(x["value"], 99))
    return allowed

def _coerce_entity_type(chosen: str, allowed: list[dict]) -> str:
    vals = [x["value"] for x in allowed]
    if chosen in vals:
        return chosen
    return vals[0] if vals else "School"

def _badge_class(title: str) -> str:
    shades = ["badge-blue-1","badge-blue-2","badge-blue-3","badge-blue-4","badge-blue-5","badge-blue-6"]
    return shades[hash((title or "").lower()) % len(shades)]

@survey_bp.route("/SurveyByEntity", methods=["GET"])
@login_required
def staff_survey_admin():
    # Session/user
    user_role = session.get("user_role")   # "ADM","FUN","PRO","GRP", etc.
    user_id   = session.get("user_id")

    # Incoming selection
    requested_entity_type = request.args.get("entity_type") or "Funder"
    selected_entity_id    = request.args.get("entity_id", type=int)

    staff_surveys = []
    try:
        engine = get_db_engine()

        # Build allowed list & sanitize UI selection
        allowed_entity_types = _allowed_entity_types(user_role, engine, user_id)
        entity_type = _coerce_entity_type(requested_entity_type, allowed_entity_types)

        # Only query if an entity is chosen
        if selected_entity_id:
            et_code = ET_CODE.get(entity_type, entity_type[:3].upper())
            with engine.begin() as conn:
                # Use .mappings() for name-based access
                result = conn.exec_driver_sql(
                    "EXEC SVY_GetEntityResponses @EntityType=?, @EntityID=?",
                    (et_code, selected_entity_id),
                ).mappings()

                for row in result:
                    first = row.get("FirstName") or ""
                    last  = row.get("Surname") or ""
                    name  = f"{first} {last}".strip()
                    staff_surveys.append({
                        "FirstName": first,
                        "Surname": last,
                        "Name": name,
                        "Email": row.get("Email") or "",
                        "Title": row.get("Title") or "",
                        "SubmittedDate": row.get("SubmittedDate"),
                        "RespondentID": row.get("RespondentID"),
                        "BadgeClass": _badge_class(row.get("Title") or ""),
                    })

    except Exception:
        import traceback; traceback.print_exc()
        flash("An error occurred while loading survey data.", "danger")

    # Always render with these keys so Jinja doesn’t blow up
    return render_template(
        "survey_staff.html",
        entity_type=entity_type,
        allowed_entity_types=allowed_entity_types,
        selected_entity_id=selected_entity_id,
        staff_surveys=staff_surveys
    )

@survey_bp.get("/api/FlaskGetAllUsers")
def api_flask_get_all_users():
    """
    Returns a JSON list of users for the instructor dropdown.
    Expected columns: FirstName, LastName, Email
    """
    engine = get_db_engine()
    with engine.connect() as conn:
        # If your proc needs params, add them; else just EXEC
        result = conn.execute(text("EXEC dbo.FlaskGetAllUsers"))
        rows = [dict(r._mapping) for r in result]
    # Optionally filter/transform here if you only want instructors
    # rows = [r for r in rows if r.get("Role") == "Instructor"]
    return jsonify(rows)

from flask import jsonify, request, current_app
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

@survey_bp.route("/api/AddMOEStaff", methods=["POST"])
def add_moe_staff():
    data = request.get_json(force=True, silent=True) or {}
    first = (data.get("firstName") or "").strip()
    sur   = (data.get("surname") or "").strip()
    email = (data.get("email") or "").strip().lower()
    moe   = (data.get("moeNumber") or "").strip()

    if not (first and sur and email and moe):
        return jsonify(ok=False, message="Please fill in all fields."), 400
    try:
        moe_int = int(moe)
    except ValueError:
        return jsonify(ok=False, message="MOE Number must be a whole number."), 400

    try:
        with get_db_engine().begin() as conn:
            row = conn.execute(
                text("""
                    EXEC dbo.FlaskAddMOEUserIfMissing
                        @Email=:email,
                        @FirstName=:first,
                        @Surname=:sur,
                        @MOENumber=:moe
                """),
                {"email": email, "first": first, "sur": sur, "moe": moe_int}
            ).mappings().first()

        if not row:
            return jsonify(ok=False, message="Couldn’t add the teacher. Please try again."), 500

        # If the proc *didn't* THROW on conflict and just returned an existing user,
        # enforce that the user actually belongs to this MOE school.
        role_existing = (row.get("Role") or "").upper()
        id_existing   = row.get("ID")
        if not (role_existing == "MOE" and int(id_existing or 0) == moe_int):
            return jsonify(
                ok=False,
                message=("That email is already linked to a different organisation. "
                         "Please ask an administrator to reassign it to this school, or use another email.")
            ), 409

        # OK — user is MOE and matches this school's MOE number
        user = {
            "Email": row["Email"],
            "FirstName": row["FirstName"],
            "Surname": row["Surname"],
            "Role": row["Role"],
            "ID": row["ID"],
            "Active": row["Active"],
            "Hidden": row["Hidden"],
            "Admin": row["Admin"],
        }
        return jsonify(ok=True, user=user)

    except DBAPIError as e:
        raw = str(getattr(e, "orig", e))
        if "Email already assigned to another entity" in raw or "already assigned to another entity" in raw:
            return jsonify(
                ok=False,
                message=("That email is already linked to a different organisation. "
                         "Please ask an administrator to reassign it to this school, or use another email.")
            ), 409
        if "MOE Number not found" in raw:
            return jsonify(ok=False, message="We can’t find that MOE number. Please choose a school from the list."), 400

        current_app.logger.exception("AddMOEStaff failed")
        return jsonify(ok=False, message="Sorry—something went wrong while adding the teacher."), 500
    
    
    
    