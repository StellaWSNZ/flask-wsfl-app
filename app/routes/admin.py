# app/routes/admin.py

from datetime import datetime, timedelta
from math import ceil
import traceback
import bcrypt
import pandas as pd
import math
import sqlalchemy.sql
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from user_agents import parse as ua_parse
from werkzeug.security import generate_password_hash
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.extensions import mail
from app.routes.auth import login_required
from app.utils.custom_email import send_account_setup_email
from app.utils.database import get_db_engine, log_alert, get_terms, get_years

# Blueprint
admin_bp = Blueprint("admin_bp", __name__)

@admin_bp.route('/CreateUser', methods=['GET', 'POST'])
@login_required
def create_user():
    try:
        # ---- perms --------------------------------------------------------
        if not session.get("user_admin"):
            return render_template(
                "error.html",
                error="You are not authorised to view that page.",
                code=403
            ), 403

        # ---- setup --------------------------------------------------------
        engine    = get_db_engine()
        user_role = session.get("user_role")
        user_id   = session.get("user_id")
        desc      = session.get("desc")

        # dev-only failure switch
        FAIL_FLAG = (request.args.get("__fail") or request.form.get("__fail"))
        if not current_app.debug:
            FAIL_FLAG = None
        if current_app.debug and request.args.get("__bubble"):
            raise  # surface error in debugger
        if FAIL_FLAG == "init":
            raise RuntimeError("Forced init failure (CreateUser)")

        funder = None
        funders, providers, schools, groups = [], [], [], []
        only_own_staff_or_empty = False

        # ---- optional header context for FUN ------------------------------
        if user_role == "FUN":
            with engine.connect() as conn:
                funder = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :fid"),
                    {"Request": "FunderByID", "fid": user_id}
                ).fetchone()

        # ---- POST: create user -------------------------------------------
        if request.method == "POST":
            email           = (request.form.get("email") or "").strip()
            firstname       = (request.form.get("firstname") or "").strip()
            surname         = (request.form.get("surname") or "").strip()
            send_email      = request.form.get("send_email") == "on"
            admin_flag      = 1 if request.form.get("admin") == "1" else 0
            selected_role   = (request.form.get("selected_role") or "").strip()
            selected_id_raw = request.form.get("selected_id")

            try:
                selected_id = int(selected_id_raw)
            except (TypeError, ValueError):
                selected_id = None

            
            

            hashed_pw = None  # invite-only; SP handles NULL

            with engine.begin() as conn:
                existing = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text"),
                    {"Request": "CheckEmailExists", "Text": email}
                ).fetchone()

                if existing:
                    flash("⚠️ Email already exists.", "warning")
                    return redirect(url_for("admin_bp.create_user"))  # ok to redirect here

                # create the user
                conn.execute(
                    text("""
                        EXEC FlaskInsertUser 
                            @Email = :email,
                            @HashPassword = :hash,
                            @Role = :role,
                            @ID = :id,
                            @FirstName = :firstname,
                            @Surname = :surname,
                            @Admin = :admin,
                            @Active = :active
                    """),
                    {
                        "email": email,
                        "hash": hashed_pw,
                        "role": selected_role,
                        "id": selected_id,
                        "firstname": firstname,
                        "surname": surname,
                        "admin": admin_flag,
                        "active": 1
                    }
                )

            flash(f"✅ User {email} created.", "success")

            if send_email:
                try:
                    send_account_setup_email(
                        mail=mail,
                        recipient_email=email,
                        first_name=firstname,
                        role=selected_role,
                        is_admin=admin_flag,
                        invited_by_name=f"{session.get('user_firstname')} {session.get('user_surname')}",
                        inviter_desc=desc
                    )
                except Exception as mail_err:
                    current_app.logger.warning(f"Email send failed for {email}: {mail_err}")
                    flash("User created, but the email could not be sent.", "warning")

            return redirect(url_for("admin_bp.create_user"))

        # ---- GET: render --------------------------------------------------
        return render_template(
            "create_user.html",
            user_role=user_role,
            name=desc,
            funder=funder,
            funders=funders,
            providers=providers,
            schools=schools,
            groups=groups,
            only_own_staff_or_empty=only_own_staff_or_empty
        )

    except Exception as e:
        # ---- universal catcher -------------------------------------------
        current_app.logger.exception("❌ create_user() failed")

        # best-effort DB alert; only on POST to avoid loops spamming alerts
        try:
            if request.method == "POST":
                sel_id = request.form.get("selected_id")
                try:
                    sel_id = int(sel_id) if sel_id is not None else None
                except ValueError:
                    sel_id = None

                log_alert(
                    email=(session.get("user_email") or session.get("email") or "")[:320],
                    role=(session.get("user_role") or "")[:10],
                    entity_id=sel_id,
                    link=str(request.url)[:2048],   # avoid NVARCHAR(2048) overflow
                    message=str(e)
                )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in CreateUser: {log_err}")

        flash("An unexpected error occurred. The issue has been logged.", "danger")
        return abort(500)  




