import discord
from discord.ext import commands, tasks
import asyncio
import aioconsole
import time
import os
from dotenv import load_dotenv
import keep_alive

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

class ConsoleState:
    active_target_id = None
    receivemessages = True

state = ConsoleState()

# Dictionary to store users being tracked: {user_id: {start_time, channel_id, user_name, duration, song_history}}
tracked_users = {}

# Persistent dictionary for song history: {user_id: [songs]}
user_history = {}


async def manual_control():
    await bot.wait_until_ready()
    print(f'Logged in as {bot.user.name}')
    print("--- Console Controller Active ---")
    print("Commands: /target [ID] | /clear | /exit | /stopresponses | /startresponses | /showdetails")
    
    while not bot.is_closed():
        user_input = await aioconsole.ainput("") # Clean prompt for better chat flow

        if not user_input.strip():
            continue

        # 1. Handle Commands
        if user_input.startswith("/target "):
            try:
                new_id = int(user_input.replace("/target ", "").strip())
                target = bot.get_channel(new_id) or bot.get_user(new_id)
                if target:
                    state.active_target_id = new_id
                    print(f" >>> [SYSTEM]: Now chatting with: {target}")
                else:
                    print(" >>> [SYSTEM]: Error: ID not found.")
            except ValueError:
                print(" >>> [SYSTEM]: Error: Invalid ID format.")
            continue

        if user_input.lower() == "/clear":
            state.active_target_id = None
            print(" >>> [SYSTEM]: Target cleared. You are now 'lurking'.")
            continue

        if user_input.lower() == "/exit":
            print("Closing...")
            await bot.close()
            break
        
        if user_input.lower() == "/stopresponses":
            print(" >>> [SYSTEM]: Stopping all responses. You will no longer receive messages from the active target.")
            state.receivemessages = False
            continue
        
        if user_input.lower() == "/startresponses":
            print(" >>> [SYSTEM]: Resuming responses. You will now receive messages from the active target.")
            state.receivemessages = True
            continue

        if user_input.lower() == "/showdetails":
            print(tracked_users)
            continue

        # 2. Handle Sending
        if state.active_target_id:
            target = bot.get_channel(state.active_target_id) or bot.get_user(state.active_target_id)
            if target:
                try:
                    await target.send(user_input)
                except Exception as e:
                    print(f" >>> [SYSTEM]: Failed to send: {e}")
        else:
            print(" >>> [SYSTEM]: No target set. Use /target [ID] to start chatting.")
            
    
        
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    bot.loop.create_task(manual_control())
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
        user_id = message.author.id
        was_already_tracked = (user_id in tracked_users)
        
        # 0. Get the member from cache for more reliable presence info
        member = message.author
        if message.guild:
            member = message.guild.get_member(message.author.id) or message.author
        
        # 1. Find the Spotify activity
        spotify_activity = discord.utils.find(lambda a: isinstance(a, discord.Spotify), member.activities)
        
        # 1. Prepare history and initial data
        initial_track = None
        if spotify_activity:
            initial_track = f"**{spotify_activity.title}** by {', '.join(spotify_activity.artists)}"
            
            # 1. Update active tracking
            tracked_users[user_id] = {
                'start_time': time.time(),
                'channel_id': message.channel.id,
                'user_name': message.author.name,
                'duration': 900,  # Default 15 minutes
                'last_notified_song': getattr(spotify_activity, 'track_id', None) if spotify_activity else None,
                'song_history': [initial_track] if initial_track else [],
                'skip_buffer': [],
                'notif_task': None
            }
            
            # 2. Update persistent history
            if initial_track:
                existing = user_history.get(user_id, [])
                if not existing or existing[0] != initial_track:
                    existing.insert(0, initial_track)
                    user_history[user_id] = existing[:5]
        
        # 3. Prepare the artist and song info
        artist_str = "Unknown Artist"
        song_title = "a song"
        if spotify_activity:
            song_title = spotify_activity.title
            artists_list = spotify_activity.artists
            if not artists_list:
                artist_str = "Unknown Artist"
            elif len(artists_list) > 1:
                artist_str = ", ".join(artists_list[:-1]) + " and " + artists_list[-1]
            else:
                artist_str = artists_list[0]
        
        # 3. Send the appropriate message
        if was_already_tracked:
            await message.channel.send(f"🎶 Tracking refreshed! **{message.author.display_name}** is listening to **{song_title}** by **{artist_str}**")
        else:
            await message.channel.send(
                f"🎵 Detected Spotify activity! Now tracking **{message.author.display_name}**'s playlist for 15 minutes.\n"
                f"Currently listening to **{song_title}** by **{artist_str}**"
            )

    # Check if the message is from our active target
    # This checks both the Channel ID (for servers) and Author ID (for DMs)
    if state.active_target_id and state.receivemessages:
        if message.channel.id == state.active_target_id or message.author.id == state.active_target_id:
            location = f"DM" if isinstance(message.channel, discord.DMChannel) else f"#{message.channel.name}"
            print(f"[{location}] {message.author}: {message.content}")


    
    await bot.process_commands(message)




