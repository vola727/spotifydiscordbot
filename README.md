# its so peak :sob:

# Spotify Presence Tracker Discord Bot

![alt text](peak.png)

A feature-rich Discord bot designed to track and announce Spotify listening activity in real-time — both through Discord presence and directly via the Spotify Web API. It supports both prefix and slash commands, features persistent data storage via MongoDB Atlas, a built-in OAuth2 flow for offline tracking, and a keep-alive server for 24/7 hosting on platforms like Render.

## Features

- **Real-time Tracking**: Announces when a tracked user starts listening to Spotify or changes tracks via Discord presence events.
- **Offline / API Tracking**: Links users' Spotify accounts via OAuth2 to poll the Spotify Web API every 15 seconds — tracking activity even when Discord is closed or set to invisible.
- **Auto-Track**: Users can opt into automatic tracking so the bot starts a session whenever they open Spotify, with no manual commands needed.
- **Skip Detection**: Detects multiple rapid skips and sends a single summary message instead of spamming the channel.
- **Ad Detection**: Detects the brief pause caused by a Spotify ad and sends a fun notification when music resumes.
- **Persistent History**: Remembers the last 5 tracks and per-artist play counts for each user across sessions, stored in MongoDB Atlas (with local JSON fallback).
- **Top Artists Stats**: Shows a ranked leaderboard of the most listened-to artists recorded during tracking sessions.
- **Colour-Matched Embeds**: Embeds use the dominant colour extracted from the album art.
- **Hybrid Commands**: Seamlessly supports both prefix (`%`) and slash commands.
- **Keep-Alive Server**: Includes a lightweight Flask server to keep the bot active on hosting platforms like Render.

## Project Structure

```
spotifydiscordbot/
├── bot.py              # Entry point — loads cogs, syncs slash commands, starts keep-alive
├── database.py         # Shared state, MongoDB Atlas integration, JSON fallback
├── keep_alive.py       # Flask server for uptime + Spotify OAuth2 callback (/login, /callback)
├── utils.py            # Shared helpers: embed builder, colour extractor, channel resolver
├── requirements.txt
└── cogs/
    ├── tracking.py     # Core tracking logic, presence listeners, trackme/stoptrack/autotrack commands
    ├── spotify_api.py  # Spotify Web API polling loop, link/unlink commands
    ├── stats.py        # history, topartists, song commands
    └── general.py      # ping, cleanse commands
```

## Getting Started

### Prerequisites

- Python 3.8 or higher
- A Discord Bot Token (from the [Discord Developer Portal](https://discord.com/developers/applications)) with **Presence Intent** and **Server Members Intent** enabled
- A [Spotify Developer App](https://developer.spotify.com/dashboard) (for offline/API tracking)
- A [MongoDB Atlas](https://www.mongodb.com/atlas) cluster (for persistent data; local JSON is used as a fallback)

### Installation

1. **Clone the repository**:

   ```bash
   git clone https://github.com/vola727/spotifydiscordbot.git
   cd spotifydiscordbot
   ```

2. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   Create a `.env` file in the root directory:

   ```env
   DISCORD_TOKEN=your_discord_bot_token

   # MongoDB (optional but recommended for persistence)
   MONGO_URI=your_mongodb_connection_string

   # Spotify OAuth2 (required for offline/API tracking)
   SPOTIFY_CLIENT_ID=your_spotify_client_id
   SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
   SPOTIFY_REDIRECT_URI=https://your-host.com/callback

   # Public URL of the bot's web server (used to generate /link URLs)
   BOT_WEB_URL=https://your-host.com
   ```

   In your Spotify Developer App settings, add `https://your-host.com/callback` as a Redirect URI.

4. **Run the bot**:
   ```bash
   python bot.py
   ```

## Commands

The default prefix is `%`. All commands are also available as slash commands.

### Tracking

| Command               | Description                                                                                    |
| :-------------------- | :--------------------------------------------------------------------------------------------- |
| `%trackme <minutes>`  | Starts tracking your Spotify activity for the specified duration.                              |
| `%stoptrack`          | Manually stops tracking your Spotify activity.                                                 |
| `%autotrack`          | Toggles auto-tracking. When enabled, the bot automatically starts a session when you play music on Spotify. Disabling it also removes you from the active tracklist. |
| `%tracklist`          | Displays all users currently being tracked, along with their current song and time remaining.  |

### Spotify Account Linking (for Offline Tracking)

| Command    | Description                                                                                   |
| :--------- | :-------------------------------------------------------------------------------------------- |
| `%link`    | Links your Spotify account via OAuth2 to enable tracking even when Discord is closed/invisible. |
| `%unlink`  | Revokes the Spotify link and removes your stored tokens.                                      |

### Stats

| Command                    | Description                                                          |
| :------------------------- | :------------------------------------------------------------------- |
| `%history`                 | Shows your last 5 recorded tracks from previous sessions.            |
| `%topartists [member]`     | Shows the top 10 most listened-to artists for you or another member. |
| `%song [member]`           | Shows what a user is currently listening to on Spotify.              |

### General

| Command    | Description                                                              |
| :--------- | :----------------------------------------------------------------------- |
| `%ping`    | Checks the bot's latency.                                                |
| `%cleanse` | *(Admin only)* Deletes all of the bot's messages sent in the last hour.  |

## How Offline Tracking Works

1. A user runs `/link` and authorizes the bot's Spotify app via the OAuth2 flow hosted on the Flask server.
2. The bot stores the access and refresh tokens in MongoDB Atlas.
3. Every 15 seconds, the `spotify_polling` loop checks all tracked + linked users.
4. If a user lacks a Discord presence (offline, invisible, or Spotify not shown), the bot calls the Spotify Web API directly to get the currently playing track.
5. Tokens are refreshed automatically before they expire.
6. If **Auto-Track** is enabled and the user isn't in the tracklist, the bot will auto-start a session if the API confirms they are actively playing.

## Update Channel

All Spotify status announcements (song changes, skips, ads) are posted to a channel named `#spotify-updates` if it exists in the server, otherwise they fall back to the channel where the tracking command was issued.

## Dependencies

| Package         | Purpose                                               |
| :-------------- | :---------------------------------------------------- |
| `discord.py`    | Core Discord library                                  |
| `python-dotenv` | Loads environment variables from `.env`               |
| `flask`         | Keep-alive server and OAuth2 callback endpoint        |
| `requests`      | Synchronous HTTP for the OAuth2 token exchange        |
| `aiohttp`       | Async HTTP for Spotify Web API polling                |
| `motor`         | Async MongoDB driver                                  |
| `dnspython`     | Required for MongoDB Atlas SRV connection strings     |
| `colorthief`    | Extracts dominant colour from album art               |
| `pillow`        | Image processing (used by colorthief)                 |
| `prettytable`   | Table formatting for debug/admin endpoints            |
