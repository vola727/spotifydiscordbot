import discord
from discord.ext import commands, tasks
from discord import ui
import asyncio
# import aioconsole
import time
import os
import datetime
from dotenv import load_dotenv
import keep_alive
from prettytable import PrettyTable
import aiohttp
from io import BytesIO
from colorthief import ColorThief
import json
import motor.motor_asyncio

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN is None:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit()


intents = discord.Intents.default()
intents.message_content = True
intents.presences = True  # Required to see Spotify activity
intents.members = True

bot = commands.Bot(command_prefix="%", intents=intents)

# class ConsoleState:
#     active_target_id = None
#     receivemessages = True
# 
# state = ConsoleState()

# Database configuration
MONGO_URI = os.getenv("MONGO_URI")
mongo_client = None
db = None
collection = None

if MONGO_URI:
    try:
        mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        db = mongo_client.spotify_bot
        collection = db.user_data
        print(" >>> [SYSTEM]: Connected to MongoDB Atlas.")
    except Exception as e:
        print(f" >>> [DEBUG]: Failed to connect to MongoDB: {e}")

# Files for persistence (Fallback)
DATA_FILE = "persistent_data.json"

# Dictionary to store users being tracked: {user_id: {start_time, channel_id, user_name, duration, song_history}}
tracked_users = {}

# Initial empty dictionaries
user_history = {}
user_artist_counts = {}
user_settings = {}

