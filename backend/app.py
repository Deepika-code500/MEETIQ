from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from pathlib import Path
from flask_mail import Mail
from flask_mail import Message
import os
import uuid
import sys
try:
    import whisper
except Exception:
    whisper = None
from pathlib import Path
from werkzeug.utils import secure_filename
from models.user_model import (
    create_admin,
    admins_collection,
    create_participant,
    participants_collection
)

from models.meeting_model import (
    create_meeting as create_meeting_record,
    meetings_collection
)
from models.notification_model import (
    create_notification,
    notifications_collection
)
from database import db
ai_jobs_collection = db["ai_jobs"]
import sys

SUMMARIZER_PATH = Path(__file__).resolve().parent.parent / "ai-services" / "summarization"

if str(SUMMARIZER_PATH) not in sys.path:
    sys.path.append(str(SUMMARIZER_PATH))

from summarizer import generate_summary
# =========================================================
# PROJECT PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"


# =========================================================
# FLASK APPLICATION
# =========================================================

app = Flask(
    __name__,
    template_folder=str(FRONTEND_DIR / "templates"),
    static_folder=str(FRONTEND_DIR / "static"),
    static_url_path="/static"
)

cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "*")
cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()] if cors_origins_env != "*" else "*"

socketio = SocketIO(
    app,
    cors_allowed_origins=cors_origins
)
socket_users = {}
socket_meeting_rooms = {}

# =========================================================
# SECRET KEY & SECURITY CONFIGURATION
# =========================================================

app.secret_key = os.getenv("SECRET_KEY", "meetiq-development-secret-key")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.getenv("FLASK_ENV") == "production":
    app.config["SESSION_COOKIE_SECURE"] = True

app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True") == "True"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)

# =========================================================
# CORS & HEALTH CHECK CONFIGURATION
# =========================================================

@app.after_request
def add_cors_headers(response):
    origins = os.getenv("CORS_ALLOWED_ORIGINS", "*")
    if origins != "*":
        allowed = [o.strip() for o in origins.split(",") if o.strip()]
        req_origin = request.headers.get("Origin")
        if req_origin and req_origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = req_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
    else:
        response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/health")
def health_check():
    """
    Staging & production health check endpoint.
    Returns HTTP 200 OK with safe service status metadata.
    Does not leak secrets, database credentials, or internal stack traces.
    """
    db_status = "connected"
    try:
        from database import client
        client.admin.command("ping")
    except Exception:
        db_status = "disconnected"

    return jsonify({
        "status": "healthy" if db_status == "connected" else "degraded",
        "service": "MeetIQ Virtual Meeting Platform",
        "database": db_status,
        "timestamp": get_utc_now().isoformat()
    }), 200 if db_status == "connected" else 503


@app.route("/ready")
def readiness_check():
    """
    Staging & production readiness check endpoint.
    Indicates whether the application and database are ready to serve client requests.
    """
    db_ready = False
    try:
        from database import client
        client.admin.command("ping")
        db_ready = True
    except Exception:
        db_ready = False

    if db_ready:
        return jsonify({
            "status": "ready",
            "service": "MeetIQ Virtual Meeting Platform",
            "database": "connected",
            "timestamp": get_utc_now().isoformat()
        }), 200
    else:
        return jsonify({
            "status": "not_ready",
            "service": "MeetIQ Virtual Meeting Platform",
            "database": "disconnected",
            "timestamp": get_utc_now().isoformat()
        }), 503


app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload size



from bson.objectid import ObjectId
from bson.errors import InvalidId

def safe_object_id(id_val):
    """
    Safely converts a string or ObjectId to a valid ObjectId.
    Returns None if id_val is invalid or None.
    """
    if not id_val:
        return None
    if isinstance(id_val, ObjectId):
        return id_val
    try:
        return ObjectId(str(id_val))
    except (InvalidId, TypeError, ValueError):
        return None


@socketio.on("connect")
def handle_socket_connect():
    user_id = session.get("user_id")
    if user_id:
        socket_users[user_id] = request.sid
        print("Socket connected:", user_id, request.sid)


@socketio.on("disconnect")
def handle_socket_disconnect():
    disconnected_socket = request.sid

    info = socket_meeting_rooms.pop(disconnected_socket, None)
    if info and info.get("role") == "participant":
        m_id = info.get("meeting_id")
        p_id = info.get("participant_id")
        if m_id and p_id:
            print("Finalizing attendance on socket disconnect:", p_id, m_id)
            finalize_attendance_session(m_id, p_id, get_utc_now())

    for user_id, socket_id in list(socket_users.items()):
        if socket_id == disconnected_socket:
            del socket_users[user_id]
            print("Socket disconnected:", user_id)
            break

# =========================================================
# TEMPORARY USER STORAGE
# =========================================================

admins = []
participants = []

# Temporary meeting storage
meetings = []


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def find_admin(email):
    email = email.strip().lower()

    return admins_collection.find_one({
        "email": email
    })


def find_participant(login_value):
    login_value = login_value.strip().lower()

    return participants_collection.find_one({
        "$or": [
            {"email": login_value},
            {"user_id": login_value}
        ]
    })


# =========================================================
# DATE FORMATTER
# =========================================================

def format_meeting_date(date_value):
    """
    Converts:
        2026-09-14
    into:
        14 September 2026
    """

    try:
        date_object = datetime.strptime(
            date_value,
            "%Y-%m-%d"
        )

        return date_object.strftime(
            "%d %B %Y"
        )

    except (ValueError, TypeError):
        return date_value


# =========================================================
# TIME FORMATTER
# =========================================================

def format_meeting_time(time_value):
    """
    Converts:
        14:30
    into:
        02:30 PM

    Also accepts:
        02:30 PM
        2:30 PM
    """

    if not time_value:
        return ""

    # If already in AM/PM format
    for time_format in [
        "%I:%M %p",
        "%I:%M%p"
    ]:

        try:
            time_object = datetime.strptime(
                time_value.strip(),
                time_format
            )

            return time_object.strftime(
                "%I:%M %p"
            )

        except ValueError:
            pass

    # If HTML time input sends 24-hour format
    try:

        time_object = datetime.strptime(
            time_value.strip(),
            "%H:%M"
        )

        return time_object.strftime(
            "%I:%M %p"
        )

    except ValueError:

        return time_value


# =========================================================
# ROLE SELECTION
# =========================================================

@app.route("/")
def role():

    return render_template(
        "auth/role.html"
    )


# =========================================================
# ADMIN LOGIN
# =========================================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        # Empty field validation
        if not email or not password:

            flash(
                "Please enter your email and password.",
                "error"
            )

            return redirect(
                url_for("admin_login")
            )

        # Find registered admin
        admin = find_admin(email)

        # Check account + password
        if admin and check_password_hash(
            admin["password"],
            password
        ):

            session["user_id"] = admin["email"]
            session["user_name"] = admin["name"]
            session["role"] = "admin"

            flash(
                "Welcome back, " + admin["name"] + "!",
                "success"
            )

            return redirect(
                url_for("admin_dashboard")
            )

        # Invalid credentials
        flash(
            "Invalid email or password.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    return render_template(
        "auth/admin_login.html"
    )


# =========================================================
# ADMIN REGISTER
# =========================================================

@app.route(
    "/admin/register",
    methods=["GET", "POST"]
)
def admin_register():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        # Required fields
        if not name or not email or not password or not confirm_password:

            flash(
                "Please fill in all fields.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Password confirmation
        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Minimum password length
        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Check duplicate email
        if find_admin(email):

            flash(
                "An admin account with this email already exists.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Create account
        new_admin = create_admin(
    name,
    email,
    password
)

        flash(
            "Admin account created successfully. Please sign in.",
            "success"
        )

        return redirect(
            url_for("admin_login")
        )

    return render_template(
        "auth/admin_register.html"
    )


# =========================================================
# PARTICIPANT LOGIN
# =========================================================

@app.route(
    "/participant/login",
    methods=["GET", "POST"]
)
def participant_login():

    if request.method == "POST":

        login_value = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        # Empty field validation
        if not login_value or not password:

            flash(
                "Please enter your email/ID and password.",
                "error"
            )

            return redirect(
                url_for("participant_login")
            )

        # Find participant
        participant = find_participant(
            login_value
        )

        # Check password
        if participant and check_password_hash(
            participant["password"],
            password
        ):

            session["user_id"] = participant["user_id"]

            session["user_name"] = participant["name"]

            session["role"] = "participant"

            flash(
                "Welcome back, " + participant["name"] + "!",
                "success"
            )

            return redirect(
                url_for("participant_dashboard")
            )

        # Invalid credentials
        flash(
            "Invalid email/ID or password.",
            "error"
        )

        return redirect(
            url_for("participant_login")
        )

    return render_template(
        "auth/participant_login.html"
    )


# =========================================================
# PARTICIPANT REGISTER
# =========================================================

@app.route(
    "/participant/register",
    methods=["GET", "POST"]
)
def participant_register():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        department = request.form.get(
            "department",
            ""
        ).strip()

        year = request.form.get(
            "year",
            ""
        ).strip()

        user_id = request.form.get(
            "user_id",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        # Required fields
        if not all([
            name,
            email,
            department,
            year,
            user_id,
            password,
            confirm_password
        ]):

            flash(
                "Please fill in all fields.",
                "error"
            )

            return redirect(
                url_for("participant_register")
            )

        # Password confirmation
        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return redirect(
                url_for("participant_register")
            )

        # Minimum password length
        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("participant_register")
            )

               # Check duplicate email
        if participants_collection.find_one({
            "email": email
        }):

            flash(
                "An account with this email already exists.",
                "error"
            )

            return redirect(
                url_for("participant_register")
            )

        # Check duplicate Student/Employee ID
        if participants_collection.find_one({
            "user_id": user_id
        }):

            flash(
                "This Student/Employee ID is already registered.",
                "error"
            )

            return redirect(
                url_for("participant_register")
            )

        # Create participant
                
        new_participant = create_participant(
            name,
            email,
            department,
            year,
            user_id,
            password
        )

       

        flash(
            "Participant account created successfully. Please sign in.",
            "success"
        )

        return redirect(
            url_for("participant_login")
        )

    return render_template(
        "auth/participant_register.html"
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin/dashboard")
def admin_dashboard():

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    admin_email = session.get("user_id")

    admin_meetings_list = list(
        meetings_collection.find({
            "created_by_email": admin_email
        }).sort("date", -1)
    )

    total_meetings = len(admin_meetings_list)

    upcoming_meetings = sum(
        1 for meeting in admin_meetings_list
        if meeting.get("status") == "upcoming"
    )

    total_participants = sum(
        len(meeting.get("invited_participants", []))
        for meeting in admin_meetings_list
    )

    ai_insights = sum(
        1 for meeting in admin_meetings_list
        if meeting.get("ai_summary")
    )

    meetings = admin_meetings_list[:5]

    return render_template(
        "admin/dashboard.html",
        total_meetings=total_meetings,
        upcoming_meetings=upcoming_meetings,
        total_participants=total_participants,
        total_invited=total_participants,
        ai_insights=ai_insights,
        meetings=meetings
    )

# =========================================================
# ADMIN MEETINGS
# =========================================================

@app.route("/admin/meetings")
def admin_meetings():

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    # -----------------------------------------------------
    # GET ALL MEETINGS FROM MONGODB
    # -----------------------------------------------------

    admin_email = session.get("user_id")

    meetings = list(
        meetings_collection.find({
            "created_by_email": admin_email
        }).sort(
            "date",
            1
        )
    )

    # -----------------------------------------------------
    # MEETING STATISTICS
    # -----------------------------------------------------

    total_meetings = len(meetings)

    upcoming_meetings = 0
    total_participants = 0
    ai_insights = 0

    for meeting in meetings:

        # Count upcoming meetings
        if meeting.get("status") == "upcoming":

            upcoming_meetings += 1

        # Count invited participants
        invited = meeting.get(
            "invited_participants",
            []
        )

        total_participants += len(invited)

        # Count meetings that have AI insights
        if meeting.get("ai_summary"):

            ai_insights += 1

    # -----------------------------------------------------
    # SEND DATA TO MEETINGS PAGE
    # -----------------------------------------------------

    return render_template(
        "admin/meetings.html",

        meetings=meetings,

        total_meetings=total_meetings,

        upcoming_meetings=upcoming_meetings,

        total_participants=total_participants,

        ai_insights=ai_insights
    )
# =========================================================
# MEETING OWNERSHIP HELPER
# =========================================================

def get_admin_meeting(meeting_id):
    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:
        return meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })
    except Exception:
        return None


# =========================================================
# ADMIN MEETING DETAILS
# =========================================================

@app.route("/admin/meetings/<meeting_id>")
def meeting_details(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "You are not authorized to access this meeting.",
            "error"
        )
        return redirect(url_for("admin_meetings"))

    invited_ids = meeting.get(
        "invited_participants",
        []
    )

    invited_participants = list(
        participants_collection.find({
            "user_id": {
                "$in": invited_ids
            }
        }).sort(
            "name",
            1
        )
    )

    return render_template(
        "admin/meeting_details.html",
        meeting=meeting,
        invited_participants=invited_participants
    )
# ============================================================
# START MEETING
# ============================================================

@app.route("/admin/meetings/<meeting_id>/start", methods=["POST"])
def start_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:

        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })

    except Exception:

        meeting = None

    if not meeting:

        flash(
            "Meeting not found or you are not authorized to manage it.",
            "error"
        )

        return redirect(
            url_for("admin_meetings")
        )

    # Only upcoming meetings can be started
    if meeting.get("status") != "upcoming":

        flash(
            "This meeting cannot be started.",
            "error"
        )

        return redirect(
            url_for(
                "meeting_details",
                meeting_id=meeting_id
            )
        )

    meetings_collection.update_one(
        {
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        },
        {
            "$set": {
                "status": "live"
            }
        }
    )

    flash(
        "Meeting started successfully.",
        "success"
    )

    return redirect(
        url_for(
            "meeting_details",
            meeting_id=meeting_id
        )
    )

