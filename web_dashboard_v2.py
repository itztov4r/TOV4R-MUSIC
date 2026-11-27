"""TOV4R multi-guild dashboard and preview-first web setup wizard.

Uses Discord OAuth2 identify+guilds, per-guild Manage Server checks, short
server-side sessions and same-origin CSRF protection. The existing owner's
DASHBOARD_KEY remains usable (as an Authorization header, never in URLs).

Requires DISCORD_OAUTH_CLIENT_SECRET and DASHBOARD_BASE_URL for OAuth.
"""
from __future__ import annotations

import asyncio
import tempfile
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web

from server_templates import (
    TEMPLATES, styled_category_name, styled_channel_name, styled_role_name,
)
from focus_setup import candidates, restore_server
from setup_service import SETUP_ROLES, setup_role
from unified_setup import provision_all as provision_server
from level_rewards import TIERS, reward_role
from store import validate_config
from voice_welcome import MAX_AUDIO

LOG = logging.getLogger("tov4r.dashboard")
API = "https://discord.com/api/v10"
MANAGE_GUILD = 1 << 5
ADMINISTRATOR = 1 << 3
SESSION_SECONDS = 3600
STATE_SECONDS = 600
PLAN_SECONDS = 600
SAFE_CONFIG = frozenset({
    "levels", "antispam", "anti_invites", "voice_xp", "security_enabled",
    "welcome_text", "blocked_words", "xp_cooldown", "language", "anti_links", "automod_progressive",
    "voice_rooms_enabled", "voice_panel_enabled", "tickets_enabled", "ai_enabled", "goodbye_enabled",
})


