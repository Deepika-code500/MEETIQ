import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/meetiq")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=2000)
db = client["meetiq"]

try:
    client.admin.command("ping")
    print("MongoDB connected successfully!")
    print("Database selected: meetiq")
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