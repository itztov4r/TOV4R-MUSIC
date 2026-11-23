"""Reusable, non-destructive Discord server setup templates for TOV4R Bot.

Every server has independent template records in SQLite. Re-running the command
reuses tracked channels and roles; it never removes or changes existing ones.
"""
from __future__ import annotations

import json
import logging

import discord
from discord import app_commands

LOG = logging.getLogger("tov4r.templates")

RULES = (
    "📜 **Qawanin dyal server**\n"
    "• Respecti kolchi, bla spam wla harassment.\n"
    "• 7t content f channel li kaynassbo.\n"
    "• Bla links mchbouha, scams, wla mass pings.\n"
    "• Respecti copyrights w Discord rules; ma tpartagich content mamssmouch bih.\n"
    "• Staff y9der ymoderati 3la 7sab qawanin server.\n\n"
    "✅ Khoud role men #choose-roles w mer7ba bik!"
)


def text(name: str, topic: str, *, readonly: bool = False) -> dict:
    return {"kind": "text", "name": name, "topic": topic, "readonly": readonly}


def voice(name: str) -> dict:
    return {"kind": "voice", "name": name}


def group(key: str, name: str, *channels: dict) -> dict:
    return {"key": key, "name": name, "channels": channels}


def profile(title: str, color: int, roles: tuple, groups: tuple) -> dict:
    return {"title": title, "color": color, "roles": roles, "groups": groups}


