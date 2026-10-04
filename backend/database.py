import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/meetiq")
timeout_ms = int(os.getenv("MONGODB_TIMEOUT_MS", "5000"))

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=timeout_ms)
db = client.get_default_database(default="meetiq")

try:
    client.admin.command("ping")
    print("MongoDB connected successfully!")
    print(f"Database selected: {db.name}")
except Exception as error:
    print("MongoDB connection failed:")
    print(error)

def init_indexes():
    try:
        db.admins.create_index("email", unique=True)
        db.participants.create_index("email", unique=True)
        db.meetings.create_index("created_by_email")
        db.meetings.create_index("status")
        db.attendance.create_index([("meeting_id", 1), ("participant_id", 1)])
    except Exception as e:
        print("MongoDB index creation warning:", e)

init_indexes()