import discord
from discord.ext import commands
import datetime
from database import user_history, user_artist_counts
from utils import create_spotify_embed

class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="history", description="Show the last 5 songs recorded during your tracking sessions")
    async def history(self, ctx):
        """Displays the persistent song history of a user."""
        await ctx.defer()
        user_id = ctx.author.id
        history_list = user_history.get(user_id, [])
        
        if not history_list:
            await ctx.send("❌ **Error:** You have never been tracked by this bot! Use `/trackme` to start tracking your session.")
            return

        history_text = "\n".join([f"{i}. {song}" for i, song in enumerate(history_list, 1)])

        embed = discord.Embed(
            title=f"📜 History for {ctx.author.display_name}",
            color=discord.Color.blue(),
            description=f"Here are the last 5 songs recorded during your tracking sessions:\n\n{history_text}"
        )

        from database import tracked_users
        status = "Active" if user_id in tracked_users else "Ended"
        embed.set_footer(text=f"Last session status: {status}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="topartists", description="Show your most listened to artists recorded during tracking sessions")
    async def topartists(self, ctx, member: discord.Member = None):
        """Displays the most listened to artists for a user."""
        await ctx.defer()
        target_member = member or ctx.author
        user_id = target_member.id
        
        artist_data = user_artist_counts.get(user_id, {})
        
        if not artist_data:
            await ctx.send(
                f"❌ **Error:** No artist data found for {target_member.display_name}. "
                f"Use `/trackme` to start recording your listening stats!"
            )
            return

        # Sort artists by count descending
        sorted_artists = sorted(artist_data.items(), key=lambda item: item[1], reverse=True)
        top_artists = sorted_artists[:10]
        
        description = ""
        for i, (artist, count) in enumerate(top_artists, 1):
            plays_word = "play" if count == 1 else "plays"
            description += f"**{i}.** {artist} — `{count} {plays_word}`\n"

        embed = discord.Embed(
            title=f"🔝 Top Artists for {target_member.display_name}",
            color=discord.Color.purple(),  # Vibrant purple for stats
            description=description,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        if target_member.display_avatar:
            embed.set_thumbnail(url=target_member.display_avatar.url)
        
        total_plays = sum(artist_data.values())
        embed.set_footer(text=f"Total recorded artist plays: {total_plays}")
        
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="song", description="Show what a user is currently listening to on Spotify")
    async def song(self, ctx, member: discord.Member = None):
        """Displays the current Spotify activity of a user."""
        await ctx.defer()
        target_member = member or ctx.author
        
        # Refresh member from guild cache
        if ctx.guild:
            target_member = ctx.guild.get_member(target_member.id) or target_member
        
        spotify = discord.utils.find(lambda a: isinstance(a, discord.Spotify) or a.name == 'Spotify', target_member.activities)
        
        if not spotify:
            error_msg = f"❌ **Error:** {target_member.display_name} is not currently listening to Spotify."
            if target_member == ctx.author:
                 error_msg += (
                    "\n\n**Troubleshooting:**\n"
                    "1. Make sure you are actively playing music on Spotify.\n"
                    "2. Ensure your Discord **Privacy Settings** have 'Display current activity as a status message' **enabled**.\n"
                    "3. Verify that your Spotify account is **linked to Discord** and 'Display Spotify as your status' is on."
                )
            await ctx.send(error_msg)
            return

        embed = await create_spotify_embed(target_member, spotify)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Stats(bot))
