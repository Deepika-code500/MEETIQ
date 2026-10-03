from database import db
from werkzeug.security import generate_password_hash

admins_collection = db["admins"]
participants_collection = db["participants"]


def create_admin(name, email, password):
    admin = {
        "name": name,
        "email": email.lower().strip(),
        "password": generate_password_hash(password)
    }

    admins_collection.insert_one(admin)

    return admin


def create_participant(
    name,
    email,
    department,
    year,
    user_id,
    password,
    team="General",
    eligible=True
):
    participant = {
        "name": name,
        "email": email.lower().strip(),
        "department": department or "General",
        "year": year or "1",
        "user_id": user_id,
        "team": team or "General",
        "eligible": eligible if isinstance(eligible, bool) else True,
        "password": generate_password_hash(password)
    }

    participants_collection.insert_one(participant)

    return participant