@admin_bp.route("/Profile")
@login_required
def profile():
    try:
        # --- hard-coded test raise (works only in DEBUG so prod is safe) ---
        FAIL_FLAG = request.args.get("__fail")
        if current_app.debug and FAIL_FLAG == "test":
            raise RuntimeError("Forced test error from /Profile for alert logging verification")

        # ---------- session-derived fields ----------
        try:
            last_login_raw = session.get("last_login_nzt")
            formatted_login = (
                datetime.fromisoformat(last_login_raw).strftime('%A, %d %B %Y, %I:%M %p')
                if last_login_raw else "Unknown"
            )
        except (ValueError, TypeError):
            formatted_login = "Unknown"

        user_email     = session.get("user_email")
        user_email_alt = session.get("user_email_alt")
        display_email  = user_email_alt or user_email
        
        user_info = {
            "email": user_email,
            "role": session.get("user_role"),
            "admin": session.get("user_admin"),
            "email_alt": user_email_alt,
            "firstname": session.get("user_firstname"),
            "surname": session.get("user_surname"),
            "desc": session.get("desc"),
            "last_login": formatted_login,
            "school_address": session.get("school_address"),
            "school_town": session.get("school_town"),
            "school_lat": session.get("school_lat"),
            "school_lon": session.get("school_lon"),
            "school_type": session.get("school_type"),
            "user_id": session.get("user_id"),
            "user_type_desc": session.get("school_type_desc"),
            "funder_lat": session.get("funder_lat"),
            "funder_lon": session.get("funder_lon"),
            "funder_address": session.get("funder_address"),
            "provider_lat": session.get("provider_lat"),
            "provider_lon": session.get("provider_lon"),
            "provider_address": session.get("provider_address"),
            "last_self_review": None,
            "last_self_review_overdue": False,
        }

        # ---------- DB lookups ----------
        school_type_options, eLearning_status = [], []
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("EXEC FlaskHelperFunctions 'SchoolTypeDropdown'"))
            school_type_options = [dict(row._mapping) for row in result]

            result = conn.execute(
                text("EXEC GetElearningStatus :Email"),
                {"Email": display_email}
            )
            eLearning_status = [dict(row._mapping) for row in result]

            last_row = conn.execute(
                text("EXEC SVY_LatestSelfReveiw :Email, :AltEmail"),
                {"Email": user_email, "AltEmail": user_email_alt}
            ).fetchone()

            last_dt = last_row[0] if last_row else None
            if isinstance(last_dt, datetime):
                user_info["last_self_review"] = last_dt.strftime('%A, %d %B %Y')
                user_info["last_self_review_overdue"] = (datetime.now() - last_dt) > timedelta(days=90)

        return render_template(
            "profile.html",
            user=user_info,
            school_type_options=school_type_options,
            eLearning_status=eLearning_status
        )

    except Exception as e:
        current_app.logger.exception("❌ profile() failed")
        log_alert(
            email=session.get("user_email") or session.get("email"),
            role=session.get("user_role"),
            entity_id=session.get("user_id"),
            link=request.url,
            message=str(e)
        )
        flash("We couldn’t load your profile completely. The issue has been logged.", "warning")
        # Render with safe fallbacks
        return render_template(
            "profile.html",
            user={
                "email": session.get("user_email"),
                "role": session.get("user_role"),
                "admin": session.get("user_admin"),
                "email_alt": session.get("user_email_alt"),
                "firstname": session.get("user_firstname"),
                "surname": session.get("user_surname"),
                "desc": session.get("desc"),
                "last_login": "Unknown",
                "school_address": session.get("school_address"),
                "school_town": session.get("school_town"),
                "school_lat": session.get("school_lat"),
                "school_lon": session.get("school_lon"),
                "school_type": session.get("school_type"),
                "user_id": session.get("user_id"),
                "user_type_desc": session.get("school_type_desc"),
                "funder_lat": session.get("funder_lat"),
                "funder_lon": session.get("funder_lon"),
                "funder_address": session.get("funder_address"),
                "provider_lat": session.get("provider_lat"),
                "provider_lon": session.get("provider_lon"),
                "provider_address": session.get("provider_address"),
                "last_self_review": None,
                "last_self_review_overdue": False,
            },
            school_type_options=[],
            eLearning_status=[]
        )


