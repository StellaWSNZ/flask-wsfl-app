# app/add_user.py
from flask import (
    Blueprint, current_app, render_template, request,
    redirect, session, url_for, flash, abort
)
import os
from sqlalchemy import create_engine, text
from app.routes.auth import login_required
from app.utils.database import get_db_engine
from dotenv import load_dotenv
from app.utils.wsfl_email import create_user_and_send, send_account_invites, temp_password
import bcrypt
load_dotenv()

# --- DB engine for dropdown queries (schools) ---
DB_URL = os.getenv("DB_URL")
engine = create_engine(DB_URL, connect_args={"TrustServerCertificate": "yes"})

# No URL prefix
user_bp = Blueprint("add_user", __name__)

def _clean(s: str) -> str:
    return (s or "").strip()

    
@user_bp.route("/add-user", methods=["GET", "POST"])
@login_required
def add_user():
    try:
        # create engine once for this request
        engine = get_db_engine()

        if request.method == "POST":
            form = request.form

            # --- find all row indices based on "first_name_N" keys ---
            indices = {
                key.split("_")[-1]
                for key in form.keys()
                if key.startswith("first_name_")
            }

            if not indices:
                flash("Please add at least one staff member.", "danger")
                return redirect(url_for("add_user.add_user"))

            role          = session.get("user_role") or "UNKNOWN"
            is_user_admin = int(session.get("user_admin") or 0)

            # Who can grant admin rights at all
            can_grant_admin = (
                role in ("ADM", "MOE") or
                (role == "FUN" and is_user_admin == 1)
            )

            # Who is sending the invites (for the email template)
            requested_by = (
                session.get("desc")
                or session.get("user_email")
                or "Water Skills for Life"
            )
            from_org = None  # tweak if you have funder/provider/school name in session

            # Build two recipient lists: admins and non-admins
            admin_recipients = []
            standard_recipients = []

            row_errors = 0

            for idx in sorted(indices, key=int):
                first = _clean(form.get(f"first_name_{idx}"))
                sur   = _clean(form.get(f"surname_{idx}"))
                email = (_clean(form.get(f"email_{idx}")) or "").lower()
                moe   = _clean(form.get(f"moe_number_{idx}"))
                raw_admin = (form.get(f"is_admin_{idx}") or "").strip().lower()

                # Optionally skip rows that are completely blank
                if not any([first, sur, email, moe]):
                    continue

                # Basic validation per row
                if not (first and sur and email and moe and moe.isdigit()):
                    row_errors += 1
                    flash(
                        f"Row {int(idx) + 1}: please provide first name, surname, "
                        "a valid email, and select a school.",
                        "danger",
                    )
                    continue

                # Did they tick "Admin?" for this row?
                requested_admin = raw_admin in ("on", "1", "true", "yes")

                if requested_admin and not can_grant_admin:
                    current_app.logger.warning(
                        "Denied admin grant attempt: user=%s role=%s target_moe=%s (row %s)",
                        session.get("email"), role, moe, idx
                    )
                    # treat as standard user instead
                    requested_admin = False

                is_admin = 1 if requested_admin else 0

                # ---- CREATE or UPDATE USER in DB FIRST ----
                try:
                    temp_pw = temp_password(sur, moe)  # or (first, moe)
                    hashed  = bcrypt.hashpw(temp_pw.encode(), bcrypt.gensalt()).decode()

                    with engine.begin() as conn:
                        conn.execute(
                            text(
                                """
                                EXEC dbo.AddOrUpdateSchoolUser
                                     @FirstName = :first,
                                     @Surname   = :sur,
                                     @Email     = :email,
                                     @MOENumber = :moe,
                                     @Hash      = :hash,
                                     @Admin     = :admin
                                """
                            ),
                            {
                                "first": first,
                                "sur":   sur,
                                "email": email,
                                "moe":   int(moe),
                                "hash":  hashed,
                                "admin": is_admin,
                            },
                        )
                except Exception:
                    current_app.logger.exception(
                        "Failed to create/update school user %s (row %s)", email, idx
                    )
                    row_errors += 1
                    flash(
                        f"Row {int(idx) + 1}: there was a problem creating this user in the database.",
                        "danger",
                    )
                    # Don't add to invite list if we couldn't create the user
                    continue

                # ---- If DB insert/update succeeded, queue invite ----
                rec = {
                    "email": email,
                    "firstname": first,
                    "role": "MOE",   # all school staff are MOE users here
                }

                if is_admin:
                    admin_recipients.append(rec)
                else:
                    standard_recipients.append(rec)

            total_sent = 0
            total_failed = 0

            # Send admin invites (if any)
            if admin_recipients:
                sent_a, failed_a = send_account_invites(
                    admin_recipients,
                    make_admin=True,
                    invited_by_name=requested_by,
                    invited_by_org=from_org,
                )
                total_sent += sent_a
                total_failed += failed_a

            # Send standard invites (if any)
            if standard_recipients:
                sent_s, failed_s = send_account_invites(
                    standard_recipients,
                    make_admin=False,
                    invited_by_name=requested_by,
                    invited_by_org=from_org,
                )
                total_sent += sent_s
                total_failed += failed_s

            if total_sent > 0:
                msg = f"{total_sent} invite(s) sent."
                if total_failed:
                    msg += f" {total_failed} invite(s) could not be sent."
                flash(msg, "success" if total_failed == 0 else "warning")
            else:
                flash("No invites were sent. Please check the form.", "warning")

            if row_errors:
                flash(f"{row_errors} row(s) had missing or invalid data and were skipped.", "warning")

            return redirect(url_for("add_user.add_user"))

        # -----------------------------
        # GET: populate dropdown
        # -----------------------------
        schools = []
        try:
            with engine.begin() as conn:
                schools = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = 'SchoolDropdownAll'")
                ).fetchall()
        except Exception as fetch_err:
            current_app.logger.warning(f"School dropdown load failed: {fetch_err}")
            flash(
                "Couldn't load the school list. You can still fill the form, or try again later.",
                "warning",
            )

        return render_template("add_user.html", schools=schools)

    except Exception as e:
        err_msg = str(e)
        current_app.logger.exception("❌ add_user() route failed")

        # Log alert ONLY on POST to avoid GET-render loops spamming the DB
        try:
            if request.method == "POST":
                engine = get_db_engine()
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            EXEC AUD_Alerts_Insert
                                 @Email        = :Email,
                                 @RoleCode     = :RoleCode,
                                 @EntityID     = :id,
                                 @Link         = :Link,
                                 @ErrorMessage = :ErrorMessage
                        """),
                        {
                            "Email": (session.get("email") or "")[:320],
                            "RoleCode": (session.get("user_role") or "")[:10],
                            "id": session.get("user_id"),
                            "Link": str(request.url)[:2048],
                            "ErrorMessage": err_msg,
                        },
                    )
        except Exception as log_err:
            current_app.logger.exception(f"⚠️ Failed to log alert")

        flash("An unexpected error occurred. The issue has been logged.", "danger")
        return abort(500)
