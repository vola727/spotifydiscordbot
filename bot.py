import sys
sys.stdout.reconfigure(line_buffering=True)
import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
import keep_alive
from database import load_persistent_data

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN is None:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit()

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
intents.members = True

bot = commands.Bot(command_prefix="%", intents=intents)

@bot.event
async def on_ready():
    # Load persistent data from DB/JSON
    await load_persistent_data()
    print(f' >>> [SYSTEM]: Logged in as {bot.user.name}')
    
    # Sync hybrid commands
    try:
        synced = await bot.tree.sync()
        print(f" >>> [SYSTEM]: Synced {len(synced)} slash command(s) successfully.")
    except Exception as e:
        print(f" >>> [DEBUG]: Error syncing commands: {e}")

async def load_extensions():
    """Dynamically load all cogs from the cogs/ directory."""
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                print(f" >>> [SYSTEM]: Loaded extension: {filename}")
            except Exception as e:
                print(f" >>> [DEBUG]: Failed to load extension {filename}: {e}")

async def main():
    async with bot:
        await load_extensions()
        keep_alive.set_bot_loop(asyncio.get_running_loop())
        keep_alive.keep_alive()
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
