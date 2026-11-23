"""Shared setup engine used by Discord /setup and the authenticated web wizard.

All changes are initiated by an authorized manager. This module does not run on
startup. Existing panels and channels are reused instead of replaced.
"""
import discord
from server_templates import TEMPLATES, apply_template
from focus_setup import focus_server, restore_active_template


async def provision_server(bot, guild, theme, *,
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
    if role is None:
        role = await guild.create_role(
            name="TOV4R Support", colour=discord.Colour(0xef3340),
            permissions=discord.Permissions.none(), reason="TOV4R setup"
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
         "Support / report / request. Click Open ticket."),
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

    restored = await restore_active_template(bot, guild, theme)
    summary = await apply_template(
        bot, guild, theme, panel_factory=role_panel_factory,
        safe_role_fn=safe_role_fn, cleanup=cleanup
    )
    summary["previously_restored"] = restored
    if focus:
        summary["focus"] = await focus_server(bot, guild, theme)
    return summary
