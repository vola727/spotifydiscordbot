from flask import Flask, request, render_template_string
from threading import Thread
import os
import requests
import urllib.parse
from database import spotify_tokens, save_persistent_data
import asyncio
import time

app = Flask('')

bot_loop = None

def set_bot_loop(loop):
    global bot_loop
    bot_loop = loop

# ---------- Shared CSS/HTML helpers ----------

BASE_STYLE = """
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', sans-serif;
    background: #0a0a0f;
    color: #e8e8f0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  /* animated mesh background */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background:
      radial-gradient(ellipse 80% 60% at 20% 10%, rgba(30,215,96,.18) 0%, transparent 60%),
      radial-gradient(ellipse 60% 80% at 80% 80%, rgba(30,215,96,.10) 0%, transparent 60%),
      radial-gradient(ellipse 50% 50% at 50% 50%, rgba(10,10,15,1) 100%, transparent);
    z-index: -1;
    animation: pulse 8s ease-in-out infinite alternate;
  }
  @keyframes pulse {
    from { opacity: .7; }
    to   { opacity: 1; }
  }
  .card {
    background: rgba(255,255,255,.04);
    border: 1px solid rgba(255,255,255,.08);
    backdrop-filter: blur(20px);
    border-radius: 24px;
    padding: 52px 56px;
    max-width: 520px;
    width: 92%;
    text-align: center;
    box-shadow: 0 8px 48px rgba(0,0,0,.5);
    animation: rise .6s cubic-bezier(.22,1,.36,1) both;
  }
  @keyframes rise {
    from { opacity:0; transform: translateY(28px); }
    to   { opacity:1; transform: translateY(0); }
  }
  .icon { font-size: 3.5rem; margin-bottom: 20px; }
  h1 {
    font-size: 2rem;
    font-weight: 900;
    letter-spacing: -.03em;
    margin-bottom: 12px;
    background: linear-gradient(135deg, #1ed760, #17a349);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  p {
    font-size: 1.05rem;
    font-weight: 600;
    color: rgba(232,232,240,.65);
    line-height: 1.6;
  }
  .badge {
    display: inline-block;
    margin-top: 28px;
    padding: 8px 22px;
    border-radius: 999px;
    background: rgba(30,215,96,.15);
    border: 1px solid rgba(30,215,96,.35);
    font-size: .85rem;
    font-weight: 700;
    color: #1ed760;
    letter-spacing: .04em;
    text-transform: uppercase;
  }
  .btn {
    display: inline-block;
    margin-top: 28px;
    padding: 13px 36px;
    border-radius: 999px;
    background: linear-gradient(135deg, #1ed760, #17a349);
    color: #000;
    font-weight: 800;
    font-size: 1rem;
    text-decoration: none;
    transition: transform .18s, box-shadow .18s;
    box-shadow: 0 4px 24px rgba(30,215,96,.35);
  }
  .btn:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(30,215,96,.5); }
  .error-icon { font-size: 3.5rem; margin-bottom: 20px; }
  .error h1 {
    background: linear-gradient(135deg, #ff5c5c, #c0392b);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .error .badge {
    background: rgba(255,92,92,.1);
    border-color: rgba(255,92,92,.35);
    color: #ff5c5c;
  }
"""

def _page(title, icon, heading, body_html, extra_class=""):
    return render_template_string(f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{title}</title>
      <style>{BASE_STYLE}</style>
    </head>
    <body>
      <div class="card {extra_class}">
        <div class="icon">{icon}</div>
        <h1>{heading}</h1>
        {body_html}
      </div>
    </body>
    </html>
    """)

# ---------- Routes ----------

@app.route('/')
def home():
    return _page(
        title="Spotify Bot • Online",
        icon="🎵",
        heading="Bot is Online",
        body_html="""
          <p>Your Spotify Discord bot is running smoothly and ready to track music.</p>
          <span class="badge">✓ All systems operational</span>
        """
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
    return f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Connecting to Spotify…</title>
      <style>{BASE_STYLE}</style>
    </head>
    <body>
      <div class="card">
        <div class="icon">🔗</div>
        <h1>Connecting to Spotify</h1>
        <p>Redirecting you to Spotify's login page to authorize your account…</p>
        <span class="badge">⏳ Redirecting</span>
      </div>
      <script>setTimeout(()=>window.location.href="{url}", 800);</script>
    </body>
    </html>
    '''

@app.route('/callback')
def callback():
    code = request.args.get('code')
    user_id = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return _page(
            title="Linking Failed • Spotify Bot",
            icon="❌",
            heading="Linking Failed",
            body_html=f"""
              <p>Spotify returned an error while linking your account:</p>
              <span class="badge" style="margin-top:16px;background:rgba(255,92,92,.1);border-color:rgba(255,92,92,.35);color:#ff5c5c;">{error}</span>
              <br><p style="margin-top:20px;">Please try the <code>/link</code> command in Discord again.</p>
            """,
            extra_class="error"
        ), 400
        
    if not code or not user_id:
        return _page(
            title="Bad Request • Spotify Bot",
            icon="⚠️",
            heading="Invalid Request",
            body_html="<p>The callback is missing required parameters. Please try linking your account again via Discord.</p>",
            extra_class="error"
        ), 400
        
    try:
        user_id = int(user_id)
    except ValueError:
        return _page(
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
            
        return _page(
            title="Account Linked • Spotify Bot",
            icon="🎉",
            heading="Account Linked!",
            body_html="""
              <p>Your Spotify account has been successfully connected to the Discord bot.</p>
              <p style="margin-top:12px;">You can now close this window and return to Discord.</p>
              <span class="badge">✓ Spotify Connected</span>
            """
        )
    else:
        err_desc = auth_data.get('error_description', 'Unknown error')
        return _page(
            title="Linking Failed • Spotify Bot",
            icon="❌",
            heading="Linking Failed",
            body_html=f"""
              <p>Could not obtain an access token from Spotify.</p>
              <span class="badge" style="margin-top:16px;background:rgba(255,92,92,.1);border-color:rgba(255,92,92,.35);color:#ff5c5c;">{err_desc}</span>
              <br><p style="margin-top:20px;">Please try the <code>/link</code> command in Discord again.</p>
            """,
            extra_class="error"
        ), 400

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

