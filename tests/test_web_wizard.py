"""Offline security and setup-wizard tests. No Discord token or network needed."""
import asyncio
import collections
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from aiohttp import web

# Import all slash-command annotations before tests patch Discord channel
# classes with in-memory doubles; importing while patched breaks discord.py.
import bot as bot_module
import web_dashboard_v2 as api
from store import Store
from test_server_templates import FakeGuild, FakeCategory, FakeText, FakeVoice


class FakeRequest:
    def __init__(self, *, headers=None, cookies=None, guild=None, data=None):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.match_info = {"guild": str(guild)} if guild is not None else {}
        self.content_type = "application/json"
        self._data = data or {}

    async def json(self):
        return self._data


class WebWizardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {
            "DASHBOARD_KEY": "test-owner-key-should-have-32-characters-value",
            "DISCORD_OAUTH_CLIENT_SECRET": "",
            "DASHBOARD_BASE_URL": "",
        })
        self.env.start()
        self.store = Store(":memory:")
        self.guild = FakeGuild()
        self.guild.name = "Only Test Server"
        self.guild.member_count = 12
        self.guild.members = []
        self.guild.roles = []
        self.guild.unavailable = False
        self.bot = SimpleNamespace(
            guilds=[self.guild], store=self.store, application_id=1570489381624619198,
            is_ready=lambda: True, get_guild=lambda gid: self.guild if gid == self.guild.id else None,
            locks=collections.defaultdict(asyncio.Lock),
            audit=AsyncMock(),
        )
        self.service = api.DashboardV2(self.bot)
        self.patches = [
            patch.object(discord, "CategoryChannel", FakeCategory),
            patch.object(discord, "TextChannel", FakeText),
            patch.object(discord, "VoiceChannel", FakeVoice),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.store.db.close()
        self.env.stop()

    def owner(self, guild=None, data=None):
        return FakeRequest(
            headers={"Authorization": "Bearer " + self.service.owner_key},
            guild=guild, data=data
        )

    def oauth(self, sid="abcdef", *, csrf=None, guild=None, data=None):
        user = self.service.sessions.get(sid)
        return FakeRequest(
            cookies={"tov4r_session": sid},
            headers={"X-CSRF-Token": csrf or ""},
            guild=guild, data=data
        )

    def new_oauth_session(self, *, sid="abcdef"):
        self.service.sessions[sid] = {
            "expires": time.time() + 1200,
            "user": {"id": "87654321", "username": "TestManager"},
            "access_token": "fake-access-token-never-used",
            "csrf": "test-csrf-token",
        }

    async def test_owner_access_requires_exact_bearer_key(self):
        with self.assertRaises(web.HTTPUnauthorized):
            self.service._principal(FakeRequest())
        with self.assertRaises(web.HTTPUnauthorized):
            self.service._principal(FakeRequest(headers={
                "Authorization": "Bearer wrong-key"
            }))
        principal = self.service._principal(self.owner())
        self.assertEqual(principal["actor"], "owner")

    async def test_oauth_cannot_see_other_server_without_manage_server(self):
        self.new_oauth_session()
        guest = self.oauth(guild=self.guild.id)
        with patch.object(self.service, "_discord", new=AsyncMock(return_value=[{
            "id": str(self.guild.id), "owner": False, "permissions": "0",
        }])):
            with self.assertRaises(web.HTTPForbidden):
                await self.service._guild(guest)
        with patch.object(self.service, "_discord", new=AsyncMock(return_value=[{
            "id": str(self.guild.id), "owner": False, "permissions": str(1 << 5),
        }])):
            principal, guild = await self.service._guild(guest)
        self.assertEqual(principal["actor"], "87654321")
        self.assertEqual(guild.id, self.guild.id)

    async def test_oauth_writes_need_csrf_even_with_manage_server(self):
        self.new_oauth_session()
        req = self.oauth(guild=self.guild.id)
        with self.assertRaises(web.HTTPForbidden):
            await self.service._guild(req, write=True)
        with patch.object(self.service, "_discord", new=AsyncMock(return_value=[{
            "id": str(self.guild.id), "owner": True, "permissions": "0",
        }])):
            req = self.oauth(guild=self.guild.id, csrf="test-csrf-token")
            _, guild = await self.service._guild(req, write=True)
            self.assertEqual(guild.id, self.guild.id)

    async def test_oauth_missing_settings_disables_login_but_keeps_owner(self):
        self.assertFalse(self.service.enabled)
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.service.login(FakeRequest())
        self.assertEqual(self.service._principal(self.owner())["actor"], "owner")

    async def test_preview_requires_valid_type_and_creates_no_discord_resources(self):
        req = self.owner(self.guild.id, {"theme": "movies", "focus": False})
        with self.assertRaises(web.HTTPBadRequest):
            await self.service.preview(self.owner(self.guild.id, {"theme": "bogus"}))
        response = await self.service.preview(req)
        data = json.loads(response.text)
        self.assertIn("plan_id", data)
        self.assertEqual(data["theme"], "movies")
        self.assertGreater(data["new_channels_estimate"], 0)
        self.assertEqual(self.guild.channels, {})
        self.assertEqual(self.guild.roles, [])

    async def test_apply_requires_same_actor_guild_and_fresh_preview(self):
        preview = await self.service.preview(
            self.owner(self.guild.id, {"theme": "movies", "focus": False}))
        plan_id = json.loads(preview.text)["plan_id"]
        with self.assertRaises(web.HTTPForbidden):
            await self.service.apply(self.owner(1234567890123457, {"plan_id": plan_id}))
        # Access is checked before preview ID is consumed.
        self.assertIn(plan_id, self.service.plans)
        with patch.object(api, "provision_server", new=AsyncMock(return_value={
            "type": "movies", "channels_new": [], "roles_new": [],
        })) as mocked:
            result = await self.service.apply(
                self.owner(self.guild.id, {"plan_id": plan_id}))
        self.assertTrue(json.loads(result.text)["ok"])
        self.assertEqual(mocked.await_count, 1)
        with self.assertRaises(web.HTTPConflict):
            await self.service.apply(self.owner(self.guild.id, {"plan_id": plan_id}))

    async def test_preview_invalidated_by_server_change(self):
        plan = await self.service.preview(self.owner(
            self.guild.id, {"theme": "gaming", "focus": False}))
        token = json.loads(plan.text)["plan_id"]
        self.guild.name = "New Name"
        # Guild name doesn't affect structure. Changing a role does.
        self.guild.roles.append(SimpleNamespace(id=77, name="Admin"))
        with self.assertRaises(web.HTTPConflict):
            await self.service.apply(self.owner(self.guild.id, {"plan_id": token}))

    async def test_settings_cannot_write_unsafe_fields(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.service.settings(self.owner(
                self.guild.id, {"support_role": 1, "antispam": False}))
        self.assertEqual(self.store.config(self.guild.id)["antispam"], True)
        response = await self.service.settings(self.owner(
            self.guild.id, {"antispam": False, "xp_cooldown": 45}))
        self.assertTrue(json.loads(response.text)["ok"])
        self.assertFalse(self.store.config(self.guild.id)["antispam"])

    async def test_guild_route_rejects_fake_cross_server_id(self):
        with self.assertRaises(web.HTTPForbidden):
            await self.service._guild(self.owner(guild=9899898989))

    async def test_preview_keeps_owner_dashboard_data_intact(self):
        self.store.update(self.guild.id, {
            "welcome_text": "Original", "xp_cooldown": 160,
        })
        await self.service.preview(self.owner(self.guild.id, {
            "theme": "tech", "focus": True,
        }))
        cfg = self.store.config(self.guild.id)
        self.assertEqual(cfg["welcome_text"], "Original")
        self.assertEqual(cfg["xp_cooldown"], 160)
        self.assertEqual(cfg["server_type"], "community")

if __name__ == "__main__":
    unittest.main()
