import discord
from discord.ext import commands
from discord import ui
import asyncio
import time
import datetime
from database import (
    tracked_users, user_history, user_artist_counts, 
    user_settings, save_persistent_data, update_artist_stats,
    update_minutes_listened, increment_songs_tracked,
    reset_all_minutes_listened
)
import re
from utils import (
    format_artists, get_presence_channel, 
    create_spotify_embed, get_spotify_color, fetch_spotify_track
)

async def start_tracking_session(bot, member, channel, duration, ctx_guild=None, is_manual=False):
    """Internal helper to initialize tracking for a user."""
    user_id = member.id
    guild = ctx_guild or (member.guild if hasattr(member, 'guild') else None)
    
    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
    if not spotify:
        return False, None
        
    target_channel = await get_presence_channel(bot, guild, channel.id)
    chan_id = target_channel.id if target_channel else channel.id
    
    artist_str = format_artists(getattr(spotify, 'artists', []))
    current_track = f"**{getattr(spotify, 'title', 'Unknown')}** by {artist_str}"
    
    history = []
    if user_id in tracked_users:
        history = tracked_users[user_id].get('song_history', [])
        if not history or history[0] != current_track:
            history.insert(0, current_track)
    else:
        history = [current_track]

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
        'session_announced': is_manual,
        'current_song_start_time': time.time()
    }
    
    persist_history = user_history.get(user_id, [])
    if not persist_history or persist_history[0] != current_track:
        persist_history.insert(0, current_track)
        user_history[user_id] = persist_history[:5]
        await update_artist_stats(user_id, getattr(spotify, 'artists', []))
    await increment_songs_tracked()
        
    return True, current_track

async def announce_spotify_status(bot, uid, guild_id, chan_id):
    """Wait for stability, detect skips, and announce the song or skip summary."""
    await asyncio.sleep(2.0)
    
    guild = bot.get_guild(guild_id)
    if not guild: return
    
    current_member = guild.get_member(uid)
    if not current_member: return
    
    session = tracked_users.get(uid)
    if not session: return
    
    buffer = session.get('skip_buffer', [])
    target_channel = await get_presence_channel(bot, guild, chan_id)
    if not target_channel: return

    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', current_member.activities)
    if not spotify: return

    if len(buffer) >= 3:
        desc = f"⏩ **{current_member.display_name}** skipped **{len(buffer)-1}** songs."
        embed = await create_spotify_embed(current_member, spotify, title_text=desc, color=discord.Color.blue())
        embed.title = f"⏩ Multiple Skips: {embed.title}" if embed.title else "⏩ Multiple Skips Detected"
        await target_channel.send(embed=embed)
    else:
        embed = await create_spotify_embed(current_member, spotify)
        await target_channel.send(embed=embed)
    
    session['skip_buffer'] = []
    session['notif_task'] = None
    session['session_announced'] = True

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