@app.route("/admin/meetings/<meeting_id>/host")
def host_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "Meeting not found or you are not authorized to access it.",
            "error"
        )
        return redirect(url_for("admin_meetings"))

    if meeting.get("status") != "live":
        flash(
            "The meeting must be started before entering the host room.",
            "error"
        )
        return redirect(
            url_for(
                "meeting_details",
                meeting_id=meeting_id
            )
        )

    return render_template(
        "admin/host_meeting.html",
        meeting=meeting,
        session_user_name=session.get("user_name")
    )
# ============================================================
# END MEETING
# ============================================================

@app.route("/admin/meetings/<meeting_id>/end", methods=["POST"])
def end_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:

        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })

    except Exception:

        meeting = None

    if not meeting:

        flash(
            "Meeting not found or you are not authorized to manage it.",
            "error"
        )

        return redirect(
            url_for("admin_meetings")
        )

    # Only live meetings can be ended
    if meeting.get("status") != "live":

        flash(
            "This meeting is not currently live.",
            "error"
        )

        return redirect(
            url_for(
                "meeting_details",
                meeting_id=meeting_id
            )
        )

    meeting_ended_at = get_utc_now()

    meetings_collection.update_one(
        {
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        },
        {
            "$set": {
                "status": "completed",
                "ended_at": meeting_ended_at
            }
        }
    )

    # Finalize everyone who is still inside the meeting.
    finalize_meeting_attendance(
        meeting_id,
        meeting_ended_at
    )

    summary_result = generate_meeting_ai_summary(meeting_id)

    if summary_result.get("success"):
        flash(
            "Meeting ended successfully and AI summary generated.",
            "success"
        )
    else:
        flash(
            "Meeting ended successfully. AI summary is not available "
            "because no transcript was captured.",
            "success"
        )

    return redirect(
        url_for(
            "meeting_details",
            meeting_id=meeting_id
        )
    )
# =========================================================
# EDIT MEETING
# =========================================================

@app.route("/admin/meetings/<meeting_id>/edit", methods=["GET", "POST"])
def edit_meeting(meeting_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    meeting = get_admin_meeting(meeting_id)

    if not meeting:
        flash("Meeting not found or you are not authorized to edit it.", "error")
        return redirect(url_for("admin_meetings"))

    if request.method == "GET":
        return render_template("admin/edit_meeting.html", meeting=meeting)

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    date = request.form.get("date", "").strip()
    time = request.form.get("time", "").strip()
    duration = request.form.get("duration", "").strip()
    meeting_type = request.form.get("meeting_type", "").strip()
    access = request.form.get("access", "").strip()

    if not title or not date or not time:
        flash("Title, date and time are required.", "error")
        return redirect(url_for("edit_meeting", meeting_id=meeting_id))

    meetings_collection.update_one(
        {
            "_id": meeting["_id"],
            "created_by_email": session.get("user_id")
        },
        {
            "$set": {
                "title": title,
                "description": description,
                "date": date,
                "formatted_date": format_meeting_date(date),
                "time": time,
                "formatted_time": format_meeting_time(time),
                "duration": duration,
                "meeting_type": meeting_type,
                "access": access
            }
        }
    )

    flash("Meeting updated successfully!", "success")
    return redirect(url_for("meeting_details", meeting_id=meeting_id))


# =========================================================
# DELETE MEETING
# =========================================================

@app.route("/admin/meetings/<meeting_id>/delete", methods=["POST"])
def delete_meeting(meeting_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    try:
        result = meetings_collection.delete_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": session.get("user_id")
        })
    except Exception:
        result = None

    if not result or result.deleted_count == 0:
        flash("Meeting not found or you are not authorized to delete it.", "error")
    else:
        flash("Meeting deleted successfully!", "success")

    return redirect(url_for("admin_meetings"))


# =========================================================
# REMOVE INVITATION
# =========================================================

@app.route("/admin/meetings/<meeting_id>/remove-invitation/<participant_id>", methods=["POST"])
def remove_invitation(meeting_id, participant_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    try:
        result = meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id),
                "created_by_email": session.get("user_id")
            },
            {
                "$pull": {
                    "invited_participants": participant_id
                }
            }
        )
    except Exception:
        result = None

    if not result or result.modified_count == 0:
        flash("Invitation not found or you are not authorized.", "error")
    else:
        flash("Invitation removed successfully!", "success")

    return redirect(url_for("meeting_details", meeting_id=meeting_id))


# =========================================================
# INVITE PARTICIPANTS
# =========================================================