@admin_bp.route("/update_profile", methods=["POST"])
@login_required
def update_profile():
    

    # ---- read & clean form ----
    original_email = (request.form.get("original_email") or "").strip()
    new_firstname  = (request.form.get("firstname") or "").strip()
    new_surname    = (request.form.get("surname") or "").strip()
    new_email      = (request.form.get("email") or "").strip()
    new_email_alt  = (request.form.get("alt_email") or "").strip()

    funder_address = (request.form.get("funder_address") or "").strip()
    funder_lat     = (request.form.get("funder_lat") or "").strip()
    funder_lon     = (request.form.get("funder_lon") or "").strip()

    school_address = (request.form.get("school_address") or "").strip()
    school_town    = (request.form.get("school_town") or "").strip()
    school_type    = (request.form.get("school_type") or "").strip()

    engine = get_db_engine()

    # Track status for user feedback
    did_user_update   = False
    did_school_update = False
    did_funder_update = False
    refreshed_session = False

    # ---- 1) Update basic user profile (separate txn) ----
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskHelperFunctionsSpecific 
                     @Request       = :request,
                     @FirstName     = :fname,
                     @Surname       = :sname,
                     @NewEmail      = :new_email,
                     @OriginalEmail = :original_email,
                     @EmailAlt      = :new_email_alt
            """), {
                "request": "UpdateUserInfo",
                "fname": new_firstname,
                "sname": new_surname,
                "new_email": new_email,
                "original_email": original_email,
                "new_email_alt": new_email_alt
            })
        did_user_update = True
    except Exception as e:
        current_app.logger.exception("❌ UpdateUserInfo failed")
        # best-effort alert; never raise from here
        try:
            log_alert(
                email=(session.get("user_email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"UpdateUserInfo failed: {str(e)[:1500]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (UpdateUserInfo): {log_err}")
        flash("We couldn’t update your basic profile details.", "danger")

    # ---- 2) Update school info (MOE admin), independent txn ----
    try:
        if (session.get("user_admin") == 1 and session.get("user_role") == "MOE"
            and school_address and school_town and school_type):
            school_id = session.get("user_id")
            if school_id:
                with engine.begin() as conn:
                    conn.execute(text("""
                        EXEC FlaskHelperFunctionsSpecific 
                             @Request     = :request,
                             @MOENumber   = :school_id,
                             @StreetAddress = :addr,
                             @TownCity    = :town,
                             @SchoolTypeID = :stype
                    """), {
                        "request": "UpdateSchoolInfo",
                        "school_id": school_id,
                        "addr": school_address,
                        "town": school_town,
                        "stype": school_type
                    })
                did_school_update = True
    except Exception as e:
        current_app.logger.exception("❌ UpdateSchoolInfo failed")
        try:
            log_alert(
                email=(session.get("user_email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"UpdateSchoolInfo failed: {str(e)[:1500]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (UpdateSchoolInfo): {log_err}")
        flash("We couldn’t update the school info.", "warning")

    # ---- 3) Update funder info (FUN admin), independent txn ----
    try:
        if (session.get("user_admin") == 1 and session.get("user_role") == "FUN"
            and funder_address and funder_lat and funder_lon):
            funder_id = session.get("user_id")
            if funder_id:
                # Convert lats/lons defensively
                try:
                    lat_val = float(funder_lat)
                    lon_val = float(funder_lon)
                except ValueError:
                    raise ValueError("Latitude/Longitude must be numeric.")

                with engine.begin() as conn:
                    conn.execute(text("""
                        EXEC FlaskHelperFunctionsSpecific 
                             @Request        = :request,
                             @FunderID       = :funder_id,
                             @FunderAddress  = :address,
                             @FunderLatitude = :lat,
                             @FunderLongitude= :lon
                    """), {
                        "request": "UpdateFunderInfo",
                        "funder_id": funder_id,
                        "address": funder_address,
                        "lat": lat_val,
                        "lon": lon_val
                    })
                did_funder_update = True
    except Exception as e:
        current_app.logger.exception("❌ UpdateFunderInfo failed")
        try:
            log_alert(
                email=(session.get("user_email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"UpdateFunderInfo failed: {str(e)[:1500]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (UpdateFunderInfo): {log_err}")
        flash("We couldn’t update the funder info.", "warning")

    # ---- 4) Refresh session (separate txn so earlier writes still stick) ----
    try:
        # Use the *new* email for login record if it changed, else original.
        lookup_email = new_email or original_email
        with engine.begin() as conn:
            updated_info = conn.execute(
                text("EXEC FlaskLoginValidation :Email"),
                {"Email": lookup_email}
            ).fetchone()

        if not updated_info:
            raise RuntimeError("FlaskLoginValidation did not return a row.")

        session["user_role"]        = updated_info.Role
        session["user_id"]          = updated_info.ID
        session["user_admin"]       = updated_info.Admin
        session["user_email"]       = updated_info.Email
        session["display_name"]     = updated_info.FirstName
        session["user_firstname"]   = updated_info.FirstName
        session["user_surname"]     = updated_info.Surname
        session["last_login_nzt"]   = str(updated_info.LastLogin_NZT)
        session["desc"]             = str(updated_info.Desc)
        session["school_address"]   = getattr(updated_info, "StreetAddress", None)
        session["school_town"]      = getattr(updated_info, "TownCity", None)
        session["school_lat"]       = getattr(updated_info, "Latitude", None)
        session["school_lon"]       = getattr(updated_info, "Longitude", None)
        session["school_type"]      = getattr(updated_info, "SchoolTypeID", None)
        session["funder_address"]   = getattr(updated_info, "Funder_Address", None)
        session["funder_lat"]       = getattr(updated_info, "Funder_Latitude", None)
        session["funder_lon"]       = getattr(updated_info, "Funder_Longitude", None)
        session["nearest_term"]     = getattr(updated_info, "CurrentTerm", None)
        session["nearest_year"]     = getattr(updated_info, "CurrentCalendarYear", None)
        session["user_email_alt"]   = getattr(updated_info, "AlternateEmail", None)

        refreshed_session = True
    except Exception as e:
        current_app.logger.exception("⚠️ Session refresh failed after profile update")
        try:
            log_alert(
                email=(session.get("user_email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"Session refresh failed: {str(e)[:1500]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (SessionRefresh): {log_err}")
        flash("Your details were saved, but we couldn’t refresh your session yet. Please reload the page.", "warning")

    # ---- Final messaging & redirect ----
    if did_user_update or did_school_update or did_funder_update:
        # success message tailored
        parts = []
        if did_user_update:   parts.append("profile")
        if did_school_update: parts.append("school")
        if did_funder_update: parts.append("funder")
        what = ", ".join(parts)
        flash(f"{what.capitalize()} details updated successfully.", "success")
    else:
        flash("No changes were saved.", "warning")

    if not refreshed_session:
        # even if session refresh failed, go back to Profile (different route, no loop).
        return redirect(url_for("admin_bp.profile"))

    return redirect(url_for("admin_bp.profile"))



# TODO: finish this
@admin_bp.route('/logo/<logo_type>/<int:logo_id>')
def serve_logo(logo_type, logo_id):
    engine = get_db_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("EXEC FlaskHelperFunctions @Request = :Request, @Text = :Text, @Number = :Number"),
            {"Request": "GetLogo", "Text": logo_type.upper(), "Number": logo_id}
        ).fetchone()

    if result:
        return Response(result[0], mimetype=result[1])
    return '', 404

@admin_bp.route('/ProviderMaintenance', methods=['GET', 'POST'])
@login_required
def provider_maintenance():
    def _safe_int(x, default=None):
        try:
            return int(x)
        except (TypeError, ValueError):
            return default

    engine    = get_db_engine()
    user_role = session.get("user_role")
    user_id   = session.get("user_id")

    if not (user_role == "ADM" or (user_role == "FUN" and session.get("user_admin") == 1)):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    selected_funder = (request.form.get("funder") or "").strip()
    selected_term   = request.form.get("term") or session.get("nearest_term")
    selected_year   = request.form.get("year") or session.get("nearest_year")

    selected_term_i   = _safe_int(selected_term)
    selected_year_i   = _safe_int(selected_year)

    if user_role == "FUN":
        selected_funder = str(user_id or "")
    selected_funder_i = _safe_int(selected_funder)

    funders, schools, providers, staff_list = [], [], [], []

    try:
        # ---------- DB work ----------
        with engine.begin() as conn:
            if user_role == "ADM":
                rows = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request"),
                    {"Request": "AllFunders"}
                ).fetchall()
                funders = [dict(r._mapping) for r in rows]

            if selected_funder_i and selected_term_i and selected_year_i:
                rows = conn.execute(text("""
                    EXEC FlaskHelperFunctionsSpecific
                         @Request = 'GetSchoolsForProviderAssignment',
                         @Term    = :term,
                         @Year    = :year,
                         @FunderID= :funder_id
                """), {
                    "term": selected_term_i,
                    "year": selected_year_i,
                    "funder_id": selected_funder_i
                }).fetchall()
                schools = [dict(r._mapping) for r in rows]

                rows = conn.execute(
                    text("EXEC FlaskHelperFunctions @Request = :Request, @Number = :funder_id"),
                    {"Request": "GetProviderByFunder", "funder_id": selected_funder_i}
                ).fetchall()
                providers = [dict(r._mapping) for r in rows]

                rows = conn.execute(
                    text("EXEC FlaskHelperFunctionsSpecific @Request = 'FunderStaff', @FunderID = :fid"),
                    {"fid": selected_funder_i}
                ).fetchall()
                staff_list = [dict(r._mapping) for r in rows]

    except Exception as e:
        current_app.logger.exception("❌ ProviderMaintenance DB load failed")

        # ---- Write error to AUD_Alerts (or use your log_alert helper) ----
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=str(e)[:2000]
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in ProviderMaintenance: {log_err}")

        flash("Couldn’t load some data for provider maintenance. The issue has been logged.", "warning")

    # ---------- Render ----------
    try:
        return render_template(
            "provider_maintenance.html",
            schools=schools,
            providers=providers,
            funders=funders,
            terms = get_terms(),
            years = get_years(),
            selected_funder=selected_funder_i,
            selected_term=selected_term_i,
            selected_year=selected_year_i,
            user_role=user_role,
            staff_list=staff_list
        )
    except Exception as e:
        current_app.logger.exception("❌ Error rendering provider_maintenance.html")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"Template render failed: {str(e)[:1500]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log render alert in ProviderMaintenance: {log_err}")
        return abort(500)


@admin_bp.route("/assign_provider", methods=["POST"])
@login_required
def assign_provider():
    try:
        data = request.get_json(silent=True) or {}
        moe_number = data.get("MOENumber")
        term       = data.get("Term")
        year       = data.get("Year")
        provider_id = data.get("ProviderID")

        # Basic validation
        if not (moe_number and term and year):
            raise ValueError("Missing required fields: MOENumber, Term, and Year are required.")

        # Normalize provider_id (allow None to unassign)
        if provider_id in ("", None):
            provider_id = None
        else:
            try:
                provider_id = int(provider_id)
            except (TypeError, ValueError):
                raise ValueError("ProviderID must be an integer or null.")

        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskHelperFunctionsSpecific
                     @Request   = 'AssignProviderToSchool',
                     @MOENumber = :moe,
                     @Year      = :year,
                     @Term      = :term,
                     @ProviderID= :pid
            """), {
                "moe": moe_number,
                "term": term,
                "year": year,
                "pid": provider_id
            })

        return jsonify(success=True)

    except Exception as e:
        current_app.logger.exception("❌ assign_provider failed")
        # ---- Write error to DB (never raise from here) ----
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"assign_provider error: {str(e)[:2000]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in assign_provider: {log_err}")

        return jsonify(success=False, error=str(e)), 500


