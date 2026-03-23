import discord
from discord.ext import commands
import datetime

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="ping", description="Check the bot's latency")
    async def ping(self, ctx):
        """Responds with the bot's current latency."""
        await ctx.defer()
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 **Pong!** Bot latency: **{latency}ms**")

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
