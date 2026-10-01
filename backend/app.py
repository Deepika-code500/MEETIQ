from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
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
            finalize_attendance_session(m_id, p_id, datetime.utcnow())

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

    meeting_ended_at = datetime.utcnow()

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

    participants = list(
        participants_collection.find().sort("name", 1)
    )

    if request.method == "GET":
        return render_template(
            "admin/invite_participants.html",
            meeting=meeting,
            participants=participants
        )

    selected_participants = request.form.getlist("participant_ids")

    meetings_collection.update_one(
        {
            "_id": meeting["_id"],
            "created_by_email": session.get("user_id")
        },
        {
            "$set": {
                "invited_participants": selected_participants
            }
        }
    )

    for participant_id in selected_participants:

        participant = participants_collection.find_one({
            "user_id": participant_id
        })

        if not participant:
            continue

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
# ADMIN PARTICIPANTS
# =========================================================

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

    now = datetime.utcnow()
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


@app.route("/admin/participants")
def admin_participants():

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    # -----------------------------------------------------
    # GET ALL PARTICIPANTS FROM MONGODB
    # -----------------------------------------------------

    participants = list(
        participants_collection.find().sort(
            "name",
            1
        )
    )

    # -----------------------------------------------------
    # GET UNIQUE DEPARTMENTS
    # -----------------------------------------------------

    departments = sorted(
        set(
            participant.get(
                "department",
                ""
            )
            for participant in participants
            if participant.get("department")
        )
    )

    # -----------------------------------------------------
    # SEND DATA TO PARTICIPANTS PAGE
    # -----------------------------------------------------

    return render_template(
        "admin/participants.html",
        participants=participants,
        departments=departments
    )

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
        end_time = datetime.utcnow()

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
        end_time = datetime.utcnow()

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

    join_time = datetime.utcnow()
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
        datetime.utcnow()
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
    from datetime import datetime

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
        finalize_attendance_session(meeting_id, participant_id, datetime.utcnow())

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
                    "summary_generated_at": datetime.utcnow()
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
            "message": "You are not authorized to summarize this meeting."
        }), 403

    result = generate_meeting_ai_summary(meeting_id)

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


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

    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
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

    import uuid

    chunk_filename = (
        f"realtime_{uuid.uuid4().hex}.webm"
    )

    chunk_path = recordings_path / chunk_filename

    try:

        # -----------------------------------------
        # 1. Save temporary audio chunk
        # -----------------------------------------

        audio.save(str(chunk_path))

        print(
            "\nMeetIQ: Processing real-time audio chunk..."
        )

        # -----------------------------------------
        # 2. Whisper speech-to-text
        # -----------------------------------------

        print("MeetIQ: Transcribing real-time audio...")

        result = model.transcribe(
            str(chunk_path)
)

        transcript = result["text"].strip()

        print(
            "Real-time Transcript:",
            transcript
        )

        if not transcript:

            if chunk_path.exists():
                chunk_path.unlink()

            return jsonify({
                "success": True,
                "transcript": "",
                "toxicity_score": 0,
                "action": "none",
                "message": "No speech detected."
            })

        # Save every Whisper transcript segment for the final AI summary.
        transcript_segment = {
            "participant_id": participant_id,
            "participant_name": session.get("user_name", "Participant"),
            "text": transcript,
            "timestamp": datetime.utcnow()
        }

        meetings_collection.update_one(
            {"_id": ObjectId(meeting_id)},
            {"$push": {"transcript_segments": transcript_segment}}
        )

        print("MeetIQ: Transcript segment saved for AI summary.")

        # -----------------------------------------
        # 3. Detect toxicity
        # -----------------------------------------

        print(
            "MeetIQ: Running real-time toxicity detection..."
        )

        toxicity = detect_text_toxicity(
            transcript
        )

        toxicity_score = float(
            toxicity.get("toxicity", 0)
        )

        print(
            "Real-time Toxicity Score:",
            toxicity_score
        )

        print(
            "Real-time Toxicity Categories:",
            toxicity
        )

        # -----------------------------------------
        # 4. Count previous toxic events
        # -----------------------------------------

        previous_toxic_events = meetings_collection.count_documents({
            "_id": ObjectId(meeting_id),
            "toxicity_events": {
                "$elemMatch": {
                    "participant_id": participant_id,
                    "toxicity_score": {
                        "$gte": 0.60
                    }
                }
            }
        })

        # -----------------------------------------
        # 5. Moderation decision
        # -----------------------------------------

        moderation = moderation_decision(
            toxicity_score,
            previous_toxic_events
        )

        action = moderation["action"]
        message = moderation["message"]

        print(
            "Previous toxic events:",
            previous_toxic_events
        )

        print(
            "Moderation Action:",
            action
        )

        print(
            "Moderation Message:",
            message
        )

        # -----------------------------------------
        # 6. Save toxicity event
        # -----------------------------------------

        if toxicity_score >= 0.60:

            toxicity_event = {
                "participant_id": participant_id,
                "transcript": transcript,
                "toxicity_score": round(
                    toxicity_score,
                    4
                ),
                "categories": toxicity,
                "action": action,
                "message": message,
                "timestamp": datetime.utcnow()
            }

            meetings_collection.update_one(
                {
                    "_id": ObjectId(meeting_id)
                },
                {
                    "$push": {
                        "toxicity_events":
                        toxicity_event
                    }
                }
            )

            print(
                "MeetIQ: Real-time toxicity event saved."
            )

        # -----------------------------------------
        # 7. Send warning / mute command
        #    to participant browser
        # -----------------------------------------

        if action in ["warning", "mute"]:

            participant_socket = socket_users.get(
                participant_id
            )

            if participant_socket:

                socketio.emit(
                    "toxicity_moderation",
                    {
                        "action": action,
                        "message": message,
                        "toxicity_score": round(
                            toxicity_score,
                            4
                        )
                    },
                    to=participant_socket
                )

                print(
                    "MeetIQ: Moderation event sent to participant."
                )

            else:

                print(
                    "MeetIQ: Participant socket not found."
                )

        # -----------------------------------------
        # 8. Delete temporary audio
        # -----------------------------------------

        if chunk_path.exists():
            chunk_path.unlink()

        return jsonify({
            "success": True,
            "transcript": transcript,
            "toxicity_score": round(
                toxicity_score,
                4
            ),
            "action": action,
            "message": message
        })

    except Exception as error:

        print(
            "Real-time audio processing error:",
            error
        )

        if chunk_path.exists():
            chunk_path.unlink()

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