@app.route("/admin/meetings/<meeting_id>/invite", methods=["GET", "POST"])
def invite_participants(meeting_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    meeting = get_admin_meeting(meeting_id)

    if not meeting:
        flash("Meeting not found or you are not authorized to invite participants.", "error")
        return redirect(url_for("admin_meetings"))

    dept_filter = request.args.get("department", "all").strip()
    team_filter = request.args.get("team", "all").strip()
    eligible_only = request.args.get("eligible_only", "false").lower() == "true"
    search_query = request.args.get("search", "").strip().lower()

    all_participants_raw = list(participants_collection.find().sort("name", 1))
    departments = sorted(list(set(p.get("department", "General") or "General" for p in all_participants_raw)))
    teams = sorted(list(set(p.get("team", "General") or "General" for p in all_participants_raw)))

    query = {}
    if dept_filter != "all" and dept_filter:
        query["department"] = {"$regex": f"^{re.escape(dept_filter)}$", "$options": "i"}
    if team_filter != "all" and team_filter:
        query["team"] = {"$regex": f"^{re.escape(team_filter)}$", "$options": "i"}
    if eligible_only:
        query["eligible"] = {"$ne": False}
    if search_query:
        query["$or"] = [
            {"name": {"$regex": re.escape(search_query), "$options": "i"}},
            {"email": {"$regex": re.escape(search_query), "$options": "i"}},
            {"user_id": {"$regex": re.escape(search_query), "$options": "i"}}
        ]

    filtered_participants = list(participants_collection.find(query).sort("name", 1))
    for p in filtered_participants:
        p.setdefault("department", "General")
        p.setdefault("team", "General")
        p.setdefault("eligible", True)

    if request.method == "GET":
        return render_template(
            "admin/invite_participants.html",
            meeting=meeting,
            participants=filtered_participants,
            departments=departments,
            teams=teams,
            selected_department=dept_filter,
            selected_team=team_filter,
            eligible_only=eligible_only,
            search_query=search_query
        )

    selected_participants = request.form.getlist("participant_ids")
    existing_invited = set(meeting.get("invited_participants", []))
    newly_invited = []

    for participant_id in selected_participants:
        participant = participants_collection.find_one({
            "$or": [{"user_id": participant_id}, {"email": participant_id}]
        })

        if not participant:
            continue

        if participant.get("eligible") is False:
            continue

        p_id = participant.get("user_id")
        if p_id not in existing_invited:
            meetings_collection.update_one(
                {
                    "_id": meeting["_id"],
                    "created_by_email": session.get("user_id")
                },
                {
                    "$addToSet": {
                        "invited_participants": p_id
                    }
                }
            )
            existing_invited.add(p_id)
            newly_invited.append(participant)

    for participant in newly_invited:
        participant_email = participant.get("email")
        participant_name = participant.get("name", "Participant")

        create_notification(
            participant_id=participant.get("user_id"),
            participant_email=participant_email,
            meeting_id=str(meeting["_id"]),
            meeting_title=meeting.get("title", "Meeting"),
            meeting_date=meeting.get("formatted_date", meeting.get("date", "")),
            meeting_time=meeting.get("formatted_time", meeting.get("time", "")),
            message=f"You have been invited to {meeting.get('title', 'a meeting')}."
        )

        if participant_email:
            meeting_link = url_for(
                "participant_meeting",
                meeting_id=str(meeting["_id"]),
                _external=True
            )

            email_body = f"""Hello {participant_name},

You have been invited to a meeting on MeetIQ.

MEETING DETAILS
------------------------------
Meeting: {meeting.get("title", "Meeting")}
Date: {meeting.get("formatted_date", meeting.get("date", "Not specified"))}
Time: {meeting.get("formatted_time", meeting.get("time", "Not specified"))}
Duration: {meeting.get("duration", "Not specified")}
Meeting Type: {meeting.get("meeting_type", "Virtual Meeting")}
------------------------------

You can view the meeting details and join the meeting here:

{meeting_link}

Please sign in to your MeetIQ participant account before joining.

Regards,
{session.get("user_name", "MeetIQ Administrator")}
MeetIQ
"""

            try:
                admin_email = session.get("user_id")

                msg = Message(
                    subject="Meeting Invitation - " + meeting.get("title", "MeetIQ Meeting"),
                    sender=admin_email,
                    recipients=[participant_email]
                )

                msg.body = email_body
                mail.send(msg)

                print("Invitation email sent to:", participant_email)

            except Exception as email_error:
                print("Email failed for", participant_email)
                print("Email error:", email_error)

    flash("Participants invited successfully!", "success")
    return redirect(url_for("admin_meetings"))


# =========================================================
# CREATE MEETING
# =========================================================

@app.route("/admin/meetings/create", methods=["GET", "POST"])
def create_meeting():

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "GET":
        return render_template("admin/create_meeting.html")

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    date = request.form.get("date", "").strip()
    time = request.form.get("time", "").strip()
    duration = request.form.get("duration", "60").strip()
    meeting_type = request.form.get("meeting_type", "team").strip()
    access = request.form.get("access", "selected").strip()

    if not title or not date or not time:
        flash("Please fill in the meeting title, date and time.", "error")
        return redirect(url_for("create_meeting"))

    admin_email = session.get("user_id")
    admin_name = session.get("user_name", "Administrator")

    create_meeting_record(
        title=title,
        description=description,
        date=date,
        formatted_date=format_meeting_date(date),
        time=time,
        formatted_time=format_meeting_time(time),
        duration=duration,
        meeting_type=meeting_type,
        access=access,
        created_by=admin_name,
        created_by_email=admin_email
    )

    flash("Meeting created successfully!", "success")
    return redirect(url_for("admin_meetings"))

# =========================================================
# ADMIN PARTICIPANTS & BULK IMPORT
# =========================================================

import csv
import io
import re


def parse_participant_import_file(file_storage):
    """
    Parses an uploaded CSV or Excel (.xlsx) file containing participant rows.
    Performs email normalization, column mapping, row-level validation, duplicate detection,
    and returns a structured validation result object.
    """
    if not file_storage or not file_storage.filename:
        raise ValueError("No file selected for import.")

    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in [".csv", ".xlsx", ".xls"]:
        raise ValueError("Unsupported file format. Please upload a .csv or .xlsx file.")

    content = file_storage.read()
    if not content:
        raise ValueError("Uploaded file is empty.")

    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Uploaded file exceeds the 10MB size limit.")

    raw_rows = []

    if ext == ".csv":
        try:
            decoded = content.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(decoded))
            for row in reader:
                raw_rows.append(row)
        except Exception as e:
            raise ValueError(f"Failed to parse CSV file: {str(e)}")
    else:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(filename=io.BytesIO(content), read_only=True)
            sheet = wb.active
            rows_iter = sheet.iter_rows(values_only=True)
            header_row = next(rows_iter, None)
            if not header_row:
                raise ValueError("Excel file is empty or missing headers.")

            headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(header_row)]
            for r in rows_iter:
                if any(cell is not None for cell in r):
                    row_dict = {headers[i]: (str(r[i]).strip() if i < len(r) and r[i] is not None else "") for i in range(len(headers))}
                    raw_rows.append(row_dict)
        except Exception as e:
            raise ValueError(f"Failed to parse Excel file: {str(e)}")

    if not raw_rows:
        raise ValueError("No data rows found in the uploaded file.")

    valid_rows = []
    invalid_rows = []
    duplicate_rows = []

    seen_emails_in_file = set()
    email_regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    for index, raw in enumerate(raw_rows, start=2):
        col_map = {str(k).lower().strip().replace(" ", "_"): str(v).strip() for k, v in raw.items() if k is not None}

        name = col_map.get("name") or col_map.get("full_name") or col_map.get("participant_name") or ""
        email = col_map.get("email") or col_map.get("email_address") or ""
        department = col_map.get("department") or col_map.get("dept") or "General"
        team = col_map.get("team") or col_map.get("group") or "General"
        year = col_map.get("year") or col_map.get("class_year") or "1"
        user_id = col_map.get("user_id") or col_map.get("id") or col_map.get("student_id") or col_map.get("employee_id") or ""
        eligible_val = col_map.get("eligible") or col_map.get("is_eligible") or col_map.get("eligibility") or "true"

        row_errors = []

        if not name:
            row_errors.append("Missing required field 'name'")
        if not email:
            row_errors.append("Missing required field 'email'")
        else:
            email = email.lower().strip()
            if not email_regex.match(email):
                row_errors.append(f"Invalid email format: '{email}'")

        if not user_id:
            user_id = email.split("@")[0] if email else f"user_{index}"

        eligible_str = str(eligible_val).strip().lower()
        is_eligible = eligible_str not in ["false", "no", "0", "ineligible", "disabled"]

        if row_errors:
            invalid_rows.append({
                "row": index,
                "data": raw,
                "errors": row_errors
            })
            continue

        if email in seen_emails_in_file:
            duplicate_rows.append({
                "row": index,
                "email": email,
                "reason": "Duplicate email entry in uploaded file"
            })
            continue
        seen_emails_in_file.add(email)

        existing_user = participants_collection.find_one({"$or": [{"email": email}, {"user_id": user_id}]})
        if existing_user:
            duplicate_rows.append({
                "row": index,
                "email": email,
                "user_id": user_id,
                "reason": "Participant already registered in system"
            })
            continue

        valid_rows.append({
            "name": name,
            "email": email,
            "department": department,
            "team": team,
            "year": str(year),
            "user_id": user_id,
            "eligible": is_eligible
        })

    return {
        "total_rows": len(raw_rows),
        "valid_count": len(valid_rows),
        "invalid_count": len(invalid_rows),
        "duplicate_count": len(duplicate_rows),
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "duplicate_rows": duplicate_rows
    }


@app.route("/admin/participants")
def admin_participants():
    """Renders the admin participant management dashboard with department and team filtering."""
    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    dept_filter = request.args.get("department", "all").strip()
    team_filter = request.args.get("team", "all").strip()
    eligible_only = request.args.get("eligible_only", "false").lower() == "true"
    search_query = request.args.get("search", "").strip().lower()

    query = {}
    if dept_filter != "all" and dept_filter:
        query["department"] = {"$regex": f"^{re.escape(dept_filter)}$", "$options": "i"}
    if team_filter != "all" and team_filter:
        query["team"] = {"$regex": f"^{re.escape(team_filter)}$", "$options": "i"}
    if eligible_only:
        query["eligible"] = {"$ne": False}
    if search_query:
        query["$or"] = [
            {"name": {"$regex": re.escape(search_query), "$options": "i"}},
            {"email": {"$regex": re.escape(search_query), "$options": "i"}},
            {"user_id": {"$regex": re.escape(search_query), "$options": "i"}}
        ]

    all_participants_raw = list(participants_collection.find().sort("name", 1))

    departments = sorted(list(set(p.get("department", "General") or "General" for p in all_participants_raw)))
    teams = sorted(list(set(p.get("team", "General") or "General" for p in all_participants_raw)))

    filtered_participants = list(participants_collection.find(query).sort("name", 1))
    for p in filtered_participants:
        p.setdefault("department", "General")
        p.setdefault("team", "General")
        p.setdefault("eligible", True)

    return render_template(
        "admin/participants.html",
        participants=filtered_participants,
        departments=departments,
        teams=teams,
        selected_department=dept_filter,
        selected_team=team_filter,
        eligible_only=eligible_only,
        search_query=search_query
    )


@app.route("/api/admin/participants/filter", methods=["GET"])
def api_filter_participants():
    """
    Returns JSON list of participants filtered by department, team, eligibility, and search.
    Enforces admin authorization.
    """
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    dept_filter = request.args.get("department", "all").strip()
    team_filter = request.args.get("team", "all").strip()
    eligible_only = request.args.get("eligible_only", "false").lower() == "true"
    search_query = request.args.get("search", "").strip().lower()

    query = {}
    if dept_filter != "all" and dept_filter:
        query["department"] = {"$regex": f"^{re.escape(dept_filter)}$", "$options": "i"}
    if team_filter != "all" and team_filter:
        query["team"] = {"$regex": f"^{re.escape(team_filter)}$", "$options": "i"}
    if eligible_only:
        query["eligible"] = {"$ne": False}
    if search_query:
        query["$or"] = [
            {"name": {"$regex": re.escape(search_query), "$options": "i"}},
            {"email": {"$regex": re.escape(search_query), "$options": "i"}},
            {"user_id": {"$regex": re.escape(search_query), "$options": "i"}}
        ]

    matched = list(participants_collection.find(query).sort("name", 1))
    all_raw = list(participants_collection.find())

    departments = sorted(list(set(p.get("department", "General") or "General" for p in all_raw)))
    teams = sorted(list(set(p.get("team", "General") or "General" for p in all_raw)))

    result_list = []
    for p in matched:
        result_list.append({
            "user_id": p.get("user_id"),
            "name": p.get("name"),
            "email": p.get("email"),
            "department": p.get("department", "General"),
            "team": p.get("team", "General"),
            "year": p.get("year", "1"),
            "eligible": p.get("eligible", True)
        })

    return jsonify({
        "success": True,
        "participants": result_list,
        "count": len(result_list),
        "departments": departments,
        "teams": teams
    }), 200


@app.route("/api/admin/participants/import/preview", methods=["POST"])
def api_import_participants_preview():
    """
    Parses uploaded Excel/CSV file and returns validation results for admin preview.
    Enforces admin authorization.
    """
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded."}), 400

    file_storage = request.files["file"]

    try:
        parse_result = parse_participant_import_file(file_storage)
        return jsonify({
            "success": True,
            "preview": parse_result
        }), 200
    except ValueError as val_err:
        return jsonify({"success": False, "message": str(val_err)}), 400
    except Exception as err:
        return jsonify({"success": False, "message": f"Failed to process import file: {str(err)}"}), 500


@app.route("/api/admin/participants/import/confirm", methods=["POST"])
def api_import_participants_confirm():
    """
    Commits valid preview rows into MongoDB participants collection.
    Enforces admin authorization.
    """
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    rows_to_import = data.get("participants", [])

    if not isinstance(rows_to_import, list) or not rows_to_import:
        return jsonify({"success": False, "message": "No valid participants provided for import."}), 400

    imported_count = 0
    skipped_count = 0

    for row in rows_to_import:
        name = str(row.get("name", "")).strip()
        email = str(row.get("email", "")).strip().lower()
        user_id = str(row.get("user_id", "")).strip() or email.split("@")[0]
        department = str(row.get("department", "General")).strip()
        team = str(row.get("team", "General")).strip()
        year = str(row.get("year", "1")).strip()
        eligible = bool(row.get("eligible", True))

        if not name or not email:
            skipped_count += 1
            continue

        existing = participants_collection.find_one({"$or": [{"email": email}, {"user_id": user_id}]})
        if existing:
            skipped_count += 1
            continue

        create_participant(
            name=name,
            email=email,
            department=department,
            year=year,
            user_id=user_id,
            password="MeetIQ@123Participant",
            team=team,
            eligible=eligible
        )
        imported_count += 1

    return jsonify({
        "success": True,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "message": f"Successfully imported {imported_count} participant(s)."
    }), 200