@admin_bp.route("/add_provider", methods=["POST"])
@login_required
def add_provider():
    try:
        data = request.get_json(silent=True) or {}
        provider_name = (data.get("provider_name") or "").strip()
        funder_id_raw = data.get("funder_id")

        if not provider_name or funder_id_raw in (None, ""):
            return jsonify({"success": False, "message": "Missing data"}), 400

        try:
            funder_id = int(funder_id_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid funder_id"}), 400

        engine = get_db_engine()
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    EXEC FlaskHelperFunctions 
                         @Request = :Request,
                         @Number  = :Number,
                         @Text    = :Text
                """),
                {
                    "Request": "AddProvider",
                    "Number": funder_id,
                    "Text": provider_name
                }
            )
            row = result.mappings().fetchone()
            if not row:
                # Defensive: SP should return a row; handle if it doesn't
                raise RuntimeError("No result returned from AddProvider.")

            new_id      = row.get("NewID")
            funder_name = row.get("FunderName")

            return jsonify({
                "success": True,
                "new_id": new_id,
                "funder_name": funder_name,
                "provider_name": provider_name
            })

    except Exception as e:
        current_app.logger.exception("❌ add_provider failed")
        msg = str(e)

        # Friendly 400 if duplicate provider (match your current behavior)
        if "Provider name already exists" in msg:
            try:
                log_alert(
                    email=(session.get("user_email") or session.get("email") or "")[:320],
                    role=(session.get("user_role") or "")[:10],
                    entity_id=session.get("user_id"),
                    link=str(request.url)[:2048],
                    message=f"add_provider duplicate: {msg[:1800]}"
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log duplicate alert in add_provider: {log_err}")
            return jsonify({"success": False, "message": "A provider with this name already exists for the selected funder."}), 400

        # ---- Write unexpected error to DB ----
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"add_provider error: {msg[:2000]}"
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in add_provider: {log_err}")

        return jsonify({"success": False, "message": msg}), 500
    

@admin_bp.route('/ManageProviders', methods=['GET', 'POST'])
@login_required
def manage_providers():
    def _safe_int(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    # --- perms ---
    if not session.get("user_admin"):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    funder_id_raw = (request.args.get("funder_id") or "").strip()
    funder_id = _safe_int(funder_id_raw)

    if funder_id is None:
        flash("No funder selected.", "warning")
        return redirect(url_for("admin_bp.provider_maintenance"))

    role = session.get("user_role")
    uid  = session.get("user_id")

    # ADM always allowed; FUN only for own funder_id
    if not (role == "ADM" or (role == "FUN" and uid == funder_id)):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    engine = get_db_engine()
    providers = []
    funder_name = "Unknown Funder"

    try:
        with engine.begin() as conn:
            # Providers for this funder
            rows = conn.execute(
                text("EXEC FlaskGetManageableProvidersByFunder @FunderID = :fid"),
                {"fid": funder_id}
            ).mappings().all()

            for r in rows:
                p = dict(r)
                # Defensive numeric parsing
                try:
                    p["Latitude"]  = float(p["Latitude"])  if p.get("Latitude")  is not None else None
                except (TypeError, ValueError):
                    p["Latitude"]  = None
                try:
                    p["Longitude"] = float(p["Longitude"]) if p.get("Longitude") is not None else None
                except (TypeError, ValueError):
                    p["Longitude"] = None
                providers.append(p)

            # Funder name
            res = conn.execute(
                text("EXEC FlaskHelperFunctions @Request = 'FunderNameID', @Number = :fid"),
                {"fid": funder_id}
            ).fetchone()
            funder_name = (res[0] if res and len(res) > 0 else "Unknown Funder")

    except Exception as e:
        current_app.logger.exception("❌ ManageProviders DB load failed")
        # --- log to DB; never raise from here ---
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"ManageProviders DB error (funder_id={funder_id}): {str(e)[:2000]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in ManageProviders: {log_err}")

        flash("An error occurred while loading providers. The issue has been logged.", "danger")
        return redirect(url_for("admin_bp.provider_maintenance"))

    # --- render (own guard so template errors are also logged) ---
    try:
        return render_template(
            "manage_providers.html",
            providers=providers,
            funder_id=funder_id,
            funder_name=funder_name
        )
    except Exception as e:
        current_app.logger.exception("❌ manage_providers.html render failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"ManageProviders template error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log render alert in ManageProviders: {log_err}")
        return abort(500)

@admin_bp.route('/UpdateProvider', methods=['POST'])
@login_required
def update_provider():
    if not session.get("user_admin"):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    pid_raw     = (request.form.get("provider_id") or "").strip()
    new_name    = (request.form.get("new_name") or "").strip()
    new_address = (request.form.get("new_address") or "").strip()
    new_lat_raw = (request.form.get("new_latitude") or "").strip()
    new_lon_raw = (request.form.get("new_longitude") or "").strip()

    # Validate/coerce ProviderID
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        msg = "Invalid provider_id."
        flash(msg, "danger")
        # Log to DB as well
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"UpdateProvider validation error: {msg} raw={pid_raw!r}"[:2000],
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert in UpdateProvider: {log_err}")
        return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))

    # Validate/coerce optional coords
    new_lat = None
    new_lon = None
    try:
        if new_lat_raw != "":
            new_lat = float(new_lat_raw)
        if new_lon_raw != "":
            new_lon = float(new_lon_raw)
    except ValueError:
        msg = "Latitude/Longitude must be numeric."
        flash(msg, "danger")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"UpdateProvider validation error: {msg} lat={new_lat_raw!r} lon={new_lon_raw!r}"[:2000],
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert in UpdateProvider: {log_err}")
        return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))

    # Execute update
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                EXEC FlaskUpdateProviderDetails 
                     @ProviderID   = :pid,
                     @NewName      = :new_name,
                     @NewAddress   = :new_address,
                     @NewLatitude  = :new_lat,
                     @NewLongitude = :new_lon
            """), {
                "pid": pid,
                "new_name": new_name,
                "new_address": new_address,
                "new_lat": new_lat,  # None is OK
                "new_lon": new_lon
            })
        flash("Provider updated successfully.", "success")
    except Exception as e:
        current_app.logger.exception("❌ UpdateProvider failed")
        # Log error to DB
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"UpdateProvider DB error (pid={pid}): {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in UpdateProvider: {log_err}")
        flash(f"Failed to update provider: {e}", "danger")

    return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))


