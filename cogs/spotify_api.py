import discord
from discord.ext import commands, tasks
import os
import aiohttp
import time
import asyncio
import traceback
from database import spotify_tokens, tracked_users, user_settings, save_persistent_data, update_artist_stats, user_history
from utils import get_spotify_color, get_presence_channel

class SpotifyAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.spotify_polling.start()

    def cog_unload(self):
        self.spotify_polling.cancel()
        if self.session:
            asyncio.create_task(self.session.close())

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    @commands.hybrid_command(name="link", description="Link your Spotify account to allow offline tracking")
    async def link(self, ctx):
        await ctx.defer(ephemeral=True)
        web_url = os.getenv("BOT_WEB_URL", "http://localhost:8080")
        login_url = f"{web_url.rstrip('/')}/login?user_id={ctx.author.id}"
        
        embed = discord.Embed(
            title="🔗 Link Your Spotify Account",
            description=(
                "To allow me to track your Spotify activity even when Discord is closed or your "
                "status is invisible, you need to authorize my Spotify app.\n\n"
                f"**[Click Here to Authorize Spotify]({login_url})**\n\n"
                "*Note: You only need to do this once. The bot will refresh its access automatically.*"
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="unlink", description="Unlink your Spotify account and revoke offline tracking access")
    async def unlink(self, ctx):
        await ctx.defer(ephemeral=True)
        user_id = ctx.author.id
        
        if user_id in spotify_tokens:
            del spotify_tokens[user_id]
            await save_persistent_data(user_id)
            embed = discord.Embed(
                description="✅ **Successfully unlinked your Spotify account.** I will no longer track your listening activity while you are offline.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                description="❌ **You don't have a linked Spotify account.**",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)

    _tick = 0

    @tasks.loop(seconds=15)
    async def spotify_polling(self):
        """Polls Spotify API for active users if they are not playing on Discord"""
        try:
            self.__class__._tick += 1
            # print(f" >>> [DEBUG]: --- Polling Loop Tick #{self._tick} ---")
            # print(f" >>> [DEBUG]: tracked_users keys: {list(tracked_users.keys())}")
            # print(f" >>> [DEBUG]: spotify_tokens keys: {list(spotify_tokens.keys())}")
            
            # 1. Handle currently tracked users (Expiration and Polling)
            tracked_ids = list(tracked_users.keys())
            for user_id in tracked_ids:
                data = tracked_users.get(user_id)
                if not data: continue
                
                # Check for expiration (Crucial for offline users!)
                elapsed = time.time() - data.get('start_time', 0)
                duration = data.get('duration', 900)
                
                if elapsed > duration:
                    settings = user_settings.get(user_id, {})
                    if settings.get('auto_track'):
                        data['start_time'] = time.time()
                        data['duration'] = 3600
                    else:
                        print(f" >>> [SYSTEM]: Tracking session expired for {data.get('user_name', user_id)} (API Cleanup)")
                        del tracked_users[user_id]
                        continue

                # Only poll if we have their token
                if user_id in spotify_tokens:
                    member = self._get_member(user_id)
                    if member:
                        spotify_activity = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
                        if spotify_activity:
                            # Discord presence is working, let Tracking cog handle it
                            # print(f" >>> [DEBUG]: {user_id} has Discord presence, skipping offline poll.")
                            continue
                    
                    # print(f" >>> [DEBUG]: Initiating offline API poll for {user_id}...")
                    await self._poll_user_spotify(user_id, data, member)

            # 2. Re-initialize Auto-Track for linked users who aren't in the list (Offline recovery)
            linked_ids = list(spotify_tokens.keys())
            # print(f" >>> [DEBUG]: Section 2 — checking {len(linked_ids)} linked user(s) for auto-start...")
            for user_id in linked_ids:
                if user_id in tracked_users:
                    # print(f" >>> [DEBUG]: {user_id} already tracked, skipping auto-start.")
                    continue
                
                settings = user_settings.get(user_id, {})
                has_autotrack = settings.get('auto_track')
                has_channel = settings.get('auto_track_channel')
                # print(f" >>> [DEBUG]: {user_id} — auto_track={has_autotrack}, channel={has_channel}")
                if has_autotrack and has_channel:
                    member = self._get_member(user_id)
                    if member:
                        activity = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
                        if activity:
                            # print(f" >>> [DEBUG]: {user_id} has Discord activity, not auto-starting.")
                            continue
                    
                    # API check to see if we should start a session
                    await self._check_autostart(user_id, member, has_channel)

        except Exception as e:
            print(f" >>> [CRITICAL] Loop crashed in spotify_polling: {e}")
            traceback.print_exc()

    @spotify_polling.before_loop
    async def before_spotify_polling(self):
        print(" >>> [DEBUG]: Waiting for bot to be ready before starting polling loop...")
        await self.bot.wait_until_ready()
        print(" >>> [DEBUG]: Bot ready — polling loop starting.")

    def _get_member(self, user_id):
        for guild in self.bot.guilds:
            m = guild.get_member(user_id)
            if m: return m
        return None

    async def _check_autostart(self, user_id, member, chan_id):
        """Checks if an offline linked user is playing music and starts a session."""
        tokens = spotify_tokens.get(user_id)
        if not tokens: return
        
        # Token refresh if needed
        if time.time() >= tokens.get('expires_at', 0):
            if not await self._refresh_token(user_id, tokens): return
            
        session = await self.get_session()
        headers = {"Authorization": f"Bearer {spotify_tokens[user_id]['access_token']}"}
        
        try:
            async with session.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers) as resp:
                if resp.status == 200:
                    playback = await resp.json()
                    if playback.get('is_playing') and playback.get('item'):
                        # They are playing! Start a session.
                        print(f" >>> [SYSTEM]: Auto-starting offline session for user {user_id}")
                        await self._start_offline_session(user_id, member, chan_id, playback)
        except Exception:
            pass

    async def _start_offline_session(self, user_id, member, chan_id, playback):
        item = playback['item']
        artists = [a['name'] for a in item.get('artists', [])]
        artist_str = ", ".join(artists) if len(artists) <= 1 else ", ".join(artists[:-1]) + " and " + artists[-1]
        current_track = f"**{item.get('name')}** by {artist_str}"
        
        tracked_users[user_id] = {
            'start_time': time.time(),
            'channel_id': chan_id,
            'user_name': member.name if member else f"User {user_id}",
            'duration': 3600,
            'last_notified_song': item.get('id', item.get('name')),
            'song_history': [current_track],
            'skip_buffer': [],
            'notif_task': None,
            'last_stop_time': None,
            'session_announced': False # Let the first poll announce it
        }

    async def _poll_user_spotify(self, user_id, tracked_data, member):
        tokens = spotify_tokens[user_id]
        
        # Check if token is expired
        if time.time() >= tokens.get('expires_at', 0):
            # Refresh token
            refreshed = await self._refresh_token(user_id, tokens)
            if not refreshed:
                return # Can't poll if refresh failed
                
        session = await self.get_session()
        headers = {
            "Authorization": f"Bearer {spotify_tokens[user_id]['access_token']}"
        }
        
        try:
            # print(f" >>> [DEBUG]: Making Spotify API request for {user_id}...")
            async with session.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers) as resp:
                # print(f" >>> [DEBUG]: API request status: {resp.status} for {user_id}")
                if resp.status == 200:
                    playback = await resp.json()
                    item = playback.get('item')
                    if not item or not playback.get('is_playing'):
                        # print(f" >>> [DEBUG]: Player is paused/stopped for {user_id}.")
                        # Stopped playing
                        if tracked_data.get('last_stop_time') is None:
                            tracked_data['last_stop_time'] = time.time()
                        return
                    
                    # Track is playing!
                    track_id = item.get('id', item.get('name'))
                    artists = [a['name'] for a in item.get('artists', [])]
                    artist_str = ", ".join(artists) if len(artists) <= 1 else ", ".join(artists[:-1]) + " and " + artists[-1]
                    new_track_str = f"**{item.get('name')}** by {artist_str}"
                    
                    # Check for ad gap
                    if tracked_data.get('last_stop_time') and tracked_data.get('session_announced'):
                        elapsed = time.time() - tracked_data['last_stop_time']
                        if 20 <= elapsed <= 90:
                            # Send ad gap message
                            channel_id = tracked_data['channel_id']
                            target_channel = await get_presence_channel(self.bot, member.guild if member else None, channel_id)
                            if target_channel:
                                embed = discord.Embed(
                                    description=f"<@{user_id}> haha bro got an ad 🫵🤣",
                                    color=discord.Color.orange()
                                )
                                await target_channel.send(embed=embed)
                        tracked_data['last_stop_time'] = None

                    if track_id != tracked_data.get('last_notified_song'):
                        # print(f" >>> [DEBUG]: New track detected! {track_id} != {tracked_data.get('last_notified_song')}")
                        tracked_data['last_notified_song'] = track_id
                        
                        # Add to histories
                        session_history = tracked_data.get('song_history', [])
                        if not session_history or session_history[0] != new_track_str:
                            session_history.insert(0, new_track_str)
                            tracked_data['song_history'] = session_history[:5]
                            
                        persist_history = user_history.get(user_id, [])
                        if not persist_history or persist_history[0] != new_track_str:
                            persist_history.insert(0, new_track_str)
                            user_history[user_id] = persist_history[:5]
                            await update_artist_stats(user_id, artists)
                        
                        channel_id = tracked_data['channel_id']
                        target_channel = await get_presence_channel(self.bot, None, channel_id) # API Polling fallback
                        if target_channel:
                            # Avoid spam by only posting if skip buffer is clean (simpler logic for polling)
                            album_url = item.get('album', {}).get('images', [{}])[0].get('url') if item.get('album', {}).get('images') else None
                            embed = discord.Embed(
                                title=item.get('name'),
                                url=item.get('external_urls', {}).get('spotify') if item.get('external_urls') else None,
                                timestamp=discord.utils.utcnow()
                            )
                            embed.color = await get_spotify_color(album_url) if album_url else discord.Color.green()
                            
                            display_name = member.display_name if member else tracked_data.get('user_name', f"User {user_id}")
                            avatar_url = member.display_avatar.url if member else None
                            if avatar_url:
                                embed.set_author(name=display_name, icon_url=avatar_url)
                            else:
                                embed.set_author(name=display_name)
                                
                            embed.add_field(name="Artist", value=", ".join([a['name'] for a in item.get('artists', [])]), inline=True)
                            embed.add_field(name="Album", value=item.get('album', {}).get('name', 'Unknown'), inline=True)
                            if album_url:
                                embed.set_thumbnail(url=album_url)
                            embed.set_footer(text="its so peak :sob: • Offline Tracking")
                            
                            await target_channel.send(embed=embed)
                            tracked_data['session_announced'] = True
                            print(f" >>> [SYSTEM]: Announced offline track update for {display_name}")
                            
                elif resp.status == 204:
                    #print(f" >>> [DEBUG]: 204 No Content for {user_id}.")
                    # Nothing playing
                    if tracked_data.get('last_stop_time') is None:
                        tracked_data['last_stop_time'] = time.time()
                elif resp.status == 401:
                    # print(f" >>> [DEBUG]: 401 Unauthorized for {user_id} — attempting token refresh...")
                    await self._refresh_token(user_id, spotify_tokens[user_id])
                elif resp.status == 403:
                    error_body = await resp.text()
                    # print(f" >>> [DEBUG]: 403 Forbidden for {user_id} — scope likely missing. Body: {error_body}")
                    # print(f" >>> [DEBUG]: User {user_id} needs to /unlink and /link again to grant correct scopes.")
                # else:
                    # print(f" >>> [DEBUG]: Unexpected status {resp.status} for {user_id}.")
                    
        except Exception as e:
            print(f" >>> [DEBUG]: Error polling Spotify API module: {e}")

    async def _refresh_token(self, user_id, tokens):
        client_id = os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not client_id or not client_secret:
            return False
            
        session = await self.get_session()
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tokens.get('refresh_token'),
            "client_id": client_id,
            "client_secret": client_secret
        }
        
        async with session.post("https://accounts.spotify.com/api/token", data=data) as resp:
            if resp.status == 200:
                new_tokens = await resp.json()
                spotify_tokens[user_id]['access_token'] = new_tokens['access_token']
                if 'refresh_token' in new_tokens:
                    spotify_tokens[user_id]['refresh_token'] = new_tokens['refresh_token']
                spotify_tokens[user_id]['expires_at'] = time.time() + new_tokens.get('expires_in', 3600)
                await save_persistent_data(user_id)
                return True
            else:
                print(f" >>> [DEBUG]: Failed to refresh token for {user_id}: {await resp.text()}")
                return False

async def setup(bot):
    await bot.add_cog(SpotifyAPI(bot))
