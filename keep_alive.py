from flask import Flask, request
from threading import Thread
import os
import requests
import urllib.parse
from database import spotify_tokens, save_persistent_data
import asyncio
import time

app = Flask('')

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")

bot_loop = None

def set_bot_loop(loop):
    global bot_loop
    bot_loop = loop

@app.route('/')
def home():
    return "Bot OK"

@app.route('/login')
def login():
    user_id = request.args.get('user_id')
    if not user_id:
        return "Missing user_id", 400
        
    scope = 'user-read-currently-playing user-read-playback-state user-read-recently-played'
    params = {
        'response_type': 'code',
        'client_id': SPOTIFY_CLIENT_ID,
        'scope': scope,
        'redirect_uri': SPOTIFY_REDIRECT_URI,
        'state': user_id
    }
    url = 'https://accounts.spotify.com/authorize?' + urllib.parse.urlencode(params)
    return f'<script>window.location.href="{url}";</script>'

@app.route('/callback')
def callback():
    code = request.args.get('code')
    user_id = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return f"Error linking account: {error}"
        
    if not code or not user_id:
        return "Invalid callback data", 400
        
    try:
        user_id = int(user_id)
    except ValueError:
        return "Invalid state parameter", 400

    token_url = "https://accounts.spotify.com/api/token"
    auth_response = requests.post(token_url, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    })
    
    auth_data = auth_response.json()
    if 'access_token' in auth_data:
        auth_data['expires_at'] = time.time() + auth_data.get('expires_in', 3600)
        
        # Update the in-memory store
        spotify_tokens[user_id] = auth_data
        
        # Schedule the persistent save on the main bot loop
        if bot_loop:
            asyncio.run_coroutine_threadsafe(save_persistent_data(user_id), bot_loop)
            
        return "🎉 Successfully linked Spotify account! You can close this window and go back to Discord."
    else:
        return f"Failed to link account: {auth_data.get('error_description', 'unknown error')}", 400

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

