"""Focused server organization for TOV4R templates.

Moves off-template channels into private, recoverable archive categories. Never
deletes channels, messages, roles, categories, bots, or permission data.
Invoked ONLY by an authorized /setup focus:true interaction, not on startup.
"""
from __future__ import annotations

import json
import logging
import time

import discord

LOG = logging.getLogger("tov4r.focus")
ARCHIVE_PREFIX = "🔒・TOV4R ARCHIVE"
LIMIT = 35
ARCHIVE_MAX_CHANNELS = 40


def archive_permissions(guild):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True,
            manage_channels=True, connect=True,
        ),
    }


def _channel_overwrites(channel):
    state = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        state.append({
            "target": str(target.id),
            "kind": "role" if isinstance(target, discord.Role) else "member",
            "allow": str(allow.value),
            "deny": str(deny.value),
        })
    return state


def _restore_overwrites(guild, state):
    result = {}
    for entry in state:
        target_id = int(entry["target"])
        if entry["kind"] == "role":
            target = guild.get_role(target_id)
        else:
            target = guild.get_member(target_id)
        if target is None:
            raise ValueError("Role/member referenced by saved permissions is missing")
        allow = discord.Permissions(int(entry["allow"]))
        deny = discord.Permissions(int(entry["deny"]))
        result[target] = discord.PermissionOverwrite.from_pair(allow, deny)
    return result


def _protected_ids(bot, guild):
    cfg = bot.store.config(guild.id)
    result = {int(cfg.get(name) or 0) for name in (
        "voice_creator", "voice_category", "ticket_category", "welcome_channel",
        "logs_channel",
    )}
    for key in ("system_channel", "rules_channel", "public_updates_channel",
                "safety_alerts_channel", "afk_channel", "widget_channel"):
        value = getattr(guild, key, None)
        if value is not None:
            result.add(value.id)
    # Voice rooms, opened/archived tickets, any configured bot panels
    for row in bot.store.all("SELECT channel FROM rooms WHERE guild=?", (guild.id,)):
        result.add(row["channel"])
    for row in bot.store.all("SELECT channel FROM tickets WHERE guild=?", (guild.id,)):
        result.add(row["channel"])
    # Safeguard the configured bot welcome/voice/ticket interfaces, even on
    # partially upgraded servers with legacy channel names.
    for row in bot.store.all("SELECT channel FROM giveaways WHERE guild=?", (guild.id,)):
        result.add(row["channel"])
    # Do not break voice and ticket panel workflows on unrelated templates.
    for channel in _all_channels(guild):
        if channel.name in ("voice-panel", "tickets", "tov4r-logs", "welcome", "➕ Create your room"):
            result.add(channel.id)
    return result


def _active_template_ids(bot, guild, template):
    rows = bot.store.all(
        "SELECT discord_id FROM template_items WHERE guild=? AND template=? "
        "AND kind IN ('text','voice')",
        (guild.id, template),
    )
    return {int(r["discord_id"]) for r in rows}


def _archive_rows(bot, guild):
    return bot.store.all(
        "SELECT * FROM focused_archives WHERE guild=? ORDER BY archived_at, channel_id",
        (guild.id,),
    )


def _archive_bins(bot, guild):
    return bot.store.all(
        "SELECT * FROM focused_archive_bins WHERE guild=? ORDER BY idx",
        (guild.id,),
    )


def _all_channels(guild):
    # Discord.py exposes a list; some lightweight test doubles use a dict.
    items = guild.channels
    return list(items.values()) if isinstance(items, dict) else list(items)


def candidates(bot, guild, template) -> tuple[list, list]:
    keep = _active_template_ids(bot, guild, template)
    protected = _protected_ids(bot, guild) | keep
    protected_categories = {
        bot.store.config(guild.id).get("ticket_category"),
        bot.store.config(guild.id).get("voice_category"),
    }
    archive_cats = {int(x["category_id"]) for x in _archive_bins(bot, guild)}
    archived = {int(x["channel_id"]) for x in _archive_rows(bot, guild)}
    valid, skipped = [], []
    for channel in _all_channels(guild):
        if isinstance(channel, discord.CategoryChannel):
            continue
        if getattr(channel, "category_id", None) in archive_cats or channel.id in archived:
            continue
        if channel.id in protected or getattr(channel, "category_id", None) in protected_categories:
            continue
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            skipped.append(f"{channel.name}: unsupported channel type")
            continue
        if isinstance(channel, discord.VoiceChannel) and channel.members:
            skipped.append(f"{channel.name}: active voice users")
            continue
        # Protect private moderation and third-party bot operational channels.
        default_visible = channel.permissions_for(guild.default_role).view_channel
        lowered = channel.name.casefold()
        if not default_visible or any(tag in lowered for tag in (
            "staff", "moderation", "mod-log", "audit", "ticket",
            "bot-log", "automod", "bot-commands",
        )):
            skipped.append(f"{channel.name}: private or operational")
            continue
        valid.append(channel)
    return valid, skipped


