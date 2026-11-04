"""Safe, opt-in Discord server setup commands for TOV4R.

No destructive operations: existing channels, categories and roles are preserved.
Requires Manage Guild and explicit confirmation. The bot needs Manage Channels
and Manage Roles. Discord role hierarchy still applies.
"""
import logging

import discord
from discord import app_commands

log = logging.getLogger("tov4r.setup")
TARGET_GUILD_ID = 1564283597173690380  # 1729 server only

CATEGORIES = {
    "📌 INFORMATION": (
        ("📜・rules", "text"),
        ("📢・announcements", "text"),
        ("👋・welcome", "text"),
    ),
    "💬 COMMUNITY": (
        ("💬・general", "text"),
        ("🎮・gaming", "text"),
        ("📸・media", "text"),
        ("🔊・General", "voice"),
        ("🎵・Music", "voice"),
    ),
    "🛠️ SUPPORT": (
        ("🎫・support", "text"),
    ),
}
ROLE_NAMES = ("Member", "Gamer", "Streamer")


def plan(guild: discord.Guild):
    missing_categories = [name for name in CATEGORIES if not discord.utils.get(guild.categories, name=name)]
    missing_channels = []
    for category, items in CATEGORIES.items():
        parent = discord.utils.get(guild.categories, name=category)
        for name, kind in items:
            existing = discord.utils.get(parent.channels, name=name) if parent else None
            if existing is None:
                missing_channels.append(name)
    missing_roles = [name for name in ROLE_NAMES if not discord.utils.get(guild.roles, name=name)]
    return missing_categories, missing_channels, missing_roles


def guild_allowed(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.guild.id == TARGET_GUILD_ID


server = app_commands.Group(name="server", description="Safe setup for your Discord community")


@server.command(name="preview", description="Preview missing channels and roles without modifying anything")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def preview(interaction: discord.Interaction):
    if not guild_allowed(interaction):
        await interaction.response.send_message("⛔ This server is not authorized for setup.", ephemeral=True)
        return
    cats, chans, roles = plan(interaction.guild)
    lines = [
        f"**Server:** {discord.utils.escape_markdown(interaction.guild.name)}",
        f"**Categories to create:** {len(cats)}",
        f"**Channels to create:** {len(chans)}",
        f"**Roles to create:** {len(roles)}",
        "",
        "**Channels:** " + (", ".join(chans) if chans else "none"),
        "**Roles:** " + (", ".join(roles) if roles else "none"),
        "",
        "Nothing was changed. Run /server setup confirm:true to apply.",
    ]
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


@server.command(name="setup", description="Create missing community channels and safe roles")
@app_commands.describe(confirm="Must be true to create the missing items")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction, confirm: bool = False):
    if not guild_allowed(interaction):
        await interaction.response.send_message("⛔ This server is not authorized for setup.", ephemeral=True)
        return
    if not confirm:
        await interaction.response.send_message(
            "No changes made. Use /server preview, then /server setup confirm:true.",
            ephemeral=True,
        )
        return
    guild = interaction.guild
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "❌ Bot requires Manage Channels and Manage Roles permissions.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    created = []
    errors = []
    reason = f"Requested by {interaction.user} ({interaction.user.id}) via TOV4R server setup"
    for name in ROLE_NAMES:
        if discord.utils.get(guild.roles, name=name) is None:
            try:
                await guild.create_role(name=name, permissions=discord.Permissions.none(), reason=reason)
                created.append(f"Role: {name}")
            except discord.DiscordException as exc:
                log.warning("Could not create role %r: %s", name, type(exc).__name__)
                errors.append(f"Role: {name}")
    for name, entries in CATEGORIES.items():
        category = discord.utils.get(guild.categories, name=name)
        if category is None:
            try:
                category = await guild.create_category(name, reason=reason)
                created.append(f"Category: {name}")
            except discord.DiscordException as exc:
                log.warning("Could not create category %r: %s", name, type(exc).__name__)
                errors.append(f"Category: {name}")
                continue
        for ch_name, kind in entries:
            if discord.utils.get(category.channels, name=ch_name):
                continue
            try:
                if kind == "voice":
                    await guild.create_voice_channel(ch_name, category=category, reason=reason)
                else:
                    overwrites = None
                    if ch_name == "📜・rules":
                        overwrites = {guild.default_role: discord.PermissionOverwrite(send_messages=False)}
                    await guild.create_text_channel(ch_name, category=category, overwrites=overwrites, reason=reason)
                created.append(f"Channel: {ch_name}")
            except discord.DiscordException as exc:
                log.warning("Could not create channel %r: %s", ch_name, type(exc).__name__)
                errors.append(f"Channel: {ch_name}")
    summary = (
        f"✅ Created {len(created)} items. "
        f"Existing channels, roles and permissions were not deleted or changed."
    )
    if errors:
        summary += f"\n⚠️ Failed ({len(errors)}): " + ", ".join(errors)
    if created:
        summary += "\n" + "\n".join("• " + x for x in created)
    await interaction.followup.send(summary[:1900], ephemeral=True)


def register_server_commands(bot):
    bot.tree.add_command(server)