# Roles are cosmetic/self-assignable. NEVER grant moderator/admin permissions here.
TEMPLATES = {
    "movies": profile(
        "🎬 Movies & Cinema", 0xE65368,
        ("🎬 Movie Fan", "🍿 Watch Party", "⭐ Reviewer", "🎞️ Classics"),
        (
            group("start", "🎬・CINEMA | START",
                  text("cinema-rules", "Qawanin dyal cinema community", readonly=True),
                  text("cinema-announcements", "News w updates dyal server", readonly=True),
                  text("choose-roles", "Khtar roles dyalk men l buttons")),
            group("chat", "🎥・CINEMA | DISCUSS",
                  text("movie-chat", "Hder 3la films, directors w actors"),
                  text("movie-recommendations", "9tar7 films lcommunity"),
                  text("movie-reviews", "Reviews w ratings bla spoilers"),
                  text("movie-spoilers", "Spoilers! 7t spoiler tags")),
            group("events", "🍿・CINEMA | EVENTS",
                  text("watch-parties", "Schedule watch parties using legal services"),
                  text("movie-polls", "Vote 3la film li ghadi tchofo"),
                  voice("🍿 Watch Party"), voice("🎧 Cinema Lounge")),
        ),
    ),
    "gaming": profile(
        "🎮 Gaming", 0x5865F2,
        ("🎮 Gamer", "🏆 Competitive", "🎥 Clip Creator", "🎙️ Voice Squad"),
        (
            group("start", "🎮・GAMING | START",
                  text("gaming-rules", "Qawanin gaming community", readonly=True),
                  text("gaming-news", "News w announcements", readonly=True),
                  text("choose-roles", "Khtar gaming roles dyalk")),
            group("chat", "🕹️・GAMING | CHAT",
                  text("game-chat", "Hder 3la games"),
                  text("looking-for-group", "L9a players bach tl3bo"),
                  text("gaming-clips", "Partage clips dyalk"),
                  text("gaming-setups", "Setup, peripherals w screenshots")),
            group("voice", "🎧・GAMING | VOICE",
                  text("tournaments", "Tournaments, rules w teams"),
                  voice("🎮 Squad One"), voice("🎮 Squad Two")),
        ),
    ),
    "music": profile(
        "🎵 Music", 0xA855F7,
        ("🎧 Listener", "🎤 Artist", "🎹 Producer", "🎛️ DJ"),
        (
            group("start", "🎵・MUSIC | START",
                  text("music-rules", "Qawanin music community", readonly=True),
                  text("music-announcements", "Events w news", readonly=True),
                  text("choose-roles", "Khtar music roles")),
            group("chat", "🎼・MUSIC | CHAT",
                  text("music-chat", "Hder 3la songs w artists"),
                  text("music-recommendations", "Recommendations w discoveries"),
                  text("your-creations", "Original tracks w creations dyalk"),
                  text("playlists", "Partage playlists w legal links")),
            group("voice", "🎙️・MUSIC | VOICE",
                  text("music-events", "Live events w jam sessions"),
                  voice("🎶 Listening Room"), voice("🎤 Jam Session")),
        ),
    ),
    "anime": profile(
        "🌸 Anime & Manga", 0xF472B6,
        ("🌸 Anime Fan", "📚 Manga Reader", "🎨 Fan Artist", "📺 Weekly Watcher"),
        (
            group("start", "🌸・ANIME | START",
                  text("anime-rules", "Qawanin anime community", readonly=True),
                  text("anime-announcements", "Anime news w events", readonly=True),
                  text("choose-roles", "Khtar anime roles")),
            group("chat", "⛩️・ANIME | CHAT",
                  text("anime-chat", "Hder 3la anime"),
                  text("anime-recommendations", "Anime suggestions"),
                  text("manga-chat", "Manga discussions"),
                  text("anime-spoilers", "Spoilers: use spoiler tags")),
            group("events", "🍥・ANIME | EVENTS",
                  text("anime-watch-events", "Organized watch-alongs via legal services"),
                  voice("🌸 Anime Lounge")),
        ),
    ),
    "study": profile(
        "📚 Study & School", 0x22C55E,
        ("📖 Student", "🧠 Study Buddy", "💻 Coding", "📝 Notes Sharer"),
        (
            group("start", "📚・STUDY | START",
                  text("study-rules", "Qawanin study community", readonly=True),
                  text("study-announcements", "Programs w updates", readonly=True),
                  text("choose-roles", "Khtar study roles")),
            group("chat", "✏️・STUDY | HELP",
                  text("homework-help", "Questions w collaborative learning"),
                  text("resources", "Free learning resources"),
                  text("study-plans", "Planning & revision"),
                  text("coding-help", "Questions informatiques")),
            group("voice", "🧠・STUDY | ROOMS",
                  voice("📖 Quiet Study"), voice("👥 Group Revision")),
        ),
    ),
    "tech": profile(
        "💻 Tech & Developers", 0x06B6D4,
        ("💻 Developer", "🛡️ Cyber Learner", "🎨 UI Designer", "🖥️ Hardware"),
        (
            group("start", "💻・TECH | START",
                  text("tech-rules", "Qawanin tech community", readonly=True),
                  text("tech-announcements", "News w releases", readonly=True),
                  text("choose-roles", "Khtar tech roles")),
            group("chat", "🧩・TECH | BUILD",
                  text("programming-help", "Coding questions"),
                  text("project-showcase", "Share projects dyalk"),
                  text("hardware-help", "PC, drivers w networking"),
                  text("cybersecurity-learning", "Safe, authorized security learning")),
            group("voice", "🛠️・TECH | VOICE",
                  voice("💻 Dev Lounge"), voice("🧠 Pair Programming")),
        ),
    ),
    "sports": profile(
        "⚽ Sports", 0xF59E0B,
        ("⚽ Football", "🏀 Basketball", "🏎️ Motorsports", "🏆 eSports"),
        (
            group("start", "⚽・SPORTS | START",
                  text("sports-rules", "Qawanin sports community", readonly=True),
                  text("sports-news", "Community updates", readonly=True),
                  text("choose-roles", "Khtar team/sport roles")),
            group("chat", "🏟️・SPORTS | CHAT",
                  text("sports-chat", "Sports discussion"),
                  text("match-discussion", "Watch-along chats"),
                  text("sports-highlights", "Highlights w legal clip links"),
                  text("predictions", "Friendly score predictions, no betting")),
            group("voice", "🏆・SPORTS | LIVE",
                  voice("🎙️ Match Talk"), voice("🏟️ Fan Lounge")),
        ),
    ),
    "friends": profile(
        "👋 Friends & Hangout", 0xFB7185,
        ("👋 Friend", "🎲 Game Night", "🍿 Movie Night", "🎤 Karaoke"),
        (
            group("start", "👋・FRIENDS | START",
                  text("friends-rules", "Community etiquette", readonly=True),
                  text("announcements", "Meetups w updates", readonly=True),
                  text("choose-roles", "Khtar interest roles")),
            group("chat", "💬・FRIENDS | CHAT",
                  text("general-chat", "Daily discussion"),
                  text("memes", "Funny memes"),
                  text("photos", "Shared photos"),
                  text("plans", "Organize plans")),
            group("voice", "🎉・FRIENDS | VOICE",
                  voice("💬 Hangout"), voice("🎮 Game Night")),
        ),
    ),
    "community": profile(
        "🌐 General Community", 0xEF3340,
        ("💬 Member", "🌟 Active", "🎉 Event Fan", "🎨 Creator"),
        (
            group("start", "🌐・COMMUNITY | START",
                  text("community-rules", "Qawanin community", readonly=True),
                  text("announcements", "Updates dyal community", readonly=True),
                  text("choose-roles", "Khtar interests roles")),
            group("chat", "💬・COMMUNITY | CHAT",
                  text("general-chat", "Hder m3a community"),
                  text("introductions", "3arref b rassek"),
                  text("media-sharing", "Sowar w clips"),
                  text("suggestions", "Ideas bach yt7ssen server")),
            group("voice", "🔊・COMMUNITY | VOICE",
                  voice("💬 General Voice"), voice("🎉 Events Voice")),
        ),
    ),
}

