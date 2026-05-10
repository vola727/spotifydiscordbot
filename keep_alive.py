from flask import Flask, request, render_template, send_from_directory
from threading import Thread
import os
import requests
import urllib.parse
from database import spotify_tokens, save_persistent_data, user_history, user_minutes_listened
import asyncio
import time

app = Flask(__name__)

bot_loop = None
bot_instance = None

def set_bot_loop(loop):
    global bot_loop
    bot_loop = loop

def set_bot(b):
    global bot_instance
    bot_instance = b

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.png',
        mimetype='image/png'
    )

@app.route('/')
def home():
    if bot_instance and bot_instance.is_ready() and not bot_instance.is_closed():
        # Calculate stats
        latency_ms = int(bot_instance.latency * 1000)
        guild_count = len(bot_instance.guilds)
        registered_users = len(spotify_tokens)
        
        # Calculate global tracking stats
        total_seconds = sum([data.get("total", 0) for data in user_minutes_listened.values() if isinstance(data, dict)])
        total_minutes = total_seconds / 60
        total_songs = sum([len(history) for history in user_history.values()])
        
        # Format large numbers
        total_minutes_str = f"{total_minutes:,.1f}"
        total_songs_str = f"{total_songs:,}"
        
        bot_id = bot_instance.user.id
        invite_link = f"https://discord.com/oauth2/authorize?client_id={bot_id}&permissions=2252076839595072&integration_type=0&scope=bot"
        
        return render_template('index.html',
            page='home_online',
            title="Spotify Bot • Dashboard",
            icon="🎵",
            heading="Bot is Online",
            latency_ms=latency_ms,
            guild_count=guild_count,
            registered_users=registered_users,
            total_minutes=total_minutes_str,
            total_songs=total_songs_str,
            invite_link=invite_link
        )
    else:
        status_text = "Bot Offline" if (bot_instance and bot_instance.is_closed()) else "Bot Starting"
        if not bot_instance:
            status_text = "Bot Offline"
            
        return render_template('index.html',
            page='home_offline',
            title=f"Spotify Bot • {status_text}",
            icon="⚠️",
            heading=status_text,
            status_text=status_text,
            extra_class="error"
        ), 503

@app.route('/commands')
def commands_page():
    return render_template('index.html',
        page='commands',
        title="Spotify Bot • Commands",
        icon="⌨️",
        heading="Bot Commands"
    )

@app.route('/login')
def login():
    user_id = request.args.get('user_id')
    if not user_id:
        return "Missing user_id", 400
        
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")
        
    scope = 'user-read-currently-playing user-read-playback-state user-read-recently-played'
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'scope': scope,
        'redirect_uri': redirect_uri,
        'state': user_id
    }
    url = 'https://accounts.spotify.com/authorize?' + urllib.parse.urlencode(params)
    return render_template('index.html',
        page='message',
        title="Connecting to Spotify…",
        icon="🔗",
        heading="Connecting to Spotify",
        body_html="<p>Redirecting you to Spotify's login page to authorize your account…</p>",
        badge_text="⏳ Redirecting",
        script=f'setTimeout(()=>window.location.href="{url}", 800);'
    )

@app.route('/callback')
def callback():
    code = request.args.get('code')
    user_id = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return render_template('index.html',
            page='message',
            title="Linking Failed • Spotify Bot",
            icon="❌",
            heading="Linking Failed",
            body_html="<p>Spotify returned an error while linking your account:</p><br><p style='margin-top:20px;'>Please try the <code>/link</code> command in Discord again.</p>",
            badge_text=error,
            badge_style="margin-top:16px;background:rgba(255,92,92,.1);border-color:rgba(255,92,92,.35);color:#ff5c5c;",
            extra_class="error"
        ), 400
        
    if not code or not user_id:
        return render_template('index.html',
            page='message',
            title="Bad Request • Spotify Bot",
            icon="⚠️",
            heading="Invalid Request",
            body_html="<p>The callback is missing required parameters. Please try linking your account again via Discord.</p>",
            extra_class="error"
        ), 400
        
    try:
        user_id = int(user_id)
    except ValueError:
        return render_template('index.html',
            page='message',
            title="Bad Request • Spotify Bot",
            icon="⚠️",
            heading="Invalid State",
            body_html="<p>The authorization state was malformed. Please try the <code>/link</code> command again in Discord.</p>",
            extra_class="error"
        ), 400

    token_url = "https://accounts.spotify.com/api/token"
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")
    
    auth_response = requests.post(token_url, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    })
    
    auth_data = auth_response.json()
    if 'access_token' in auth_data:
        auth_data['expires_at'] = time.time() + auth_data.get('expires_in', 3600)
        
        # Update the in-memory store
        spotify_tokens[user_id] = auth_data
        
        # Schedule the persistent save on the main bot loop
        if bot_loop:
            asyncio.run_coroutine_threadsafe(save_persistent_data(user_id), bot_loop)
            
        return render_template('index.html',
            page='message',
            title="Account Linked • Spotify Bot",
            icon="🎉",
            heading="Account Linked!",
            body_html="""
              <p>Your Spotify account has been successfully connected to the Discord bot.</p>
              <p style="margin-top:12px;">You can now close this window and return to Discord.</p>
            """,
            badge_text="✓ Spotify Connected"
        )
    else:
        err_desc = auth_data.get('error_description', 'Unknown error')
        return render_template('index.html',
            page='message',
            title="Linking Failed • Spotify Bot",
            icon="❌",
            heading="Linking Failed",
            body_html="<p>Could not obtain an access token from Spotify.</p><br><p style='margin-top:20px;'>Please try the <code>/link</code> command in Discord again.</p>",
            badge_text=err_desc,
            badge_style="margin-top:16px;background:rgba(255,92,92,.1);border-color:rgba(255,92,92,.35);color:#ff5c5c;",
            extra_class="error"
        ), 400

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
