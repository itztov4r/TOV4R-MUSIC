"""TOV4R Server Manager: read-only audits and explicitly confirmed changes.

Never performs mass deletion, auto-invites bots, or changes settings in the background.
All reports are delivered ephemerally to a server manager.
"""
from __future__ import annotations

import io
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger("tov4r.server")
RISKY = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "ban_members", "kick_members", "manage_webhooks", "manage_messages",
    "moderate_members", "mention_everyone", "manage_threads",
    "view_audit_log", "manage_events",
)
PROTECTED_NAMES = ("owner", "admin", "staff", "moderator", "mod", "support")
SENSITIVE_CHANNEL_NAMES = ("staff", "admin", "mod-log", "logs", "private", "ticket")


def privileged_permissions(role: discord.Role) -> list[str]:
    return [name for name in RISKY if getattr(role.permissions, name, False)]


def snapshot(guild: discord.Guild) -> dict:
    """Collect real Discord state; never infer bot feature health or unused status."""
    issues = []
    repeated_roles = defaultdict(list)
    repeated_channels = defaultdict(list)

    roles = []
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        enabled = [name for name, value in role.permissions if value]
        risks = privileged_permissions(role)
        repeated_roles[role.name.casefold().strip()].append(role.id)
        if role.is_default() and risks:
            issues.append({
                "level": "critical", "kind": "everyone_permissions",
                "role_id": str(role.id), "details": risks,
            })
        elif risks and not role.managed:
            issues.append({
                "level": "review", "kind": "elevated_role",
                "role_id": str(role.id), "role_name": role.name, "details": risks,
            })
        roles.append({
            "id": str(role.id), "name": role.name, "position": role.position,
            "managed": role.managed, "is_everyone": role.is_default(),
            "member_count_cached": len(role.members),
            "permissions": enabled,
            "color": role.color.value, "mentionable": role.mentionable,
        })

    channels = []
    for channel in sorted(guild.channels, key=lambda c: (getattr(c, "position", 0), c.name)):
        category_id = getattr(channel, "category_id", None)
        repeated_channels[(category_id, channel.name.casefold().strip())].append(channel.id)
        overwrites = []
        for target, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            overwrites.append({
                "id": str(target.id), "name": target.name,
                "type": "role" if isinstance(target, discord.Role) else "member",
                "allow_bits": str(allow.value), "deny_bits": str(deny.value),
            })
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            lowered = channel.name.casefold()
            if any(mark in lowered for mark in SENSITIVE_CHANNEL_NAMES):
                if channel.permissions_for(guild.default_role).view_channel:
                    issues.append({
                        "level": "review", "kind": "possibly_public_sensitive_channel",
                        "channel_id": str(channel.id), "channel_name": channel.name,
                        "note": "Name-based warning only; verify manually.",
                    })
        channels.append({
            "id": str(channel.id), "name": channel.name,
            "type": str(channel.type),
            "category_id": str(category_id) if category_id else None,
            "permissions_synced": getattr(channel, "permissions_synced", None),
            "overwrites": overwrites,
        })

    for name, ids in repeated_roles.items():
        if len(ids) > 1:
            issues.append({
                "level": "review", "kind": "duplicate_role_name",
                "name": name, "role_ids": list(map(str, ids)),
                "note": "Same name does not necessarily mean redundant.",
            })
    for (_, name), ids in repeated_channels.items():
        if len(ids) > 1:
            issues.append({
                "level": "review", "kind": "duplicate_channel_name_in_category",
                "name": name, "channel_ids": list(map(str, ids)),
            })

    bots = []
    for member in guild.members:
        if not member.bot:
            continue
        bots.append({
            "id": str(member.id), "name": str(member), "roles": [
                {"id": str(r.id), "name": r.name} for r in member.roles
                if not r.is_default()
            ],
            "administrator": member.guild_permissions.administrator,
            "manage_roles": member.guild_permissions.manage_roles,
            "manage_channels": member.guild_permissions.manage_channels,
        })
        if member.guild_permissions.administrator:
            issues.append({
                "level": "review", "kind": "bot_administrator",
                "bot_id": str(member.id), "bot_name": str(member),
            })

    if not guild.chunked:
        issues.append({
            "level": "info", "kind": "partial_member_cache",
            "note": "Bot roster and member counts may be incomplete. No absence/unused claims.",
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "guild": {"id": str(guild.id), "name": guild.name,
                  "member_count": guild.member_count,
                  "members_fully_cached": guild.chunked},
        "summary": {"roles": len(roles), "channels": len(channels),
                    "bots_visible": len(bots), "issues_to_review": len(issues)},
        "roles": roles, "channels": channels, "bots": bots, "findings": issues,
        "limitations": [
            "A bot cannot verify third-party bot settings or whether another bot works.",
            "Duplicate names do not establish that a role or channel is unnecessary.",
            "No channel, role, or bot is deleted automatically.",
            "Adding third-party bots requires an authorized human OAuth2 approval.",
        ],
    }


def manager(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.manage_guild
    )


async def secret(interaction: discord.Interaction, message: str, **kwargs) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(message, ephemeral=True, **kwargs)


class Confirm(discord.ui.View):
    """One-use interactive confirmation, bound to the initiating admin."""
    def __init__(self, actor_id: int, guild_id: int, operation):
        super().__init__(timeout=120)
        self.actor_id, self.guild_id, self.operation = actor_id, guild_id, operation
        self.used = False

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.actor_id or i.guild_id != self.guild_id:
            await secret(i, "Had confirmation machi dyalek.")
            return False
        if not manager(i):
            await secret(i, "Khass Manage Server.")
            return False
        return True

    @discord.ui.button(label="Confirm change", emoji="✅", style=discord.ButtonStyle.danger)
    async def accept(self, i: discord.Interaction, button: discord.ui.Button):
        if self.used:
            await secret(i, "Already processed.")
            return
        self.used = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await i.response.edit_message(view=self)
        try:
            result = await self.operation(i)
            await secret(i, "✅ " + result)
        except (ValueError, discord.Forbidden, discord.HTTPException) as exc:
            LOG.warning("Server change rejected: %s", exc)
            await secret(i, "❌ Change not applied: " + str(exc)[:700])

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, i: discord.Interaction, button: discord.ui.Button):
        self.used = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await i.response.edit_message(content="Cancelled. Nothing changed.", view=self)


