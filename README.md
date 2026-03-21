# its so peak :sob:
# Spotify Presence Tracker Discord Bot

![alt text](peak.png)

A feature-rich Discord bot designed to track and announce Spotify listening activity in real-time. It supports both traditional prefix commands and modern slash commands, features a built-in console for direct interaction, and includes a keep-alive mechanism for 24/7 hosting.

## Features

- **Real-time Tracking**: Automatically announces when a user starts listening to Spotify or changes tracks.
- **Skip Detection**: Sophisticated logic that detects multiple skips within a short window and sends a summary message to avoid chat spam.
- **Persistent History**: Remembers the last 5 tracks recorded for each user across tracking sessions.
- **Hybrid Commands**: Seamlessly supports both prefix (`%`) and slash commands.
- **Console Controller**: Provides a terminal-based interface to send messages through the bot and manage target channels/users directly.
- **Keep-Alive System**: Includes a lightweight Flask server to keep the bot active on hosting platforms like Replit/Render.

## Getting Started

### Prerequisites

- Python 3.8 or higher
- A Discord Bot Token (from the [Discord Developer Portal](https://discord.com/developers/applications))

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
   Create a `.env` file in the root directory and add your bot token:
   ```env
   DISCORD_TOKEN=your_token_here
   ```
   *(Note: Ensure line 11 in `bot.py` is updated to load from `os.getenv('DISCORD_TOKEN')` or matches your configuration).*

4. **Run the bot**:
   ```bash
   python bot.py
   ```

## Commands

The default prefix is `%`. All commands are also available as slash commands.

| Command | Description |
| :--- | :--- |
| `%trackme <minutes>` | Starts tracking your Spotify activity for the specified duration. |
| `%tracklist` | Displays a list of all users currently being tracked. |
| `%history` | Shows your last 5 recorded tracks from previous sessions. |
| `%stoptrack` | Manually stops tracking your Spotify activity. |
| `%ping` | Checks the bot's latency. |

## Console Controller

When the bot is running, you can use the terminal console to interact with Discord directly:

- `/target [ID]`: Set a target user or channel ID to chat with.
- `/clear`: Clear the active target.
- `/stopresponses`: Stop printing incoming messages from the target to the console.
- `/startresponses`: Resume printing incoming messages.
- `/showdetails`: Print the current state of tracked users.
- `/exit`: Shut down the bot safely.

## Dependencies

- `discord.py`: The core library for interacting with Discord.
- `python-dotenv`: For managing environment variables.
- `flask`: Powers the `keep_alive` server.
- `aioconsole`: Enables asynchronous terminal input for the controller.

## Note for Future Me

To host the server, watch [this video](https://www.youtube.com/watch?v=HZis54wRF98).
<br>You already have a Render and UptimeRobot account, just use those.

<br> :thumbsup: