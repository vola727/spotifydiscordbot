import os
import json
import asyncio
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
        data = {
            "user_history": {str(k): v for k, v in list(user_history.items())},
            "user_artist_counts": {str(k): v for k, v in list(user_artist_counts.items())},
            "user_settings": {str(k): v for k, v in list(user_settings.items())},
            "spotify_tokens": {str(k): v for k, v in list(spotify_tokens.items())}
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
            data = {
                "history": user_history.get(user_id, []),
                "artist_counts": user_artist_counts.get(user_id, {}),
                "settings": user_settings.get(user_id, {}),
                "spotify_token": spotify_tokens.get(user_id, {})
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
                token = document.get("spotify_token", {})
                if token:
                    spotify_tokens[uid] = token
            
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
