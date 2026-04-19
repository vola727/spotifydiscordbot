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
  .footer-container {
    position: fixed;
    bottom: 24px;
    width: 100%;
    display: flex;
    justify-content: center;
    z-index: 10;
  }
  .footer {
    display: flex;
    align-items: center;
    gap: 8px;
    text-decoration: none;
    color: rgba(232, 232, 240, 0.6);
    font-size: 0.9rem;
    font-weight: 600;
    transition: all 0.2s ease;
    padding: 10px 20px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
    backdrop-filter: blur(10px);
  }
  .footer:hover {
    color: #1ed760;
    background: rgba(30, 215, 96, 0.1);
    border-color: rgba(30, 215, 96, 0.3);
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(30, 215, 96, 0.2);
  }
  .footer svg {
    fill: currentColor;
    width: 20px;
    height: 20px;
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
      <div class="footer-container">
        <a href="https://github.com/vola727/spotifydiscordbot" target="_blank" rel="noopener noreferrer" class="footer">
          <svg viewBox="0 0 24 24">
            <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
          </svg>
          Source Code
        </a>
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
    return _page(
        title="Connecting to Spotify…",
        icon="🔗",
        heading="Connecting to Spotify",
        body_html=f'''
          <p>Redirecting you to Spotify's login page to authorize your account…</p>
          <span class="badge">⏳ Redirecting</span>
          <script>setTimeout(()=>window.location.href="{url}", 800);</script>
        '''
    )

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

