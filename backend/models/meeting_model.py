from database import db

meetings_collection = db["meetings"]


def create_meeting(
    title,
    description,
    date,
    formatted_date,
    time,
    formatted_time,
    duration,
    meeting_type,
    access,
    created_by,
    created_by_email=None
):
    meeting = {
        "title": title,
        "description": description,
        "date": date,
        "formatted_date": formatted_date,
        "time": time,
        "formatted_time": formatted_time,
        "duration": duration,
        "meeting_type": meeting_type,
        "access": access,
        "created_by": created_by,
        "created_by_email": created_by_email,
        "invited_participants": [],
        "status": "upcoming",
        "ai_summary": None,
        "ai_insights": []
    }

    result = meetings_collection.insert_one(meeting)
    meeting["_id"] = result.inserted_id

    return meeting
