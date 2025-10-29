## Survey.py
# Standard library
import hashlib
import json
import re
import traceback
from collections import namedtuple
from datetime import timezone
from zoneinfo import ZoneInfo

# Third-party
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Local
from app.extensions import mail
from app.routes.auth import login_required
from app.utils.custom_email import (
    send_elearning_reminder_email,
    send_survey_invitation_email,
    send_survey_invite_email,
    send_survey_reminder_email,
)
from app.utils.database import get_db_engine, log_alert

# Blueprint
survey_bp = Blueprint("survey_bp", __name__)

@survey_bp.route("/Form/<string:routename>")
def survey_by_routename(routename):
    engine = get_db_engine()
    Label = namedtuple("Label", ["pos", "text"])
    questions = []
    seen_ids = {}

    try:
        current_app.logger.info("📝 survey_by_routename start | route=%s | user=%s",
                                routename, session.get("user_email"))

        with engine.connect() as conn:
            # 1) Resolve survey id
            res = conn.execute(
                text("EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename"),
                {"routename": routename}
            )
            row = res.fetchone()
            res.close()

            if not row:
                current_app.logger.warning("🔎 Survey not found | route=%s", routename)
                flash(f"Survey '{routename}' not found.", "danger")
                return redirect("/Profile")

            survey_id = getattr(row, "SurveyID", row[0])
            current_app.logger.info("✅ resolved survey_id=%s for route=%s", survey_id, routename)

            # 2) Access control
            user_role = session.get("user_role")
            user_id = session.get("user_id")
            current_app.logger.info("🔐 user_role=%s user_id=%s", user_role, user_id)

            if survey_id == 3:
                # Teacher Assessment — funders only
                if user_role not in ["FUN", "ADM"]:
                    flash("This assessment is restricted to funders and WSNZ Administrators.", "warning")
                    return redirect("/MyForms")

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

        # Ensure likert labels are ordered
        for q in questions:
            if q["labels"]:
                q["labels"].sort(key=lambda L: (L.pos is None, L.pos))

        current_app.logger.info("📦 survey %s built | questions=%d", survey_id, len(questions))

        # 5) Render — no 'schools' passed; template for survey 3 fetches via AJAX
        ctx = {
            "questions": questions,
            "route_name": routename,
            "survey_id": survey_id,
        }
        return render_template(f"survey_form_{survey_id}.html", **ctx)

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ survey_by_routename error | route=%s | %s", routename, e, exc_info=True)
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.survey_by_routename", routename=routename, _external=True),
                message=f"/Form/{routename} failed: {e}\n{tb}"[:4000],
            )
        except Exception:
            pass
        return "Internal Server Error: Failed to load survey", 500


@survey_bp.route("/api/surveys/<int:survey_id>/questions/<int:question_id>/options")
def api_dropdown_options(survey_id, question_id):
    try:
        with get_db_engine().connect() as conn:
            result = conn.execute(
                text("EXEC dbo.SVY_GetOptions @SurveyID=:sid, @QuestionID=:qid"),
                {"sid": survey_id, "qid": question_id}
            ).mappings()
            rows = [dict(r) for r in result]

        payload = [{"id": r["OptionID"], "value": r["OptionValue"], "label": r["Label"]} for r in rows]
        current_app.logger.info("📥 options | survey_id=%s qid=%s count=%d", survey_id, question_id, len(payload))
        return jsonify(payload)

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ api_dropdown_options error | survey_id=%s qid=%s | %s",
                                 survey_id, question_id, e, exc_info=True)
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.api_dropdown_options", survey_id=survey_id, question_id=question_id, _external=True),
                message=f"/api/surveys/{survey_id}/questions/{question_id}/options failed: {e}\n{tb}"[:4000],
            )
        except Exception:
            pass
        return jsonify([]), 500