@admin_bp.route('/DeleteProvider', methods=['POST'])
@login_required
def delete_provider():
    if not session.get("user_admin"):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    pid_raw = (request.form.get("provider_id") or "").strip()
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        msg = "Invalid provider_id."
        flash(msg, "danger")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"DeleteProvider validation error: {msg} raw={pid_raw!r}"[:2000],
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert in DeleteProvider: {log_err}")
        return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))

    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("EXEC FlaskDeleteProvider @ProviderID = :pid"), {"pid": pid})
        flash("Provider deleted.", "success")
    except Exception as e:
        current_app.logger.exception("❌ DeleteProvider failed")
        # Log error to DB
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"DeleteProvider DB error (pid={pid}): {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in DeleteProvider: {log_err}")
        flash(f"Could not delete provider: {e}", "danger")

    return redirect(request.referrer or url_for("admin_bp.provider_maintenance"))



@admin_bp.route('/AddProviderDetails', methods=['POST'])
@login_required
def add_provider_details():
    if not session.get("user_admin"):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    funder_id_raw = (request.form.get("funder_id") or "").strip()
    name          = (request.form.get("provider_name") or "").strip()
    address       = (request.form.get("address") or "").strip()
    lat_raw       = (request.form.get("latitude") or "").strip()
    lon_raw       = (request.form.get("longitude") or "").strip()

    # --- validate/coerce funder id ---
    try:
        funder_id = int(funder_id_raw)
    except (TypeError, ValueError):
        msg = "Invalid funder_id."
        flash(msg, "danger")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"AddProviderDetails validation error: {msg} raw={funder_id_raw!r}"[:2000],
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert in AddProviderDetails: {log_err}")
        return redirect(request.referrer or url_for('admin_bp.provider_maintenance'))

    # --- validate/coerce coords (optional) ---
    try:
        latitude  = float(lat_raw) if lat_raw != "" else None
        longitude = float(lon_raw) if lon_raw != "" else None
    except ValueError:
        msg = "Latitude and longitude must be numeric."
        flash(msg, "danger")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"AddProviderDetails validation error: {msg} lat={lat_raw!r} lon={lon_raw!r}"[:2000],
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert in AddProviderDetails: {log_err}")
        return redirect(request.referrer or url_for('admin_bp.provider_maintenance'))

    # --- DB call ---
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskAddProviderWithDetails
                         @FunderID   = :fid,
                         @Description= :desc,
                         @Address    = :addr,
                         @Latitude   = :lat,
                         @Longitude  = :lon
                """),
                {
                    "fid": funder_id,
                    "desc": name,
                    "addr": address,
                    "lat": latitude,
                    "lon": longitude
                }
            )
        flash("Provider added successfully.", "success")
    except Exception as e:
        current_app.logger.exception("❌ AddProviderDetails failed")
        # log error to DB
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"AddProviderDetails DB error (funder_id={funder_id}): {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in AddProviderDetails: {log_err}")
        flash(str(e), "danger")

    return redirect(request.referrer or url_for('admin_bp.provider_maintenance'))


@admin_bp.route("/get_funder_staff/<int:funder_id>")
@login_required
def get_funder_staff(funder_id):
    # (Optional) enforce FUN only sees own funder, ADM can see all:
    role = session.get("user_role")
    uid  = session.get("user_id")
    if not (role == "ADM" or (role == "FUN" and uid == funder_id)):
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            result = conn.execute(
                text("EXEC FlaskHelperFunctionsSpecific @Request = 'FunderStaff', @FunderID = :fid"),
                {"fid": funder_id}
            ).mappings().all()
        return jsonify([dict(row) for row in result])
    except Exception as e:
        current_app.logger.exception("❌ get_funder_staff failed")
        # log error to DB
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"get_funder_staff DB error (funder_id={funder_id}): {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert in get_funder_staff: {log_err}")
        return jsonify({"error": "Failed to load staff"}), 500


@admin_bp.route("/assign_kaiako_staff", methods=["POST"])
@login_required
def assign_kaiako_staff():
    data = request.get_json()
    moe = data.get("MOENumber")
    term = data.get("Term")
    year = data.get("Year")
    email = data.get("Email")

    print(f"📥 Incoming assign_kaiako_staff request")
    print(f"   ➤ MOE Number: {moe}")
    print(f"   ➤ Term: {term}")
    print(f"   ➤ Year: {year}")
    print(f"   ➤ Staff Email: {email}")

    if not all([moe, term, year]):
        print("❌ Missing MOE, Term, or Year.")
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            if email == "":
                print("🗑️ Deleting staff assignment...")
                conn.execute(
                    text("EXEC FlaskDeleteEntityStaff @MOENumber = :moe, @Term = :term, @CalendarYear = :year"),
                    {"moe": moe, "term": term, "year": year}
                )
                print("✅ Staff assignment deleted.")
            else:
                print("🔄 Assigning/Updating staff...")
                conn.execute(
                    text("EXEC FlaskAssignEntityStaff @MOENumber = :moe, @Term = :term, @CalendarYear = :year, @Email = :email"),
                    {"moe": moe, "term": term, "year": year, "email": email}
                )
                print("✅ Staff assigned/updated in EntityStaff.")

        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Error during DB execution: {e}")
        return jsonify({"success": False, "message": str(e)}), 500



@admin_bp.route("/SchoolType", methods=["GET", "POST"])
@login_required
def edit_school_type():
    if session.get("user_role") != "ADM":
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    engine = get_db_engine()

    # ----------------------- POST: update then redirect (PRG) -----------------------
    if request.method == "POST":
        moenumber_raw = (request.form.get("moenumber") or "").strip()
        new_type_raw  = (request.form.get("schooltype") or "").strip()

        # preserve UX state via query params
        q        = (request.form.get("search_term") or "").strip()
        sort_by  = (request.form.get("sort_by") or "schoolname").lower()
        sort_dir = (request.form.get("sort_direction") or "asc").lower()
        try:
            page = int(request.args.get("page", 1))
            page = 1 if page < 1 else page
        except Exception:
            page = 1

        # Validate/coerce IDs early
        try:
            moenumber = int(moenumber_raw)
        except (TypeError, ValueError):
            msg = "Invalid MOENumber."
            flash(msg, "danger")
            # log to DB
            try:
                log_alert(
                    email=(session.get("user_email") or session.get("email") or "")[:320],
                    role=(session.get("user_role") or "")[:10],
                    entity_id=session.get("user_id"),
                    link=str(request.url)[:2048],
                    message=f"SchoolType POST validation: {msg} raw={moenumber_raw!r}"[:2000],
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log alert (SchoolType POST validation): {log_err}")
            return redirect(url_for("admin_bp.edit_school_type",
                                    q=q, sort_by=sort_by, dir=sort_dir, page=page))

        try:
            new_type = int(new_type_raw)
        except (TypeError, ValueError):
            msg = "Invalid SchoolTypeID."
            flash(msg, "danger")
            try:
                log_alert(
                    email=(session.get("user_email") or session.get("email") or "")[:320],
                    role=(session.get("user_role") or "")[:10],
                    entity_id=session.get("user_id"),
                    link=str(request.url)[:2048],
                    message=f"SchoolType POST validation: {msg} raw={new_type_raw!r}"[:2000],
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log alert (SchoolType POST validation 2): {log_err}")
            return redirect(url_for("admin_bp.edit_school_type",
                                    q=q, sort_by=sort_by, dir=sort_dir, page=page))

        # DB update
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        EXEC FlaskSchoolTypeChanger
                             @Request     = 'UpdateSchoolType',
                             @MOENumber   = :moe,
                             @SchoolTypeID= :stype
                    """),
                    {"moe": moenumber, "stype": new_type},
                )
            flash("School type updated successfully.", "success")
        except Exception as e:
            current_app.logger.exception("❌ SchoolType POST DB update failed")
            # log to DB (never raise here)
            try:
                log_alert(
                    email=(session.get("user_email") or session.get("email") or "")[:320],
                    role=(session.get("user_role") or "")[:10],
                    entity_id=session.get("user_id"),
                    link=str(request.url)[:2048],
                    message=f"SchoolType POST DB error (MOE={moenumber}, Type={new_type}): {str(e)[:1800]}",
                )
            except Exception as log_err:
                current_app.logger.error(f"⚠️ Failed to log alert (SchoolType POST DB): {log_err}")
            flash("We couldn’t update the school type. The issue has been logged.", "danger")

        # Always PRG back to list (no redirect to self-on-exception loop risk here)
        return redirect(url_for("admin_bp.edit_school_type",
                                q=q, sort_by=sort_by, dir=sort_dir, page=page))

    # ----------------------- GET: load paged list (fail-soft) -----------------------
    q        = (request.args.get("q") or "").strip()
    sort_by  = (request.args.get("sort_by") or "schoolname").lower()
    sort_dir = (request.args.get("dir") or "asc").lower()
    try:
        page = int(request.args.get("page", 1))
        page = 1 if page < 1 else page
    except Exception:
        page = 1
    page_size = 50

    if sort_by not in ("schoolname", "moe"):
        sort_by = "schoolname"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    school_types = []
    school_data  = []
    total_rows   = 0
    pages        = 1

    try:
        with engine.begin() as conn:
            # Dropdown
            school_types = conn.execute(
                text("EXEC FlaskSchoolTypeChanger @Request = 'GetSchoolTypeDropdown'")
            ).mappings().all()

            # Paged directory — consume multiple result sets
            exec_sql = """
                SET NOCOUNT ON;
                EXEC FlaskSchoolTypeChanger
                     @Request=?,
                     @Search=?,
                     @SortBy=?,
                     @SortDir=?,
                     @Page=?,
                     @PageSize=?;
            """
            res    = conn.exec_driver_sql(exec_sql, ("GetSchoolDirectoryPaged", q, sort_by, sort_dir, page, page_size))
            cursor = res.cursor

            # First result: rows
            cols = [d[0] for d in (cursor.description or [])]
            rows = cursor.fetchall() if cursor.description else []
            school_data = [dict(zip(cols, r)) for r in rows]

            # Second result: TotalRows
            if cursor.nextset() and cursor.description:
                cols2 = [d[0] for d in cursor.description]
                row2  = cursor.fetchone()
                if row2 is not None:
                    rec = dict(zip(cols2, row2))
                    total_rows = int(rec.get("TotalRows", 0) or 0)

            pages = max(1, ceil(total_rows / page_size)) if total_rows else 1

    except Exception as e:
        current_app.logger.exception("❌ SchoolType GET DB load failed")
        # Log to DB; show a warning and render empty state
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"SchoolType GET DB error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (SchoolType GET DB): {log_err}")
        flash("Couldn’t load the school list. The issue has been logged.", "warning")

    # Render (no DB in fallback; avoids loops)
    return render_template(
        "edit_school_type.html",
        is_mobile=is_mobile_request(request),
        school_data=school_data,
        school_types=school_types,
        glossary=None,  # lazy-loaded via JSON endpoint
        search_term=q,
        sort_by=sort_by,
        sort_direction=sort_dir,
        page=page,
        pages=pages,
        page_size=page_size,
        total_rows=total_rows,
    )