# Shown to Discord users as an actual slash-command dropdown.
TEMPLATE_CHOICES = [
    app_commands.Choice(name=p["title"], value=key)
    for key, p in TEMPLATES.items()
]


def template_keys() -> tuple[str, ...]:
    return tuple(TEMPLATES)


def _get_item(bot, guild_id: int, template: str, key: str) -> int | None:
    item = bot.store.one(
        "SELECT discord_id FROM template_items WHERE guild=? AND template=? AND item_key=?",
        (guild_id, template, key),
    )
    return int(item["discord_id"]) if item else None


def _save_item(bot, guild_id: int, template: str, key: str, discord_id: int, kind: str) -> None:
    bot.store.run(
        "INSERT OR REPLACE INTO template_items(guild,template,item_key,discord_id,kind)"
        " VALUES(?,?,?,?,?)",
        (guild_id, template, key, discord_id, kind),
    )


async def apply_template(bot, guild: discord.Guild, template_key: str,
                         *, panel_factory, safe_role_fn) -> dict:
    """Create or reuse a template without removing/moving/editing existing resources.

    Caller MUST hold bot.locks[('setup', guild.id)] to prevent duplicate creations.
    """
    if template_key not in TEMPLATES:
        raise ValueError("Type ma kaynch: " + ", ".join(template_keys()))
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        raise ValueError("TOV4R Bot khaso Manage Channels.")
    if not me.guild_permissions.manage_roles:
        raise ValueError("TOV4R Bot khaso Manage Roles bach ycreate templates.")
    if not me.guild_permissions.send_messages:
        raise ValueError("TOV4R Bot khaso Send Messages.")

    plan = TEMPLATES[template_key]
    result = {"type": template_key, "title": plan["title"],
              "categories_new": [], "channels_new": [], "roles_new": [],
              "reused": 0, "roles_panel": "skipped", "warnings": []}

    # Keep role hierarchy unchanged, with harmless, cosmetic permissions.
    roles = []
    for name in plan["roles"]:
        key = "role:" + name
        rid = _get_item(bot, guild.id, template_key, key)
        role = guild.get_role(rid) if rid else None
        if role is None:
            role = discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(
                name=name, permissions=discord.Permissions.none(),
                colour=discord.Colour(plan["color"]), mentionable=False,
                reason=f"TOV4R setup type {template_key}",
            )
            result["roles_new"].append(name)
        else:
            result["reused"] += 1
        _save_item(bot, guild.id, template_key, key, role.id, "role")
        roles.append(role)

    channels = {}
    for group_spec in plan["groups"]:
        cat_key = "category:" + group_spec["key"]
        category_id = _get_item(bot, guild.id, template_key, cat_key)
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name=group_spec["name"])
        if not isinstance(category, discord.CategoryChannel):
            category = await guild.create_category(
                group_spec["name"], reason=f"TOV4R setup type {template_key}",
            )
            result["categories_new"].append(category.name)
        else:
            result["reused"] += 1
        _save_item(bot, guild.id, template_key, cat_key, category.id, "category")

        for spec in group_spec["channels"]:
            item_key = f'{group_spec["key"]}:{spec["kind"]}:{spec["name"]}'
            channel_id = _get_item(bot, guild.id, template_key, item_key)
            channel = guild.get_channel(channel_id) if channel_id else None
            expected = discord.TextChannel if spec["kind"] == "text" else discord.VoiceChannel
            if not isinstance(channel, expected):
                channel = discord.utils.get(
                    category.text_channels if spec["kind"] == "text" else category.voice_channels,
                    name=spec["name"],
                )
            made = False
            if not isinstance(channel, expected):
                if spec["kind"] == "text":
                    ow = (
                        {
                            guild.default_role: discord.PermissionOverwrite(
                                send_messages=False, send_messages_in_threads=False),
                            me: discord.PermissionOverwrite(
                                view_channel=True, send_messages=True, embed_links=True),
                        }
                        if spec["readonly"] else None
                    )
                    channel = await guild.create_text_channel(
                        spec["name"], category=category,
                        topic=spec["topic"], overwrites=ow if ow is not None else {},
                        reason=f"TOV4R setup type {template_key}",
                    )
                else:
                    channel = await guild.create_voice_channel(
                        spec["name"], category=category,
                        reason=f"TOV4R setup type {template_key}",
                    )
                made = True
                result["channels_new"].append(channel.name)
            else:
                result["reused"] += 1
            _save_item(bot, guild.id, template_key, item_key, channel.id, spec["kind"])
            channels[spec["name"]] = (channel, made)

    # One optional self-role panel per template; only safe roles are exposed.
    panel_entry = channels.get("choose-roles")
    if panel_entry:
        panel_channel, _ = panel_entry
        safe_roles = [role for role in roles if safe_role_fn(role, guild)]
        if safe_roles:
            key = "panel:roles"
            mid = _get_item(bot, guild.id, template_key, key)
            has_panel = False
            if mid:
                try:
                    await panel_channel.fetch_message(mid)
                    has_panel = True
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException) as exc:
                    # Avoid accidental duplicate if the bot cannot check the old panel.
                    result["warnings"].append(f"Can't check existing roles panel: {type(exc).__name__}")
                    has_panel = True
            if has_panel:
                result["roles_panel"] = "existing"
            else:
                panel_roles = [(role.id, role.name) for role in safe_roles][:5]
                e = discord.Embed(
                    title=f"{plan['title']} • Choose your roles",
                    description="Khtar role b click 3la l buttons. Click marra khra bach t7ydo.\n"
                                "Roles hna ghir interests, bla admin permissions.",
                    colour=plan["color"],
                )
                msg = await panel_channel.send(
                    embed=e, view=panel_factory(bot, panel_roles),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                bot.store.run(
                    "INSERT OR REPLACE INTO role_panels(message,guild,roles) VALUES(?,?,?)",
                    (msg.id, guild.id, json.dumps(panel_roles)),
                )
                _save_item(bot, guild.id, template_key, key, msg.id, "panel")
                result["roles_panel"] = "created"
        else:
            result["warnings"].append("No eligible roles for self-role panel: check bot hierarchy.")

    # Only write guidance into a BRAND NEW rules channel, never into old channels.
    rule_channel = next(
        ((ch, made) for name, (ch, made) in channels.items() if name.endswith("-rules")),
        None,
    )
    if rule_channel and rule_channel[1]:
        channel = rule_channel[0]
        await channel.send(
            embed=discord.Embed(title=f"{plan['title']} • Rules", description=RULES,
                                colour=plan["color"]),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    bot.store.update(guild.id, {"server_type": template_key})
    return result


def format_result(summary: dict) -> str:
    warning = "\n⚠️ " + " | ".join(summary["warnings"][:3]) if summary["warnings"] else ""
    return (
        f"✅ **{summary['title']} server setup wajd!**\n"
        f"📁 Categories jdod: **{len(summary['categories_new'])}**\n"
        f"#️⃣ Channels jdod: **{len(summary['channels_new'])}**\n"
        f"🎭 Roles jdod: **{len(summary['roles_new'])}**\n"
        f"🔁 Existing resources reused: **{summary['reused']}**\n"
        f"🎛️ Self-roles panel: **{summary['roles_panel']}**\n"
        "🛡️ Ma tmsa7 7ta channel/role qdim; ma tzadat 7ta permission admin.\n"
        "Staff y9der ydir /configure bach ykml custom settings." + warning
    )
