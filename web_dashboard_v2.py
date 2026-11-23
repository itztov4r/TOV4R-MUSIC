"""TOV4R multi-guild dashboard and preview-first web setup wizard.

Uses Discord OAuth2 identify+guilds, per-guild Manage Server checks, short
server-side sessions and same-origin CSRF protection. The existing owner's
DASHBOARD_KEY remains usable (as an Authorization header, never in URLs).

Requires DISCORD_OAUTH_CLIENT_SECRET and DASHBOARD_BASE_URL for OAuth.
"""
from __future__ import annotations

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
from setup_service import provision_server
from store import validate_config

LOG = logging.getLogger("tov4r.dashboard")
API = "https://discord.com/api/v10"
MANAGE_GUILD = 1 << 5
SESSION_SECONDS = 3600
STATE_SECONDS = 600
PLAN_SECONDS = 600
SAFE_CONFIG = frozenset({
    "levels", "antispam", "anti_invites",
    "welcome_text", "blocked_words", "xp_cooldown",
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
        for item in guilds:
            try:
                allowed = bool(item.get("owner")) or (
                    int(item.get("permissions", "0")) & MANAGE_GUILD
                ) == MANAGE_GUILD
                if allowed:
                    guild = self.bot.get_guild(int(item["id"]))
                    if guild is not None:
                        permitted[str(guild.id)] = guild
            except (ValueError, TypeError, KeyError):
                continue
        return permitted

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
                    guild.me and guild.me.guild_permissions.manage_channels
                    and guild.me.guild_permissions.manage_roles
                ),
                "settings": {key:cfg[key] for key in SAFE_CONFIG},
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
        from server_templates import _get_item
        spec = TEMPLATES[theme]
        structure = []
        missing_cats = 0
        missing_channels = 0
        for group in spec["groups"]:
            cid = _get_item(self.bot, guild.id, theme, "category:" + group["key"])
            known = guild.get_channel(cid) if cid else None
            name = styled_category_name(group["name"])
            exists = isinstance(known, discord.CategoryChannel) or any(
                c.name in (name, group["name"]) for c in guild.categories
            )
            if not exists:
                missing_cats += 1
            channels = []
            for ch in group["channels"]:
                item_key = f'{group["key"]}:{ch["kind"]}:{ch["name"]}'
                ch_id = _get_item(self.bot, guild.id, theme, item_key)
                known_ch = guild.get_channel(ch_id) if ch_id else None
                display = styled_channel_name(ch["name"], ch["kind"])
                found = known_ch is not None or any(
                    c.name in (display, ch["name"])
                    for c in self._channels(guild) if getattr(c, "category_id", None)
                    == (known.id if isinstance(known, discord.CategoryChannel) else None)
                )
                if not found:
                    missing_channels += 1
                channels.append({
                    "name": display, "kind": ch["kind"],
                    "readonly": bool(ch.get("readonly", False)),
                    "exists": bool(found),
                })
            structure.append({"category": name, "exists": exists, "channels": channels})
        missing_roles = sum(
            not any(r.name in (name, styled_role_name(name))
                    for r in guild.roles)
            for name in spec["roles"]
        )
        focus_count, protected_count = 0, 0
        if focus:
            to_move, skipped = candidates(self.bot, guild, theme)
            focus_count = len(to_move)
            protected_count = len(skipped)
        return {
            "theme": theme, "title": spec["title"],
            "categories": structure, "roles": [
                styled_role_name(r) for r in spec["roles"]
            ],
            "new_categories_estimate": missing_cats,
            "new_channels_estimate": missing_channels,
            "new_roles_estimate": missing_roles,
            "focus": focus, "off_theme_archive_estimate": focus_count,
            "protected_channels": protected_count,
            "warnings": [
                "Preview is an estimate; live Discord changes can alter the result.",
                "No roles, messages, or existing channels are deleted by this wizard.",
                "Focus archives unrelated public channels privately and supports undo."
            ],
        }

    async def preview(self, request):
        principal, guild = await self._guild(request, write=True)
        data = await self._json(request)
        theme = data.get("theme")
        focus = data.get("focus", False)
        if theme not in TEMPLATES or type(focus) is not bool:
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
            # Recheck after taking the same lock as the slash setup command.
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
        changes = validate_config(data)
        self.bot.store.update(guild.id, changes)
        await self.bot.audit(
            guild, "Web settings", "Updated: " + ", ".join(changes) +
            " by Discord user " + principal["actor"]
        )
        return web.json_response({"ok": True})

    async def restore(self, request):
        principal, guild = await self._guild(request, write=True)
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
