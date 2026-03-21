import discord
from discord.ext import commands, tasks
import asyncio
import time
import os
from dotenv import load_dotenv
import keep_alive

load_dotenv()
TOKEN = "MTQ4NDgzMjQ1MDY1NTAyNzMxMA.GlWF2i.yy3MEXHKcb0nt1ofdCwuxxfaFrXLtUg5ocXNeU"

if TOKEN is None:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit()


intents = discord.Intents.default()
intents.message_content = True
intents.presences = True  # Required to see Spotify activity
intents.members = True

bot = commands.Bot(command_prefix="%", intents=intents)

# Dictionary to store users being tracked: {user_id: {start_time, channel_id}}
tracked_users = {}

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
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
        
        # 1. Update/Reset the tracking data
        tracked_users[user_id] = {
            'start_time': time.time(),
            'channel_id': message.channel.id,
            'user_name': message.author.name
        }
        
        # 2. Prepare the artist and song info
        spotify_activity = discord.utils.find(lambda a: isinstance(a, discord.Spotify), message.author.activities)
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





    await bot.process_commands(message)




@bot.event
async def on_presence_update(before, after):
    user_id = after.id
    
    # Check if user is in our 15-minute tracking window
    if user_id in tracked_users:
        data = tracked_users[user_id]
        start_time = data['start_time']
        channel_id = data['channel_id']
        
        # If 15 minutes (900 seconds) have passed, stop tracking
        if time.time() - start_time > 900:
            del tracked_users[user_id]
            return

        # Check if the activity change involves Spotify
        before_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify), before.activities)
        after_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify), after.activities)

        if after_spotify:
            if not before_spotify or before_spotify.title != after_spotify.title:
                channel = after.guild.get_channel(channel_id)


                if channel:
                    # Cleanly join artist names
                    artists_list = after_spotify.artists
                    if not artists_list:
                        artist_str = "Unknown Artist"
                    elif len(artists_list) > 1:
                        artist_str = ", ".join(artists_list[:-1]) + " and " + artists_list[-1]
                    else:
                        artist_str = artists_list[0]

                    await channel.send(
                        f"🎶 **{after.display_name}** is now listening to **{after_spotify.title}** by **{artist_str}**"
                    )



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
        # Get member object from the guild
        member = ctx.guild.get_member(user_id)
        name = data.get('user_name', f"User {user_id}")
        
        # Calculate time remaining
        elapsed = time.time() - data['start_time']
        remaining = max(0, int((900 - elapsed) / 60))
        
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

keep_alive.keep_alive()
bot.run(TOKEN)