@app.route("/api/admin/meetings/<meeting_id>/participants/bulk-invite", methods=["POST"])
def api_bulk_invite_meeting_participants(meeting_id):
    """
    Invites participants to a meeting by specific IDs or department/team/eligibility criteria.
    Prevents invitations to ineligible participants and avoids duplicate invitations.
    Enforces admin authorization.
    """
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    meeting = get_admin_meeting(meeting_id)
    if not meeting:
        return jsonify({"success": False, "message": "Meeting not found or unauthorized."}), 404

    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    participant_ids = payload.get("participant_ids", [])
    if isinstance(participant_ids, str):
        participant_ids = [p.strip() for p in participant_ids.split(",") if p.strip()]

    dept_filter = payload.get("department", "all")
    team_filter = payload.get("team", "all")
    eligible_only = str(payload.get("eligible_only", "true")).lower() == "true"

    query = {}
    if participant_ids:
        query["$or"] = [{"user_id": {"$in": participant_ids}}, {"email": {"$in": participant_ids}}]
    else:
        if dept_filter != "all" and dept_filter:
            query["department"] = {"$regex": f"^{re.escape(dept_filter)}$", "$options": "i"}
        if team_filter != "all" and team_filter:
            query["team"] = {"$regex": f"^{re.escape(team_filter)}$", "$options": "i"}

    target_participants = list(participants_collection.find(query))

    invited_added = 0
    ineligible_skipped = 0

    existing_invited = set(meeting.get("invited_participants", []))

    for p in target_participants:
        p_user_id = p.get("user_id")
        p_email = p.get("email")
        p_eligible = p.get("eligible", True)

        if p_eligible is False and eligible_only:
            ineligible_skipped += 1
            continue

        if p_user_id not in existing_invited and p_email not in existing_invited:
            meetings_collection.update_one(
                {"_id": meeting["_id"]},
                {"$addToSet": {"invited_participants": p_user_id}}
            )
            existing_invited.add(p_user_id)
            invited_added += 1

            create_notification(
                participant_id=p_user_id,
                participant_email=p_email,
                meeting_id=str(meeting["_id"]),
                meeting_title=meeting.get("title", "Meeting"),
                meeting_date=meeting.get("formatted_date", meeting.get("date", "")),
                meeting_time=meeting.get("formatted_time", meeting.get("time", "")),
                message=f"You have been invited to {meeting.get('title', 'a meeting')}."
            )

    return jsonify({
        "success": True,
        "invited_added": invited_added,
        "ineligible_skipped": ineligible_skipped,
        "total_invited": len(existing_invited),
        "message": f"Invited {invited_added} participant(s). {ineligible_skipped} ineligible participant(s) skipped."
    }), 200


# =========================================================
# ADMIN ATTENDANCE
# =========================================================


@app.route("/admin/attendance")
def admin_attendance():

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(
            url_for("admin_login")
        )

    meetings = list(
        meetings_collection.find({
            "created_by_email": session.get("user_id")
        }).sort([
            ("date", -1),
            ("time", -1)
        ])
    )

    attendance_meetings = []

    for meeting in meetings:

        attendance_records = meeting.get(
            "attendance",
            []
        )

        participants = []

        for participant_id in meeting.get(
            "invited_participants",
            []
        ):

            participant = participants_collection.find_one({
                "user_id": participant_id
            })

            participant_name = (
                participant.get("name", "Participant")
                if participant
                else participant_id
            )

            participant_email = (
                participant.get("email", "")
                if participant
                else ""
            )

            sessions = [
                record
                for record in attendance_records
                if record.get("participant_id") == participant_id
            ]

            total_seconds = sum(
                int(record.get("duration_seconds", 0) or 0)
                for record in sessions
            )

            join_times = [
                record.get("join_time")
                for record in sessions
                if record.get("join_time")
            ]

            leave_times = [
                record.get("leave_time")
                for record in sessions
                if record.get("leave_time")
            ]

            participants.append({
                "participant_id": participant_id,
                "participant_name": participant_name,
                "participant_email": participant_email,
                "attended": len(sessions) > 0,
                "sessions": sessions,
                "session_count": len(sessions),
                "total_seconds": total_seconds,
                "first_join": min(join_times) if join_times else None,
                "last_leave": max(leave_times) if leave_times else None
            })

        attended_count = sum(
            1
            for participant in participants
            if participant["attended"]
        )

        attendance_meetings.append({
            "meeting": meeting,
            "participants": participants,
            "invited_count": len(participants),
            "attended_count": attended_count
        })

# =========================================================
# ADMIN ANALYTICS & AI INSIGHTS HELPERS AND ROUTES
# =========================================================

def compute_admin_analytics(admin_email, time_range="all"):
    """
    Calculate real admin analytics metrics based on meetings created by admin_email.
    Supports time_range filtering: 'all', 'today', '7days', '30days'.
    """
    from bson.objectid import ObjectId

    now = get_utc_now()
    query = {"created_by_email": admin_email}

    try:
        meetings = list(meetings_collection.find(query).sort([("date", -1), ("time", -1)]))
    except Exception as error:
        print("MeetIQ DB Error in compute_admin_analytics:", error)
        meetings = []

    if time_range != "all":
        filtered = []
        for m in meetings:
            m_date_str = m.get("date")
            m_ended = m.get("ended_at")
            m_dt = None

            if isinstance(m_ended, datetime):
                m_dt = m_ended
            elif m_date_str:
                try:
                    m_dt = datetime.strptime(m_date_str, "%Y-%m-%d")
                except ValueError:
                    pass

            if m_dt:
                delta_days = (now - m_dt).days
                if time_range == "today" and (delta_days == 0 or m_date_str == now.strftime("%Y-%m-%d")):
                    filtered.append(m)
                elif time_range == "7days" and delta_days <= 7:
                    filtered.append(m)
                elif time_range == "30days" and delta_days <= 30:
                    filtered.append(m)
            else:
                filtered.append(m)
        meetings = filtered

    total_meetings = len(meetings)
    completed_meetings = [m for m in meetings if m.get("status") == "completed"]
    live_meetings = [m for m in meetings if m.get("status") == "live"]
    upcoming_meetings = [m for m in meetings if m.get("status") == "upcoming"]

    all_invited = set()
    for m in meetings:
        all_invited.update(m.get("invited_participants", []))
    total_participants = len(all_invited)

    all_attendance_sessions = []
    unique_attended_participants = set()
    total_invited_count = 0
    total_actual_attended_count = 0

    meeting_analytics = []

    for m in meetings:
        invited_list = m.get("invited_participants", [])
        expected_count = len(invited_list)
        total_invited_count += expected_count

        attendance_records = m.get("attendance", [])
        m_attended_participants = set(
            rec.get("participant_id")
            for rec in attendance_records
            if rec.get("participant_id")
        )
        actual_count = len(m_attended_participants)
        total_actual_attended_count += actual_count
        unique_attended_participants.update(m_attended_participants)

        meeting_duration_sec = 0
        ended_at = m.get("ended_at")
        started_at = m.get("started_at")
        if isinstance(ended_at, datetime) and isinstance(started_at, datetime):
            meeting_duration_sec = max(0, int((ended_at - started_at).total_seconds()))
        else:
            if attendance_records:
                session_durations = [
                    int(rec.get("duration_seconds", 0) or 0)
                    for rec in attendance_records
                ]
                meeting_duration_sec = max(session_durations, default=0)

        m_session_durations = [
            int(rec.get("duration_seconds", 0) or 0)
            for rec in attendance_records
            if rec.get("leave_time") or rec.get("duration_seconds")
        ]
        avg_participant_dur_sec = (
            sum(m_session_durations) / len(m_session_durations)
            if m_session_durations else 0
        )

        all_attendance_sessions.extend(attendance_records)

        m_attendance_rate = (
            round((actual_count / expected_count) * 100, 1)
            if expected_count > 0 else 0.0
        )

        meeting_analytics.append({
            "meeting_id": str(m.get("_id")),
            "title": m.get("title") or m.get("topic") or "Untitled Meeting",
            "date": m.get("date", "N/A"),
            "time": m.get("time", "N/A"),
            "status": m.get("status", "upcoming"),
            "duration_seconds": meeting_duration_sec,
            "duration_minutes": round(meeting_duration_sec / 60.0, 1),
            "expected_participants": expected_count,
            "actual_participants": actual_count,
            "attendance_rate": m_attendance_rate,
            "avg_participant_duration_minutes": round(avg_participant_dur_sec / 60.0, 1),
            "summary_available": bool(m.get("ai_summary"))
        })

    completed_durations = [
        ma["duration_seconds"]
        for ma in meeting_analytics
        if ma["status"] == "completed" and ma["duration_seconds"] > 0
    ]
    avg_meeting_duration_sec = (
        sum(completed_durations) / len(completed_durations)
        if completed_durations else 0
    )

    closed_sessions = [
        rec for rec in all_attendance_sessions
        if rec.get("leave_time") or rec.get("status") == "completed"
    ]
    session_durations = [
        int(rec.get("duration_seconds", 0) or 0)
        for rec in closed_sessions
    ]
    avg_attendance_duration_sec = (
        sum(session_durations) / len(session_durations)
        if session_durations else 0
    )
    max_session_duration_sec = max(session_durations, default=0)
    min_session_duration_sec = min(session_durations, default=0) if session_durations else 0

    overall_attendance_rate = (
        round((total_actual_attended_count / total_invited_count) * 100, 1)
        if total_invited_count > 0 else 0.0
    )

    return {
        "time_range": time_range,
        "total_meetings": total_meetings,
        "completed_meetings": len(completed_meetings),
        "live_meetings": len(live_meetings),
        "upcoming_meetings": len(upcoming_meetings),
        "total_participants": total_participants,
        "unique_attended_participants": len(unique_attended_participants),
        "avg_meeting_duration_minutes": round(avg_meeting_duration_sec / 60.0, 1),
        "avg_attendance_duration_minutes": round(avg_attendance_duration_sec / 60.0, 1),
        "overall_attendance_rate": overall_attendance_rate,
        "total_attendance_sessions": len(all_attendance_sessions),
        "closed_attendance_sessions": len(closed_sessions),
        "max_session_duration_minutes": round(max_session_duration_sec / 60.0, 1),
        "min_session_duration_minutes": round(min_session_duration_sec / 60.0, 1),
        "meeting_analytics": meeting_analytics
    }