class ServerManager(commands.GroupCog, group_name="server",
                    group_description="TOV4R server audit and confirmed management"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def check_role(i: discord.Interaction, role: discord.Role, *,
                   deleting: bool = False) -> None:
        if role.guild.id != i.guild_id or role.is_default() or role.managed:
            raise ValueError("Default/managed roles cannot be edited here.")
        if not i.guild.me or role >= i.guild.me.top_role:
            raise ValueError("Move Tov4r Bot's role above the target role.")
        if i.user.id != i.guild.owner_id and role >= i.user.top_role:
            raise ValueError("Your role must be above the target role.")
        if deleting:
            if i.user.id != i.guild.owner_id:
                raise ValueError("Only the server owner can remove a role.")
            if any(keyword in role.name.casefold() for keyword in PROTECTED_NAMES):
                raise ValueError("Protected staff/owner role. Remove manually if truly needed.")
            if not i.guild.chunked:
                raise ValueError("Member list is incomplete: cannot confirm unused role.")
            if role.members:
                raise ValueError("Role is assigned to members. No deletion performed.")

    @app_commands.command(name="scan", description="Private full server audit + JSON backup")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        data = snapshot(i.guild)
        findings = data["findings"]
        high = sum(f["level"] == "critical" for f in findings)
        review = sum(f["level"] == "review" for f in findings)
        summary = data["summary"]
        lines = [
            f"**{i.guild.name} • TOV4R Audit**",
            f"Roles: **{summary['roles']}** | Channels: **{summary['channels']}** | "
            f"Bots visible: **{summary['bots_visible']}**",
            f"Critical: **{high}** | Review: **{review}**",
            "",
            "**First findings:**",
        ]
        for f in findings[:12]:
            detail = f.get("name") or f.get("role_name") or f.get("channel_name") or f.get("bot_name") or ""
            lines.append(f"• {f['level'].upper()}: {f['kind']} {detail}"[:180])
        if not findings:
            lines.append("No obvious issues detected. This is not a security guarantee.")
        lines.append("\nRead the attached JSON before approving any edits.")
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        if len(payload) > 7_000_000:
            await secret(i, "\n".join(lines) + "\n⚠ Full report exceeds attachment limit.")
        else:
            file = discord.File(io.BytesIO(payload), filename=f"tov4r-audit-{i.guild.id}.json")
            await secret(i, "\n".join(lines)[:1800], file=file)

    @app_commands.command(name="bots", description="Review installed bots and elevated permissions")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bots(self, i: discord.Interaction):
        data = snapshot(i.guild)
        lines = ["**Bots visible in server:**"]
        for bot in data["bots"]:
            flags = []
            if bot["administrator"]:
                flags.append("ADMIN ⚠️")
            if bot["manage_roles"]:
                flags.append("manage roles")
            if bot["manage_channels"]:
                flags.append("manage channels")
            lines.append(f"• {bot['name']} — {', '.join(flags) or 'no high-risk flags reported'}")
        if not data["bots"]:
            lines.append("None visible in cache.")
        lines.append(
            "\nI can't check if third-party bots work or install them automatically. "
            "Review capabilities and authorize new bots yourself via official OAuth2."
        )
        if not i.guild.chunked:
            lines.append("⚠ Member cache incomplete.")
        await secret(i, "\n".join(lines)[:1950])

    @app_commands.command(name="fix_everyone", description="Preview removing dangerous @everyone server permissions")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def fix_everyone(self, i: discord.Interaction):
        role = i.guild.default_role
        risks = privileged_permissions(role)
        if not risks:
            return await secret(i, "✅ @everyone has no listed high-risk permissions.")
        if not i.guild.me or not i.guild.me.guild_permissions.manage_roles:
            return await secret(i, "Bot needs Manage Roles.")
        original = role.permissions.value
        proposed = discord.Permissions(original)
        for name in risks:
            setattr(proposed, name, False)

        async def apply(confirm_i: discord.Interaction):
            if not confirm_i.guild.me.guild_permissions.manage_roles:
                raise ValueError("Bot lost Manage Roles.")
            current = role.permissions
            if current.value != original:
                raise ValueError("Permissions changed since preview; rerun command.")
            await role.edit(permissions=proposed, reason=f"TOV4R approved by {confirm_i.user.id}")
            await self.bot.audit(i.guild, "Server security", f"@everyone: removed {', '.join(risks)}")
            return "Removed from @everyone: " + ", ".join(risks)

        await secret(i, "**Preview — @everyone**\nRemove: " + ", ".join(risks) +
                     "\nNo changes until you click Confirm. This may affect existing access.",
                     view=Confirm(i.user.id, i.guild_id, apply))

    @app_commands.command(name="harden_role", description="Preview removing dangerous powers from a selected role")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def harden_role(self, i: discord.Interaction, role: discord.Role):
        try:
            self.check_role(i, role)
        except ValueError as exc:
            return await secret(i, str(exc))
        risks = privileged_permissions(role)
        if not risks:
            return await secret(i, "✅ No listed high-risk powers on this role.")
        original = role.permissions.value
        proposed = discord.Permissions(original)
        for name in risks:
            setattr(proposed, name, False)

        async def apply(confirm_i: discord.Interaction):
            self.check_role(confirm_i, role)
            if not confirm_i.user.guild_permissions.manage_roles:
                raise ValueError("You need Manage Roles.")
            if role.permissions.value != original:
                raise ValueError("Role changed since preview; rerun command.")
            await role.edit(permissions=proposed, reason=f"TOV4R approved by {confirm_i.user.id}")
            await self.bot.audit(i.guild, "Role hardening", f"{role.name} ({role.id}): {', '.join(risks)}")
            return "Updated role " + role.name

        await secret(i, f"**Preview — {role.name}**\nWill remove: {', '.join(risks)}"
                     "\n⚠ This may break moderation/bot functions using this role."
                     "\nNothing changes without Confirm.",
                     view=Confirm(i.user.id, i.guild_id, apply))

    @app_commands.command(name="remove_role", description="Owner only: delete one empty role after confirmation")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove_role(self, i: discord.Interaction, role: discord.Role):
        try:
            self.check_role(i, role, deleting=True)
        except ValueError as exc:
            return await secret(i, str(exc))

        async def apply(confirm_i: discord.Interaction):
            self.check_role(confirm_i, role, deleting=True)
            if not confirm_i.user.guild_permissions.manage_roles:
                raise ValueError("You need Manage Roles.")
            name = role.name
            await role.delete(reason=f"TOV4R owner confirmed {confirm_i.user.id}")
            await self.bot.audit(i.guild, "Role removed", f"{name} ({role.id}) by {confirm_i.user.id}")
            return f"Removed empty role {name}."

        await secret(i, f"⚠ **Permanently delete role {role.name} ({role.id})?**"
                     "\nOnly the owner can approve. Audit/report first. Deletion cannot be undone.",
                     view=Confirm(i.user.id, i.guild_id, apply))

    @app_commands.command(name="move_channel", description="Move a text channel to a category with confirmation")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def move_channel(self, i: discord.Interaction,
                           channel: discord.TextChannel,
                           category: discord.CategoryChannel):
        if channel.category_id == category.id:
            return await secret(i, "Channel is already in that category.")
        original = channel.category_id

        async def apply(confirm_i: discord.Interaction):
            if not confirm_i.user.guild_permissions.manage_channels:
                raise ValueError("You need Manage Channels.")
            if not confirm_i.guild.me.guild_permissions.manage_channels:
                raise ValueError("Bot needs Manage Channels.")
            if channel.category_id != original:
                raise ValueError("Channel changed since preview; rerun command.")
            await channel.edit(category=category, sync_permissions=False,
                               reason=f"TOV4R confirmed by {confirm_i.user.id}")
            await self.bot.audit(i.guild, "Channel organized",
                                 f"{channel.name} -> {category.name} by {confirm_i.user.id}")
            return f"Moved #{channel.name} into {category.name}; overwrites not synced."

        await secret(i, f"**Preview — Move #{channel.name}**\n"
                     f"From: {channel.category.name if channel.category else '(no category)'}"
                     f"\nTo: {category.name}\nExisting channel overrides won't be synchronized.",
                     view=Confirm(i.user.id, i.guild_id, apply))

    async def cog_app_command_error(self, i: discord.Interaction,
                                    error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            return await secret(i, "❌ Khassk permissions dyal server administration.")
        LOG.error("Server manager command failed", exc_info=error)
        await secret(i, "❌ Couldn't complete. Check server/bot permissions and try again.")
