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