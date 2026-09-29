from database import db


notifications_collection = db["notifications"]


def create_notification(
    participant_id,
    participant_email,
    meeting_id,
    meeting_title,
    meeting_date,
    meeting_time,
    message
):

    notification = {

        "participant_id": participant_id,

        "participant_email": participant_email,

        "meeting_id": meeting_id,

        "meeting_title": meeting_title,

        "meeting_date": meeting_date,

        "meeting_time": meeting_time,

        "message": message,

        "type": "meeting_invitation",

        "read": False

    }


    result = notifications_collection.insert_one(
        notification
    )


    notification["_id"] = result.inserted_id


    return notification