@bot.event
async def on_presence_update(before, after):
    user_id = after.id
    
    # Check if user is in our 15-minute tracking window
    if user_id in tracked_users:
        data = tracked_users[user_id]
        start_time = data['start_time']
        channel_id = data['channel_id']
        
        # If the specified duration (default 15 mins) has passed, stop tracking
        duration = data.get('duration', 900)
        if time.time() - start_time > duration:
            del tracked_users[user_id]
            return

        # Check if the activity change involves Spotify
        before_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify), before.activities)
        after_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify), after.activities)

        if after_spotify:
            track_id = getattr(after_spotify, 'track_id', after_spotify.title)
            if track_id != data.get('last_notified_song'):
                # Update last notified song to prevent duplicates
                tracked_users[user_id]['last_notified_song'] = track_id
                
                # Cleanly join artist names
                artists_list = after_spotify.artists
                if not artists_list:
                    artist_str = "Unknown Artist"
                elif len(artists_list) > 1:
                    artist_str = ", ".join(artists_list[:-1]) + " and " + artists_list[-1]
                else:
                    artist_str = artists_list[0]

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

                # --- Skip Detection and Delayed Notification ---
                async def send_notification(uid, track_info, artist):
                    await asyncio.sleep(2.5)  # Wait for more skips
                    session = tracked_users.get(uid)
                    if not session: return
                    
                    buffer = session.get('skip_buffer', [])
                    chan_id = session['channel_id']
                    channel = bot.get_channel(chan_id)
                    
                    if not channel: return

                    if len(buffer) >= 3:
                        # Summary message for multiple skips
                        last_track = buffer[-1]
                        await channel.send(
                            f"⏩ **{after.display_name}** skipped **{len(buffer)-1}** songs. Currently playing: {last_track}"
                        )
                    else:
                        # Normal message for single/double change
                        await channel.send(
                            f"🎶 **{after.display_name}** is now listening to **{track_info}** by **{artist}**"
                        )
                    
                    # Clear buffer and task
                    session['skip_buffer'] = []
                    session['notif_task'] = None

                # Manage existing task and buffer
                if data.get('notif_task'):
                    data['notif_task'].cancel()
                
                if 'skip_buffer' not in data: data['skip_buffer'] = []
                data['skip_buffer'].append(f"**{after_spotify.title}** by {artist_str}")
                
                data['notif_task'] = bot.loop.create_task(send_notification(user_id, after_spotify.title, artist_str))



@bot.hybrid_command(name="tracklist", description="Display everyone currently being tracked for their Spotify music status")
async def tracklist(ctx):

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
            spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify), member.activities)
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
    
    # Prepare song info
    song_title = getattr(spotify, 'title', 'Unknown Song')
    artists_list = getattr(spotify, 'artists', [])
    if not artists_list:
        artist_str = "Unknown Artist"
    elif len(artists_list) > 1:
        artist_str = ", ".join(artists_list[:-1]) + " and " + artists_list[-1]
    else:
        artist_str = artists_list[0]
    
    current_track = f"**{song_title}** by {artist_str}"
    
    # Get existing history if updating
    history = []
    if was_already_tracked:
        history = tracked_users[user_id].get('song_history', [])
        if not history or history[0] != current_track:
            history.insert(0, current_track)
    else:
        history = [current_track]

    tracked_users[user_id] = {
        'start_time': time.time(),
        'channel_id': ctx.channel.id,
        'user_name': ctx.author.name,
        'duration': minutes * 60,
        'last_notified_song': getattr(spotify, 'track_id', spotify.title),
        'song_history': history[:5],
        'skip_buffer': [],
        'notif_task': None
    }
    
    # Update persistent history
    persist_history = user_history.get(user_id, [])
    if not persist_history or persist_history[0] != current_track:
        persist_history.insert(0, current_track)
        user_history[user_id] = persist_history[:5]

    if was_already_tracked:
        await ctx.send(
            f"✅ **Tracking updated!** I'll continue to monitor your Spotify activity for the next {minutes} minutes in this channel.\n"
            f"🎶 Currently listening to **{song_title}** by **{artist_str}**"
        )
    else:
        await ctx.send(
            f"🎵 **Now tracking you, {ctx.author.display_name}!** I'll post your Spotify updates in this channel for the next {minutes} minutes.\n"
            f"🎶 Currently listening to **{song_title}** by **{artist_str}**"
        )


@bot.hybrid_command(name="ping", description="Check the bot's latency")
async def ping(ctx):
    """Responds with the bot's current latency."""
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 **Pong!** Bot latency: **{latency}ms**")


@trackme.error
async def trackme_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("minutes is a required argument that is missing. For example, %trackme 30")


@bot.hybrid_command(name="stoptrack", description="Stop tracking your Spotify activity")
async def stoptrack(ctx):
    """Removes you from the tracking list."""
    user_id = ctx.author.id
    if user_id in tracked_users:
        del tracked_users[user_id]
        await ctx.send(f"⏹️ **Tracking stopped.** I'll no longer post Spotify updates for **{ctx.author.display_name}** in this channel.")
    else:
        await ctx.send("🔍 You are not currently being tracked.")


@bot.hybrid_command(name="history", description="Show the last 5 songs recorded during your tracking sessions")
async def history(ctx):
    """Displays the persistent song history of a user."""
    user_id = ctx.author.id
    history_list = user_history.get(user_id, [])
    
    if not history_list:
        await ctx.send("❌ **Error:** You have never been tracked by this bot! Use `/trackme` to start tracking your session.")
        return

    embed = discord.Embed(
        title=f"📜 Persistent History for {ctx.author.display_name}",
        color=discord.Color.blue(),
        description="Here are the last 5 songs recorded during your tracking sessions:"
    )
    
    for i, song in enumerate(history_list, 1):
        embed.add_field(name=f"{i}. {song}", value="\u200b", inline=False)

    status = "Active" if user_id in tracked_users else "Ended"
    embed.set_footer(text=f"Last session status: {status}")

    await ctx.send(embed=embed)


keep_alive.keep_alive()
bot.run(TOKEN)