def compute_admin_ai_insights(admin_email, selected_meeting_id=None):
    """
    Aggregate AI summaries, discussion points, decisions, action items,
    moderation toxicity events, and attention signals for admin's meetings.
    """
    from bson.objectid import ObjectId

    query = {"created_by_email": admin_email}
    if selected_meeting_id:
        try:
            query["_id"] = ObjectId(selected_meeting_id)
        except Exception:
            pass

    try:
        meetings = list(meetings_collection.find(query).sort([("date", -1), ("time", -1)]))
    except Exception as error:
        print("MeetIQ DB Error in compute_admin_ai_insights:", error)
        meetings = []

    discussion_points = []
    decisions = []
    action_items = []
    moderation_events = []
    attention_signals = []

    meetings_with_summary_count = 0
    total_meetings = len(meetings)

    for m in meetings:
        m_id_str = str(m.get("_id"))
        m_title = m.get("title") or m.get("topic") or "Untitled Meeting"
        m_date = m.get("date", "N/A")

        ai_summary = m.get("ai_summary")
        if ai_summary and isinstance(ai_summary, dict):
            meetings_with_summary_count += 1

            for dp in ai_summary.get("discussion_points", []):
                if isinstance(dp, str) and dp.strip():
                    discussion_points.append({
                        "meeting_id": m_id_str,
                        "meeting_title": m_title,
                        "date": m_date,
                        "point": dp.strip()
                    })

            for dec in ai_summary.get("decisions", []):
                if isinstance(dec, str) and dec.strip():
                    decisions.append({
                        "meeting_id": m_id_str,
                        "meeting_title": m_title,
                        "date": m_date,
                        "decision": dec.strip()
                    })

            for act in ai_summary.get("action_items", []):
                if isinstance(act, dict):
                    action_items.append({
                        "meeting_id": m_id_str,
                        "meeting_title": m_title,
                        "date": m_date,
                        "task": act.get("task") or act.get("action", "N/A"),
                        "owner": act.get("owner") or act.get("assignee") or "Unassigned"
                    })
                elif isinstance(act, str) and act.strip():
                    action_items.append({
                        "meeting_id": m_id_str,
                        "meeting_title": m_title,
                        "date": m_date,
                        "task": act.strip(),
                        "owner": "Unassigned"
                    })

        for te in m.get("toxicity_events", []):
            if isinstance(te, dict):
                score = float(te.get("toxicity_score", 0) or 0)
                ts = te.get("timestamp")
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts or "")
                moderation_events.append({
                    "meeting_id": m_id_str,
                    "meeting_title": m_title,
                    "date": m_date,
                    "participant_id": te.get("participant_id", "Unknown"),
                    "toxicity_score": round(score, 3),
                    "action": te.get("action", "flagged"),
                    "message": te.get("message", "Measured model signal"),
                    "timestamp": ts_str
                })

        for att in m.get("attention_alerts", []):
            if isinstance(att, dict):
                attention_signals.append({
                    "meeting_id": m_id_str,
                    "meeting_title": m_title,
                    "date": m_date,
                    "participant_id": att.get("participant_id", "Unknown"),
                    "participant_name": att.get("participant_name") or att.get("participant_id", "Participant"),
                    "score": att.get("score", 0),
                    "status": att.get("status", "Signal Monitored"),
                    "time": att.get("time", "")
                })

    mod_scores = [ev["toxicity_score"] for ev in moderation_events]
    avg_toxicity_score = round(sum(mod_scores) / len(mod_scores), 3) if mod_scores else 0.0
    highest_toxicity_score = round(max(mod_scores, default=0.0), 3)
    meetings_with_moderation_count = len(set(ev["meeting_id"] for ev in moderation_events))

    att_scores = [att["score"] for att in attention_signals]
    avg_attention_score = round(sum(att_scores) / len(att_scores), 1) if att_scores else 100.0
    meetings_with_attention_count = len(set(att["meeting_id"] for att in attention_signals))
    monitored_participants_count = len(set(att["participant_id"] for att in attention_signals))

    all_admin_meetings = list(meetings_collection.find({"created_by_email": admin_email}).sort([("date", -1)]))

    return {
        "selected_meeting_id": selected_meeting_id,
        "total_meetings": total_meetings,
        "meetings_with_summary": meetings_with_summary_count,
        "total_discussion_points": len(discussion_points),
        "total_decisions": len(decisions),
        "total_action_items": len(action_items),
        "discussion_points": discussion_points,
        "decisions": decisions,
        "action_items": action_items,
        "moderation_summary": {
            "total_events": len(moderation_events),
            "meetings_with_moderation": meetings_with_moderation_count,
            "avg_toxicity_score": avg_toxicity_score,
            "highest_toxicity_score": highest_toxicity_score,
            "events": moderation_events
        },
        "attention_summary": {
            "avg_attention_score": avg_attention_score,
            "meetings_with_attention": meetings_with_attention_count,
            "monitored_participants": monitored_participants_count,
            "total_alerts": len(attention_signals),
            "signals": attention_signals
        },
        "admin_meetings_list": [
            {
                "id": str(m["_id"]),
                "title": m.get("title") or m.get("topic") or "Untitled Meeting",
                "date": m.get("date", "")
            }
            for m in all_admin_meetings
        ]
    }


@app.route("/admin/analytics")
def admin_analytics():
    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    time_range = request.args.get("time_range", "all")
    admin_email = session.get("user_id")

    analytics_data = compute_admin_analytics(admin_email, time_range=time_range)

    return render_template(
        "admin/analytics.html",
        analytics=analytics_data
    )


@app.route("/admin/ai-insights")
def admin_ai_insights():
    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    meeting_id = request.args.get("meeting_id")
    admin_email = session.get("user_id")

    insights_data = compute_admin_ai_insights(admin_email, selected_meeting_id=meeting_id)

    return render_template(
        "admin/ai_insights.html",
        insights=insights_data
    )


@app.route("/api/admin/analytics")
def api_admin_analytics():
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    time_range = request.args.get("time_range", "all")
    admin_email = session.get("user_id")

    analytics_data = compute_admin_analytics(admin_email, time_range=time_range)

    return jsonify({
        "success": True,
        "analytics": analytics_data
    })


@app.route("/api/admin/ai-insights")
def api_admin_ai_insights():
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    meeting_id = request.args.get("meeting_id")
    admin_email = session.get("user_id")

    insights_data = compute_admin_ai_insights(admin_email, selected_meeting_id=meeting_id)

    return jsonify({
        "success": True,
        "insights": insights_data
    })


# =========================================================
# ATTENDANCE HELPERS
# =========================================================

def parse_duration_minutes(duration_value):
    """
    Convert common meeting-duration formats to minutes.
    Examples: 60, "60", "60 minutes", "1 hour", "1.5 hours".
    """
    if duration_value is None:
        return 0.0

    try:
        if isinstance(duration_value, (int, float)):
            return float(duration_value)
    except Exception:
        pass

    text = str(duration_value).strip().lower()

    if not text:
        return 0.0

    number_match = re.search(r"(\d+(?:\.\d+)?)", text)

    if not number_match:
        return 0.0

    value = float(number_match.group(1))

    if "hour" in text or "hr" in text:
        return value * 60.0

    return value


def finalize_attendance_session(meeting_id, participant_id, end_time=None):
    """
    Close open attendance session(s) for a participant.
    Returns tuple (finalized_bool, duration_seconds).
    """
    from bson.objectid import ObjectId

    if end_time is None:
        end_time = get_utc_now()

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return False, 0

    meeting = meetings_collection.find_one({
        "_id": meeting_object_id
    })

    if not meeting:
        return False, 0

    attendance = meeting.get("attendance", [])

    open_indexes = [
        index
        for index, record in enumerate(attendance)
        if (
            record.get("participant_id") == participant_id
            and not record.get("leave_time")
        )
    ]

    if not open_indexes:
        return False, 0

    finalized = False
    last_duration = 0

    for target_index in open_indexes:
        target = attendance[target_index]
        join_time = target.get("join_time")

        if isinstance(join_time, str):
            try:
                join_time = datetime.fromisoformat(join_time)
            except Exception:
                join_time = end_time
        elif not isinstance(join_time, datetime):
            join_time = end_time

        duration_seconds = max(
            0,
            int((end_time - join_time).total_seconds())
        )
        last_duration = duration_seconds

        target["leave_time"] = end_time
        target["duration_seconds"] = duration_seconds
        target["duration_minutes"] = round(
            duration_seconds / 60.0,
            2
        )
        target["status"] = "completed"
        finalized = True

    if finalized:
        meetings_collection.update_one(
            {"_id": meeting_object_id},
            {"$set": {"attendance": attendance}}
        )

    return True, last_duration


def finalize_meeting_attendance(meeting_id, end_time=None):
    """
    Close every open attendance session when the host ends a meeting.
    """
    from bson.objectid import ObjectId

    if end_time is None:
        end_time = get_utc_now()

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return 0

    meeting = meetings_collection.find_one({
        "_id": meeting_object_id
    })

    if not meeting:
        return 0

    attendance = meeting.get("attendance", [])

    if not attendance:
        return 0

    changed = False

    for record in attendance:
        if record.get("leave_time"):
            continue

        join_time = record.get("join_time")

        if isinstance(join_time, str):
            try:
                join_time = datetime.fromisoformat(join_time)
            except Exception:
                join_time = end_time
        elif not isinstance(join_time, datetime):
            join_time = end_time

        duration_seconds = max(
            0,
            int((end_time - join_time).total_seconds())
        )

        record["leave_time"] = end_time
        record["duration_seconds"] = duration_seconds
        record["duration_minutes"] = round(
            duration_seconds / 60.0,
            2
        )
        record["status"] = "completed"
        changed = True

    if changed:
        meetings_collection.update_one(
            {"_id": meeting_object_id},
            {"$set": {"attendance": attendance}}
        )

    return sum(
        1
        for record in attendance
        if record.get("status") == "completed"
    )


def get_participant_attendance_percentage(meetings, participant_id):
    """
    Attendance is meeting-based for participants:
    attended meetings / completed meetings the participant was invited to.
    A meeting counts as attended when at least one attendance session exists.
    """

    eligible_meetings = [
        meeting
        for meeting in meetings
        if meeting.get("status") == "completed"
    ]

    if not eligible_meetings:
        return 0

    attended_meetings = 0

    for meeting in eligible_meetings:
        attended = any(
            record.get("participant_id") == participant_id
            for record in meeting.get("attendance", [])
        )

        if attended:
            attended_meetings += 1

    return round(
        (attended_meetings / len(eligible_meetings)) * 100
    )



# =========================================================
# PARTICIPANT DASHBOARD
# =========================================================