@survey_bp.route("/submit/<string:routename>", methods=["POST"])
def submit_survey(routename):
    try:
        email = session.get("user_email")
        if not email:
            return "Email required", 400

        form = request.form  # MultiDict
        current_app.logger.info("📝 submit_survey start | routename=%s | email=%s", routename, email)

        # ---- Parse qN + qN_id pairs into dict
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

        current_app.logger.info("📦 parsed answers: %d questions", len(answers))

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
                    current_app.logger.warning("🔎 survey not found | routename=%s", routename)
                    return f"Survey '{routename}' not found", 400
                survey_id = row[0] if hasattr(row, "__getitem__") else row.SurveyID

            current_app.logger.info("✅ resolved SurveyID=%s", survey_id)

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
            current_app.logger.info("👤 RespondentID=%s", respondent_id)

            # ---- Map QuestionID -> QuestionCode
            qtypes = conn.execute(
                text("EXEC SVY_GetQuestionTypesBySurveyID @SurveyID=:sid"),
                {"sid": survey_id},
            ).mappings().all()
            qtype_map = {str(r["QuestionID"]): r["QuestionCode"] for r in qtypes}
            current_app.logger.info("🗺️ loaded %d question types", len(qtype_map))

            # ---- Truthy/falsy sets
            truthy = {"1", "true", "t", "yes", "y", "on"}
            falsy  = {"0", "false", "f", "no", "n", "off"}

            # ---- Insert answers
            inserted = 0
            for qid_str, payload in answers.items():
                qtype = qtype_map.get(qid_str)
                if not qtype:
                    continue
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
                        inserted += 1

                elif qtype == "DDL":
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
                        inserted += 1
                    elif val:
                        conn.execute(
                            text("""
                                EXEC SVY_InsertAnswer2
                                    @RespondentID = :rid,
                                    @QuestionID   = :qid,
                                    @AnswerText   = :val;
                            """),
                            {"rid": respondent_id, "qid": qid, "val": val},
                        )
                        inserted += 1

                elif qtype in ("BOOL", "YN", "CHK"):
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
                        inserted += 1

                else:
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
                        inserted += 1

        current_app.logger.info("✅ submit_survey done | answers_inserted=%d", inserted)
        flash("✅ Survey submitted successfully!", "success")

        rn_lower = routename.lower()
        if rn_lower.startswith("form/guest") or rn_lower.startswith("guest/"):
            return redirect("/thankyou")
        return redirect(url_for("survey_bp.list_my_surveys"))

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ submit_survey error | route=%s | %s", routename, e, exc_info=True)
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.submit_survey", routename=routename, _external=True),
                message=f"/submit/{routename} failed: {e}\n{tb}"[:4000],
            )
        except Exception:
            pass

        flash(f"❌ Error submitting survey: {e}", "danger")
        rn_lower = routename.lower()
        if rn_lower.startswith("form/guest") or rn_lower.startswith("guest/"):
            return redirect("/thankyou")
        return redirect(url_for("survey_bp.list_my_surveys"))

@survey_bp.route("/submit/guest/<int:survey_id>", methods=["POST"])
def submit_guest_survey(survey_id):
    try:
        engine = get_db_engine()
        form_data = request.form.to_dict()
        email = session.get("user_email")
        if not email:
            return "Email required", 400

        current_app.logger.info("📝 submit_guest_survey start | survey_id=%s | email=%s", survey_id, email)

        responses = {k[1:]: v for k, v in form_data.items() if k.startswith("q")}
        current_app.logger.info("📦 parsed guest responses: %d questions", len(responses))

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
                raise Exception("Could not retrieve RespondentID")
            current_app.logger.info("👤 Guest RespondentID=%s", respondent_id)

            inserted = 0
            for qid_str, value in responses.items():
                qid = int(qid_str)
                if value.isdigit():
                    conn.execute(text("""
                        EXEC SVY_InsertAnswer2 
                            @RespondentID = :rid, 
                            @QuestionID = :qid, 
                            @AnswerLikert = :val;
                    """), {"rid": respondent_id, "qid": qid, "val": int(value)})
                    inserted += 1
                else:
                    conn.execute(text("""
                        EXEC SVY_InsertAnswer2 
                            @RespondentID = :rid, 
                            @QuestionID = :qid, 
                            @AnswerText = :val;
                    """), {"rid": respondent_id, "qid": qid, "val": value})
                    inserted += 1

        current_app.logger.info("✅ submit_guest_survey done | answers_inserted=%d", inserted)
        flash("✅ Survey submitted successfully!")
        return redirect("/thankyou")

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ submit_guest_survey error | survey_id=%s | %s", survey_id, e, exc_info=True)
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.submit_guest_survey", survey_id=survey_id, _external=True),
                message=f"/submit/guest/{survey_id} failed: {e}\n{tb}"[:4000],
            )
        except Exception:
            pass
        return "Internal Server Error", 500