class DashboardV2:
    def __init__(self, bot):
        self.bot = bot
        self.owner_key = os.getenv("DASHBOARD_KEY", "").strip()
        self.secret = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "").strip()
        self.base_url = os.getenv("DASHBOARD_BASE_URL", "").rstrip("/")
        self.enabled = bool(
            self.secret and self.base_url
            and (self.base_url.startswith("https://")
                 or self.base_url.startswith("http://localhost:")
                 or self.base_url.startswith("http://127.0.0.1:"))
        )
        self.sessions = {}  # sid -> authenticated OAuth token and CSRF
        self.pending = {}   # random OAuth state -> timestamp
        self.plans = {}     # random preview id -> actor, guild, fingerprint
        self._timeout = aiohttp.ClientTimeout(total=12)

    def register(self, app):
        app.router.add_get("/wizard", self.page)
        app.router.add_get("/wizard/assets/{name}", self.asset)
        app.router.add_get("/auth/login", self.login)
        app.router.add_get("/auth/callback", self.callback)
        app.router.add_post("/auth/logout", self.logout)
        app.router.add_get("/api/v2/meta", self.meta)
        app.router.add_get("/api/v2/bootstrap", self.bootstrap)
        app.router.add_post("/api/v2/guild/{guild}/preview", self.preview)
        app.router.add_post("/api/v2/guild/{guild}/apply", self.apply)
        app.router.add_put("/api/v2/guild/{guild}/settings", self.settings)
        app.router.add_post("/api/v2/guild/{guild}/restore", self.restore)
        app.router.add_put("/api/v2/guild/{guild}/voice/audio", self.voice_upload)
        app.router.add_delete("/api/v2/guild/{guild}/voice/audio", self.voice_reset)

    @staticmethod
    def _new_id():
        return secrets.token_urlsafe(32)

    def _prune(self):
        now = time.time()
        self.pending = {k:v for k,v in self.pending.items()
                        if v["at"] + STATE_SECONDS >= now}
        self.sessions = {k:v for k,v in self.sessions.items()
                         if v["expires"] >= now}
        self.plans = {k:v for k,v in self.plans.items()
                      if v["at"] + PLAN_SECONDS >= now}

    @property
    def _secure_cookies(self):
        return self.base_url.startswith("https://")

    async def page(self, request):
        return web.FileResponse(Path(__file__).parent / "web" / "wizard.html")

    async def asset(self, request):
        name = request.match_info["name"]
        if name not in ("wizard.js", "wizard.css"):
            raise web.HTTPNotFound()
        return web.FileResponse(Path(__file__).parent / "web" / name)

    async def meta(self, request):
        return web.json_response({
            "oauth_enabled": self.enabled,
            "bot_connected": self.bot.is_ready(),
            "oauth_login_url": "/auth/login" if self.enabled else None,
            "templates": [{"id": key, "name": spec["title"]}
                          for key, spec in TEMPLATES.items()],
        })

    async def login(self, request):
        if not self.enabled:
            raise web.HTTPServiceUnavailable(
                text="Discord login not configured. Contact the dashboard owner."
            )
        self._prune()
        nonce = self._new_id()
        self.pending[nonce] = {"at": time.time()}
        redirect = self.base_url + "/auth/callback"
        url = "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": str(self.bot.application_id),
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "identify guilds",
            "state": nonce,
            "prompt": "consent",
        })
        reply = web.HTTPFound(url)
        reply.set_cookie(
            "tov4r_oauth_state", nonce,
            max_age=STATE_SECONDS, secure=self._secure_cookies,
            httponly=True, samesite="Lax", path="/",
        )
        raise reply

    async def _discord(self, method, url, *, access_token=None, data=None):
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = "Bearer " + access_token
        auth = None
        if data is not None:
            auth = aiohttp.BasicAuth(str(self.bot.application_id), self.secret)
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.request(method, url, headers=headers,
                                       auth=auth, data=data) as response:
                if response.status >= 400:
                    LOG.warning("Discord OAuth call rejected status=%s endpoint=%s",
                                response.status, url.split("?")[0])
                    raise web.HTTPUnauthorized(text="Discord authorization expired or denied")
                return await response.json()

    async def callback(self, request):
        if not self.enabled:
            raise web.HTTPNotFound()
        self._prune()
        state = request.query.get("state", "")
        cookie_state = request.cookies.get("tov4r_oauth_state", "")
        info = self.pending.pop(state, None)
        if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
            raise web.HTTPForbidden(text="Invalid login state")
        if not info or info["at"] + STATE_SECONDS < time.time():
            raise web.HTTPForbidden(text="Expired login session")
        code = request.query.get("code", "")
        if not code or request.query.get("error"):
            raise web.HTTPBadRequest(text="Discord login declined")
        token = await self._discord("POST", API + "/oauth2/token", data={
            "grant_type": "authorization_code",
            "code": code, "redirect_uri": self.base_url + "/auth/callback",
        })
        if not token.get("access_token") or not {"identify", "guilds"}.issubset(
            set(token.get("scope", "").split())
        ):
            raise web.HTTPForbidden(text="OAuth permissions missing")
        user = await self._discord(
            "GET", API + "/users/@me", access_token=token["access_token"]
        )
        sid = self._new_id()
        self.sessions[sid] = {
            "user": {"id": str(user["id"]), "username": user.get("username", "Discord")},
            "access_token": token["access_token"], "csrf": self._new_id(),
            "expires": min(time.time() + SESSION_SECONDS,
                           time.time() + int(token.get("expires_in", SESSION_SECONDS))),
        }
        response = web.HTTPFound("/wizard")
        response.set_cookie(
            "tov4r_session", sid, max_age=SESSION_SECONDS,
            httponly=True, secure=self._secure_cookies,
            samesite="Lax", path="/",
        )
        response.del_cookie("tov4r_oauth_state", path="/")
        raise response

    def _principal(self, request):
        supplied = request.headers.get("Authorization", "")
        if self.owner_key and hmac.compare_digest(
            supplied, "Bearer " + self.owner_key
        ):
            return {"actor": "owner", "user": "Dashboard owner",
                    "sid": None, "token": None, "csrf": None}
        sid = request.cookies.get("tov4r_session", "")
        user = self.sessions.get(sid)
        if not user or user["expires"] < time.time():
            raise web.HTTPUnauthorized(text="Discord login required")
        return {"actor": str(user["user"]["id"]), "user": user["user"]["username"],
                "sid": sid, "token": user["access_token"], "csrf": user["csrf"]}

    async def _admin_guilds(self, principal):
        if principal["sid"] is None:
            return {str(g.id): g for g in self.bot.guilds}
        guilds = await self._discord(
            "GET", API + "/users/@me/guilds",
            access_token=principal["token"]
        )
        permitted = {}
        principal['setup_guilds'] = set()
        for item in guilds:
            try:
                allowed = bool(item.get("owner")) or (
                    int(item.get("permissions", "0")) & (MANAGE_GUILD | ADMINISTRATOR)
                ) != 0
                if allowed:
                    guild = self.bot.get_guild(int(item["id"]))
                    if guild is not None:
                        permitted[str(guild.id)] = guild
                        if str(guild.owner_id)==principal['actor'] or int(item.get('permissions','0')) & ADMINISTRATOR:
                            principal['setup_guilds'].add(str(guild.id))
            except (ValueError, TypeError, KeyError):
                continue
        return permitted

    @staticmethod
    def _can_setup(principal, guild):
        return principal['sid'] is None or str(guild.id) in principal.get('setup_guilds', set())

    def _csrf_check(self, request, principal):
        if principal["sid"] is None:
            return  # Bearer header owner access cannot be forged by form posts.
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, principal["csrf"]):
            raise web.HTTPForbidden(text="CSRF validation failed")

    async def _guild(self, request, *, write=False):
        principal = self._principal(request)
        if write:
            self._csrf_check(request, principal)
        gid = request.match_info["guild"]
        guild = (await self._admin_guilds(principal)).get(gid)
        if guild is None:
            raise web.HTTPForbidden(text="Manage Server permission required")
        return principal, guild

    @staticmethod
    async def _json(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="JSON request required")
        data = await request.json()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        return data

    @staticmethod
    def voice_status(guild, cfg, can_manage):
        """Report current welcome configuration and actual voice connection, not an uptime claim."""
        channel_id = cfg.get("voice_welcome_channel", 0)
        channel = guild.get_channel(channel_id) if channel_id else None
        voice = getattr(guild, "voice_client", None)
        connected = bool(channel and voice and voice.is_connected()
                         and voice.channel == channel)
        return {
            "configured": bool(channel_id),
            "channel_available": channel is not None,
            "connected": connected,
            "channel_name": channel.name if channel and can_manage else None,
            "can_manage": bool(can_manage),
        }

    async def _voice_access(self, request):
        principal, guild = await self._guild(request, write=True)
        if not self._can_setup(principal, guild):
            raise web.HTTPForbidden(text="Server owner or Administrator required")
        cog = self.bot.get_cog("VoiceWelcome")
        if cog is None:
            raise web.HTTPServiceUnavailable(text="Voice welcome module unavailable")
        return principal, guild, cog

    async def _recheck_voice_access(self, request, expected_guild):
        _, guild, _ = await self._voice_access(request)
        if guild.id != expected_guild.id:
            raise web.HTTPForbidden(text="Server mismatch")

    async def voice_upload(self, request):
        principal, guild, cog = await self._voice_access(request)
        formats = {".mp3": "mp3", ".wav": "wav", ".ogg": "ogg", ".m4a": "mov"}
        fmt = formats.get(request.headers.get("X-Audio-Extension", "").lower())
        if request.content_type != "application/octet-stream" or not fmt:
            raise web.HTTPUnsupportedMediaType(text="MP3, WAV, OGG or M4A required")
        if request.headers.get("X-Audio-Consent") != "yes":
            raise web.HTTPBadRequest(text="Confirm permission to play the recording")
        if request.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise web.HTTPUnsupportedMediaType(text="Compressed request bodies are not supported")
        size = request.content_length
        if size is None or not 0 < size <= MAX_AUDIO:
            raise web.HTTPBadRequest(text="File size must be between 1 byte and 64 MB")
        if cog.audio_lock.locked():
            raise web.HTTPConflict(text="Audio conversion busy; try again later")
        try:
            async with cog.audio_lock:
                target = cog.custom_audio(guild.id)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=target.parent) as directory:
                    source = Path(directory) / "input"
                    output = Path(directory) / "welcome.ogg"
                    async def receive():
                        count = 0
                        with source.open("wb") as stream:
                            async for chunk in request.content.iter_chunked(65536):
                                count += len(chunk)
                                if count > size or count > MAX_AUDIO:
                                    raise ValueError("Invalid size")
                                stream.write(chunk)
                        if count != size:
                            raise ValueError("Incomplete audio upload")
                    await asyncio.wait_for(receive(), timeout=90)
                    await cog.convert_audio(source, output, fmt)
                    await self._recheck_voice_access(request, guild)
                    output.replace(target)
        except (ValueError, OSError, asyncio.TimeoutError, aiohttp.ClientError):
            raise web.HTTPBadRequest(text="Audio upload or conversion failed; existing recording unchanged")
        self.bot.store.event(guild.id, "Web voice welcome", "Custom audio updated by " + principal["actor"])
        return web.json_response({"ok": True, "message": "Custom recording saved (first 5 minutes). Existing room settings unchanged."})

    async def voice_reset(self, request):
        principal, guild, cog = await self._voice_access(request)
        if cog.audio_lock.locked():
            raise web.HTTPConflict(text="Audio conversion busy; try again later")
        async with cog.audio_lock:
            await self._recheck_voice_access(request, guild)
            cog.custom_audio(guild.id).unlink(missing_ok=True)
        self.bot.store.event(guild.id, "Web voice welcome", "Default audio restored by " + principal["actor"])
        return web.json_response({"ok": True, "message": "Default greeting restored."})

    async def bootstrap(self, request):
        principal = self._principal(request)
        guilds = await self._admin_guilds(principal)
        selected = []
        for guild in guilds.values():
            cfg = self.bot.store.config(guild.id)
            selected.append({
                "id": str(guild.id), "name": guild.name,
                "members": guild.member_count,
                "channels": len(guild.channels), "roles": len(guild.roles),
                "bots": sum(m.bot for m in guild.members),
                "type": cfg["server_type"],
                "can_setup": bool(
                    self._can_setup(principal, guild) and guild.me and guild.me.guild_permissions.manage_roles
                ),
                "settings": {key:cfg[key] for key in SAFE_CONFIG},
                "voice_welcome": self.voice_status(guild, cfg, self._can_setup(principal, guild)),
                "open_rooms": self.bot.store.one("SELECT COUNT(*) n FROM rooms WHERE guild=?", (guild.id,))["n"],
                "open_tickets": self.bot.store.one("SELECT COUNT(*) n FROM tickets WHERE guild=? AND closed=0", (guild.id,))["n"],
                "can_manage_ai": principal["sid"] is None or str(guild.owner_id) == principal["actor"],
                "activity": self.bot.store.all(
                    "SELECT kind, detail, at FROM events WHERE guild=? "
                    "ORDER BY id DESC LIMIT 12", (guild.id,)
                ),
            })
        return web.json_response({
            "actor": principal["user"], "owner": principal["sid"] is None,
            "csrf": principal["csrf"], "guilds": selected,
            "ready": self.bot.is_ready()
        })

    @staticmethod
    def _channels(guild):
        channels = guild.channels
        return list(channels.values()) if isinstance(channels, dict) else list(channels)

    @staticmethod
    def _fingerprint(guild):
        data = sorted(
            (str(c.id), c.name, str(c.type), str(getattr(c, "category_id", None)))
            for c in DashboardV2._channels(guild)
        )
        roles = sorted((str(r.id), r.name) for r in guild.roles)
        return hashlib.sha256(
            json.dumps([data, roles], separators=(",", ":")).encode()
        ).hexdigest()

    def _plan(self, guild, theme, focus):
        return {
            "theme": theme, "title": "TOV4R Staff & Community",
            "categories": [], "roles": [name for _,name,_ in SETUP_ROLES] + [name for _,name,_,_ in TIERS] + ["TOV4R Verified","TOV4R • Gaming","TOV4R • Music","TOV4R • Movies"],
            "new_categories_estimate": 2, "new_channels_estimate": 8,
            "new_roles_estimate": sum(setup_role(self.bot,guild,key,name) is None for key,name,_ in SETUP_ROLES) + sum(reward_role(self.bot,guild,n) is None for n,_,_,_ in TIERS) + 4,
            "focus": False, "off_theme_archive_estimate": 0, "protected_channels": 0,
            "warnings": ["Upper-bound estimates for missing essential service channels and roles. Existing channels are not deleted or moved.",
                         "Server owner or Administrator setup. Verification gate preserves existing member access via a dedicated role and restricts new members. Admin and Moderator permissions remain manual.",
                         "Existing panel messages may be refreshed. Security alerts use private logs; voice XP remains opt-in."]
        }

    async def preview(self, request):
        principal, guild = await self._guild(request, write=True)
        if not self._can_setup(principal, guild):
            raise web.HTTPForbidden(text='Server owner or Administrator only for full setup')
        data = await self._json(request)
        theme = data.get("theme")
        focus = data.get("focus", False)
        if theme not in TEMPLATES or focus is not False:
            raise web.HTTPBadRequest(text="Invalid type or focus flag")
        self._prune()
        plan = self._plan(guild, theme, focus)
        plan_id = self._new_id()
        self.plans[plan_id] = {
            "at": time.time(), "actor": principal["actor"],
            "guild": str(guild.id), "theme": theme,
            "focus": focus, "fingerprint": self._fingerprint(guild)
        }
        plan["plan_id"] = plan_id
        return web.json_response(plan)

    async def apply(self, request):
        principal, guild = await self._guild(request, write=True)
        if not self._can_setup(principal, guild):
            raise web.HTTPForbidden(text='Server owner or Administrator only for full setup')
        data = await self._json(request)
        plan_id = data.get("plan_id")
        if not isinstance(plan_id, str) or len(plan_id) > 128:
            raise web.HTTPBadRequest(text="Valid plan required")
        plan = self.plans.pop(plan_id, None)
        if not plan or plan["at"] + PLAN_SECONDS < time.time():
            raise web.HTTPConflict(text="Preview expired; generate it again")
        if plan["guild"] != str(guild.id) or plan["actor"] != principal["actor"]:
            raise web.HTTPForbidden(text="Preview owner or server mismatch")
        if plan["fingerprint"] != self._fingerprint(guild):
            raise web.HTTPConflict(text="Server changed since preview; review again")
        from bot import VoicePanel, TicketPanel, RolePanel, safe_role, embed
        async with self.bot.locks[("setup", guild.id)]:
            # Refresh authorization after taking the same lock as slash setup.
            if principal['sid'] is not None:
                permitted = await self._admin_guilds(principal)
                if str(guild.id) not in permitted or not self._can_setup(principal, guild):
                    raise web.HTTPForbidden(text='Administrator permission no longer available')
            if plan["fingerprint"] != self._fingerprint(guild):
                raise web.HTTPConflict(text="Server changed during setup; preview again")
            try:
                summary = await provision_server(
                    self.bot, guild, plan["theme"],
                    voice_panel_factory=VoicePanel,
                    ticket_panel_factory=TicketPanel,
                    role_panel_factory=RolePanel,
                    safe_role_fn=safe_role, embed_factory=embed,
                    focus=plan["focus"], cleanup=False,
                )
                await self.bot.audit(
                    guild, "Web Setup Wizard",
                    "Template " + plan["theme"] + " by Discord user " +
                    principal["actor"] + ", focus=" + str(plan["focus"])
                )
            except discord.Forbidden:
                raise web.HTTPForbidden(text="Bot lacks a required Discord permission")
            except (discord.HTTPException, ValueError) as exc:
                LOG.warning("Wizard apply failed guild=%s cause=%s",
                            guild.id, type(exc).__name__)
                raise web.HTTPBadRequest(text="Setup could not finish: " + str(exc)[:300])
        return web.json_response({"ok": True, "result": summary})

    async def settings(self, request):
        principal, guild = await self._guild(request, write=True)
        data = await self._json(request)
        if not data or set(data) - SAFE_CONFIG:
            raise web.HTTPBadRequest(text="Unsupported or empty settings change")
        try:
            changes = validate_config(data)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        if "ai_enabled" in changes and principal["sid"] is not None and str(guild.owner_id) != principal["actor"]:
            raise web.HTTPForbidden(text="Only the server owner can change AI access")
        old = self.bot.store.config(guild.id)
        self.bot.store.update(guild.id, changes)
        if any(key in changes and old[key] != changes[key] for key in ("levels", "voice_xp")):
            if hasattr(self.bot, "voice_previous"):
                self.bot.voice_previous = {k for k in self.bot.voice_previous if k[0] != guild.id}
        if "security_enabled" in changes and old["security_enabled"] != changes["security_enabled"]:
            if hasattr(self.bot, "security"):
                self.bot.security.clear(guild.id)
        await self.bot.audit(
            guild, "Web settings", "Updated: " + ", ".join(changes) +
            " by Discord user " + principal["actor"]
        )
        return web.json_response({"ok": True})

    async def restore(self, request):
        principal, guild = await self._guild(request, write=True)
        if not self._can_setup(principal, guild):
            raise web.HTTPForbidden(text="Server owner or Administrator only for layout restore")
        async with self.bot.locks[("setup", guild.id)]:
            result = await restore_server(self.bot, guild)
            await self.bot.audit(
                guild, "Web layout restore",
                str(len(result["restored"])) + " archived channels restored by " +
                principal["actor"]
            )
        return web.json_response({"ok": True, "result": result})

    async def logout(self, request):
        principal = self._principal(request)
        self._csrf_check(request, principal)
        if principal["sid"]:
            self.sessions.pop(principal["sid"], None)
        response = web.json_response({"ok": True})
        response.del_cookie("tov4r_session", path="/")
        return response


def add_dashboard_v2(app, bot):
    service = DashboardV2(bot)
    service.register(app)
    if not service.enabled:
        LOG.info("Discord OAuth dashboard login inactive: set "
                 "DISCORD_OAUTH_CLIENT_SECRET and DASHBOARD_BASE_URL")
    return service