@app.route("/participant/dashboard")
def participant_dashboard():

    if session.get("role") != "participant":

        flash(
            "Please sign in as a participant.",
            "error"
        )

        return redirect(
            url_for("participant_login")
        )

    # Get the logged-in participant's notifications
    participant_id = session.get("user_id")

    notifications = list(
        notifications_collection.find({
            "participant_id": participant_id
        }).sort(
            "_id",
            -1
        )
    )

    # Count unread notifications
    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    # -----------------------------------------------------
    # GET THE LOGGED-IN PARTICIPANT'S MEETINGS
    # -----------------------------------------------------
    participant_meetings_list = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort([
            ("date", 1),
            ("time", 1)
        ])
    )

    # Include upcoming and currently live meetings.
    upcoming_meetings = [
        meeting
        for meeting in participant_meetings_list
        if meeting.get("status", "upcoming") in ["upcoming", "live"]
    ][:5]

    # Number shown in the Upcoming Meetings card.
    upcoming_meetings_count = sum(
        1
        for meeting in participant_meetings_list
        if meeting.get("status", "upcoming") in ["upcoming", "live"]
    )

    # Number of invited meetings that already have an AI summary.
    ai_summary_count = sum(
        1
        for meeting in participant_meetings_list
        if meeting.get("ai_summary")
    )

    attendance_percentage = get_participant_attendance_percentage(
        participant_meetings_list,
        participant_id
    )

    return render_template(
        "participants/dashboards.html",
        notifications=notifications,
        unread_notifications=unread_notifications,
        meetings=upcoming_meetings,
        upcoming_meetings_count=upcoming_meetings_count,
        ai_summary_count=ai_summary_count,
        attendance_percentage=attendance_percentage
    )
@app.route("/api/participant/notifications/<notification_id>/read", methods=["POST"])
def mark_participant_notification_read(notification_id):

    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        notification_object_id = ObjectId(notification_id)
    except Exception:
        return jsonify({
            "success": False,
            "message": "Invalid notification ID."
        }), 400

    notification = notifications_collection.find_one({
        "_id": notification_object_id,
        "participant_id": participant_id
    })

    if not notification:
        return jsonify({
            "success": False,
            "message": "Notification not found."
        }), 404

    notifications_collection.update_one(
        {
            "_id": notification_object_id,
            "participant_id": participant_id
        },
        {
            "$set": {
                "read": True
            }
        }
    )

    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    return jsonify({
        "success": True,
        "unread_notifications": unread_notifications
    })


# =========================================================
# PARTICIPANT ATTENDANCE
# =========================================================

@app.route("/participant/attendance")
def participant_attendance():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort([
            ("date", -1),
            ("time", -1)
        ])
    )

    attendance_rows = []

    for meeting in meetings:

        participant_sessions = [
            record
            for record in meeting.get("attendance", [])
            if record.get("participant_id") == participant_id
        ]

        total_seconds = sum(
            int(record.get("duration_seconds", 0) or 0)
            for record in participant_sessions
        )

        last_session = (
            participant_sessions[-1]
            if participant_sessions
            else None
        )

        attendance_rows.append({
            "meeting": meeting,
            "sessions": participant_sessions,
            "session_count": len(participant_sessions),
            "total_seconds": total_seconds,
            "last_session": last_session
        })

    attendance_percentage = get_participant_attendance_percentage(
        meetings,
        participant_id
    )

    completed_meetings = [
        meeting
        for meeting in meetings
        if meeting.get("status") == "completed"
    ]

    attended_meetings = sum(
        1
        for meeting in completed_meetings
        if any(
            record.get("participant_id") == participant_id
            for record in meeting.get("attendance", [])
        )
    )

    return render_template(
        "participants/attendance.html",
        attendance_rows=attendance_rows,
        attendance_percentage=attendance_percentage,
        attended_meetings=attended_meetings,
        eligible_meetings=len(completed_meetings)
    )


# =========================================================
# ATTENDANCE: JOIN
# =========================================================

@app.route("/api/attendance/<meeting_id>/join", methods=["POST"])
def attendance_join(meeting_id):

    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")
    participant_name = session.get(
        "user_name",
        "Participant"
    )

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    meeting = meetings_collection.find_one({
        "_id": meeting_object_id
    })

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    invited_participants = meeting.get("invited_participants", [])
    if participant_id not in invited_participants:
        return jsonify({
            "success": False,
            "message": "Participant not authorized or invited to this meeting."
        }), 403

    if meeting.get("status") != "live":
        return jsonify({
            "success": False,
            "message": "Attendance can only be recorded while the meeting is live."
        }), 400

    # Do not create duplicate open sessions if the join request
    # is accidentally sent twice.
    open_session = next(
        (
            record
            for record in meeting.get("attendance", [])
            if (
                record.get("participant_id") == participant_id
                and not record.get("leave_time")
            )
        ),
        None
    )

    if open_session:
        join_t = open_session.get("join_time")
        join_t_str = join_t.isoformat() if isinstance(join_t, datetime) else str(join_t)
        return jsonify({
            "success": True,
            "already_recorded": True,
            "session_id": open_session.get("session_id"),
            "join_time": join_t_str
        }), 200

    join_time = get_utc_now()
    session_id = str(uuid.uuid4())

    attendance_record = {
        "session_id": session_id,
        "participant_id": participant_id,
        "participant_name": participant_name,
        "join_time": join_time,
        "leave_time": None,
        "duration_seconds": 0,
        "duration_minutes": 0,
        "status": "active"
    }

    meetings_collection.update_one(
        {
            "_id": meeting_object_id,
            "status": "live"
        },
        {
            "$push": {
                "attendance": attendance_record
            }
        }
    )

    print(
        "ATTENDANCE JOIN:",
        participant_id,
        meeting_id,
        join_time
    )

    return jsonify({
        "success": True,
        "already_recorded": False,
        "session_id": session_id,
        "join_time": join_time.isoformat()
    }), 200


# =========================================================
# ATTENDANCE: LEAVE
# =========================================================

@app.route("/api/attendance/<meeting_id>/leave", methods=["POST"])
def attendance_leave(meeting_id):

    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    meeting = meetings_collection.find_one({"_id": meeting_object_id})
    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    participant_id = session.get("user_id")

    finalized, duration_seconds = finalize_attendance_session(
        meeting_id,
        participant_id,
        get_utc_now()
    )

    print(
        "ATTENDANCE LEAVE:",
        participant_id,
        meeting_id,
        finalized,
        duration_seconds
    )

    return jsonify({
        "success": True,
        "recorded": bool(finalized),
        "duration_seconds": duration_seconds
    }), 200


@app.route("/participant/meetings")
def participant_meetings():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    notifications = list(
        notifications_collection.find({
            "participant_id": participant_id
        }).sort("_id", -1)
    )

    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort("date", 1)
    )

    return render_template(
        "participants/meetings.html",
        meetings=meetings,
        notifications=notifications,
        unread_notifications=unread_notifications
    )
@app.route("/participant/meetings/<meeting_id>")
def participant_meeting(meeting_id):

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "Meeting not found.",
            "error"
        )
        return redirect(
            url_for("participant_dashboard")
        )

    invited_participants = meeting.get(
        "invited_participants",
        []
    )

    if participant_id not in invited_participants:
        flash(
            "You are not authorized to access this meeting.",
            "error"
        )
        return redirect(
            url_for("participant_dashboard")
        )

    return render_template(
        "participants/meeting.html",
        meeting=meeting
    )
@app.route("/participant/summary/<meeting_id>")
def participant_summary(meeting_id):

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "Meeting not found or you are not authorized to view it.",
            "error"
        )
        return redirect(
            url_for("participant_meetings")
        )

    ai_summary = meeting.get(
        "ai_summary",
        {
            "discussion_points": [],
            "decisions": [],
            "action_items": []
        }
    )

    return render_template(
        "participants/summary.html",
        meeting=meeting,
        ai_summary=ai_summary
    )
@app.route("/participant/summaries")
def participant_summaries():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id,
            "ai_summary": {
                "$exists": True
            }
        }).sort("date", -1)
    )

    return render_template(
        "participants/summaries.html",
        meetings=meetings
    )
# =========================================================
# PARTICIPANT AI INSIGHTS
# =========================================================

@app.route("/participant/insights")
def participant_insights():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    notifications = list(
        notifications_collection.find({
            "participant_id": participant_id
        }).sort("_id", -1)
    )

    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort(
            "date",
            -1
        )
    )

    insights = []

    for meeting in meetings:

        attention_alerts = meeting.get(
            "attention_alerts",
            []
        )

        participant_alerts = [
            alert
            for alert in attention_alerts
            if alert.get("participant_id") == participant_id
        ]

        ai_summary = meeting.get(
            "ai_summary",
            {}
        )

        insights.append({
            "meeting": meeting,
            "attention_alerts": participant_alerts,
            "alert_count": len(participant_alerts),
            "summary_available": bool(ai_summary),
            "discussion_count": len(
                ai_summary.get("discussion_points", [])
            ) if isinstance(ai_summary, dict) else 0,
            "decision_count": len(
                ai_summary.get("decisions", [])
            ) if isinstance(ai_summary, dict) else 0,
            "action_count": len(
                ai_summary.get("action_items", [])
            ) if isinstance(ai_summary, dict) else 0
        })

    total_meetings = len(insights)

    meetings_with_summary = sum(
        1
        for item in insights
        if item["summary_available"]
    )

    total_attention_alerts = sum(
        item["alert_count"]
        for item in insights
    )

    return render_template(
        "participants/insights.html",
        insights=insights,
        total_meetings=total_meetings,
        meetings_with_summary=meetings_with_summary,
        total_attention_alerts=total_attention_alerts,
        notifications=notifications,
        unread_notifications=unread_notifications
    )


# =========================================================
# ATTENTION MONITORING API
# =========================================================

@app.route("/api/attention/<meeting_id>", methods=["POST"])
def attention_api(meeting_id):

    if session.get("role") != "participant":
        return {
            "success": False,
            "message": "Unauthorized"
        }, 401

    from bson.objectid import ObjectId
    from datetime import datetime, timezone

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id
        })
    except Exception:
        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found or participant is not authorized"
        }, 404

    # Attention monitoring is allowed only while the meeting is live
    if meeting.get("status") != "live":
        return {
            "success": False,
            "message": "Attention monitoring is available only during a live meeting"
        }, 400

    data = request.get_json(silent=True) or {}

    score = data.get("score", 0)

    try:
        score = int(score)
    except (ValueError, TypeError):
        score = 0

    # Keep score between 0 and 100
    score = max(0, min(100, score))

    if score == 100:
        status = "Attentive"

    elif score == 75:
        status = "Partially Attentive"

    else:
        status = "Not Attentive"

    # Store attention warnings
    if score in [75, 0]:

        alert = {
            "participant_id": participant_id,
            "participant_name": session.get("user_name"),
            "score": score,
            "status": status,
            "time": datetime.now().strftime("%H:%M:%S")
        }

        try:

            meetings_collection.update_one(
                {
                    "_id": ObjectId(meeting_id),
                    "invited_participants": participant_id
                },
                {
                    "$push": {
                        "attention_alerts": alert
                    }
                }
            )

        except Exception as error:

            print(
                "Attention alert storage error:",
                error
            )

    return {
        "success": True,
        "meeting_id": meeting_id,
        "score": score,
        "status": status
    }
# =========================================================
# ADMIN ATTENTION ALERTS
# =========================================================

