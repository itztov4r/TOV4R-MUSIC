"""Opt-in native Discord AutoMod and developer information."""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

DASHBOARD = "https://tov4r-bot-production.up.railway.app"
COMMAND_BADGE = "https://support-dev.discord.com/hc/en-us/articles/7612640996503"
AUTOMOD_BADGE = "https://support-dev.discord.com/hc/en-us/articles/13847462843543"

class DeveloperEdition(commands.Cog):
    dev = app_commands.Group(name="dev", description="Developer information and badge requirements")
    native = app_commands.Group(name="native_automod", description="Opt-in Discord AutoMod rules")

    def __init__(self, bot):
        self.bot = bot
        self.rule_lock = asyncio.Lock()

    @dev.command(name="ping", description="Show gateway latency")
    async def ping(self, i: discord.Interaction):
        await i.response.send_message(f"Gateway: {self.bot.latency * 1000:.0f} ms", ephemeral=True)

    @dev.command(name="info", description="Show live bot information and useful links")
    async def info(self, i: discord.Interaction):
        await i.response.send_message(
            f"TOV4R Bot | Application ID: {self.bot.application_id}\n"
            f"Connected servers: {len(self.bot.guilds)}\n"
            f"Dashboard: {DASHBOARD}\nWebsite: https://tov4r.xyz",
            ephemeral=True)

    @dev.command(name="badges", description="Check global commands and official badge requirements")
    async def badges(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            count = len(await self.bot.tree.fetch_commands())
            status = f"{count} registered global commands; " + ("command requirement met" if count else "requirement not met")
        except discord.HTTPException:
            status = "Unable to verify registered global commands."
        await i.followup.send(
            f"Supports Commands: {status}.\n{COMMAND_BADGE}\n"
            f"Uses AutoMod: Discord requires 100 rules across servers. "
            f"Use /native_automod status for this server's count; "
            f"global badge award is not verified here.\n{AUTOMOD_BADGE}",
            ephemeral=True)

    @native.command(name="status", description="Show native rule counts for this server")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_guild=True)
    async def status(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            rules = await i.guild.fetch_automod_rules()
        except discord.HTTPException:
            return await i.followup.send("Cannot read Discord AutoMod rules. Check permissions.", ephemeral=True)
        owned = [r for r in rules if r.creator_id == self.bot.user.id]
        await i.followup.send(
            f"Native rules: {len(rules)}\nCreated by this bot: {len(owned)}\n"
            f"Enabled bot-created rules: {sum(r.enabled for r in owned)}\n"
            "Server-only count; not proof of a global badge.", ephemeral=True)

    @native.command(name="setup", description="Optionally create a native anti-mention-spam rule")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_guild=True)
    @app_commands.describe(confirm="True to create; false for a preview", mention_limit="Maximum mentions per message")
    async def setup_rule(self, i: discord.Interaction, confirm: bool = False,
                         mention_limit: app_commands.Range[int, 3, 50] = 8):
        await i.response.defer(ephemeral=True, thinking=True)
        if not confirm:
            return await i.followup.send(
                f"Preview: block messages exceeding {mention_limit} mentions. "
                "Run again with confirm=True to enable. Existing rules will not be changed.", ephemeral=True)
        async with self.rule_lock:
            try:
                rules = await i.guild.fetch_automod_rules()
                if any(r.trigger.type == discord.AutoModRuleTriggerType.mention_spam for r in rules):
                    return await i.followup.send("A native mention-spam rule already exists; no changes made.", ephemeral=True)
                await i.guild.create_automod_rule(
                    name="TOV4R | Mention spam protection",
                    event_type=discord.AutoModRuleEventType.message_send,
                    trigger=discord.AutoModTrigger(mention_limit=mention_limit),
                    actions=[discord.AutoModRuleAction(custom_message="Too many mentions in one message.")],
                    enabled=True,
                    reason=f"Opt-in native AutoMod requested by administrator {i.user.id}")
            except discord.HTTPException:
                return await i.followup.send("Discord rejected the operation. Check Manage Server permissions and rule limits; use status before retrying.", ephemeral=True)
        await i.followup.send("Native mention-spam protection enabled. Existing rules untouched.", ephemeral=True)

    async def cog_app_command_error(self, i: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, (app_commands.MissingPermissions, app_commands.BotMissingPermissions)):
            message = "Manage Server permission is required for you and the bot."
        else:
            message = "Command failed. No success is assumed; check status before retrying."
        if i.response.is_done():
            await i.followup.send(message, ephemeral=True)
        else:
            await i.response.send_message(message, ephemeral=True)
