from flask import Flask, request, render_template, send_from_directory
from threading import Thread
import os
import requests
import urllib.parse
import database as _db
from database import spotify_tokens, save_persistent_data, user_minutes_listened
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
        total_songs = _db.global_songs_tracked
        
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

# ---------- UptimeRobot Status Page ----------

import datetime as _dt

_status_cache = {"data": None, "fetched_at": 0}
_CACHE_TTL = 300  # 5 minutes

def _fetch_uptimerobot():
    """Fetch and cache UptimeRobot monitor data."""
    now = time.time()
    if _status_cache["data"] and (now - _status_cache["fetched_at"]) < _CACHE_TTL:
        return _status_cache["data"]

    api_key = os.getenv("UPTIMEROBOT_API_KEY")
    if not api_key:
        return None

    try:
        resp = requests.post("https://api.uptimerobot.com/v2/getMonitors", data={
            "api_key": api_key,
            "format": "json",
            "response_times": 1,
            "response_times_limit": 100,
            "logs": 1,
            "logs_limit": 50,
            "custom_uptime_ratios": "1-7-30"
        }, timeout=10)
        data = resp.json()
        if data.get("stat") == "ok":
            _status_cache["data"] = data
            _status_cache["fetched_at"] = now
            return data
    except Exception as e:
        print(f" >>> [DEBUG]: Error fetching UptimeRobot data: {e}")

    return _status_cache.get("data")  # return stale cache on error


def _compute_bars(logs, num_bars=50):
    """Build uptime bar data from UptimeRobot downtime logs (last 3 days)."""
    now = time.time()
    span = 3 * 24 * 3600
    start = now - span
    seg = span / num_bars

    downtimes = []
    for log in logs:
        if log.get("type") == 1:  # type 1 = down
            ds = log["datetime"]
            de = ds + log.get("duration", 0)
            downtimes.append((ds, de))

    bars = []
    for i in range(num_bars):
        s = start + i * seg
        e = s + seg
        down = sum(max(0, min(e, de) - max(s, ds)) for ds, de in downtimes)
        ratio = 1 - (down / seg) if seg else 1

        if ratio >= 0.999:
            status = "up"
        elif ratio > 0:
            status = "degraded"
        else:
            status = "down"

        dt_label = _dt.datetime.fromtimestamp(s, tz=_dt.timezone.utc).strftime("%b %d, %H:%M UTC")
        tooltip = dt_label if status == "up" else f"{dt_label} — {int(down/60)}m downtime"
        bars.append({"status": status, "tooltip": tooltip})

    return bars


def _compute_chart(response_times):
    """Convert response times list into SVG polyline points and avg value."""
    if not response_times:
        return "", 0
    rts = [r for r in reversed(response_times[:80]) if r.get("value", 0) > 0]
    if not rts:
        return "", 0
    vals = [r["value"] for r in rts]
    mx = max(vals) * 1.2 or 1
    avg = int(sum(vals) / len(vals))
    pts = []
    for i, r in enumerate(rts):
        x = (i / max(len(rts) - 1, 1)) * 500
        y = 100 - ((r["value"] / mx) * 80 + 5)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts), avg


def _format_events(logs):
    """Turn raw UptimeRobot logs into human-readable event dicts."""
    now = time.time()
    events = []
    for log in logs[:10]:
        lt = log.get("type")
        ts = log.get("datetime", 0)
        dur = log.get("duration", 0)
        diff = now - ts
        if diff < 3600:
            ago = f"{int(diff/60)}m ago"
        elif diff < 86400:
            ago = f"{int(diff/3600)}h ago"
        else:
            ago = f"{int(diff/86400)}d ago"

        if lt == 1:
            d = f"{int(dur/60)}m" if dur < 3600 else f"{dur/3600:.1f}h"
            events.append({"type": "down", "description": f"Downtime detected — lasted {d}", "time_ago": ago})
        elif lt == 2:
            events.append({"type": "up", "description": "Service recovered — back online", "time_ago": ago})
        elif lt == 98:
            events.append({"type": "up", "description": "Monitoring started", "time_ago": ago})
    return events


@app.route('/status')
def status_page():
    data = _fetch_uptimerobot()

    if not data or not data.get("monitors"):
        is_online = bot_instance and bot_instance.is_ready() and not bot_instance.is_closed()
        return render_template('index.html',
            page='status_fallback',
            title="Status • Spotify Bot",
            icon="📊",
            heading="System Status",
            is_online=is_online,
            latency_ms=int(bot_instance.latency * 1000) if is_online else None
        )

    mon = data["monitors"][0]
    status_map = {0: ("Paused", "paused"), 1: ("Pending", "unknown"),
                  2: ("Operational", "up"), 8: ("Degraded", "degraded"), 9: ("Down", "down")}
    overall_text, overall_class = status_map.get(mon.get("status"), ("Unknown", "unknown"))

    ratios = mon.get("custom_uptime_ratio", "0-0-0").split("-")
    bars = _compute_bars(mon.get("logs", []))
    chart_points, avg_response = _compute_chart(mon.get("response_times", []))
    events = _format_events(mon.get("logs", []))

    current_lat = int(bot_instance.latency * 1000) if (bot_instance and bot_instance.is_ready()) else None

    return render_template('index.html',
        page='status',
        title="Status • Spotify Bot",
        icon="📊",
        heading="System Status",
        overall_text=overall_text,
        overall_class=overall_class,
        monitor_name=mon.get("friendly_name", "Bot Monitor"),
        uptime_24h=ratios[0] if len(ratios) > 0 else "0",
        uptime_7d=ratios[1] if len(ratios) > 1 else "0",
        uptime_30d=ratios[2] if len(ratios) > 2 else "0",
        bars=bars,
        chart_points=chart_points,
        avg_response=avg_response,
        events=events,
        current_latency=current_lat
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