@app.route("/api/admin/attention/<meeting_id>")
def admin_attention_alerts(meeting_id):

    if session.get("role") != "admin":
        return {
            "success": False,
            "message": "Unauthorized"
        }, 401

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:

        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })

    except Exception:

        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found"
        }, 404

    alerts = meeting.get(
        "attention_alerts",
        []
    )

    return {
        "success": True,
        "alerts": alerts
    }
# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    flash(
        "You have been logged out successfully.",
        "success"
    )

    return redirect(
        url_for("role")
    )

@app.route("/test-email")
def test_email():

    msg = Message(
        subject="MeetIQ Email Test",
        recipients=["YOUR_TEST_EMAIL@gmail.com"]
    )

    msg.body = """
Hello!

This is a test email from MeetIQ.

If you received this email, the MeetIQ email system is working correctly.

Regards,
MeetIQ
"""

    mail.send(msg)

    return "Test email sent successfully!"
@app.route("/api/participant/meeting-status/<meeting_id>")
def participant_meeting_status(meeting_id):

    if session.get("role") != "participant":
        return {
            "success": False,
            "message": "Unauthorized"
        }, 401

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id
        })
    except Exception:
        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found or unauthorized"
        }, 404

    return {
        "success": True,
        "status": meeting.get("status", "upcoming")
    }
@socketio.on("join_meeting")
def handle_join_meeting(data):
    print("JOIN MEETING EVENT:", data)
    meeting_id = data.get("meeting_id")

    if not meeting_id:
        return

    room = f"meeting_{meeting_id}"

    join_room(room)

    participant_id = session.get("user_id")
    participant_name = session.get("user_name")
    role = session.get("role")

    sid = getattr(request, "sid", None)
    if sid and meeting_id:
        socket_meeting_rooms[sid] = {
            "meeting_id": str(meeting_id),
            "participant_id": participant_id,
            "role": role
        }

    emit(
        "participant_joined",
        {
            "participant_id": participant_id,
            "participant_name": participant_name,
            "message": "A participant joined the meeting."
        },
        room=room,
        include_self=False
    )

def validate_same_meeting_sockets(meeting_id, target_id):
    """
    Verify that current socket and target participant belong to the same meeting.
    Returns target_socket_id if valid, else None.
    """
    if not meeting_id or not target_id:
        return None

    sid = getattr(request, "sid", None)
    if not sid or not isinstance(sid, str):
        try:
            curr_obj = request._get_current_object()
            sid = getattr(curr_obj, "sid", None)
        except Exception:
            sid = None

    if not sid or not isinstance(sid, str):
        return None

    sender_info = socket_meeting_rooms.get(sid)
    if not sender_info or sender_info.get("meeting_id") != str(meeting_id):
        return None

    target_socket_id = socket_users.get(target_id)
    if not target_socket_id:
        return None

    target_info = socket_meeting_rooms.get(target_socket_id)
    if not target_info or target_info.get("meeting_id") != str(meeting_id):
        return None

    return target_socket_id


@socketio.on("webrtc_offer")
def handle_webrtc_offer(data):

    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    offer = data.get("offer")

    if not meeting_id or not target_id or not offer:
        return

    target_socket_id = validate_same_meeting_sockets(meeting_id, target_id)

    if not target_socket_id:
        print(
            "Blocked cross-meeting/invalid WebRTC offer:",
            target_id
        )
        return

    emit(
        "webrtc_offer",
        {
            "sender_id": session.get("user_id"),
            "offer": offer
        },
        to=target_socket_id
    )


@socketio.on("webrtc_ice_candidate")
def handle_webrtc_ice_candidate(data):

    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    candidate = data.get("candidate")

    if not meeting_id or not target_id or not candidate:
        return

    target_socket_id = validate_same_meeting_sockets(meeting_id, target_id)

    if not target_socket_id:
        print(
            "Blocked cross-meeting/invalid ICE candidate:",
            target_id
        )
        return

    emit(
        "webrtc_ice_candidate",
        {
            "sender_id": session.get("user_id"),
            "candidate": candidate
        },
        to=target_socket_id
    )


@socketio.on("webrtc_answer")
def handle_webrtc_answer(data):

    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    answer = data.get("answer")

    if not meeting_id or not target_id or not answer:
        return

    target_socket_id = validate_same_meeting_sockets(meeting_id, target_id)

    if not target_socket_id:
        print(
            "Blocked cross-meeting/invalid WebRTC answer:",
            target_id
        )
        return

    emit(
        "webrtc_answer",
        {
            "sender_id": session.get("user_id"),
            "answer": answer
        },
        to=target_socket_id
    )


@socketio.on("leave_meeting")
def handle_leave_meeting(data):

    meeting_id = data.get("meeting_id")

    if not meeting_id:
        return

    room = f"meeting_{meeting_id}"

    leave_room(room)

    sid = getattr(request, "sid", None)
    if sid and sid in socket_meeting_rooms:
        socket_meeting_rooms.pop(sid, None)

    participant_id = session.get("user_id")
    if session.get("role") == "participant" and participant_id:
        finalize_attendance_session(meeting_id, participant_id, get_utc_now())

    emit(
        "participant_left",
        {
            "message": "A participant left the meeting."
        },
        room=room,
        include_self=False
    )


@socketio.on("send_chat_message")
def handle_chat_message(data):

    meeting_id = data.get("meeting_id")
    message = data.get("message")

    if not meeting_id or not message:
        return

    sid = getattr(request, "sid", None)
    sender_info = socket_meeting_rooms.get(sid) if sid else None
    if not sender_info or sender_info.get("meeting_id") != str(meeting_id):
        print("Blocked chat message from non-room socket.")
        return

    message = str(message).strip()[:1000]

    if not message:
        return

    user_id = session.get("user_id")
    user_name = session.get("user_name", "User")

    if not user_id:
        return

    room = f"meeting_{meeting_id}"

    emit(
        "receive_chat_message",
        {
            "sender_id": user_id,
            "sender_name": user_name or "User",
            "message": message
        },
        room=room
    )
# ============================================================
# WHISPER SPEECH-TO-TEXT
# ============================================================

whisper_path = (
    Path(__file__).resolve().parent.parent
    / "ai-services"
    / "speech-to-text"
)

if str(whisper_path) not in sys.path:
    sys.path.insert(0, str(whisper_path))

try:
    import speech_to_text
    model = getattr(speech_to_text, "model", None)
    print("MeetIQ: Whisper module connected (shared model).")
except (ImportError, Exception) as e:
    speech_to_text = None
    model = None
    print("MeetIQ: Whisper module not available:", e)
# ============================================================
# =========================================================
# AI MEETING SUMMARY
# =========================================================

def build_meeting_transcript(meeting):
    """Build a readable transcript from saved Whisper segments."""
    segments = meeting.get("transcript_segments", [])

    lines = []

    for segment in segments:
        speaker = segment.get("participant_name", "Participant")
        spoken_text = str(segment.get("text", "")).strip()

        if spoken_text:
            lines.append(f"{speaker}: {spoken_text}")

    return "\n".join(lines)


def generate_meeting_ai_summary(meeting_id):
    """Generate and save the AI summary using the local Ollama summarizer."""
    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found."
        }

    transcript = build_meeting_transcript(meeting)

    if not transcript.strip():
        return {
            "success": False,
            "message": "No meeting transcript is available yet."
        }

    print("\nMeetIQ: Generating AI meeting summary...")
    print("MeetIQ: Transcript length:", len(transcript))

    try:
        ai_summary = generate_summary(transcript)

        if not isinstance(ai_summary, dict):
            raise ValueError(
                "The summarizer returned an invalid summary format."
            )

        ai_summary.setdefault("discussion_points", [])
        ai_summary.setdefault("decisions", [])
        ai_summary.setdefault("action_items", [])

        meetings_collection.update_one(
            {"_id": ObjectId(meeting_id)},
            {
                "$set": {
                    "ai_summary": ai_summary,
                    "transcript": transcript,
                    "summary_generated_at": get_utc_now()
                }
            }
        )

        print("MeetIQ: AI meeting summary saved successfully.")

        return {
            "success": True,
            "summary": ai_summary
        }

    except Exception as error:
        print("MeetIQ: AI summary generation failed:", error)

        return {
            "success": False,
            "message": str(error)
        }


# TOXICITY DETECTION
# ============================================================

toxicity_path = (
    Path(__file__).resolve().parent.parent
    / "ai-services"
    / "toxicity-detection"
)

if str(toxicity_path) not in sys.path:
    sys.path.insert(0, str(toxicity_path))

try:
    from toxicity_detector import (
        detect_text_toxicity,
        analyze_voice,
        moderation_decision
    )
    print("MeetIQ: Toxicity detection module connected.")
except (ImportError, Exception) as e:
    detect_text_toxicity = None
    analyze_voice = None
    moderation_decision = None
    print("MeetIQ: Toxicity detection module not available:", e)
