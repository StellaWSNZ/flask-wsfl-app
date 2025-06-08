from flask import Blueprint, render_template, request, redirect, flash, session
from sqlalchemy import text
from app.utils.database import get_db_engine
from collections import namedtuple
from app.routes.auth import login_required
import traceback

survey_bp = Blueprint("survey_bp", __name__)

@survey_bp.route("/survey/<string:routename>")
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
def submit_survey(routename):
    try:
        engine = get_db_engine()
        form_data = request.form.to_dict()
        print("📥 Raw form data:", form_data)

        email = session.get("user_email")
        if not email:
            print("⚠️ Email is missing from form data!")
            return "Email required", 400

        # Extract question responses (e.g., q1, q2, ...)
        responses = {k[1:]: v for k, v in form_data.items() if k.startswith("q")}
        print("📝 Parsed responses:", responses)

        with engine.begin() as conn:
            # Get the SurveyID using the route name
            result = conn.execute(text("""
                EXEC SVY_GetSurveyIDByRouteName @RouteName = :routename
            """), {"routename": routename})
            
            row = result.fetchone()
            result.fetchall()  # clean up remaining cursor
            result.close()

            if not row:
                return f"Survey '{routename}' not found", 400

            survey_id = row.SurveyID

            # 🔹 Step 1: Insert respondent (via SP)
            conn.execute(text("""
                EXEC SVY_InsertRespondent 
                    @SurveyID = :survey_id,
                    @Email = :email,
                    @RespondentID = NULL;
            """), {"survey_id": survey_id, "email": email})

            # 🔹 Step 2: Get the respondent ID (via SP)
            respondent_result = conn.execute(text("""
                EXEC SVY_GetRespondentID 
                    @SurveyID = :survey_id,
                    @Email = :email;
            """), {"survey_id": survey_id, "email": email})

            respondent_id = respondent_result.scalar()
            print("🆔 Respondent ID:", respondent_id)

            if not respondent_id:
                raise Exception("❌ Could not retrieve RespondentID after insertion")

            # 🔹 Step 3: Insert answers
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

@survey_bp.route("/mysurveys")
@login_required
def list_my_surveys():
    email = session.get("user_email")
    engine = get_db_engine()

    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                EXEC SVY_GetSurveysCompletedByUser @Email = :email
            """), {"email": email}).fetchall()

        return render_template("survey_list.html", surveys=rows)

    except Exception as e:
        print("❌ Failed to load survey list:")
        import traceback; traceback.print_exc()
        return "Internal Server Error", 500


@survey_bp.route("/mysurvey/<int:respondent_id>")
@login_required
def view_my_survey_response(respondent_id):
    email = session.get("user_email")
    engine = get_db_engine()

    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                EXEC SVY_GetSurveyResponseByRespondentID @RespondentID = :rid
            """), {"rid": respondent_id}).fetchall()

            if not rows:
                return "Survey response not found or incomplete."

            questions = []

            for row in rows:
                (respondent_id, email, submitted_date, survey_id, qid, qtext,
                 qcode, answer_likert, answer_text) = row

                question = {
                    "id": qid,
                    "text": qtext,
                    "type": qcode,
                    "answer_likert": answer_likert,
                    "answer_text": answer_text,
                    "labels": []
                }

                # Add Likert labels if needed
                if qcode == "LIK":
                    label_rows = conn.execute(text("""
                        EXEC SVY_GetLikertLabelsByQuestionID @QuestionID = :qid
                    """), {"qid": qid}).fetchall()

                    question["labels"] = [(pos, label) for pos, label in label_rows]

                questions.append(question)

        return render_template("survey_view.html", questions=questions)

    except Exception as e:
        print("❌ Failed to load response:")
        import traceback; traceback.print_exc()
        return "Internal Server Error", 500
