from flask import Blueprint, render_template, request, redirect, flash, session
from sqlalchemy import text
from app.utils.database import get_db_engine
from collections import namedtuple

survey_bp = Blueprint("survey_bp", __name__)

@survey_bp.route("/survey/<int:survey_id>")
def survey(survey_id):
    engine = get_db_engine()
    Label = namedtuple("Label", ["pos", "text"])
    questions = []
    seen_ids = {}

    with engine.connect() as conn:
        rows = conn.execute(text("EXEC SVY_GetSurveyQuestions @SurveyID = :survey_id"),
                            {"survey_id": survey_id}).fetchall()

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

    return render_template("survey_form.html", questions=questions)

@survey_bp.route("/submit", methods=["POST"])
def submit_survey():
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
        print("📨 Inserting respondent with email:", email)

        with engine.begin() as conn:
            # Step 1: Get new RespondentID
            respondent_result = conn.execute(text("""
                DECLARE @RespondentID INT;
                EXEC SVY_InsertRespondent @Email = :email, @RespondentID = @RespondentID OUTPUT;
                SELECT @RespondentID as RespondentID;
            """), {"email": email})
            respondent_id = respondent_result.scalar()
            print("🆔 Respondent ID:", respondent_id)

            # Step 2: Loop and insert answers
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
        print("❌ Submission failed:", e)
        return "Internal Server Error", 500