@app.route("/api/meeting-summary/<meeting_id>", methods=["POST"])
def create_meeting_summary(meeting_id):
    """Generate the AI summary from Whisper transcript segments."""
    if session.get("role") not in ["admin", "participant"]:
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    try:
        meeting = meetings_collection.find_one({
            "_id": safe_object_id(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    if session.get("role") == "admin":
        authorized = (
            meeting.get("created_by_email")
            == session.get("user_id")
        )
    else:
        authorized = (
            session.get("user_id")
            in meeting.get("invited_participants", [])
        )

    if not authorized:
        return jsonify({
            "success": False,
            "message": "You are not authorized to summarize this meeting."
        }), 403

    job_id = f"job_{uuid.uuid4().hex}"
    job_doc = {
        "job_id": job_id,
        "meeting_id": str(meeting_id),
        "type": "summary",
        "status": "PENDING",
        "created_by": session.get("user_id"),
        "result": None,
        "error": None,
        "created_at": get_utc_now(),
        "completed_at": None
    }
    ai_jobs_collection.insert_one(job_doc)

    if os.getenv("AI_ASYNC_ENABLED", "True").lower() == "true":
        try:
            from celery_app import async_generate_summary_task
            async_generate_summary_task.delay(job_id, str(meeting_id))
            return jsonify({
                "success": True,
                "async": True,
                "job_id": job_id,
                "status": "PENDING",
                "message": "AI summary generation enqueued successfully."
            }), 202
        except Exception as e:
            print("Celery enqueue failed, falling back to synchronous execution:", e)

    result = generate_meeting_ai_summary(meeting_id)

    if not result.get("success"):
        ai_jobs_collection.update_one(
            {"job_id": job_id},
            {"$set": {"status": "FAILED", "error": result.get("message"), "completed_at": get_utc_now()}}
        )
        return jsonify(result), 400

    ai_jobs_collection.update_one(
        {"job_id": job_id},
        {"$set": {"status": "COMPLETED", "result": result.get("summary"), "completed_at": get_utc_now()}}
    )

    result["job_id"] = job_id
    result["status"] = "COMPLETED"
    return jsonify(result)


@app.route("/api/ai/jobs/<job_id>", methods=["GET"])
def get_ai_job_status(job_id):
    """
    Return status and result for an asynchronous AI job.
    Enforces authentication and meeting ownership authorization.
    """
    if session.get("role") not in ["admin", "participant"]:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    user_id = session.get("user_id")

    job = ai_jobs_collection.find_one({"job_id": job_id})
    if not job:
        return jsonify({"success": False, "message": "Job not found."}), 404

    meeting_id = job.get("meeting_id")
    meeting = meetings_collection.find_one({"_id": safe_object_id(meeting_id)})
    if not meeting:
        return jsonify({"success": False, "message": "Associated meeting not found."}), 404

    if session.get("role") == "admin":
        authorized = (meeting.get("created_by_email") == user_id)
    else:
        authorized = (user_id in meeting.get("invited_participants", []))

    if not authorized:
        return jsonify({"success": False, "message": "Unauthorized access to job."}), 403

    return jsonify({
        "success": True,
        "job_id": job_id,
        "meeting_id": meeting_id,
        "type": job.get("type"),
        "status": job.get("status"),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at")
    }), 200


def get_ice_servers_config():
    """
    Constructs WebRTC ICE servers configuration list based on environment settings.
    Always retains Google public STUN server and conditionally appends TURN credentials if TURN_ENABLED is True.
    """
    ice_servers = [
        {"urls": ["stun:stun.l.google.com:19302"]}
    ]

    turn_enabled = os.getenv("TURN_ENABLED", "False").lower() in ["true", "1", "yes"]
    turn_url = os.getenv("TURN_SERVER_URL", "").strip()
    turn_username = os.getenv("TURN_USERNAME", "").strip()
    turn_password = os.getenv("TURN_PASSWORD", "").strip()

    if turn_enabled and turn_url:
        urls_list = [u.strip() for u in turn_url.split(",") if u.strip()]
        turn_entry = {
            "urls": urls_list
        }
        if turn_username:
            turn_entry["username"] = turn_username
        if turn_password:
            turn_entry["credential"] = turn_password
        ice_servers.append(turn_entry)

    return ice_servers


@app.route("/api/webrtc/ice-servers", methods=["GET"])
def get_ice_servers():
    """
    Returns WebRTC ICE servers configuration for authorized meeting participants/admins.
    Enforces authentication and meeting authorization boundaries.
    """
    if session.get("role") not in ["admin", "participant"]:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    meeting_id = request.args.get("meeting_id")
    if not meeting_id:
        return jsonify({"success": False, "message": "Meeting ID is required."}), 400

    meeting = meetings_collection.find_one({"_id": safe_object_id(meeting_id)})
    if not meeting:
        return jsonify({"success": False, "message": "Meeting not found."}), 404

    user_id = session.get("user_id")
    if session.get("role") == "admin":
        authorized = (meeting.get("created_by_email") == user_id)
    else:
        authorized = (user_id in meeting.get("invited_participants", []))

    if not authorized:
        return jsonify({"success": False, "message": "Unauthorized access to ICE configuration."}), 403

    return jsonify({
        "success": True,
        "ice_servers": get_ice_servers_config()
    }), 200



@app.route("/api/meeting-summary/<meeting_id>", methods=["GET"])
def get_meeting_summary(meeting_id):
    """Return the saved AI summary and transcript status."""
    if session.get("role") not in ["admin", "participant"]:
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    if session.get("role") == "admin":
        authorized = (
            meeting.get("created_by_email")
            == session.get("user_id")
        )
    else:
        authorized = (
            session.get("user_id")
            in meeting.get("invited_participants", [])
        )

    if not authorized:
        return jsonify({
            "success": False,
            "message": "You are not authorized to view this summary."
        }), 403

    return jsonify({
        "success": True,
        "meeting_id": meeting_id,
        "summary": meeting.get(
            "ai_summary",
            {
                "discussion_points": [],
                "decisions": [],
                "action_items": []
            }
        ),
        "transcript_available": bool(
            meeting.get("transcript", "").strip()
        ),
        "transcript_segments": len(
            meeting.get("transcript_segments", [])
        ),
        "generated_at": meeting.get("summary_generated_at")
    })


def process_audio_chunk_inference(meeting_id, participant_id, chunk_path, participant_name="Participant"):
    """
    Performs Whisper speech recognition and Detoxify text toxicity analysis on an audio chunk.
    Saves transcript segments and toxicity events to MongoDB, emits Socket.IO moderation if needed,
    and cleans up the audio chunk file.
    """
    chunk_path = Path(chunk_path)

    try:
        transcript = ""
        if "model" in globals() and model is not None:
            result = model.transcribe(str(chunk_path))
            transcript = result.get("text", "").strip()
        else:
            try:
                import whisper
                whisper_model = whisper.load_model("base")
                result = whisper_model.transcribe(str(chunk_path))
                transcript = result.get("text", "").strip()
            except Exception as e:
                print("MeetIQ: Whisper transcription error inside worker:", e)
                transcript = ""

        if not transcript:
            if chunk_path.exists():
                chunk_path.unlink()
            return {
                "success": True,
                "transcript": "",
                "toxicity_score": 0.0,
                "action": "none",
                "message": "No speech detected."
            }

        # Save transcript segment
        transcript_segment = {
            "participant_id": participant_id,
            "participant_name": participant_name,
            "text": transcript,
            "timestamp": get_utc_now()
        }

        meetings_collection.update_one(
            {"_id": safe_object_id(meeting_id)},
            {"$push": {"transcript_segments": transcript_segment}}
        )

        toxicity_score = 0.0
        toxicity = {}
        action = "none"
        message = "No toxicity detected."

        if detect_text_toxicity is not None:
            try:
                toxicity = detect_text_toxicity(transcript)
                toxicity_score = float(toxicity.get("toxicity", 0.0))
            except Exception as e:
                print("MeetIQ: Toxicity error inside worker:", e)

            previous_toxic_events = meetings_collection.count_documents({
                "_id": safe_object_id(meeting_id),
                "toxicity_events": {
                    "$elemMatch": {
                        "participant_id": participant_id,
                        "toxicity_score": {"$gte": 0.60}
                    }
                }
            })

            if moderation_decision is not None:
                moderation = moderation_decision(toxicity_score, previous_toxic_events)
                action = moderation["action"]
                message = moderation["message"]

            if toxicity_score >= 0.60:
                toxicity_event = {
                    "participant_id": participant_id,
                    "transcript": transcript,
                    "toxicity_score": round(toxicity_score, 4),
                    "categories": toxicity,
                    "action": action,
                    "message": message,
                    "timestamp": get_utc_now()
                }

                meetings_collection.update_one(
                    {"_id": safe_object_id(meeting_id)},
                    {"$push": {"toxicity_events": toxicity_event}}
                )

            if action in ["warning", "mute"]:
                participant_socket = socket_users.get(participant_id)
                if participant_socket:
                    try:
                        socketio.emit(
                            "toxicity_moderation",
                            {
                                "action": action,
                                "message": message,
                                "toxicity_score": round(toxicity_score, 4)
                            },
                            to=participant_socket
                        )
                    except Exception as socket_err:
                        print("MeetIQ: Socket emit error:", socket_err)

        if chunk_path.exists():
            chunk_path.unlink()

        return {
            "success": True,
            "transcript": transcript,
            "toxicity_score": round(toxicity_score, 4),
            "action": action,
            "message": message
        }

    finally:
        if chunk_path.exists():
            try:
                chunk_path.unlink()
            except Exception:
                pass


@app.route("/api/meeting-audio/realtime", methods=["POST"])
def realtime_meeting_audio():
    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    participant_id = session.get("user_id")
    meeting_id = request.form.get("meeting_id")
    audio = request.files.get("audio")

    if not meeting_id or not audio:
        return jsonify({
            "success": False,
            "message": "Meeting ID or audio is missing."
        }), 400

    try:
        meeting = meetings_collection.find_one({
            "_id": safe_object_id(meeting_id),
            "invited_participants": participant_id,
            "status": "live"
        })
    except Exception:
        meeting = None

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found, unauthorized, or not live."
        }), 403

    recordings_path = (
        Path(__file__).resolve().parent.parent
        / "ai-services"
        / "speech-to-text"
        / "recordings"
    )

    recordings_path.mkdir(
        parents=True,
        exist_ok=True
    )

    allowed_extensions = {".webm", ".wav", ".m4a", ".mp3", ".ogg"}
    filename = secure_filename(audio.filename or "audio.webm")
    ext = os.path.splitext(filename)[1].lower() if filename else ".webm"
    if ext and ext not in allowed_extensions:
        return jsonify({
            "success": False,
            "message": "Unsupported audio file format."
        }), 400

    chunk_filename = f"realtime_{uuid.uuid4().hex}{ext if ext else '.webm'}"
    chunk_path = (recordings_path / chunk_filename).resolve()

    if not str(chunk_path).startswith(str(recordings_path.resolve())):
        return jsonify({
            "success": False,
            "message": "Invalid file path traversal attempt."
        }), 400

    try:
        audio.save(str(chunk_path))
    except Exception as error:
        return jsonify({
            "success": False,
            "message": f"Failed to save audio recording: {str(error)}"
        }), 500

    job_id = f"job_whisper_{uuid.uuid4().hex}"
    job_doc = {
        "job_id": job_id,
        "meeting_id": str(meeting_id),
        "participant_id": participant_id,
        "type": "whisper",
        "status": "PENDING",
        "created_by": participant_id,
        "result": None,
        "error": None,
        "created_at": get_utc_now(),
        "completed_at": None
    }
    ai_jobs_collection.insert_one(job_doc)

    if os.getenv("AI_ASYNC_ENABLED", "True").lower() == "true":
        try:
            from celery_app import async_transcribe_audio_task
            async_transcribe_audio_task.delay(
                job_id,
                str(meeting_id),
                participant_id,
                str(chunk_path),
                session.get("user_name", "Participant")
            )
            return jsonify({
                "success": True,
                "async": True,
                "job_id": job_id,
                "status": "PENDING",
                "message": "Audio processing enqueued successfully."
            }), 202
        except Exception as enqueue_error:
            print("MeetIQ: Celery enqueue failed for audio, falling back to synchronous execution:", enqueue_error)

    try:
        result_data = process_audio_chunk_inference(
            meeting_id,
            participant_id,
            chunk_path,
            session.get("user_name", "Participant")
        )

        ai_jobs_collection.update_one(
            {"job_id": job_id},
            {"$set": {"status": "COMPLETED", "result": result_data, "completed_at": get_utc_now()}}
        )
        result_data["job_id"] = job_id
        result_data["status"] = "COMPLETED"
        return jsonify(result_data)

    except Exception as error:
        print("Real-time audio processing error:", error)
        ai_jobs_collection.update_one(
            {"job_id": job_id},
            {"$set": {"status": "FAILED", "error": str(error), "completed_at": get_utc_now()}}
        )
        if chunk_path.exists():
            try:
                chunk_path.unlink()
            except Exception:
                pass

        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

# =========================================================
# RUN APPLICATION
# =========================================================
if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        allow_unsafe_werkzeug=True
    )
