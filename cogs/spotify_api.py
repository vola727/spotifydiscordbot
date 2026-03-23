import discord
from discord.ext import commands, tasks
import os
import aiohttp
import time
import asyncio
import traceback
from database import spotify_tokens, tracked_users, save_persistent_data, update_artist_stats, user_history
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

    @tasks.loop(seconds=15)
    async def spotify_polling(self):
        """Polls Spotify API for active users if they are not playing on Discord"""
        try:
            await self.bot.wait_until_ready()
            
            # Snapshot keys to avoid RuntimeError
            user_ids = list(tracked_users.keys())
            for user_id in user_ids:
                if user_id not in tracked_users:
                    continue
                data = tracked_users[user_id]
                
                # Only poll if we have their token
                if user_id not in spotify_tokens:
                    continue
                    
                # Check if discord is already showing them playing Spotify
                member = None
                for guild in self.bot.guilds:
                    member_in_guild = guild.get_member(user_id)
                    if member_in_guild:
                        member = member_in_guild
                        break
                        
                if member:
                    spotify_activity = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', member.activities)
                    if spotify_activity:
                        # They are online and Discord is picking it up. Skip polling.
                        continue
                
                # They are either offline, or Discord isn't picking it up. Let's ask Spotify.
                await self._poll_user_spotify(user_id, data, member)
        except Exception as e:
            print(f" >>> [CRITICAL] Loop crashed in spotify_polling: {e}")
            traceback.print_exc()

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
            async with session.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers) as resp:
                if resp.status == 200:
                    playback = await resp.json()
                    item = playback.get('item')
                    if not item or not playback.get('is_playing'):
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
                        target_channel = await get_presence_channel(self.bot, member.guild if member else None, channel_id)
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
                            
                elif resp.status == 204:
                    # Nothing playing
                    if tracked_data.get('last_stop_time') is None:
                        tracked_data['last_stop_time'] = time.time()
                    
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
