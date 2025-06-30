## Survey.py
from flask import Blueprint, render_template, request, redirect, flash, session, url_for, current_app
from sqlalchemy import text
from app.utils.database import get_db_engine
from collections import namedtuple
from app.routes.auth import login_required
import traceback
from app.extensions import mail
from itsdangerous import URLSafeTimedSerializer,BadSignature, SignatureExpired
survey_bp = Blueprint("survey_bp", __name__)


# 🔹 Load and show a survey form by its route name
@survey_bp.route("/Form/<string:routename>")
def survey_by_routename(routename):
    engine = get_db_engine()
    Label = namedtuple("Label", ["pos", "text"])
    questions = []
    seen_ids = {}

    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename
            """), {"routename": routename})

            row = result.fetchone()
            result.fetchall()
            result.close()

            if not row:
                flash(f"Survey '{routename}' not found.", "danger")
                return redirect("/Profile")

            survey_id = row.SurveyID

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

        return render_template(f"survey_form_{survey_id}.html", questions=questions, route_name=routename)

    except Exception:
        traceback.print_exc()
        return "Internal Server Error: Failed to load survey", 500


@survey_bp.route("/submit/<string:routename>", methods=["POST"])
def submit_survey(routename):
    try:
        engine = get_db_engine()
        form_data = request.form.to_dict()

        email = session.get("user_email")
        if not email:
            return "Email required", 400

        responses = {k[1:]: v for k, v in form_data.items() if k.startswith("q")}

        with engine.begin() as conn:
            # 🔹 Get survey ID
            if routename.startswith("guest/") and routename.split("/")[1].isdigit():
                survey_id = int(routename.split("/")[1])
            else:
                result = conn.execute(text("""
                    EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename
                """), {"routename": routename})

                row = result.fetchone()
                result.fetchall()
                result.close()

                if not row:
                    return f"Survey '{routename}' not found", 400

                survey_id = row.SurveyID

            # 🔹 Insert respondent
            conn.execute(text("""
                EXEC SVY_InsertRespondent 
                    @SurveyID = :survey_id,
                    @Email = :email,
                    @RespondentID = NULL;
            """), {"survey_id": survey_id, "email": email})

            # 🔹 Get respondent ID
            respondent_result = conn.execute(text("""
                EXEC SVY_GetRespondentID 
                    @SurveyID = :survey_id,
                    @Email = :email;
            """), {"survey_id": survey_id, "email": email})

            respondent_id = respondent_result.scalar()
            if not respondent_id:
                raise Exception("❌ Could not retrieve RespondentID")

            # 🔹 Get question types
            question_types_result = conn.execute(text("""
                EXEC SVY_GetQuestionTypesBySurveyID @SurveyID = :sid
            """), {"sid": survey_id}).fetchall()
            question_type_map = {str(row.QuestionID): row.QuestionCode for row in question_types_result}

            # 🔹 Insert answers
            for qid_str, value in responses.items():
                qtype = question_type_map.get(qid_str)
                qid = int(qid_str)

                if qtype == "LIK":
                    if value:
                        conn.execute(text("""
                            EXEC SVY_InsertAnswer 
                                @RespondentID = :rid, 
                                @QuestionID = :qid, 
                                @AnswerLikert = :val;
                        """), {"rid": respondent_id, "qid": qid, "val": int(value)})
                else:
                    if value is not None:
                        conn.execute(text("""
                            EXEC SVY_InsertAnswer 
                                @RespondentID = :rid, 
                                @QuestionID = :qid, 
                                @AnswerText = :val;
                        """), {"rid": respondent_id, "qid": qid, "val": value})

        flash("✅ Survey submitted successfully!", "success")
        if routename.startswith("Form/guest"):
            return redirect("/thankyou")
        return redirect(url_for("survey_bp.list_my_surveys"))

    except Exception:
        traceback.print_exc()
        return "Internal Server Error", 500


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
                        EXEC SVY_InsertAnswer 
                            @RespondentID = :rid, 
                            @QuestionID = :qid, 
                            @AnswerLikert = :val;
                    """), {"rid": respondent_id, "qid": qid, "val": int(value)})
                else:
                    conn.execute(text("""
                        EXEC SVY_InsertAnswer 
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


# 🔹 View a specific completed survey by respondent ID
# 🔹 View a specific completed survey by respondent ID
@survey_bp.route("/MyForms/<int:respondent_id>")
@login_required
def view_my_survey_response(respondent_id):
    session_email = session.get("user_email")
    is_admin = session.get("user_role") == "ADM"

    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            # Get all questions and answers (including unanswered)
            rows = conn.execute(text("""
                EXEC SVY_GetSurveyResponseByRespondentID @RespondentID = :rid
            """), {"rid": respondent_id}).fetchall()

            if not rows:
                flash("Survey response not found.", "warning")
                return redirect(url_for("survey_bp.list_my_surveys"))

            response_email = rows[0][1]

            questions = {}
            for row in rows:
                (_, _, _, _, qid, qtext, qcode, answer_likert, answer_text) = row

                if qid not in questions:
                    question = {
                        "id": qid,
                        "text": qtext,
                        "type": qcode,
                        "answer_likert": answer_likert,
                        "answer_text": answer_text,
                        "labels": []
                    }

                    # For Likert questions, load all labels
                    if qcode == "LIK":
                        label_rows = conn.execute(text("""
                            EXEC SVY_GetLikertLabelsByQuestionID @QuestionID = :qid
                        """), {"qid": qid}).fetchall()
                        question["labels"] = [(pos, label) for pos, label in label_rows]

                    questions[qid] = question
                else:
                    # Just in case, update answers if found in another row
                    if answer_likert and not questions[qid]["answer_likert"]:
                        questions[qid]["answer_likert"] = answer_likert
                    if answer_text and not questions[qid]["answer_text"]:
                        questions[qid]["answer_text"] = answer_text

        return render_template("survey_view.html", questions=list(questions.values()))

    except Exception:
        traceback.print_exc()
        return "Internal Server Error", 500

@survey_bp.route("/Form/invite/<token>")
def survey_invite_token(token):
    try:
        s = URLSafeTimedSerializer(current_app.secret_key)
        data = s.loads(token, max_age=86400)  # 1 day
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

@survey_bp.route("/Form/id/<int:survey_id>")
def survey_by_id(survey_id):
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

        return render_template(f"survey_form_{survey_id}.html", questions=questions, route_name=f"id/{survey_id}")

    except Exception:
        traceback.print_exc()
        return "Internal Server Error: Failed to load survey", 500
    
    
from flask import request, redirect, url_for, flash
from app.utils.custom_email import send_survey_invite_email

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