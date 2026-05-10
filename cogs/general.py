import discord
from discord.ext import commands
import datetime
from database import guild_settings, save_guild_data

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="ping", description="Check the bot's latency")
    async def ping(self, ctx):
        """Responds with the bot's current latency."""
        await ctx.defer()
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 **Pong!** Bot latency: **{latency}ms**")

    @commands.hybrid_command(name="help", description="Shows this sleek help menu")
    async def help(self, ctx):
        """Displays all available commands and their descriptions."""
        await ctx.defer(ephemeral=True)
        
        prefix = guild_settings.get(ctx.guild.id, {}).get("prefix", "%") if ctx.guild else "%"
        
        embed = discord.Embed(
            title="✨ Bot Help Menu",
            description=f"Here are all the available commands. You can use slash commands (`/`) or the `{prefix}` prefix.",
            color=discord.Color.from_rgb(30, 215, 96) # Spotify Green
        )
        
        cog_metadata = {
            "Tracking": {"emoji": "🎵", "title": "Spotify Tracking"},
            "Stats": {"emoji": "📊", "title": "Statistics"},
            "SpotifyAPI": {"emoji": "🔗", "title": "Account Linking"},
            "General": {"emoji": "🛠️", "title": "General"}
        }

        # Iterate through cogs in a specific order if possible, or just as they come
        for cog_name in ["Tracking", "Stats", "SpotifyAPI", "General"]:
            cog = self.bot.get_cog(cog_name)
            if cog:
                meta = cog_metadata.get(cog_name, {"emoji": "⚙️", "title": cog_name})
                commands_list = []
                for command in cog.get_commands():
                    # Skip hidden commands or the help command itself if we wanted to (but we show it)
                    desc = command.description or command.help or "No description provided."
                    commands_list.append(f"`/{command.name}` - {desc}")
                
                if commands_list:
                    embed.add_field(
                        name=f"{meta['emoji']} {meta['title']}",
                        value="\n".join(commands_list),
                        inline=False
                    )

        if self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
        embed.set_footer(text="Spotify Discord Bot • Tracking your peak moments")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="prefix", description="Change the bot's command prefix for this server")
    @commands.has_permissions(administrator=True)
    async def prefix(self, ctx, new_prefix: str):
        """Sets a new prefix for the bot in this server. (Admin only)"""
        await ctx.defer(ephemeral=True)
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
            return
            
        if len(new_prefix) > 5:
            await ctx.send("❌ Prefix cannot be longer than 5 characters.", ephemeral=True)
            return

        if ctx.guild.id not in guild_settings:
            guild_settings[ctx.guild.id] = {}
            
        guild_settings[ctx.guild.id]["prefix"] = new_prefix
        await save_guild_data(ctx.guild.id)
        
        await ctx.send(f"✅ The bot's prefix for this server has been changed to `{new_prefix}`", ephemeral=True)

    @prefix.error
    async def prefix_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ **Error:** You need **Administrator** permissions to use this command.", ephemeral=True)

    @commands.hybrid_command(name="cleanse", description="Removes all my messages sent in the last hour (Admin only)")
    @commands.has_permissions(administrator=True)
    async def cleanse(self, ctx):
        """Deletes all messages from the bot sent in the last hour."""
        await ctx.defer(ephemeral=True)
        
        count = 0
        one_hour_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        
        async for message in ctx.channel.history(after=one_hour_ago, limit=500):
            if message.author == self.bot.user:
                try:
                    await message.delete()
                    count += 1
                except discord.HTTPException:
                    pass
                    
        await ctx.send(f"🧹 Cleaned up **{count}** of my messages from the last hour.", ephemeral=True)

    @cleanse.error
    async def cleanse_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ **Error:** You need **Administrator** permissions to use this command.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(General(bot))