# --- Glossary lazy-load JSON endpoint ----------------------------------------
@admin_bp.route("/SchoolType/glossary.json", methods=["GET"])
@login_required
def school_type_glossary_json():
    if session.get("user_role") != "ADM":
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            rows = conn.execute(
                text("EXEC FlaskSchoolTypeChanger @Request = 'GetGlossary'")
            ).mappings().all()

        return jsonify([
            {"SchoolType": r.get("SchoolType"), "Definition": r.get("Definition")}
            for r in rows
        ])

    except Exception as e:
        current_app.logger.exception("❌ glossary.json load failed")
        # Log to DB, never raise here
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"SchoolType glossary load error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (glossary.json): {log_err}")
        return jsonify({"error": "Failed to load glossary"}), 500

@admin_bp.route("/EditUser")
@login_required
def admin_user_entities():
    if session.get("user_role") != "ADM":
        flash("You don’t have permission to access this page", "danger")
        return redirect(url_for("home_bp.home"))

    try:
        return render_template("admin_user_entities.html")
    except Exception as e:
        current_app.logger.exception("❌ admin_user_entities render failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"EditUser template error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (EditUser render): {log_err}")
        return abort(500)
@admin_bp.route("/get_users")
@login_required
def get_users():
    # Only admins can hit this
    if session.get("user_role") != "ADM":
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    try:
        engine = get_db_engine()

        with engine.begin() as conn:
            # Pull from your stored proc / query
            df = pd.read_sql("EXEC FlaskGetAllUsers", conn)

        # 🔹 Replace NaN/NaT with None so JSON is valid
        df = df.where(pd.notna(df), None)

        # Optional: if you don't care about ID here, you can drop it:
        # if "ID" in df.columns:
        #     df = df.drop(columns=["ID"])

        records = df.to_dict(orient="records")

        # Extra safety: normalise any stray float NaN/Inf
        for row in records:
            for k, v in row.items():
                if isinstance(v, float):
                    if math.isnan(v) or math.isinf(v):
                        row[k] = None

        return jsonify(records), 200

    except Exception as e:
        current_app.logger.exception("❌ get_users failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"get_users DB error: {str(e)[:1800]}",
            )
        except Exception as log_err:
            current_app.logger.error(
                f"⚠️ Failed to log alert (get_users): {log_err}"
            )
        return jsonify({"error": "Failed to load users"}), 500
@admin_bp.route("/update_user_role_entity", methods=["POST"])
@login_required
def update_user_role_entity():
    if session.get("user_role") != "ADM":
        return render_template(
    "error.html",
    error="You are not authorised to view that page.",
    code=403
), 403

    data = request.get_json(silent=True) or {}
    email        = (data.get("email") or "").strip()
    role         = (data.get("role") or "").strip()
    entity_id    = data.get("entityId")
    full_name    = (data.get("fullName") or "").strip()
    entity_name  = (data.get("entityName") or "").strip()
    display_role = (data.get("displayRole") or "").strip()

    # Validate required fields
    if not (email and role and entity_id not in (None, "")):
        msg = "Missing fields"
        flash("❌ Missing required fields.", "warning")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"update_user_role_entity validation error: {msg} payload={str(data)[:1000]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert (update_user_role_entity): {log_err}")
        return jsonify(success=False, message=msg), 400

    # Coerce entity ID to int
    try:
        entity_id = int(entity_id)
    except (TypeError, ValueError):
        msg = "Invalid entityId"
        flash("❌ Invalid entity ID.", "warning")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"update_user_role_entity validation error: {msg} raw={data.get('entityId')!r}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log validation alert (update_user_role_entity int): {log_err}")
        return jsonify(success=False, message=msg), 400

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    EXEC FlaskUpdateUserRoleAndEntity
                         @Email   = :email,
                         @Role    = :role,
                         @EntityID= :entity_id
                """),
                {"email": email, "role": role, "entity_id": entity_id}
            )

        # Success
        flash(f"✅ Updated {full_name or email} to {display_role or role} – {entity_name or entity_id}.", "success")
        return jsonify(success=True)

    except Exception as e:
        current_app.logger.exception("❌ update_user_role_entity failed")
        try:
            log_alert(
                email=(session.get("user_email") or session.get("email") or "")[:320],
                role=(session.get("user_role") or "")[:10],
                entity_id=session.get("user_id"),
                link=str(request.url)[:2048],
                message=f"update_user_role_entity DB error (email={email}, role={role}, entity={entity_id}): {str(e)[:1600]}",
            )
        except Exception as log_err:
            current_app.logger.error(f"⚠️ Failed to log alert (update_user_role_entity): {log_err}")
        flash("🔥 Error during update.", "danger")
        return jsonify(success=False, message="Internal server error"), 500