def save_json():
    """Fallback to save dictionaries to a local JSON file."""
    try:
        # Create snapshots to avoid RuntimeError: dictionary changed size during iteration
        # when running in a separate thread.
        data = {
            "user_history": {str(k): v for k, v in list(user_history.items())},
            "user_artist_counts": {str(k): v for k, v in list(user_artist_counts.items())},
            "user_settings": {str(k): v for k, v in list(user_settings.items())}
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
         print(f" >>> [DEBUG]: Error saving local JSON: {e}")



async def save_persistent_data(user_id=None):
    """Saves user stats and settings. If user_id is provided, saves specifically to MongoDB."""
    # Always save local JSON as fallback in a separate thread to avoid blocking the event loop
    await asyncio.to_thread(save_json)

    if collection is not None and user_id:
        try:
            # Save specific user data to MongoDB
            data = {
                "history": user_history.get(user_id, []),
                "artist_counts": user_artist_counts.get(user_id, {}),
                "settings": user_settings.get(user_id, {})
            }
            await collection.update_one({"_id": str(user_id)}, {"$set": data}, upsert=True)
        except Exception as e:
            print(f" >>> [DEBUG]: Error saving to MongoDB: {e}")

async def load_persistent_data():
    """Loads user stats and settings from MongoDB (priority) or local JSON."""
    global user_history, user_artist_counts, user_settings
    
    # 1. Try loading from MongoDB first
    if collection is not None:
        try:
            cursor = collection.find({})
            async for document in cursor:
                uid = int(document["_id"])
                user_history[uid] = document.get("history", [])
                user_artist_counts[uid] = document.get("artist_counts", {})
                user_settings[uid] = document.get("settings", {})
            
            if user_history:
                print(f" >>> [SYSTEM]: Loaded persistent data for {len(user_history)} users from MongoDB.")
                return # Successfully loaded from DB, skip JSON
        except Exception as e:
            print(f" >>> [DEBUG]: Error loading from MongoDB: {e}")

    # 2. Fallback to local JSON if MongoDB fails or is empty
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                user_history.update({int(k): v for k, v in data.get("user_history", {}).items()})
                user_artist_counts.update({int(k): v for k, v in data.get("user_artist_counts", {}).items()})
                user_settings.update({int(k): v for k, v in data.get("user_settings", {}).items()})
                print(f" >>> [SYSTEM]: Loaded backup data from local JSON.")
        except Exception as e:
            print(f" >>> [DEBUG]: Error loading backup JSON: {e}")

# Cache for album cover colors: {album_cover_url: discord.Color}
album_color_cache = {}

# Global session for aiohttp
_session = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

async def get_spotify_color(url: str):
    """Downloads the album cover and extracts the dominant color."""
    if not url:
        return discord.Color.green()
    
    if url in album_color_cache:
        return album_color_cache[url]
    
    try:
        session = await get_session()
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.read()
                with BytesIO(data) as f:
                    color_thief = ColorThief(f)
                    # quality=1 is slowest but most accurate, quality=10 is faster
                    # Run in a thread to avoid blocking the event loop
                    dominant_color = await asyncio.to_thread(color_thief.get_color, quality=10)
                    discord_color = discord.Color.from_rgb(*dominant_color)
                    album_color_cache[url] = discord_color
                    return discord_color
    except Exception as e:
        print(f" >>> [DEBUG]: Error extracting color from {url}: {e}")
    
    return discord.Color.green()


async def update_artist_stats(user_id, artists):
    """Increments the play count for each artist for a specific user."""
    if user_id not in user_artist_counts:
        user_artist_counts[user_id] = {}
    
    for artist in artists:
        user_artist_counts[user_id][artist] = user_artist_counts[user_id].get(artist, 0) + 1
    
    # Save after updates
    await save_persistent_data(user_id)


def format_artists(artists_list):
    """Formats a list of artists into a readable string (Consistently)."""
    if not artists_list:
        return "Unknown Artist"
    elif len(artists_list) > 1:
        return ", ".join(artists_list[:-1]) + " and " + artists_list[-1]
    else:
        return artists_list[0]


async def get_presence_channel(guild, fallback_channel_id):
    if guild:
        # Priority: #spotify-updates channel in the same guild
        for channel in guild.text_channels:
            if channel.name.lower().strip() == "spotify-updates":
                # Check if bot has permissions to send messages there
                permissions = channel.permissions_for(guild.me)
                if permissions.send_messages and permissions.embed_links:
                    return channel
                else:
                    print(f" >>> [DEBUG]: Found #spotify-updates in '{guild.name}' but missing 'Send Messages' or 'Embed Links' permissions.")
                
    # Fallback logic
    fallback_channel = bot.get_channel(fallback_channel_id)
    if not fallback_channel:
        try:
            fallback_channel = await bot.fetch_channel(fallback_channel_id)
        except Exception:
            return None
            
    if guild:
        # If we reached here, no valid #spotify-updates were found or accessible
        has_named_channel = any(c.name.lower().strip() == "spotify-updates" for c in guild.text_channels) if guild.text_channels else False
        if not has_named_channel:
             print(f" >>> [DEBUG]: No text channel named 'spotify-updates' found in server '{guild.name}'.")
    
    return fallback_channel


async def start_tracking_session(member, channel, duration, ctx_guild=None, is_manual=False):
    """Internal helper to initialize tracking for a user across multiple commands."""
    user_id = member.id
    guild = ctx_guild or (member.guild if hasattr(member, 'guild') else None)
    
    # 1. Find the Spotify activity
    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
    if not spotify:
        return False, None
        
    # 2. Get target channel (fallback to provided channel or #spotify-updates)
    target_channel = await get_presence_channel(guild, channel.id)
    chan_id = target_channel.id if target_channel else channel.id
    
    # 3. Prepare tracks
    artist_str = format_artists(getattr(spotify, 'artists', []))
    current_track = f"**{getattr(spotify, 'title', 'Unknown')}** by {artist_str}"
    
    # 4. Handle session history (Carry over if updating an existing session)
    history = []
    if user_id in tracked_users:
        history = tracked_users[user_id].get('song_history', [])
        if not history or history[0] != current_track:
            history.insert(0, current_track)
    else:
        history = [current_track]

    # 5. Initialize/Update tracking object
    tracked_users[user_id] = {
        'start_time': time.time(),
        'channel_id': chan_id,
        'user_name': member.name,
        'duration': duration,
        'last_notified_song': getattr(spotify, 'track_id', getattr(spotify, 'title', None)),
        'song_history': history[:5],
        'skip_buffer': [],
        'notif_task': None,
        'last_stop_time': None,
        'session_announced': is_manual  # If manual, the command usually handles the first embed
    }
    
    # 6. Update persistent history & stats
    persist_history = user_history.get(user_id, [])
    if not persist_history or persist_history[0] != current_track:
        persist_history.insert(0, current_track)
        user_history[user_id] = persist_history[:5]
        await update_artist_stats(user_id, getattr(spotify, 'artists', []))
        
    return True, current_track


async def announce_spotify_status(uid, guild_id, chan_id):
    """Wait for stability, detect skips, and announce the song or skip summary."""
    # Stabilize: wait for 2 seconds of no further presence changes
    await asyncio.sleep(2.0)
    
    guild = bot.get_guild(guild_id)
    if not guild: return
    
    # Refresh the member from the guild to get the absolute latest status
    current_member = guild.get_member(uid)
    if not current_member: return
    
    session = tracked_users.get(uid)
    if not session: return
    
    buffer = session.get('skip_buffer', [])
    target_channel = await get_presence_channel(guild, chan_id)
    if not target_channel: return

    # Check current spotify activity after the wait
    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', current_member.activities)
    if not spotify: return

    if len(buffer) >= 3:
        # Summary message for 2 or more skips (3+ tracks total in buffer)
        desc = f"⏩ **{current_member.display_name}** skipped **{len(buffer)-1}** songs."
        embed = await create_spotify_embed(current_member, spotify, title_text=desc, color=discord.Color.blue())
        embed.title = f"⏩ Multiple Skips: {embed.title}" if embed.title else "⏩ Multiple Skips Detected"
        
        await target_channel.send(embed=embed)
    else:
        # Normal message for single track change or first detection
        embed = await create_spotify_embed(current_member, spotify)
        await target_channel.send(embed=embed)
    
    # Clear buffer and reset task handle
    session['skip_buffer'] = []
    session['notif_task'] = None
    session['session_announced'] = True



async def create_spotify_embed(member, spotify, title_text=None, color=None):
    """Creates a standardized embed for Spotify activity with dynamic album color."""
    if not spotify or not isinstance(spotify, discord.Spotify):
        embed = discord.Embed(
            description=title_text if title_text else f"**{member.display_name}** is listening to Spotify",
            color=color or discord.Color.green()
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        return embed

    # If no color is provided, try to extract it from the album cover
    if color is None:
        color = await get_spotify_color(spotify.album_cover_url)

    artists = ", ".join(spotify.artists)
    track_title = spotify.title
    album = spotify.album
    track_id = spotify.track_id
    
    embed = discord.Embed(
        title=track_title,
        url=f"https://open.spotify.com/track/{track_id}" if track_id else None,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    
    if title_text:
        embed.description = title_text
    
    embed.add_field(name="Artist", value=artists, inline=True)
    embed.add_field(name="Album", value=album, inline=True)
    
    if spotify.album_cover_url:
        embed.set_thumbnail(url=spotify.album_cover_url)
        
    embed.set_footer(text="its so peak :sob:")
    return embed


# async def manual_control():
#     await bot.wait_until_ready()
#     print(f'Logged in as {bot.user.name}')
#     print("--- Console Controller Active ---")
#     print("Commands: /target [ID] | /clear | /exit | /stopresponses | /startresponses | /showdetails")
#     
#     while not bot.is_closed():
#         user_input = await aioconsole.ainput("") # Clean prompt for better chat flow
# 
#         if not user_input.strip():
#             continue
# 
#         # 1. Handle Commands
#         if user_input.startswith("/target "):
#             try:
#                 new_id = int(user_input.replace("/target ", "").strip())
#                 target = bot.get_channel(new_id) or bot.get_user(new_id)
#                 if target:
#                     state.active_target_id = new_id
#                     print(f" >>> [SYSTEM]: Now chatting with: {target}")
#                 else:
#                     print(" >>> [SYSTEM]: Error: ID not found.")
#             except ValueError:
#                 print(" >>> [SYSTEM]: Error: Invalid ID format.")
#             continue
# 
#         if user_input.lower() == "/clear":
#             state.active_target_id = None
#             print(" >>> [SYSTEM]: Target cleared. You are now 'lurking'.")
#             continue
# 
#         if user_input.lower() == "/exit":
#             print("Closing...")
#             global _session
#             if _session:
#                 await _session.close()
#             await bot.close()
#             break
#         
#         if user_input.lower() == "/stopresponses":
#             print(" >>> [SYSTEM]: Stopping all responses. You will no longer receive messages from the active target.")
#             state.receivemessages = False
#             continue
#         
#         if user_input.lower() == "/startresponses":
#             print(" >>> [SYSTEM]: Resuming responses. You will now receive messages from the active target.")
#             state.receivemessages = True
#             continue
# 
#         if user_input.lower() == "/showdetails":
#             if not tracked_users:
#                 print(" >>> [SYSTEM]: No users are currently being tracked.")
#                 continue
# 
#             table = PrettyTable()
#             table.field_names = [
#                 "User ID", "Username", "Channel", "Start Time", 
#                 "Total (min)", "Left (min/sec)", "Track ID", 
#                 "History", "Skips", "Last Paused"
#             ]
#             
#             for uid, data in tracked_users.items():
#                 elapsed = time.time() - data['start_time']
#                 
#                 # Format time left (respecting user's recent float logic for the column name but making it readable)
#                 time_left_raw = data['duration'] - elapsed
#                 mins = int(max(0, time_left_raw // 60))
#                 secs = int(max(0, time_left_raw % 60))
#                 time_left_str = f"{mins}m {secs}s"
#                 
#                 # Format start time
#                 start_dt = datetime.datetime.fromtimestamp(data['start_time'])
#                 start_str = start_dt.strftime("%H:%M:%S")
#                 
#                 # Format last stop time (pause)
#                 last_stop = data.get('last_stop_time')
#                 stop_str = datetime.datetime.fromtimestamp(last_stop).strftime("%H:%M:%S") if last_stop else "---"
#                 
#                 # Get channel object for nice display
#                 channel = bot.get_channel(data['channel_id'])
#                 chan_name = f"#{channel.name}" if channel and hasattr(channel, 'name') else f"ID: {data['channel_id']}"
#                 
#                 history = data.get('song_history', [])
#                 current = history[0].replace("**", "")[:25] + "..." if history else "---"
#                 
#                 table.add_row([
#                     uid, 
#                     data['user_name'], 
#                     chan_name, 
#                     start_str,
#                     int(data['duration'] / 60),
#                     time_left_str,
#                     data.get('last_notified_song', "---"),
#                     current,
#                     len(data.get('skip_buffer', [])),
#                     stop_str
#                 ])
#             
#             print(table)
#             continue
# 
#         # 2. Handle Sending
#         if state.active_target_id:
#             target = bot.get_channel(state.active_target_id) or bot.get_user(state.active_target_id)
#             if target:
#                 try:
#                     await target.send(user_input)
#                 except Exception as e:
#                     print(f" >>> [SYSTEM]: Failed to send: {e}")
#         else:
#             print(" >>> [SYSTEM]: No target set. Use /target [ID] to start chatting.")
            
    
        
@bot.event
async def on_ready():
    # Load data once the event loop is active
    await load_persistent_data()
    print(f'Logged in as {bot.user.name}')
    # bot.loop.create_task(manual_control())
    try:
        # Syncing slash commands (can take a moment)
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s) successfully.")
    except Exception as e:
        print(f"Error syncing commands: {e}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return


    # Detection logic for Spotify Invites/Links (Official 'Invite to Listen' only)
    is_spotify = False
    
    # Check message activity (Official 'Invite to Listen' button)
    party_id = message.activity.get('party_id') if message.activity else None
    if party_id and "spotify" in party_id.lower():
        is_spotify = True



    if is_spotify:
        was_already_tracked = (message.author.id in tracked_users)
        
        # Get member from cache for reliable info
        member = message.guild.get_member(message.author.id) if message.guild else message.author
        
        # Start the tracking session using the helper (is_manual=True because it's a direct invite)
        started, current_track = await start_tracking_session(member, message.channel, 900, ctx_guild=message.guild, is_manual=True)
        
        if started:
            # Prepare info for detection embed
            spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
            artist_str = format_artists(getattr(spotify, 'artists', []))
            song_title = spotify.title if hasattr(spotify, 'title') else "a song"
            
            target_channel = await get_presence_channel(message.guild, message.channel.id)
            title = "🎶 Tracking Refreshed!" if was_already_tracked else "🎵 Spotify Activity Detected!"
            desc = f"Now tracking **{member.display_name}**'s playlist.\nCurrently listening to **{song_title}** by **{artist_str}**"
            
            embed = await create_spotify_embed(member, spotify, title_text=desc)
            embed.title = f"{title}: {embed.title}" if embed.title else title
            await target_channel.send(embed=embed)
    
    await bot.process_commands(message)




@bot.event
async def on_presence_update(before, after):
    user_id = after.id
    
    # Check if the activity change involves Spotify (Detect at the top for both branches)
    before_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', before.activities)
    after_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', after.activities)

    # 1. Handle Already Tracked Users
    if user_id in tracked_users:
        data = tracked_users[user_id]
        start_time = data['start_time']
        channel_id = data['channel_id']
        
        # Get the original guild if possible
        orig_channel = bot.get_channel(channel_id)
        tracker_guild = orig_channel.guild if (orig_channel and hasattr(orig_channel, 'guild')) else after.guild
        
        # Only process for the guild that matches the tracking guild (prevent duplicates)
        if after.guild and tracker_guild and after.guild.id != tracker_guild.id:
            return

        # Handle duration expiration and seamless renewal for auto-track
        duration = data.get('duration', 900)
        if time.time() - start_time > duration:
            user_settings_data = user_settings.get(user_id, {})
            if user_settings_data.get('auto_track'):
                # Seamless renewal: Reset the start time and duration for another hour
                data['start_time'] = time.time()
                data['duration'] = 3600  # Default 1h for auto-renewal
                # print(f" >>> [DEBUG]: Renewed tracking session for {data['user_name']} (Auto-Track)")
            else:
                del tracked_users[user_id]
                return

        # 1. Detect when Spotify activity stops
        if before_spotify and not after_spotify:
            data['last_stop_time'] = time.time()
            return

        # 2. Detect when it resumes
        if after_spotify:
            # Check for "ad gap" (20-60 seconds)
            # ONLY fire if they are currently in the tracklist and have had at least one song announced
            if data.get('last_stop_time') and data.get('session_announced'):
                elapsed = time.time() - data['last_stop_time']
                if 20 <= elapsed <= 90:
                    # ONLY send the "ad" message if they are actively being tracked
                    target_channel = await get_presence_channel(after.guild, channel_id)
                    if target_channel:
                        # Double check they are still in tracked_users before sending
                        if user_id in tracked_users:
                            embed = discord.Embed(
                                description=f"<@{user_id}> haha bro got an ad 🫵🤣",
                                color=discord.Color.orange()
                            )
                            await target_channel.send(embed=embed)
                
                # Reset stop time regardless of duration to prevent duplicate firing
                data['last_stop_time'] = None

            track_id = getattr(after_spotify, 'track_id', after_spotify.title)
            if track_id != data.get('last_notified_song'):
                # Update last notified song to prevent duplicates
                tracked_users[user_id]['last_notified_song'] = track_id
                
                # Cleanly join artist names
                artist_str = format_artists(getattr(after_spotify, 'artists', []))

                # Update song histories (active and persistent)
                new_track = f"**{after_spotify.title}** by {artist_str}"
                
                # Active Session History
                session_history = data.get('song_history', [])
                if not session_history or session_history[0] != new_track:
                    session_history.insert(0, new_track)
                    tracked_users[user_id]['song_history'] = session_history[:5]
                
                # Persistent History
                persist_history = user_history.get(user_id, [])
                if not persist_history or persist_history[0] != new_track:
                    persist_history.insert(0, new_track)
                    user_history[user_id] = persist_history[:5]
                    await update_artist_stats(user_id, getattr(after_spotify, 'artists', []))

                # Manage existing task and buffer to ensure we only send ONE message once they STOP skipping
                if data.get('notif_task'):
                    data['notif_task'].cancel()
                
                if 'skip_buffer' not in data: 
                    data['skip_buffer'] = []
                
                # Add the track to skip buffer
                data['skip_buffer'].append(new_track)
                
                # Start/Restart the 2-second stability timer
                data['notif_task'] = bot.loop.create_task(announce_spotify_status(user_id, after.guild.id, channel_id))

    # 2. Handle Auto-Track Initialization for non-tracked users
    else:
        settings = user_settings.get(user_id, {})
        if settings.get('auto_track') and after_spotify:
            channel_id = settings.get('auto_track_channel')
            if channel_id:
                # IMPORTANT: Only initialize in the guild where the auto_track_channel exists
                # This prevents duplicate sessions if the user is in multiple guilds with the bot
                target_channel = await get_presence_channel(after.guild, channel_id)
                
                # Check if target_channel belongs to 'after.guild'
                if target_channel and hasattr(target_channel, 'guild') and target_channel.guild.id == after.guild.id:
                    # Use helper for initialization
                    started, current_track = await start_tracking_session(after, target_channel, 3600, ctx_guild=after.guild)
                    
                    if started:
                        # TRIGGER THE FIRST ANNOUNCEMENT!
                        # Clear any existing task (rare)
                        if tracked_users[user_id].get('notif_task'):
                            tracked_users[user_id]['notif_task'].cancel()
                            
                        tracked_users[user_id]['skip_buffer'].append(current_track)
                        tracked_users[user_id]['notif_task'] = bot.loop.create_task(announce_spotify_status(user_id, after.guild.id, channel_id))

@bot.hybrid_command(name="tracklist", description="Display everyone currently being tracked for their Spotify music status")
async def tracklist(ctx):
    await ctx.defer()

    if not tracked_users:
        await ctx.send("🔍 The tracking list is currently empty.")
        return

    embed = discord.Embed(
        title="🎵 Currently Tracked Listeners",
        color=discord.Color.green(),
        description="Here's everyone currently in the tracking list:"
    )

    for user_id, data in tracked_users.items():
        # Get member object from the guild if possible
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        name = data.get('user_name', f"User {user_id}")
        
        # Calculate time remaining
        elapsed = time.time() - data['start_time']
        duration = data.get('duration', 900)
        remaining = max(0, int((duration - elapsed) / 60))
        
        # Get current song
        song_info = "*Paused or not visible*"
        if member:
            spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
            if spotify:
                artists = ", ".join(spotify.artists)
                song_info = f"**{spotify.title}**\nby {artists}"

        embed.add_field(
            name=f"👤 {name} ({remaining}m left)",
            value=song_info,
            inline=False
        )

    await ctx.send(embed=embed)


@bot.hybrid_command(name="trackme", description="Start tracking your Spotify activity for a specified number of minutes")
async def trackme(ctx, minutes: int):
    """Adds you to the tracking list for a specified number of minutes."""
    await ctx.defer()
    user_id = ctx.author.id
    
    # Check if user is currently listening to Spotify
    # We check both the specific Spotify activity type and the activity name as a fallback
    member = ctx.author
    if ctx.guild:
        # Sometimes get_member from the guild cache is more reliable for presence info
        member = ctx.guild.get_member(ctx.author.id) or ctx.author
        
    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
    
    if not spotify:
        await ctx.send(
            "❌ **Error: Spotify not detected!**\n\n"
            "I couldn't find your Spotify activity. Please check the following:\n"
            "1. Make sure you are actively playing music on Spotify.\n"
            "2. Ensure your Discord **Privacy Settings** have 'Display current activity as a status message' **enabled**.\n"
            "3. Verify that your Spotify account is **linked to Discord** and 'Display Spotify as your status' is on."
        )
        return

    was_already_tracked = (user_id in tracked_users)
    
    # Use helper to initialize
    started, current_track = await start_tracking_session(member, ctx.channel, minutes * 60, ctx_guild=ctx.guild, is_manual=True)
    
    if started:
        title = "✅ Tracking Updated!" if was_already_tracked else "🎵 Now Tracking!"
        desc = f"I'll post your Spotify updates in this channel for the next {minutes} minutes."

        embed = await create_spotify_embed(member, spotify, title_text=desc)
        embed.title = f"{title}: {embed.title}" if embed.title else title
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Failed to start tracking session.")


@bot.hybrid_command(name="ping", description="Check the bot's latency")
async def ping(ctx):
    """Responds with the bot's current latency."""
    await ctx.defer()
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 **Pong!** Bot latency: **{latency}ms**")


@trackme.error
async def trackme_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("minutes is a required argument that is missing. For example, %trackme 30")


@bot.hybrid_command(name="stoptrack", description="Stop tracking your Spotify activity")
async def stoptrack(ctx):
    """Removes you from the tracking list."""
    await ctx.defer()
    user_id = ctx.author.id
    if user_id in tracked_users:
        del tracked_users[user_id]
        embed = discord.Embed(
            description=f"⏹️ **Tracking stopped.** I'll no longer post Spotify updates for **{ctx.author.display_name}** in this channel.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("🔍 You are not currently being tracked.")


@bot.hybrid_command(name="history", description="Show the last 5 songs recorded during your tracking sessions")
async def history(ctx):
    """Displays the persistent song history of a user."""
    await ctx.defer()
    user_id = ctx.author.id
    history_list = user_history.get(user_id, [])
    
    if not history_list:
        await ctx.send("❌ **Error:** You have never been tracked by this bot! Use `/trackme` to start tracking your session.")
        return

    history_text = "\n".join([f"{i}. {song}" for i, song in enumerate(history_list, 1)])

    embed = discord.Embed(
        title=f"📜 History for {ctx.author.display_name}",
        color=discord.Color.blue(),
        description=f"Here are the last 5 songs recorded during your tracking sessions:\n\n{history_text}"
    )

    status = "Active" if user_id in tracked_users else "Ended"
    embed.set_footer(text=f"Last session status: {status}")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="topartists", description="Show your most listened to artists recorded during tracking sessions")
async def topartists(ctx, member: discord.Member = None):
    """Displays the most listened to artists for a user."""
    await ctx.defer()
    target_member = member or ctx.author
    user_id = target_member.id
    
    artist_data = user_artist_counts.get(user_id, {})
    
    if not artist_data:
        await ctx.send(
            f"❌ **Error:** No artist data found for {target_member.display_name}. "
            f"Use `/trackme` to start recording your listening stats!"
        )
        return

    # Sort artists by count descending
    sorted_artists = sorted(artist_data.items(), key=lambda item: item[1], reverse=True)
    
    # Take top 10
    top_artists = sorted_artists[:10]
    
    description = ""
    for i, (artist, count) in enumerate(top_artists, 1):
        # Using a sleek format for the list
        plays_word = "play" if count == 1 else "plays"
        description += f"**{i}.** {artist} — `{count} {plays_word}`\n"

    embed = discord.Embed(
        title=f"🔝 Top Artists for {target_member.display_name}",
        color=discord.Color.purple(),  # Vibrant purple for stats
        description=description,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    
    if target_member.display_avatar:
        embed.set_thumbnail(url=target_member.display_avatar.url)
    
    total_plays = sum(artist_data.values())
    embed.set_footer(text=f"Total recorded artist plays: {total_plays}")
    
    await ctx.send(embed=embed)


class AutoTrackView(ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.value = None

    @ui.button(label="Enable Auto-Track", style=discord.ButtonStyle.green, emoji="✅")
    async def enable(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer()

    @ui.button(label="Disable Auto-Track", style=discord.ButtonStyle.red, emoji="❌")
    async def disable(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.defer()


@bot.hybrid_command(name="autotrack", description="Toggle whether the bot automatically tracks you when you start listening to Spotify")
async def autotrack(ctx):
    """Two-step process to toggle the auto-track setting for the user."""
    await ctx.defer()
    user_id = ctx.author.id
    
    if user_id not in user_settings:
        user_settings[user_id] = {}
    
    current = user_settings[user_id].get('auto_track', False)
    status_emoji = "✅" if current else "❌"
    status_text = "Enabled" if current else "Disabled"
    
    embed = discord.Embed(
        title="🎵 Auto-Track Configuration",
        description=(
            "**Auto-Track** automatically starts a 1-hour tracking session whenever you begin listening to music on Spotify. "
            "You won't have to manually use `/trackme` ever again!\n\n"
            "By enabling this, updates will be posted to the **#spotify-updates** channel (if it exists) or the original tracking channel.\n\n"
            f"**Current Setting:** `{status_text} {status_emoji}`\n\n"
            "What would you like to do?"
        ),
        color=discord.Color.blue()
    )
    
    view = AutoTrackView(ctx)
    message = await ctx.send(embed=embed, view=view)
    
    await view.wait()
    
    if view.value is None:
        await message.edit(content="⌛ Request timed out.", view=None)
        return

    new_status = view.value
    user_settings[user_id]['auto_track'] = new_status
    
    if new_status:
        user_settings[user_id]['auto_track_channel'] = ctx.channel.id
        status_text = "enabled"
        
        # Start tracking immediately if already listening
        member = ctx.guild.get_member(user_id) if ctx.guild else ctx.author
        # We set is_manual=True here because the user EXPLICITLY triggered this via button, so we handle the notification manually below.
        started, current_track = await start_tracking_session(member, ctx.channel, 3600, ctx_guild=ctx.guild, is_manual=True)
        
        # Check for #spotify-updates for the message description
        has_named_channel = any(c.name.lower().strip() == "spotify-updates" for c in ctx.guild.text_channels) if ctx.guild else False
        
        target_loc = "**#spotify-updates**" if has_named_channel else "this channel"
        extra = ""
        
        if started:
            extra = "\n\nI've also started tracking your current music right now! Check the updates channel. 🎶"
            
            # Send the initial "Currently Playing" embed in the tracking channel
            data = tracked_users[user_id]
            target_channel = bot.get_channel(data['channel_id'])
            if target_channel:
                spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
                artist_str = format_artists(getattr(spotify, 'artists', []))
                song_title = spotify.title if hasattr(spotify, 'title') else "a song"
                notif_desc = f"Now tracking **{member.display_name}**'s playlist.\nCurrently listening to **{song_title}** by **{artist_str}**"
                
                embed_msg = await create_spotify_embed(member, spotify, title_text=notif_desc)
                embed_msg.title = f"🎵 Spotify Activity Detected: {embed_msg.title}" if embed_msg.title else "🎵 Spotify Activity Detected"
                await target_channel.send(embed=embed_msg)
        
        desc = (
            f"✅ **Auto-tracking {status_text}!**\n\n"
            f"From now on, I'll automatically start tracking your Spotify activity whenever you start listening. "
            f"I'll post updates in {target_loc}.{extra}"
        )
        color = discord.Color.green()
    else:
        status_text = "disabled"
        desc = f"❌ **Auto-tracking {status_text}.**"
        color = discord.Color.red()

    await save_persistent_data(user_id)
    success_embed = discord.Embed(description=desc, color=color)
    await message.edit(embed=success_embed, view=None)



@bot.hybrid_command(name="song", description="Show what a user is currently listening to on Spotify")
async def song(ctx, member: discord.Member = None):
    """Displays the current Spotify activity of a user."""
    await ctx.defer()
    # Use the provided member or default to ctx.author
    target_member = member or ctx.author
    
    # Refresh member from guild cache for more up-to-date presence info
    if ctx.guild:
        target_member = ctx.guild.get_member(target_member.id) or target_member
    
    # Check for Spotify activity (both as a type and as a name fallback)
    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', target_member.activities)
    
    if not spotify:
        error_msg = f"❌ **Error:** {target_member.display_name} is not currently listening to Spotify."
        if target_member == ctx.author:
             error_msg += (
                "\n\n**Troubleshooting:**\n"
                "1. Make sure you are actively playing music on Spotify.\n"
                "2. Ensure your Discord **Privacy Settings** have 'Display current activity as a status message' **enabled**.\n"
                "3. Verify that your Spotify account is **linked to Discord** and 'Display Spotify as your status' is on."
            )
        await ctx.send(error_msg)
        return

    # Create and send the embed
    embed = await create_spotify_embed(target_member, spotify)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="cleanse", description="Removes all my messages sent in the last hour (Admin only)")
@commands.has_permissions(administrator=True)
async def cleanse(ctx):
    """Deletes all messages from the bot sent in the last hour."""
    # Defer since history checking can take some time
    await ctx.defer(ephemeral=True)
    
    count = 0
    # Use timezone-aware datetime
    one_hour_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    
    async for message in ctx.channel.history(after=one_hour_ago, limit=500):
        if message.author == bot.user:
            try:
                await message.delete()
                count += 1
            except discord.HTTPException:
                pass
                
    await ctx.send(f"🧹 Cleaned up **{count}** of my messages from the last hour.", ephemeral=True)


@cleanse.error
async def cleanse_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ **Error:** You need **Administrator** permissions to use this command.", ephemeral=True)


keep_alive.keep_alive()
bot.run(TOKEN)
