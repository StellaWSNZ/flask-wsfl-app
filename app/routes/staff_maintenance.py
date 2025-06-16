from flask import Blueprint, render_template, session, redirect, url_for, flash, request
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine  # adjust path as needed
from app.routes.auth import login_required
from app.utils.email import send_account_setup_email
from app.extensions import mail
staff_bp = Blueprint("staff_bp", __name__)

import sys
from flask import Blueprint, render_template, session
import pandas as pd
from sqlalchemy import text
from app.utils.database import get_db_engine
import traceback
staff_bp = Blueprint("staff_bp", __name__)

@staff_bp.route("/staff_maintenance")
@login_required
def staff_maintenance():
    user_id = session.get("user_id")
    user_role = session.get("user_role")

    if not user_id or not user_role:
        return "Unauthorized", 403

    engine = get_db_engine()
    with engine.connect() as conn:
        # print("Connected to DB")
        sys.stdout.flush()

        try:
            with engine.begin() as conn:
                result = conn.execute(
                    text("EXEC FlaskGetStaffDetails @RoleType = :role, @ID = :id, @Email = :email"),
                    {
                        "role": user_role.upper(),
                        "id": int(user_id),
                        "email": session["user_email"]
                    }
                )

                rows = result.fetchall()
            sys.stdout.flush()

            data = pd.DataFrame(rows, columns=result.keys())
            sys.stdout.flush()
            # print(data)
            return render_template("staff_maintenance.html", data=data.to_dict(orient="records"), columns=data.columns, name = session.get('desc'))


        except Exception as e:
            print("❌ Exception occurred:", e)
            sys.stdout.flush()
            return f"Error: {e}", 500


@staff_bp.route('/update_staff', methods=['POST'])
@login_required
def update_staff():
    try:
        user_id = request.form.get("user_id")
        firstname = request.form.get("firstname")
        lastname = request.form.get("lastname")
        email = request.form.get("email")
        old_email = request.form.get("old_email")
        admin = 1 if request.form.get("admin") == "1" else 0
        role = session.get("role")
        id_from_session = session.get("id")

        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskUpdateStaffDetails 
                        @OldEmail = :old_email,
                        @NewEmail = :new_email,
                        @FirstName = :firstname,
                        @LastName = :lastname,
                        @Admin = :admin,
                @PerformedByEmail = :performed_by
                """),
                {
                    "old_email": old_email,
                    "new_email": email,
                    "firstname": firstname,
                    "lastname": lastname,
                    "admin": admin,
            "performed_by": session["user_email"]
                }
            )

        flash("Staff member updated successfully.", "success")
    except Exception as e:
        print("❌ Error in update_staff:")
        print(traceback.format_exc())
        flash("An error occurred while updating staff details.", "danger")

    return redirect(url_for("staff_bp.staff_maintenance"))

@staff_bp.route('/invite_user', methods=['POST'])
@login_required
def invite_user():
    try:
        # print("📥 Received form data:", request.form)

        email = request.form['email'].strip().lower()
        admin = int(request.form['admin'])
        first_name = request.form['firstname']
        role = session.get("user_role")
        if not role:
            raise ValueError("Missing user_role in session")

        
        invited_by = session.get('user_firstname') + ' ' + session.get('user_surname')
        inviter_desc = session.get('desc')


        engine = get_db_engine()
        with engine.begin() as conn:
            print("🛠️ Inserting user into DB...")
            conn.execute(
                text("EXEC FlaskInviteUser @Email = :email, @Admin = :admin, @PerformedByEmail = :performed_by"),
                {
                    "email": email,
                    "admin": admin,
                    "performed_by": session["user_email"]
                }
            )

        # print("📨 Sending account setup email...")
        send_account_setup_email(
            mail = mail,
            recipient_email=email,
            first_name=first_name,
            role=role,
            invited_by_name=invited_by,
            inviter_desc=inviter_desc,
            is_admin=(admin == 1)
        )

        flash(f"✅ Invitation sent to {email}.", "success")
        return redirect(url_for('staff_bp.staff_maintenance'))

    except Exception as e:
        print("🚨 Error in /invite_user:", e)
        flash("⚠️ Failed to invite user. Please check the logs.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance'))


    
@staff_bp.route('/add_staff', methods=['POST'])
@login_required
def add_staff():
    email = request.form['email'].strip().lower()
    firstname = request.form['firstname'].strip()
    lastname = request.form['lastname'].strip()
    selected_role = session.get("user_role")
    selected_id = session.get("user_id")
    account_status = request.form['account_status']
    admin = 1 if request.form.get("admin") == "1" else 0
    active = 1 if account_status == "enable" else 0

    hashed_pw = None
    send_email = account_status == "enable"
    user_desc = session.get("desc")

    engine = get_db_engine()

    with engine.begin() as conn:
        existing = conn.execute(
            text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
            {"Request": "CheckEmailExists", "Text": email}
        ).fetchone()

        if existing:
            flash("⚠️ Email already exists.", "warning")
            return redirect(url_for('staff_bp.staff_maintenance'))

    with engine.begin() as conn:
         conn.execute(
        text("""EXEC FlaskInsertUser 
                @Email = :email,
                @HashPassword = :hash,
                @Role = :role,
                @ID = :id,
                @FirstName = :firstname,
                @Surname = :surname,
                @Admin = :admin,
                @Active = :active,
                  @PerformedByEmail = :performed_by"""),
        {
            "email": email,
            "hash": hashed_pw,
            "role": selected_role,
            "id": selected_id,
            "firstname": firstname,
            "surname": lastname,
            "admin": admin,
            "active": active,
        "performed_by": session["user_email"]
        }
    )

    flash(f"✅ User {email} created.", "success")

    if send_email:
        send_account_setup_email(
            mail=mail,
            recipient_email=email,
            first_name=firstname,
            role=selected_role,
            is_admin=admin,
            invited_by_name=f"{session.get('user_firstname')} {session.get('user_surname')}",
            inviter_desc=user_desc
        )

    return redirect(url_for('staff_bp.staff_maintenance'))


@staff_bp.route('/disable_user', methods=['POST'])
def disable_user():
    email = request.form.get('email')
    if not email:
        flash("Missing email address.", "danger")
        return redirect(url_for('staff_bp.staff_maintenance'))

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(
                text("EXEC FlaskDisableUser :Email, :PerformedBy"),
                {
                    "Email": email,
                    "PerformedBy": session.get("user_email")
                }
            )
            conn.commit()
        flash("User has been disabled successfully.", "success")
    except Exception as e:
        print(f"❌ Error in /disable_user: {e}")
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for('staff_bp.staff_maintenance'))