# 🔹 For users to view their own surveys
@survey_bp.route("/MyForms")
@login_required
def list_my_surveys():
    email = session.get("user_email")
    current_app.logger.info("📄 /MyForms for %s", email)
    return _load_survey_list(email)

@survey_bp.route("/StaffForms")
@login_required
def list_target_surveys():
    if session.get("user_admin") != 1:
        current_app.logger.warning("🚫 /StaffForms unauthorized | user=%s", session.get("user_email"))
        return "Unauthorized", 403

    email = session.get("survey_target_email")
    if not email:
        flash("No survey target set", "warning")
        return redirect(url_for("survey_bp.list_my_surveys"))

    current_app.logger.info("📄 /StaffForms for target=%s (by %s)", email, session.get("user_email"))
    return _load_survey_list(email)

@survey_bp.route("/set_survey_target", methods=["POST"])
@login_required
def set_survey_target():
    if session.get("user_admin") != 1:
        current_app.logger.warning("🚫 /set_survey_target unauthorized | user=%s", session.get("user_email"))
        return "Unauthorized", 403

    session["survey_target_email"]     = request.form.get("email")
    session["survey_target_firstname"] = request.form.get("firstname")
    session["survey_target_lastname"]  = request.form.get("lastname")

    current_app.logger.info(
        "🎯 set_survey_target -> %s (%s %s) by %s",
        session.get("survey_target_email"),
        session.get("survey_target_firstname"),
        session.get("survey_target_lastname"),
        session.get("user_email"),
    )
    return redirect(url_for("survey_bp.list_target_surveys"))



