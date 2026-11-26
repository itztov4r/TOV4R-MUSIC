"""Shared setup engine used by Discord /setup and the authenticated web wizard.

All changes are initiated by an authorized manager. This module does not run on
startup. Existing panels and channels are reused instead of replaced.
"""
import discord
from server_templates import TEMPLATES
from template_security import member_role_permissions
from level_rewards import provision_rewards, tracked_reward


async def provision_modules(bot, guild, theme, *,
                           voice_panel_factory, ticket_panel_factory,
                           role_panel_factory, safe_role_fn, embed_factory,
                           focus=False, cleanup=False):
    if theme not in TEMPLATES:
        raise ValueError("Unknown server template")
    if not guild.me or not guild.me.guild_permissions.manage_channels:
        raise ValueError("TOV4R Bot needs Manage Channels.")
    if not guild.me.guild_permissions.manage_roles:
        raise ValueError("TOV4R Bot needs Manage Roles.")
    if not guild.me.guild_permissions.send_messages:
        raise ValueError("TOV4R Bot needs Send Messages.")
    cfg = bot.store.config(guild.id)

    async def category(key, name, private=False):
        ch = guild.get_channel(cfg[key])
        if not isinstance(ch, discord.CategoryChannel):
            overwrites = (
                {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                 guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)}
                if private else {}
            )
            ch = await guild.create_category(name, overwrites=overwrites,
                                             reason="TOV4R setup")
            cfg[key] = ch.id
            bot.store.update(guild.id, {key: ch.id})
        return ch

    voice = await category("voice_category", "TOV4R • VOICE")
    await category("ticket_category", "TOV4R • SUPPORT", True)
    role = guild.get_role(cfg["support_role"])
    if role is None or role.managed or role.id == guild.default_role.id:
        role = await guild.create_role(
            name="TOV4R Support", colour=discord.Colour(0xef3340),
            permissions=discord.Permissions.none(), mentionable=False, reason="TOV4R setup"
        )
        cfg["support_role"] = role.id
        bot.store.update(guild.id, {"support_role": role.id})
    if guild.get_channel(cfg["voice_creator"]) is None:
        channel = await guild.create_voice_channel(
            "➕ Create your room", category=voice, reason="TOV4R setup"
        )
        cfg["voice_creator"] = channel.id
        bot.store.update(guild.id, {"voice_creator": channel.id})

    for key, name in (("welcome_channel", "welcome"),
                      ("logs_channel", "tov4r-logs")):
        if guild.get_channel(cfg[key]) is not None:
            continue
        if key == "logs_channel":
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                role: discord.PermissionOverwrite(view_channel=True)
            }
        else:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False),
                guild.me: discord.PermissionOverwrite(send_messages=True)
            }
        channel = await guild.create_text_channel(
            name, overwrites=overwrites, reason="TOV4R setup"
        )
        cfg[key] = channel.id
        bot.store.update(guild.id, {key: channel.id})

    for name, factory, description in (
        ("voice-panel", voice_panel_factory,
         "Dkhel l **➕ Create your room**, men ba3d control room dyalek hna."),
        ("tickets", ticket_panel_factory,
         "Choose Support, Report or Partnership from the menu."),
    ):
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel is None:
            channel = await guild.create_text_channel(
                name, overwrites={
                    guild.default_role: discord.PermissionOverwrite(send_messages=False),
                    guild.me: discord.PermissionOverwrite(send_messages=True),
                }, reason="TOV4R setup"
            )
            await channel.send(embed=embed_factory("TOV4R • " + name, description),
                               view=factory(bot))

    return {"ok": True}

SETUP_ROLES = (("admin", "TOV4R Admin", 0xef3340),
               ("moderator", "TOV4R Moderator", 0x5865f2),
               ("support", "TOV4R Support", 0x2ecc71),
               ("member", "TOV4R Member", 0x95a5a6))

def setup_role(bot, guild, key, name):
    if key == "support":
        role = guild.get_role(bot.store.config(guild.id)["support_role"])
        if role and not role.managed and role.id != guild.default_role.id and not tracked_reward(bot,guild.id,role.id):
            return role
    tracked = bot.store.one("SELECT discord_id FROM template_items WHERE guild=? AND template=? AND item_key=?",
                            (guild.id, "__staff_setup__", key))
    role = guild.get_role(tracked["discord_id"]) if tracked else None
    if role and not role.managed and role.id != guild.default_role.id:
        return role
    # Do not silently grant ticket access to an unrelated role with the same name.
    if key == "support":
        return None
    return next((r for r in guild.roles if r.name == name and not r.managed and r.id != guild.default_role.id), None)

def reserved_staff_role(bot, guild, role):
    if role.id == bot.store.config(guild.id)["support_role"]:
        return True
    if role.name.casefold() in {name.casefold() for key,name,_ in SETUP_ROLES if key != "member"}:
        return True
    return bool(bot.store.one("SELECT 1 FROM template_items WHERE guild=? AND template=? AND item_key IN ('admin','moderator','support') AND discord_id=?",
                              (guild.id, "__staff_setup__", role.id)))

async def provision_server(bot, guild, theme, *, voice_panel_factory=None,
                           ticket_panel_factory=None, role_panel_factory=None,
                           safe_role_fn=None, embed_factory=None, focus=False, cleanup=False):
    """Roles-only setup. No creation, deletion, moving or editing of channels."""
    if theme not in TEMPLATES:
        raise ValueError("Unknown server type")
    if focus or cleanup:
        raise ValueError("Setup is now roles-only; channel cleanup and focus are disabled.")
    if not guild.me or not guild.me.guild_permissions.manage_roles:
        raise ValueError("TOV4R Bot needs Manage Roles.")
    summary = {"type": theme, "title": "TOV4R Staff & Community", "categories_new": [],
               "channels_new": [], "roles_new": [], "reused": 0,
               "roles_panel": "not created", "permissions_fixed": 0, "cleanup": None,
               "warnings": []}
    for key,name,color in SETUP_ROLES:
        role = setup_role(bot,guild,key,name)
        if role is None:
            role = await guild.create_role(name=name, colour=discord.Colour(color),
                permissions=member_role_permissions() if key == "member" else discord.Permissions.none(),
                mentionable=False, reason="TOV4R roles-only setup; staff rights configured by server owner")
            summary["roles_new"].append(role.name)
        else:
            summary["reused"] += 1
        bot.store.run("INSERT OR REPLACE INTO template_items VALUES(?,?,?,?,?)",
                      (guild.id,"__staff_setup__",key,role.id,"role"))
        if key == "support":
            bot.store.update(guild.id,{"support_role":role.id})
            # A previously configured automatic role must never become staff.
            if bot.store.config(guild.id)["autorole"] == role.id:
                bot.store.update(guild.id,{"autorole":0})
    summary["roles_new"].extend(await provision_rewards(bot,guild))
    bot.store.update(guild.id,{"server_type":theme})
    return summary
