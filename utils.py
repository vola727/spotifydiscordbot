import discord
import aiohttp
import asyncio
from io import BytesIO
from colorthief import ColorThief
import datetime
import os
import base64
import time

# Cache for album cover colors: {album_cover_url: discord.Color}
album_color_cache = {}

# Global session for aiohttp
_session = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

_app_token = None
_app_token_expires = 0

async def get_app_token():
    global _app_token, _app_token_expires
    if _app_token and time.time() < _app_token_expires:
        return _app_token
        
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
        
    session = await get_session()
    auth_str = f"{client_id}:{client_secret}"
    b64_auth_str = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {b64_auth_str}"}
    data = {"grant_type": "client_credentials"}
    
    async with session.post("https://accounts.spotify.com/api/token", headers=headers, data=data) as resp:
        if resp.status == 200:
            res = await resp.json()
            _app_token = res.get("access_token")
            _app_token_expires = time.time() + res.get("expires_in", 3600) - 60
            return _app_token
    return None

async def fetch_spotify_track(track_id):
    token = await get_app_token()
    if not token:
        return None
        
    session = await get_session()
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(f"https://api.spotify.com/v1/tracks/{track_id}", headers=headers) as resp:
        if resp.status == 200:
            return await resp.json()
    return None

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
                    # quality=10 is faster
                    dominant_color = await asyncio.to_thread(color_thief.get_color, quality=10)
                    discord_color = discord.Color.from_rgb(*dominant_color)
                    album_color_cache[url] = discord_color
                    return discord_color
    except Exception as e:
        print(f" >>> [DEBUG]: Error extracting color from {url}: {e}")
    
    return discord.Color.green()

def format_artists(artists_list):
    """Formats a list of artists into a readable string (Consistently)."""
    if not artists_list:
        return "Unknown Artist"
    elif len(artists_list) > 1:
        return ", ".join(artists_list[:-1]) + " and " + artists_list[-1]
    else:
        return artists_list[0]

async def get_presence_channel(bot, guild, fallback_channel_id):
    """Helper to find the best channel for Spotify updates."""
    if guild:
        # Priority: #spotify-updates channel in the same guild
        for channel in guild.text_channels:
            if channel.name.lower().strip() == "spotify-updates":
                permissions = channel.permissions_for(guild.me)
                if permissions.send_messages and permissions.embed_links:
                    return channel
                else:
                    print(f" >>> [DEBUG]: Found #spotify-updates in '{guild.name}' but missing permissions.")
                
    # Fallback logic
    fallback_channel = bot.get_channel(fallback_channel_id)
    if not fallback_channel:
        try:
            fallback_channel = await bot.fetch_channel(fallback_channel_id)
        except Exception:
            return None
            
    return fallback_channel

async def create_spotify_embed(member, spotify, title_text=None, color=None):
    """Creates a standardized embed for Spotify activity with dynamic album color."""
    if not spotify or not isinstance(spotify, discord.Spotify):
        embed = discord.Embed(
            description=title_text if title_text else f"**{member.display_name}** is listening to Spotify",
            color=color or discord.Color.green()
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        return embed

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