class Tracking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        is_spotify = False
        party_id = message.activity.get('party_id') if message.activity else None
        if party_id and "spotify" in party_id.lower():
            is_spotify = True

        if is_spotify:
            was_already_tracked = (message.author.id in tracked_users)
            member = message.guild.get_member(message.author.id) if message.guild else message.author
            
            started, current_track = await start_tracking_session(self.bot, member, message.channel, 900, ctx_guild=message.guild, is_manual=True)
            
            if started:
                spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
                artist_str = format_artists(getattr(spotify, 'artists', []))
                song_title = spotify.title if hasattr(spotify, 'title') else "a song"
                
                title = "🎶 Tracking Refreshed!" if was_already_tracked else "🎵 Spotify Activity Detected!"
                desc = f"Now tracking **{member.display_name}**'s playlist.\nCurrently listening to **{song_title}** by **{artist_str}**"
                
                embed = await create_spotify_embed(member, spotify, title_text=desc)
                embed.title = f"{title}: {embed.title}" if embed.title else title
                await message.channel.send(embed=embed)

        track_match = re.search(r"https://open\.spotify\.com/track/([a-zA-Z0-9]+)", message.content)
        if track_match:
            track_id = track_match.group(1).split("?")[0]
            track_data = await fetch_spotify_track(track_id)
            if track_data and not track_data.get('error'):
                artists = ", ".join([a['name'] for a in track_data.get('artists', [])])
                track_title = track_data.get('name')
                album = track_data.get('album', {}).get('name', 'Unknown Album')
                album_url = track_data.get('album', {}).get('images', [{}])[0].get('url') if track_data.get('album', {}).get('images') else None
                
                embed = discord.Embed(
                    title=track_title,
                    url=f"https://open.spotify.com/track/{track_id}",
                    timestamp=discord.utils.utcnow()
                )
                embed.color = await get_spotify_color(album_url) if album_url else discord.Color.green()
                
                embed.add_field(name="Artist", value=artists, inline=True)
                embed.add_field(name="Album", value=album, inline=True)
                
                if album_url:
                    embed.set_thumbnail(url=album_url)
                    
                embed.set_footer(text="its so peak :sob:")
                
                await message.reply(embed=embed, mention_author=False)

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        user_id = after.id
        before_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', before.activities)
        after_spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', after.activities)

        if user_id in tracked_users:
            data = tracked_users[user_id]
            start_time = data['start_time']
            channel_id = data['channel_id']
            
            orig_channel = self.bot.get_channel(channel_id)
            tracker_guild = orig_channel.guild if (orig_channel and hasattr(orig_channel, 'guild')) else after.guild
            
            if after.guild and tracker_guild and after.guild.id != tracker_guild.id:
                return

            duration = data.get('duration', 900)
            if time.time() - start_time > duration:
                user_settings_data = user_settings.get(user_id, {})
                if user_settings_data.get('auto_track'):
                    data['start_time'] = time.time()
                    data['duration'] = 3600 
                else:
                    if data.get('current_song_start_time'):
                        listened_secs = time.time() - data['current_song_start_time']
                        if before_spotify and hasattr(before_spotify, 'duration') and before_spotify.duration:
                            listened_secs = min(listened_secs, before_spotify.duration.total_seconds())
                        else:
                            listened_secs = min(listened_secs, 600)
                        if listened_secs > 10:
                            await update_minutes_listened(user_id, listened_secs)
                    del tracked_users[user_id]
                    return

            if before_spotify and not after_spotify:
                if data.get('current_song_start_time'):
                    listened_secs = time.time() - data['current_song_start_time']
                    if hasattr(before_spotify, 'duration') and before_spotify.duration:
                        listened_secs = min(listened_secs, before_spotify.duration.total_seconds())
                    else:
                        listened_secs = min(listened_secs, 600)
                    if listened_secs > 10:
                        await update_minutes_listened(user_id, listened_secs)
                    data['current_song_start_time'] = None
                data['last_stop_time'] = time.time()
                return

            if after_spotify:
                if data.get('last_stop_time') and data.get('session_announced'):
                    elapsed = time.time() - data['last_stop_time']
                    if 20 <= elapsed <= 90:
                        target_channel = await get_presence_channel(self.bot, after.guild, channel_id)
                        if target_channel:
                            if user_id in tracked_users:
                                embed = discord.Embed(
                                    description=f"<@{user_id}> haha bro got an ad 🫵🤣",
                                    color=discord.Color.orange()
                                )
                                await target_channel.send(embed=embed)
                    data['last_stop_time'] = None
                    if not data.get('current_song_start_time'):
                        data['current_song_start_time'] = time.time()

                track_id = getattr(after_spotify, 'track_id', None) or getattr(after_spotify, 'title', None)
                if track_id and track_id != data.get('last_notified_song'):
                    if data.get('current_song_start_time'):
                        listened_secs = time.time() - data['current_song_start_time']
                        if before_spotify and hasattr(before_spotify, 'duration') and before_spotify.duration:
                            listened_secs = min(listened_secs, before_spotify.duration.total_seconds())
                        else:
                            listened_secs = min(listened_secs, 600)
                        if listened_secs > 10:
                            await update_minutes_listened(user_id, listened_secs)
                    data['current_song_start_time'] = time.time()
                    
                    tracked_users[user_id]['last_notified_song'] = track_id
                    artist_str = format_artists(getattr(after_spotify, 'artists', []))
                    new_track = f"**{after_spotify.title}** by {artist_str}"
                    
                    session_history = data.get('song_history', [])
                    if not session_history or session_history[0] != new_track:
                        session_history.insert(0, new_track)
                        tracked_users[user_id]['song_history'] = session_history[:5]
                    
                    persist_history = user_history.get(user_id, [])
                    if not persist_history or persist_history[0] != new_track:
                        persist_history.insert(0, new_track)
                        user_history[user_id] = persist_history[:5]
                        await update_artist_stats(user_id, getattr(after_spotify, 'artists', []))
                    await increment_songs_tracked()

                    if data.get('notif_task'):
                        data['notif_task'].cancel()
                    
                    if 'skip_buffer' not in data: 
                        data['skip_buffer'] = []
                    
                    data['skip_buffer'].append(new_track)
                    data['notif_task'] = self.bot.loop.create_task(announce_spotify_status(self.bot, user_id, after.guild.id, channel_id))
        else:
            settings = user_settings.get(user_id, {})
            if settings.get('auto_track') and after_spotify:
                channel_id = settings.get('auto_track_channel')
                if channel_id:
                    target_channel = await get_presence_channel(self.bot, after.guild, channel_id)
                    if target_channel and hasattr(target_channel, 'guild') and target_channel.guild.id == after.guild.id:
                        started, current_track = await start_tracking_session(self.bot, after, target_channel, 3600, ctx_guild=after.guild)
                        if started:
                            if tracked_users[user_id].get('notif_task'):
                                tracked_users[user_id]['notif_task'].cancel()
                                
                            tracked_users[user_id]['skip_buffer'].append(current_track)
                            tracked_users[user_id]['notif_task'] = self.bot.loop.create_task(announce_spotify_status(self.bot, user_id, after.guild.id, channel_id))

    @commands.hybrid_command(name="tracklist", description="Display everyone currently being tracked for their Spotify music status")
    async def tracklist(self, ctx):
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
            member = ctx.guild.get_member(user_id) if ctx.guild else None
            name = data.get('user_name', f"User {user_id}")
            elapsed = time.time() - data['start_time']
            duration = data.get('duration', 900)
            remaining = max(0, int((duration - elapsed) / 60))
            
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

    @commands.hybrid_command(name="trackme", description="Start tracking your Spotify activity for a specified number of minutes")
    async def trackme(self, ctx, minutes: int):
        await ctx.defer()
        user_id = ctx.author.id
        member = ctx.author
        if ctx.guild:
            member = ctx.guild.get_member(ctx.author.id) or ctx.author
            
        spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
        if not spotify:
            await ctx.send("❌ **Error: Spotify not detected!**")
            return

        was_already_tracked = (user_id in tracked_users)
        started, current_track = await start_tracking_session(self.bot, member, ctx.channel, minutes * 60, ctx_guild=ctx.guild, is_manual=True)
        
        if started:
            title = "✅ Tracking Updated!" if was_already_tracked else "🎵 Now Tracking!"
            desc = f"I'll post your Spotify updates in this channel for the next {minutes} minutes."
            embed = await create_spotify_embed(member, spotify, title_text=desc)
            embed.title = f"{title}: {embed.title}" if embed.title else title
            await ctx.send(embed=embed)
        else:
            await ctx.send("❌ Failed to start tracking session.")

    @commands.hybrid_command(name="stoptrack", description="Stop tracking your Spotify activity")
    async def stoptrack(self, ctx):
        await ctx.defer()
        user_id = ctx.author.id
        if user_id in tracked_users:
            data = tracked_users[user_id]
            if data.get('current_song_start_time'):
                listened_secs = time.time() - data['current_song_start_time']
                member = ctx.guild.get_member(user_id) if ctx.guild else ctx.author
                spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities) if member else None
                if spotify and hasattr(spotify, 'duration') and spotify.duration:
                    listened_secs = min(listened_secs, spotify.duration.total_seconds())
                else:
                    listened_secs = min(listened_secs, 600)
                if listened_secs > 10:
                    await update_minutes_listened(user_id, listened_secs)
            del tracked_users[user_id]
            desc = f"⏹️ **Tracking stopped.** I'll no longer post Spotify updates for **{ctx.author.display_name}** in this channel."
            
            # Check if autotrack is on and alert the user
            if user_settings.get(user_id, {}).get('auto_track'):
                desc += "\n\n> ⚠️ **Note:** Your **Auto-Track** is currently **enabled**. If you continue listening to Spotify, you will be automatically re-added to the tracking list."

            embed = discord.Embed(
                description=desc,
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("🔍 You are not currently being tracked.")

    @commands.hybrid_command(name="autotrack", description="Toggle whether the bot automatically tracks you when you start listening to Spotify")
    async def autotrack(self, ctx):
        await ctx.defer()
        user_id = ctx.author.id
        if user_id not in user_settings:
            user_settings[user_id] = {}
        
        current = user_settings[user_id].get('auto_track', False)
        status_emoji = "✅" if current else "❌"
        status_text = "Enabled" if current else "Disabled"
        
        embed = discord.Embed(
            title="🎵 Auto-Track Configuration",
            description=f"**Current Setting:** `{status_text} {status_emoji}`\n\nWhat would you like to do?",
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
            member = ctx.guild.get_member(user_id) if ctx.guild else ctx.author
            started, current_track = await start_tracking_session(self.bot, member, ctx.channel, 3600, ctx_guild=ctx.guild, is_manual=True)
            
            if started:
                data = tracked_users[user_id]
                target_channel = self.bot.get_channel(data['channel_id'])
                if target_channel:
                    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
                    artist_str = format_artists(getattr(spotify, 'artists', []))
                    song_title = spotify.title if hasattr(spotify, 'title') else "a song"
                    notif_desc = f"Now tracking **{member.display_name}**'s playlist.\nCurrently listening to **{song_title}** by **{artist_str}**"
                    embed_msg = await create_spotify_embed(member, spotify, title_text=notif_desc)
                    await target_channel.send(embed=embed_msg)
            desc = f"✅ **Auto-tracking enabled!**"
            color = discord.Color.green()
        else:
            # Remove from tracklist if they are there
            if user_id in tracked_users:
                data = tracked_users[user_id]
                if data.get('current_song_start_time'):
                    listened_secs = time.time() - data['current_song_start_time']
                    member = ctx.guild.get_member(user_id) if ctx.guild else ctx.author
                    spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities) if member else None
                    if spotify and hasattr(spotify, 'duration') and spotify.duration:
                        listened_secs = min(listened_secs, spotify.duration.total_seconds())
                    else:
                        listened_secs = min(listened_secs, 600)
                    if listened_secs > 10:
                        await update_minutes_listened(user_id, listened_secs)
                del tracked_users[user_id]
            desc = f"❌ **Auto-tracking disabled.**"
            color = discord.Color.red()

        await save_persistent_data(user_id)
        await message.edit(embed=discord.Embed(description=desc, color=color), view=None)

    @commands.command(name="setminutes", hidden=True)
    async def setminutes(self, ctx, user: discord.User, seconds: int):
        AUTHORIZED_USER_ID = 550994878486544384
        if ctx.author.id != AUTHORIZED_USER_ID:
            await ctx.send("❌ You are not authorized to use this command.", ephemeral=True)
            return
        await ctx.defer()
        
        from database import user_minutes_listened, save_persistent_data
        user_minutes_listened[user.id] = {"total": seconds, "months": {}, "weeks": {}}
        await save_persistent_data(user.id)
        
        mins = seconds / 60
        hrs = mins / 60
        embed = discord.Embed(
            description=f"✅ **Set listening time for <@{user.id}>** to **{seconds:,}s** ({hrs:.1f} hours).",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.command(name="dumpdata", hidden=True)
    async def dumpdata(self, ctx):
        AUTHORIZED_USER_ID = 550994878486544384
        if ctx.author.id != AUTHORIZED_USER_ID:
            await ctx.send("❌ You are not authorized to use this command.", ephemeral=True)
            return
        await ctx.defer()
        
        from database import save_json, DATA_FILE
        save_json()
        
        try:
            await ctx.send(
                content="📦 **Here's the current data snapshot:**",
                file=discord.File(DATA_FILE)
            )
        except FileNotFoundError:
            await ctx.send("❌ No data file found — nothing has been saved yet.")

async def setup(bot):
    await bot.add_cog(Tracking(bot))