# 🔹 Internal function to load surveys by email
def _load_survey_list(email):
    engine = get_db_engine()
    try:
        current_app.logger.info("🔎 _load_survey_list | email=%s", email)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                EXEC SVY_GetSurveysCompletedByUser @Email = :email
            """), {"email": email}).fetchall()

        current_app.logger.info("✅ _load_survey_list returned %d rows | email=%s", len(rows or []), email)
        return render_template("survey_list.html", surveys=rows, target_email=email)

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ _load_survey_list failed for %s: %s", email, e, exc_info=True)
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.list_my_surveys", _external=True),
                message=f"_load_survey_list failed for target={email}\n{tb}"[:4000],
            )
        except Exception:
            pass
        return "Internal Server Error", 500



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

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("EXEC FlaskHelperFunctions @Request='SchoolStaffFunder', @Number=:uid"),
                {"uid": uid},
            ).mappings()

            rows = [dict(r) for r in result]

        payload = [{"Name": r.get("Name"), "Email": r.get("Email")} for r in rows]
        current_app.logger.info("👩‍🏫 api_teachers | school_id=%s | count=%d", uid, len(payload))
        return jsonify(payload)

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ api_teachers error | school_id=%s | %s", school_id, e, exc_info=True)
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.api_teachers", _external=True),
                message=f"/api/teachers failed (school_id={school_id}): {e}\n{tb}"[:4000],
            )
        except Exception:
            pass
        return jsonify([]), 500


@survey_bp.get("/api/classes")
def api_classes():
    school_id = request.args.get("school_id", type=str)
    term      = request.args.get("term", type=int)
    year      = request.args.get("year", type=int)

    if not school_id:
        return jsonify({"error": "school_id required"}), 400

    try:
        current_app.logger.info("📚 /api/classes | school_id=%s term=%s year=%s", school_id, term, year)
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
                "ClassID":     r.get("ClassID"),
                "ClassName":   r.get("ClassName") or r.get("Name"),
                "Term":        r.get("Term"),
                "CalendarYear":r.get("CalendarYear"),
            }
            for r in rows
        ]
        current_app.logger.info("✅ /api/classes returned %d rows for school_id=%s", len(data), school_id)
        return jsonify(data)

    except Exception as e:
        tb = traceback.format_exc()
        current_app.logger.error("❌ /api/classes failed: %s", e, exc_info=True)
        # best-effort alert (won't raise)
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.api_classes", _external=True),
                message=f"/api/classes failed for school_id={school_id}, term={term}, year={year}\n{tb}"[:4000],
            )
        except Exception:
            pass
        return jsonify({"error": "Internal Server Error"}), 500



@survey_bp.route("/MyForms/<int:respondent_id>")
@login_required
def view_my_survey_response(respondent_id):
    session_email = session.get("user_email")
    is_admin = session.get("user_role") == "ADM"

    def _normalize_tf(qcode, answer_bool, answer_likert, answer_text):
        """
        Return 1 (Yes), 0 (No), or None for non-T/F or missing.
        Prefers AnswerBoolean, then Likert 1/2, then textual '1/0/yes/no'.
        """
        if qcode != 'T/F':
            return None

        if answer_bool is not None:
            return 1 if bool(answer_bool) else 0
        if answer_likert in (1, 2):
            return 1 if answer_likert == 1 else 0
        if isinstance(answer_text, str) and answer_text.strip():
            t = answer_text.strip().lower()
            if t in ("1", "true", "t", "yes", "y"): return 1
            if t in ("0", "false", "f", "no", "n"):  return 0
        return None

    try:
        current_app.logger.info("🔎 view_my_survey_response called | respondent_id=%s | user=%s",
                                respondent_id, session_email)

        engine = get_db_engine()
        with engine.begin() as conn:
            rows = conn.execute(text("""
                EXEC SVY_GetSurveyResponseByRespondentID @RespondentID = :rid
            """), {"rid": respondent_id}).mappings().all()
            current_app.logger.info("✅ SVY_GetSurveyResponseByRespondentID returned %d rows", len(rows))

            if not rows:
                flash("Survey response not found.", "warning")
                return redirect(url_for("survey_bp.list_my_surveys"))

            questions = {}
            for row in rows:
                sid = row["SurveyID"]
                qid = row["QuestionID"]
                qtext = row["QuestionText"]
                qcode = row["QuestionCode"]
                answer_likert = row.get("AnswerLikert")
                answer_text   = row.get("AnswerText")
                answer_bool   = row.get("AnswerBoolean")

                if qid not in questions:
                    tf_val = _normalize_tf(qcode, answer_bool, answer_likert, answer_text)
                    question = {
                        "id": qid,
                        "text": qtext,
                        "type": qcode,
                        "answer_likert": answer_likert if qcode == "LIK" else None,
                        "answer_text": None if qcode in ("LIK", "T/F") else (answer_text or None),
                        "answer_tf_value": tf_val if qcode == "T/F" else None,
                        "labels": []
                    }

                    if qcode == "LIK":
                        label_rows = conn.execute(text("""
                            EXEC SVY_GetLikertLabelsByQuestionID @QuestionID = :qid, @SurveyID = :sid
                        """), {"qid": qid, "sid": sid}).fetchall()
                        question["labels"] = [(pos, label) for pos, label in label_rows]

                    questions[qid] = question
                else:
                    if qcode == "LIK" and answer_likert and not questions[qid]["answer_likert"]:
                        questions[qid]["answer_likert"] = answer_likert
                    if qcode not in ("LIK", "T/F") and answer_text and not questions[qid]["answer_text"]:
                        questions[qid]["answer_text"] = answer_text
                    if qcode == "T/F" and questions[qid]["answer_tf_value"] is None:
                        questions[qid]["answer_tf_value"] = _normalize_tf(qcode, answer_bool, answer_likert, answer_text)

            email = rows[0]["Email"]
            submitted_raw = rows[0]["SubmittedDate"]
            title = rows[0]["Title"]
            submitted = submitted_raw or "Not submitted yet"

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
            fullname = f"{rows[0]['FirstName'] or ''} {rows[0]['Surname'] or ''}".strip() or None

            current_app.logger.info(
                "📄 Render survey_view | respondent_id=%s | email=%s | title=%s | role=%s | entity=%s",
                respondent_id, email, title, role, entity
            )

            return render_template(
                "survey_view.html",
                questions=list(questions.values()),
                email=email,
                role=role,
                submitted=submitted,
                entity=entity,
                fullname=fullname,
                title=title
            )

    except Exception as e:
        tb_str = traceback.format_exc()
        current_app.logger.error("🔥 view_my_survey_response failed: %s", e, exc_info=True)
        # Best-effort DB alert (never raise)
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.view_my_survey_response", respondent_id=respondent_id, _external=True),
                message=f"/MyForms/{respondent_id} failed: {e}\n{tb_str}"[:4000],
            )
        except Exception:
            pass
        flash("Something went wrong loading the survey form.", "danger")
        return f"<pre>{tb_str}</pre>", 500


@survey_bp.route("/Form/invite/<token>")
def survey_invite_token(token):
    try:
        current_app.logger.info("🔐 survey_invite_token received token")
        s = URLSafeTimedSerializer(current_app.secret_key)
        data = s.loads(token, max_age=259200)  # 3 days

        # Log the minimal safely-loggable bits (avoid logging the whole token or PII-heavy payload)
        current_app.logger.info(
            "✅ token OK | email=%s | role=%s | user_id=%s | survey_id=%s",
            data.get("email"), data.get("role"), data.get("user_id"), data.get("survey_id")
        )

        session.clear()
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
        current_app.logger.warning("⏲️ survey_invite_token expired")
        # Best-effort alert
        try:
            log_alert(
                email=None, role=None, entity_id=None,
                link=url_for("survey_bp.survey_invite_token", token=token, _external=True),
                message="Survey invite token expired.",
            )
        except Exception:
            pass
        return render_template("error.html", message="This survey link has expired."), 403

    except BadSignature:
        current_app.logger.warning("🔏 survey_invite_token bad signature")
        try:
            log_alert(
                email=None, role=None, entity_id=None,
                link=url_for("survey_bp.survey_invite_token", token=token, _external=True),
                message="Survey invite token invalid or tampered.",
            )
        except Exception:
            pass
        return render_template("error.html", message="Invalid or tampered survey link."), 403

    except Exception as e:
        tb_str = traceback.format_exc()
        current_app.logger.error("❌ survey_invite_token crashed: %s", e, exc_info=True)
        try:
            log_alert(
                email=None, role=None, entity_id=None,
                link=url_for("survey_bp.survey_invite_token", token=token, _external=True),
                message=f"/Form/invite/<token> failed: {e}\n{tb_str}"[:4000],
            )
        except Exception:
            pass
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
                question = {"id": qid, "text": qtext, "type": qcode, "labels": []}
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

    except Exception as e:
        traceback.print_exc()
        # best-effort DB alert (don’t block UX)
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.guest_survey_by_id", survey_id=survey_id, _external=True),
                message=f"guest_survey_by_id({survey_id}) failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return "Internal Server Error", 500

@survey_bp.route("/send_invite", methods=["POST"])
@login_required
def send_survey_invite():
    if session.get("user_admin") != 1:
        return "Unauthorized", 403

    recipient_email = request.form.get("email")
    first_name      = request.form.get("firstname")
    role            = request.form.get("role")
    user_id         = request.form.get("userid")  # optional
    survey_id       = request.form.get("survey_id", 1)  # default = 1

    if not recipient_email or not first_name:
        flash("Missing email or name", "danger")
        return redirect(request.referrer or "/")

    try:
        send_survey_invite_email(
            mail=mail,
            recipient_email=recipient_email,
            first_name=first_name,
            role=role,
            user_id=user_id,
            survey_id=survey_id,
            invited_by_name=(session.get("user_firstname","") + " " + session.get("user_surname","")).strip()
        )
        flash(f"📧 Invitation sent to {recipient_email}", "success")

    except Exception as e:
        traceback.print_exc()
        flash("❌ Failed to send survey invitation.", "danger")
        # best-effort alert log
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.send_survey_invite", _external=True),
                message=f"/send_invite failed for {recipient_email}: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass

    return redirect(request.referrer or "/")


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

        # Log to AUD_Alerts — won't break page
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.email_survey_link", _external=True),
                message=f"/send_survey_link failed for {email}: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass

    return redirect(url_for("staff_bp.staff_maintenance"))
@survey_bp.route("/send_survey_reminder", methods=["POST"])
@login_required
def send_survey_reminder():
    try:
        email = request.form["email"]
        firstname = request.form["firstname"]
        requested_by = request.form["requested_by"]
        from_org = request.form["from_org"]

        print(from_org)
        print(requested_by)

        send_survey_reminder_email(mail, email, firstname, requested_by, from_org)
        flash(f"📧 Reminder sent to {firstname}.", "info")

    except Exception as e:
        print("❌ Exception occurred in send_survey_reminder():")
        traceback.print_exc()
        flash("❌ Failed to send reminder.", "danger")

        # Log to AUD_Alerts
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.send_survey_reminder", _external=True),
                message=f"/send_survey_reminder failed for {email}: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass

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
    shades = ["badge-blue-1","badge-blue-2","badge-blue-3",
              "badge-blue-4","badge-blue-5","badge-blue-6"]
    key = (title or "").strip().lower().encode("utf-8")
    # 2-byte digest → small integer → palette index
    h2 = hashlib.blake2b(key, digest_size=2).digest()
    idx = int.from_bytes(h2, "big") % len(shades)
    return shades[idx]


@survey_bp.route("/SurveyByEntity", methods=["GET"])
@login_required
def staff_survey_admin():
    user_role = session.get("user_role")
    user_id   = session.get("user_id")

    requested_entity_type = request.args.get("entity_type") or "Funder"
    selected_entity_id    = request.args.get("entity_id", type=int)

    staff_surveys = []
    # sensible fallbacks so render_template still works after an exception
    allowed_entity_types = []
    entity_type = requested_entity_type

    try:
        engine = get_db_engine()
        allowed_entity_types = _allowed_entity_types(user_role, engine, user_id)
        entity_type = _coerce_entity_type(requested_entity_type, allowed_entity_types)

        if selected_entity_id:
            et_code = ET_CODE.get(entity_type, entity_type[:3].upper())
            with engine.begin() as conn:
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
                        "Details": row.get("Details"),
                        "SubjectEmail": row.get("SubjectEmail") or row.get("Email"),
                        "RespondentID": row.get("RespondentID"),
                        "BadgeClass": _badge_class(row.get("Title") or ""),
                    })

    except Exception as e:
        traceback.print_exc()
        flash("An error occurred while loading survey data.", "danger")
        # ship to AUD_Alerts (never break the response)
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.staff_survey_admin", _external=True, entity_type=requested_entity_type, entity_id=selected_entity_id),
                message=f"/SurveyByEntity failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass

    # Precompute unique/sorted titles for the filter dropdown
    form_titles = sorted({s["Title"] for s in staff_surveys if s.get("Title")})

    return render_template(
        "survey_staff.html",
        entity_type=entity_type,
        allowed_entity_types=allowed_entity_types,
        selected_entity_id=selected_entity_id,
        staff_surveys=staff_surveys,
        form_titles=form_titles,
    )
# ---------- API: all users for instructor dropdown ----------
@survey_bp.get("/api/FlaskGetAllUsers")
def api_flask_get_all_users():
    """
    Returns a JSON list of users for the instructor dropdown.
    Expected columns: FirstName, LastName, Email
    """
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("EXEC dbo.FlaskGetAllUsers"))
            rows = [dict(r._mapping) for r in result]
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.api_flask_get_all_users", _external=True),
                message=f"/api/FlaskGetAllUsers failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return jsonify([]), 500


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

        role_existing = (row.get("Role") or "").upper()
        id_existing   = row.get("ID")
        if not (role_existing == "MOE" and int(id_existing or 0) == moe_int):
            return jsonify(
                ok=False,
                message=("That email is already linked to a different organisation. "
                         "Please ask an administrator to reassign it to this school, or use another email.")
            ), 409

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

        current_app.logger.exception("AddMOEStaff failed (DBAPIError)")
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.add_moe_staff", _external=True),
                message=f"/api/AddMOEStaff DBAPIError: {raw}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return jsonify(ok=False, message="Sorry—something went wrong while adding the teacher."), 500

    except Exception as e:
        current_app.logger.exception("AddMOEStaff failed (generic)")
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.add_moe_staff", _external=True),
                message=f"/api/AddMOEStaff failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return jsonify(ok=False, message="Sorry—something went wrong while adding the teacher."), 500

    
    
 
@survey_bp.route("/SurveyBuilder", methods=["GET"])
@login_required
def survey_builder():
    if session.get("user_role") != "ADM":
        flash("Unauthorized access", "danger")
        # (optional) log unauthorized attempts
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.survey_builder", _external=True),
                message="Unauthorized access to /SurveyBuilder.",
            )
        except Exception:
            pass
        return redirect(url_for("home_bp.home"))

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "EXEC FlaskFormBuilderHelper @Request='ListLikertScales';"
            )).fetchall()

        scales = []
        for r in rows:
            raw = r.LabelsJSON
            labels = []
            if raw is not None:
                try:
                    if isinstance(raw, (bytes, bytearray, memoryview)):
                        raw = bytes(raw).decode("utf-8", errors="ignore")
                    if isinstance(raw, str) and raw.strip() != "":
                        labels = json.loads(raw)
                except Exception as e:
                    current_app.logger.warning("LabelsJSON parse failed: %s; raw=%r", e, raw)
                    labels = []
            scales.append({
                "id": int(r.LikertScaleID),
                "name": r.ScaleName,
                "max": int(r.MaxValue),
                "labels": labels
            })

        return render_template("survey_builder.html", scales=scales)

    except Exception as e:
        current_app.logger.exception("SurveyBuilder crashed")
        # best-effort DB alert
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.survey_builder", _external=True),
                message=f"/SurveyBuilder failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return f"<pre>SurveyBuilder error:\n{e}</pre>", 500

@survey_bp.route("/survey/likert-scales", methods=["GET"])
@login_required
def likert_list():
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                EXEC FlaskFormBuilderHelper @Request='ListLikertScales';
            """)).fetchall()

        out = []
        for r in rows:
            labels = []
            if getattr(r, "LabelsJSON", None):
                try:
                    labels = json.loads(r.LabelsJSON)
                except Exception:
                    labels = []
            out.append({
                "id": int(r.LikertScaleID),
                "name": r.ScaleName,
                "max": int(r.MaxValue),
                "labels": labels
            })
        return jsonify(out)

    except Exception as e:
        current_app.logger.exception("likert_list failed")
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.likert_list", _external=True),
                message=f"GET /survey/likert-scales failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return jsonify([]), 500

@survey_bp.route("/survey/likert-scales", methods=["POST"])
@login_required
def likert_create():
    try:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        labels = data.get("labels") or []  # [{position:int, text:str}, ...]

        if not name or not labels:
            return jsonify({"ok": False, "error": "name and labels required"}), 400

        engine = get_db_engine()
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    EXEC dbo.FlaskFormBuilderHelper
                         @Request='CreateLikertScale',
                         @ScaleName=:n,
                         @LabelsJSON=:lbls;
                """),
                {"n": name, "lbls": json.dumps(labels)}
            ).fetchone()

        return jsonify({"ok": True, "id": int(row.LikertScaleID)})

    except Exception as e:
        current_app.logger.exception("likert_create failed")
        try:
            log_alert(
                email=session.get("user_email"),
                role=session.get("user_role"),
                entity_id=session.get("user_id"),
                link=url_for("survey_bp.likert_create", _external=True),
                message=f"POST /survey/likert-scales failed: {e}\n{traceback.format_exc()}"[:4000],
            )
        except Exception:
            pass
        return jsonify({"ok": False, "error": "Failed to create Likert scale"}), 500