async def _get_archive_category(bot, guild):
    for row in _archive_bins(bot, guild):
        category = guild.get_channel(int(row["category_id"]))
        if (isinstance(category, discord.CategoryChannel)
                and category.name.startswith(ARCHIVE_PREFIX)
                and len(category.channels) < ARCHIVE_MAX_CHANNELS):
            return category
    idx = 1 + max((int(r["idx"]) for r in _archive_bins(bot, guild)), default=0)
    category = await guild.create_category(
        f"{ARCHIVE_PREFIX} {idx:02}",
        overwrites=archive_permissions(guild),
        reason="TOV4R focused template setup: recoverable off-theme archive",
    )
    bot.store.run(
        "INSERT INTO focused_archive_bins(guild,idx,category_id) VALUES(?,?,?)",
        (guild.id, idx, category.id),
    )
    return category


async def focus_server(bot, guild, template, *, max_channels=LIMIT):
    """Apply explicitly requested focused layout and reversible private archive."""
    if not guild.me or not guild.me.guild_permissions.manage_channels:
        raise ValueError("Bot needs Manage Channels.")
    wanted, skipped = candidates(bot, guild, template)
    results = {"archived": [], "skipped": list(skipped),
               "remaining": max(0, len(wanted) - max_channels)}
    for channel in wanted[:max_channels]:
        try:
            category = await _get_archive_category(bot, guild)
        except (discord.Forbidden, discord.HTTPException) as exc:
            results["skipped"].append(f"Archive category failed: {type(exc).__name__}")
            break

        original = _channel_overwrites(channel)
        bot.store.run(
            "INSERT OR IGNORE INTO focused_archives "
            "(guild,channel_id,previous_category_id,previous_position,"
            "previous_overwrites,archive_category_id,archived_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (guild.id, channel.id, channel.category_id or 0,
             channel.position, json.dumps(original), category.id, time.time()),
        )
        try:
            await channel.edit(
                category=category, overwrites=archive_permissions(guild),
                sync_permissions=False,
                reason=f"TOV4R focused {template} layout: archiving off-theme channel",
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            bot.store.run(
                "DELETE FROM focused_archives WHERE guild=? AND channel_id=?",
                (guild.id, channel.id),
            )
            results["skipped"].append(f"{channel.name}: {type(exc).__name__}")
            continue
        results["archived"].append(channel.name)
        LOG.info("Focused archive guild=%s channel=%s original-category=%s",
                 guild.id, channel.id, channel.category_id)
    return results


async def restore_server(bot, guild, *, max_channels=LIMIT, only_ids=None):
    """Restore saved categories and permissions from persisted archive records."""
    if not guild.me or not guild.me.guild_permissions.manage_channels:
        raise ValueError("Bot needs Manage Channels.")
    rows = _archive_rows(bot, guild)
    if only_ids is not None:
        rows = [row for row in rows if int(row["channel_id"]) in only_ids]
    results = {"restored": [], "skipped": [], "remaining": max(0, len(rows) - max_channels)}
    for row in rows[:max_channels]:
        channel = guild.get_channel(int(row["channel_id"]))
        if channel is None:
            bot.store.run("DELETE FROM focused_archives WHERE guild=? AND channel_id=?",
                          (guild.id, int(row["channel_id"])))
            continue
        if channel.category_id != int(row["archive_category_id"]):
            results["skipped"].append(f"{channel.name}: moved since archive")
            continue
        if channel.overwrites != archive_permissions(guild):
            results["skipped"].append(f"{channel.name}: permissions changed since archive")
            continue
        previous = (guild.get_channel(int(row["previous_category_id"]))
                    if row["previous_category_id"] else None)
        if row["previous_category_id"] and not isinstance(previous, discord.CategoryChannel):
            results["skipped"].append(f"{channel.name}: old category no longer exists")
            continue
        try:
            overwrites = _restore_overwrites(guild, json.loads(row["previous_overwrites"]))
            await channel.edit(
                category=previous, overwrites=overwrites,
                sync_permissions=False,
                position=int(row["previous_position"]),
                reason="TOV4R focused setup: restore archived channel",
            )
        except (discord.Forbidden, discord.HTTPException, ValueError) as exc:
            results["skipped"].append(f"{channel.name}: {type(exc).__name__}")
            continue
        bot.store.run("DELETE FROM focused_archives WHERE guild=? AND channel_id=?",
                      (guild.id, channel.id))
        results["restored"].append(channel.name)

    # Archive bins are kept intact (and private) for subsequent focused operations.
    return results


async def restore_active_template(bot, guild, template):
    """Bring a previously focused template back when selecting it again."""
    ids = _active_template_ids(bot, guild, template)
    if not ids:
        return {"restored": [], "skipped": [], "remaining": 0}
    return await restore_server(bot, guild, max_channels=LIMIT, only_ids=ids)

