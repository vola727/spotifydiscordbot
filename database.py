import os
import json
import asyncio
import datetime
import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DATA_FILE = "persistent_data.json"

mongo_client = None
db = None
collection = None

# Shared state between files
user_history = {}
user_artist_counts = {}
user_settings = {}
spotify_tokens = {} # {user_id: {access_token, refresh_token, expires_at}}
tracked_users = {} # {user_id: {start_time, channel_id, user_name, duration, song_history, etc.}}
user_minutes_listened = {}

if MONGO_URI:
    try:
        mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        db = mongo_client.spotify_bot
        collection = db.user_data
        print(" >>> [SYSTEM]: Connected to MongoDB Atlas.")
    except Exception as e:
        print(f" >>> [DEBUG]: Failed to connect to MongoDB: {e}")

def save_json():
    """Fallback to save dictionaries to a local JSON file."""
    try:
        cleaned_tracked_users = {}
        for k, v in list(tracked_users.items()):
            cleaned_session = {sk: sv for sk, sv in v.items() if sk != 'notif_task'}
            cleaned_tracked_users[str(k)] = cleaned_session

        data = {
            "user_history": {str(k): v for k, v in list(user_history.items())},
            "user_artist_counts": {str(k): v for k, v in list(user_artist_counts.items())},
            "user_settings": {str(k): v for k, v in list(user_settings.items())},
            "spotify_tokens": {str(k): v for k, v in list(spotify_tokens.items())},
            "tracked_users": cleaned_tracked_users,
            "user_minutes_listened": {str(k): v for k, v in list(user_minutes_listened.items())}
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
         print(f" >>> [DEBUG]: Error saving local JSON: {e}")

async def save_persistent_data(user_id=None):
    """Saves user stats and settings. If user_id is provided, saves specifically to MongoDB."""
    # Always save local JSON as fallback
    await asyncio.to_thread(save_json)

    if collection is not None and user_id:
        try:
            session = tracked_users.get(user_id)
            cleaned_session = {k: v for k, v in session.items() if k != 'notif_task'} if session else None

            data = {
                "history": user_history.get(user_id, []),
                "artist_counts": user_artist_counts.get(user_id, {}),
                "settings": user_settings.get(user_id, {}),
                "spotify_token": spotify_tokens.get(user_id, {}),
                "tracked_session": cleaned_session,
                "minutes_listened": user_minutes_listened.get(user_id, {"total": 0, "months": {}, "weeks": {}})
            }
            await collection.update_one({"_id": str(user_id)}, {"$set": data}, upsert=True)
        except Exception as e:
            print(f" >>> [DEBUG]: Error saving to MongoDB: {e}")

async def load_persistent_data():
    """Loads user stats and settings from MongoDB (priority) or local JSON."""
    global user_history, user_artist_counts, user_settings, spotify_tokens
    
    # 1. Try loading from MongoDB first
    if collection is not None:
        try:
            cursor = collection.find({})
            async for document in cursor:
                uid = int(document["_id"])
                user_history[uid] = document.get("history", [])
                user_artist_counts[uid] = document.get("artist_counts", {})
                user_settings[uid] = document.get("settings", {})
                user_minutes_listened[uid] = document.get("minutes_listened", {"total": 0, "months": {}, "weeks": {}})
                token = document.get("spotify_token", {})
                if token:
                    spotify_tokens[uid] = token
                session = document.get("tracked_session")
                if session:
                    tracked_users[uid] = session
            
            if user_history or spotify_tokens:
                print(f" >>> [SYSTEM]: Loaded persistent data from MongoDB.")
                return # Successfully loaded from DB, skip JSON
        except Exception as e:
            print(f" >>> [DEBUG]: Error loading from MongoDB: {e}")

    # 2. Fallback to local JSON
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                user_history.update({int(k): v for k, v in data.get("user_history", {}).items()})
                user_artist_counts.update({int(k): v for k, v in data.get("user_artist_counts", {}).items()})
                user_settings.update({int(k): v for k, v in data.get("user_settings", {}).items()})
                spotify_tokens.update({int(k): v for k, v in data.get("spotify_tokens", {}).items()})
                tracked_users.update({int(k): v for k, v in data.get("tracked_users", {}).items()})
                user_minutes_listened.update({int(k): v for k, v in data.get("user_minutes_listened", {}).items()})
                print(f" >>> [SYSTEM]: Loaded backup data from local JSON.")
        except Exception as e:
            print(f" >>> [DEBUG]: Error loading backup JSON: {e}")

async def update_artist_stats(user_id, artists):
    """Increments the play count for each artist for a specific user."""
    if user_id not in user_artist_counts:
        user_artist_counts[user_id] = {}
    
    for artist in artists:
        user_artist_counts[user_id][artist] = user_artist_counts[user_id].get(artist, 0) + 1
    
    # Save after updates
    await save_persistent_data(user_id)

async def update_minutes_listened(user_id, seconds):
    """Adds the song's duration in seconds to the user's total listening time."""
    if user_id not in user_minutes_listened:
        user_minutes_listened[user_id] = {"total": 0, "months": {}, "weeks": {}}
    
    if isinstance(user_minutes_listened[user_id], (int, float)):
        user_minutes_listened[user_id] = {"total": user_minutes_listened[user_id], "months": {}, "weeks": {}}

    now = datetime.datetime.now(datetime.timezone.utc)
    month_key = now.strftime("%Y-%m")
    year, week, _ = now.isocalendar()
    week_key = f"{year}-W{week:02d}"

    data = user_minutes_listened[user_id]
    data["total"] = data.get("total", 0) + seconds
    
    if "months" not in data: data["months"] = {}
    if "weeks" not in data: data["weeks"] = {}
    
    data["months"][month_key] = data["months"].get(month_key, 0) + seconds
    data["weeks"][week_key] = data["weeks"].get(week_key, 0) + seconds
    
    # Save after updates
    await save_persistent_data(user_id)
