"""Safe role/channel policy and strictly scoped template cleanup.

Only items positively recorded as created by this bot may be automatically removed.
All deletions require an explicit /setup cleanup:true invocation by a server manager.
"""
from __future__ import annotations

import logging
import discord

LOG = logging.getLogger("tov4r.template_security")

# Interest roles give ordinary communication abilities only.
# Never grant permissions such as Administrator, Manage Roles, or Manage Channels.
def member_role_permissions() -> discord.Permissions:
    return discord.Permissions(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        add_reactions=True,
        connect=True,
        speak=True,
    )


def policy_overwrites(guild: discord.Guild, kind: str,
                      readonly: bool = False) -> dict:
    """Only define permission overwrites on newly-created template channels."""
    if kind == "voice":
        return {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True,
            ),
        }
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=not readonly,
            send_messages_in_threads=not readonly,
            create_public_threads=False if readonly else None,
            create_private_threads=False if readonly else None,
            mention_everyone=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            send_messages=True, embed_links=True, attach_files=True,
        ),
    }


def track_created(bot, guild_id: int, template: str, item_key: str,
                  item: object, kind: str) -> None:
    bot.store.run(
        "INSERT OR REPLACE INTO template_created_items "
        "(guild,template,item_key,discord_id,kind) VALUES(?,?,?,?,?)",
        (guild_id, template, item_key, item.id, kind),
    )


def was_created(bot, guild_id: int, template: str,
                item_key: str, discord_id: int) -> bool:
    found = bot.store.one(
        "SELECT discord_id FROM template_created_items "
        "WHERE guild=? AND template=? AND item_key=?",
        (guild_id, template, item_key),
    )
    return bool(found and int(found["discord_id"]) == discord_id)


async def clean_extra_channels(bot, guild: discord.Guild, chosen: str,
                               templates: dict, max_deletes: int = 12) -> dict:
    """Delete *empty, unchanged* channels the bot created under OTHER templates.

    No detection based only on names, apparent duplicates, inactivity, member
    counts, or guessed purpose. Never delete user-created channels or messages.
    """
    result = {"deleted": [], "skipped": [], "categories_deleted": []}
    if not guild.me or not guild.me.guild_permissions.manage_channels:
        result["skipped"].append("Bot needs Manage Channels.")
        return result
    if not 1 <= max_deletes <= 20:
        raise ValueError("Cleanup limit must be 1..20")

    tracked = bot.store.all(
        "SELECT template,item_key,discord_id,kind "
        "FROM template_created_items WHERE guild=? AND template<>? "
        "ORDER BY template,item_key",
        (guild.id, chosen),
    )
    essential = bot.store.config(guild.id)
    protected_ids = {
        int(essential.get(key, 0))
        for key in ("logs_channel", "welcome_channel", "voice_creator",
                    "voice_category", "ticket_category")
    }

    for entry in tracked:
        if len(result["deleted"]) >= max_deletes:
            result["skipped"].append("Max cleanup actions reached; other entries not processed.")
            break
        if entry["kind"] not in {"text", "voice"}:
            continue
        old_key = entry["template"]
        expected_name = entry["item_key"].split(":", 2)[-1]
        cid = int(entry["discord_id"])
        channel = guild.get_channel(cid)
        if channel is None:
            # Stale tracking is harmless. Remove only the stale index.
            bot.store.run("DELETE FROM template_created_items "
                          "WHERE guild=? AND template=? AND item_key=?",
                          (guild.id, old_key, entry["item_key"]))
            continue
        if cid in protected_ids:
            result["skipped"].append(f"{expected_name}: protected core channel")
            continue
        expected_type = discord.TextChannel if entry["kind"] == "text" else discord.VoiceChannel
        if not isinstance(channel, expected_type) or channel.name != expected_name:
            result["skipped"].append(f"{expected_name}: edited name/type")
            continue

        # Only remove channels under their original bot-created category.
        group_key = entry["item_key"].split(":", 1)[0]
        category = bot.store.one(
            "SELECT discord_id FROM template_created_items "
            "WHERE guild=? AND template=? AND item_key=? AND kind='category'",
            (guild.id, old_key, f"category:{group_key}"),
        )
        if not category or channel.category_id != int(category["discord_id"]):
            result["skipped"].append(f"{expected_name}: moved or unverified category")
            continue

        template_spec = templates.get(old_key)
        expected_spec = None
        if template_spec:
            for group in template_spec["groups"]:
                if group["key"] == group_key:
                    expected_spec = next(
                        (item for item in group["channels"]
                         if item["name"] == expected_name and item["kind"] == entry["kind"]),
                        None,
                    )
                    break
        if expected_spec is None:
            result["skipped"].append(f"{expected_name}: unknown template definition")
            continue

        # Refuse anything changed in channel permissions by the owner.
        expected_overwrites = policy_overwrites(
            guild, entry["kind"],
            readonly=expected_spec.get("readonly", False)
                     or expected_name == "choose-roles",
        )
        if channel.overwrites != expected_overwrites:
            result["skipped"].append(f"{expected_name}: customized permissions")
            continue

        if entry["kind"] == "voice":
            if channel.members:
                result["skipped"].append(f"{expected_name}: members connected")
                continue
        else:
            if channel.threads:
                result["skipped"].append(f"{expected_name}: contains threads")
                continue
            try:
                # A single message is enough to prevent deletion. Never purge history.
                has_message = False
                async for _ in channel.history(limit=1):
                    has_message = True
                    break
            except (discord.Forbidden, discord.HTTPException):
                result["skipped"].append(f"{expected_name}: cannot verify history")
                continue
            if has_message:
                result["skipped"].append(f"{expected_name}: has messages")
                continue

        try:
            await channel.delete(reason=f"TOV4R opt-in empty template cleanup by setup in {guild.id}")
        except (discord.Forbidden, discord.HTTPException):
            result["skipped"].append(f"{expected_name}: deletion failed")
            continue
        result["deleted"].append(expected_name)
        bot.store.run(
            "DELETE FROM template_items WHERE guild=? AND template=? "
            "AND item_key=? AND discord_id=?",
            (guild.id, old_key, entry["item_key"], cid),
        )
        bot.store.run(
            "DELETE FROM template_created_items WHERE guild=? AND template=? "
            "AND item_key=? AND discord_id=?",
            (guild.id, old_key, entry["item_key"], cid),
        )
        LOG.info("Deleted empty bot-created channel guild=%s name=%s", guild.id, expected_name)

    # No automatic category deletion: Discord cache may lag, and a category
    # might contain archived threads or other resources that are not obvious.
    return result
