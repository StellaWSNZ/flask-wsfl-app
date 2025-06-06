from flask import Blueprint, render_template, request, redirect, flash, session
from sqlalchemy import text
from app.utils.database import get_db_engine
from collections import namedtuple
from app.routes.auth import login_required
import traceback

survey_bp = Blueprint("survey_bp", __name__)

@survey_bp.route("/survey/<string:routename>")
@login_required
def survey_by_routename(routename):
    engine = get_db_engine()
    Label = namedtuple("Label", ["pos", "text"])
    questions = []
    seen_ids = {}

    try:
        with engine.connect() as conn:
            # Get SurveyID by RouteName
            result = conn.execute(text("""
                EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename
            """), {"routename": routename})

            row = result.fetchone()
            result.fetchall()  # ✅ consume remaining rows
            result.close()     # ✅ ensure connection is clean

            if not row:
                flash(f"Survey '{routename}' not found.", "danger")
                return redirect("/profile")

            survey_id = row.SurveyID

            # Now get the actual survey questions
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
            else:
                question = seen_ids[qid]

            if qcode == "LIK" and label:
                question["labels"].append(Label(pos, label))

        return render_template("survey_form.html", questions=questions, route_name=routename)

    except Exception as e:
        print("❌ Error loading survey form:")
        traceback.print_exc()
        return "Internal Server Error: Failed to load survey", 500


@survey_bp.route("/submit/<string:routename>", methods=["POST"])
@login_required
def submit_survey(routename):
    try:
        engine = get_db_engine()
        form_data = request.form.to_dict()
        print("📥 Raw form data:", form_data)

        email = session.get("user_email")
        if not email:
            print("⚠️ Email is missing from form data!")
            return "Email required", 400

        responses = {k[1:]: v for k, v in form_data.items() if k.startswith("q")}
        print("📝 Parsed responses:", responses)

        with engine.begin() as conn:
            # Get SurveyID from RouteName
            result = conn.execute(text("""
                EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename
            """), {"routename": routename})
            row = result.fetchone()
            result.fetchall()
            result.close()

            if not row:
                return f"Survey '{routename}' not found", 400

            survey_id = row.SurveyID

            # Insert respondent
            respondent_result = conn.execute(text("""
                DECLARE @RespondentID INT;
                EXEC SVY_InsertRespondent @Email = :email, @RespondentID = @RespondentID OUTPUT;
                SELECT @RespondentID as RespondentID;
            """), {"email": email})
            respondent_id = respondent_result.scalar()
            print("🆔 Respondent ID:", respondent_id)

            # Insert answers
            for qid_str, value in responses.items():
                qid = int(qid_str)
                print(f"➕ Inserting answer: Question {qid}, Value = {value}")
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
        return redirect("/profile")

    except Exception as e:
        print("❌ Submission failed:")
        traceback.print_exc()
        return f"Internal Server Error: {e}", 500
