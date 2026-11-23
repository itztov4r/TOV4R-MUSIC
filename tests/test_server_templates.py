"""Mocked integration checks for themed Discord server setup.

Tests never connect to Discord, modify real servers, or use a Discord token.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord
import server_templates as tpl
from store import Store


class FakeMember:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeRole:
    def __init__(self, role_id, name, permissions):
        self.id = role_id
        self.name = name
        self.permissions = permissions
        self.managed = False

    async def edit(self, *, permissions=None, name=None, reason):
        if permissions is not None: self.permissions = permissions
        if name is not None: self.name = name


class FakeCategory:
    def __init__(self, channel_id, name):
        self.id = channel_id
        self.name = name
        self.text_channels = []
        self.voice_channels = []

    async def edit(self, *, name, reason):
        self.name = name


class FakeText:
    def __init__(self, channel_id, name, category, topic, overwrites, guild):
        self.id = channel_id
        self.name = name
        self.category = category
        self.category_id = category.id
        self.topic = topic
        self.overwrites = overwrites
        self.messages = {}
        self.threads = []
        self.guild = guild

    async def history(self, limit=1):
        for message in list(self.messages.values())[-limit:]:
            yield message

    async def edit(self, *, overwrites=None, name=None, reason):
        if overwrites is not None: self.overwrites = overwrites
        if name is not None: self.name = name

    async def delete(self, *, reason):
        self.category.text_channels.remove(self)
        self.guild.channels.pop(self.id, None)

    async def send(self, *, embed, allowed_mentions=None, view=None):
        mid = self.guild.new_id()
        message = SimpleNamespace(id=mid, embed=embed, view=view)
        self.messages[mid] = message
        return message

    async def fetch_message(self, message_id):
        return self.messages[message_id]


class FakeVoice:
    def __init__(self, channel_id, name, category, overwrites, guild):
        self.id = channel_id
        self.name = name
        self.category = category
        self.category_id = category.id
        self.overwrites = overwrites
        self.members = []
        self.guild = guild

    async def edit(self, *, overwrites=None, name=None, reason):
        if overwrites is not None: self.overwrites = overwrites
        if name is not None: self.name = name

    async def delete(self, *, reason):
        self.category.voice_channels.remove(self)
        self.guild.channels.pop(self.id, None)


class FakeGuild:
    def __init__(self):
        self.id = 1234567890123456
        self._id = 500
        self.roles = []
        self.categories = []
        self.channels = {}
        self.me = FakeMember(
            guild_permissions=FakeMember(
                manage_channels=True, manage_roles=True, send_messages=True
            )
        )
        self.default_role = FakeMember(name="@everyone")

    def new_id(self):
        self._id += 1
        return self._id

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def create_role(self, *, name, permissions, colour, mentionable, reason):
        role = FakeRole(self.new_id(), name, permissions)
        self.roles.append(role)
        return role

    async def create_category(self, name, *, reason):
        category = FakeCategory(self.new_id(), name)
        self.categories.append(category)
        self.channels[category.id] = category
        return category

    async def create_text_channel(self, name, *, category, topic, overwrites, reason):
        # Mirrors discord.py: None is not a valid overwrites argument.
        if not isinstance(overwrites, dict):
            raise TypeError("overwrites parameter expects a dict.")
        channel = FakeText(self.new_id(), name, category, topic, overwrites, self)
        category.text_channels.append(channel)
        self.channels[channel.id] = channel
        return channel

    async def create_voice_channel(self, name, *, category, overwrites, reason):
        if not isinstance(overwrites, dict):
            raise TypeError("overwrites parameter expects a dict.")
        channel = FakeVoice(self.new_id(), name, category, overwrites, self)
        category.voice_channels.append(channel)
        self.channels[channel.id] = channel
        return channel


class ServerTemplatesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.bot = SimpleNamespace(store=self.store)
        self.guild = FakeGuild()
        self.patches = [
            patch.object(tpl.discord, "CategoryChannel", FakeCategory),
            patch.object(tpl.discord, "TextChannel", FakeText),
            patch.object(tpl.discord, "VoiceChannel", FakeVoice),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.store.db.close()

    async def apply(self, template_key, *, cleanup=False):
        return await tpl.apply_template(
            self.bot, self.guild, template_key,
            panel_factory=lambda bot, roles: ("panel", roles),
            safe_role_fn=lambda role, guild: True, cleanup=cleanup,
        )

    async def test_movies_creates_complete_server_without_admin_roles(self):
        result = await self.apply("movies")
        self.assertEqual(len(result["categories_new"]), 3)
        self.assertEqual(len(result["roles_new"]), 4)
        self.assertGreaterEqual(len(result["channels_new"]), 10)
        self.assertEqual(result["roles_panel"], "created")
        self.assertEqual(self.store.config(self.guild.id)["server_type"], "movies")
        self.assertTrue(all(not role.permissions.administrator for role in self.guild.roles))
        self.assertTrue(all(not role.permissions.manage_roles for role in self.guild.roles))
        self.assertTrue(all(role.permissions.send_messages for role in self.guild.roles))
        self.assertIn(tpl.styled_channel_name("movie-reviews", "text"), [ch.name for ch in self.guild.channels.values()])
        self.assertIn(tpl.styled_channel_name("movie-spoilers", "text"), [ch.name for ch in self.guild.channels.values()])
        self.assertIn(tpl.styled_channel_name("🍿 Watch Party", "voice"), [ch.name for ch in self.guild.channels.values()])
        panels = self.store.all("SELECT * FROM role_panels WHERE guild=?", (self.guild.id,))
        self.assertEqual(len(panels), 1)
        rules = next(ch for ch in self.guild.channels.values() if ch.name == tpl.styled_channel_name("cinema-rules", "text"))
        self.assertEqual(len(rules.messages), 1)
        self.assertIn(self.guild.me, rules.overwrites)
        self.assertTrue(rules.overwrites[self.guild.me].send_messages)

    async def test_normal_channels_always_use_dict_overwrites(self):
        # This reproduces the production error that affected /setup on Gfrx.
        await self.apply("movies")
        channels = [ch for ch in self.guild.channels.values()
                    if isinstance(ch, FakeText)]
        self.assertTrue(channels)
        self.assertTrue(all(isinstance(ch.overwrites, dict) for ch in channels))
        public = next(ch for ch in channels if ch.name == tpl.styled_channel_name("movie-chat", "text"))
        self.assertIsInstance(public.overwrites, dict)
        self.assertTrue(public.overwrites[self.guild.default_role].send_messages)
        readonly = next(ch for ch in channels if ch.name == tpl.styled_channel_name("cinema-rules", "text"))
        self.assertIn(self.guild.default_role, readonly.overwrites)

    async def test_repeated_setup_does_not_duplicate_items_or_messages(self):
        await self.apply("movies")
        before = (len(self.guild.roles), len(self.guild.channels),
                  sum(len(ch.messages) for ch in self.guild.channels.values()
                      if isinstance(ch, FakeText)))
        again = await self.apply("movies")
        after = (len(self.guild.roles), len(self.guild.channels),
                 sum(len(ch.messages) for ch in self.guild.channels.values()
                     if isinstance(ch, FakeText)))
        self.assertEqual(before, after)
        self.assertEqual(again["roles_new"], [])
        self.assertEqual(again["channels_new"], [])
        self.assertEqual(again["categories_new"], [])
        self.assertEqual(again["roles_panel"], "existing")

    async def test_different_types_isolated_and_keep_old_channels(self):
        await self.apply("movies")
        old_channel_ids = set(self.guild.channels)
        old_role_ids = {role.id for role in self.guild.roles}
        result = await self.apply("gaming")
        self.assertEqual(result["type"], "gaming")
        self.assertTrue(old_channel_ids.issubset(self.guild.channels))
        self.assertTrue(old_role_ids.issubset({role.id for role in self.guild.roles}))
        self.assertEqual(self.store.config(self.guild.id)["server_type"], "gaming")
        self.assertEqual(len(self.store.all(
            "SELECT * FROM role_panels WHERE guild=?", (self.guild.id,)
        )), 2)

    async def test_public_text_and_voice_permissions(self):
        await self.apply("movies")
        public = next(ch for ch in self.guild.channels.values()
                      if isinstance(ch, FakeText) and ch.name == tpl.styled_channel_name("movie-chat", "text"))
        choose = next(ch for ch in self.guild.channels.values()
                      if isinstance(ch, FakeText) and ch.name == tpl.styled_channel_name("choose-roles", "text"))
        rule = next(ch for ch in self.guild.channels.values()
                    if isinstance(ch, FakeText) and ch.name == tpl.styled_channel_name("cinema-rules", "text"))
        voice = next(ch for ch in self.guild.channels.values()
                     if isinstance(ch, FakeVoice) and ch.name == tpl.styled_channel_name("🍿 Watch Party", "voice"))
        everyone = self.guild.default_role
        self.assertTrue(public.overwrites[everyone].send_messages)
        self.assertTrue(public.overwrites[everyone].view_channel)
        self.assertFalse(rule.overwrites[everyone].send_messages)
        self.assertFalse(choose.overwrites[everyone].send_messages)
        self.assertTrue(voice.overwrites[everyone].connect)

    async def test_cleanup_deletes_only_empty_bots_channels_from_old_theme(self):
        await self.apply("movies")
        await self.apply("gaming")
        clean = await self.apply("gaming", cleanup=True)
        self.assertIsNotNone(clean["cleanup"])
        self.assertIn(tpl.styled_channel_name("movie-chat","text"), clean["cleanup"]["deleted"])
        self.assertIn(tpl.styled_channel_name("🍿 Watch Party","voice"), clean["cleanup"]["deleted"])
        remaining = [ch.name for ch in self.guild.channels.values()]
        self.assertNotIn(tpl.styled_channel_name("movie-chat","text"), remaining)
        self.assertNotIn(tpl.styled_channel_name("🍿 Watch Party","voice"), remaining)
        # Rules and role panel contain bot messages: NEVER delete them.
        self.assertIn(tpl.styled_channel_name("cinema-rules","text"), remaining)
        self.assertIn(tpl.styled_channel_name("choose-roles","text"), remaining)
        # Active gaming template is untouched.
        self.assertIn(tpl.styled_channel_name("game-chat","text"), remaining)

    async def test_cleanup_respects_modified_permissions_and_other_messages(self):
        await self.apply("movies")
        channel = next(ch for ch in self.guild.channels.values()
                       if isinstance(ch, FakeText) and ch.name == tpl.styled_channel_name("movie-chat", "text"))
        channel.overwrites = {}  # User customized: no longer equals template policy.
        await self.apply("gaming")
        clean = await self.apply("gaming", cleanup=True)
        self.assertNotIn(tpl.styled_channel_name("movie-chat","text"), clean["cleanup"]["deleted"])
        self.assertIn(channel.id, self.guild.channels)

    async def test_cleanup_never_deletes_preexisting_channels_or_roles(self):
        # An arbitrary user-created empty channel is NOT tracked as bot-created.
        category = await self.guild.create_category("User Category", reason="manual")
        channel = await self.guild.create_text_channel(
            "old-unused-channel", category=category, topic="",
            overwrites={}, reason="manual",
        )
        await self.apply("movies")
        await self.apply("gaming", cleanup=True)
        self.assertIn(channel.id, self.guild.channels)
        self.assertTrue(all(role in self.guild.roles for role in self.guild.roles))

    async def test_premium_names_are_consistent_and_idempotent(self):
        result = await self.apply("movies")
        self.assertTrue(result["categories_new"])
        self.assertTrue(all("━━" in name for name in result["categories_new"]))
        self.assertTrue(all("・" in name for name in result["channels_new"]))
        self.assertTrue(all("・" in name for name in result["roles_new"]))
        ids = set(self.guild.channels)
        again = await self.apply("movies")
        self.assertEqual(set(self.guild.channels), ids)
        self.assertEqual(again["channels_new"], [])
        self.assertEqual(again["roles_new"], [])

    async def test_legacy_tracked_names_upgrade_without_duplicate(self):
        await self.apply("movies")
        ch = next(ch for ch in self.guild.channels.values()
                  if isinstance(ch, FakeText) and ch.name == tpl.styled_channel_name("movie-chat", "text"))
        original_id = ch.id
        ch.name = "movie-chat"
        category = next(cat for cat in self.guild.categories
                        if "CINEMA" in cat.name and "DISCUSS" in cat.name)
        category.name = "🎥・CINEMA | DISCUSS"
        again = await self.apply("movies")
        self.assertEqual(ch.id, original_id)
        self.assertEqual(ch.name, tpl.styled_channel_name("movie-chat","text"))
        self.assertIn("━━", category.name)
        self.assertEqual(again["channels_new"], [])

    def test_all_template_definitions_are_unique_and_safe(self):
        self.assertGreaterEqual(len(tpl.TEMPLATES), 9)
        self.assertEqual(len(tpl.TEMPLATES), len(tpl.TEMPLATE_CHOICES))
        self.assertLessEqual(len(tpl.TEMPLATE_CHOICES), 25)
        for key, template in tpl.TEMPLATES.items():
            self.assertLessEqual(len(template["roles"]), 5)
            self.assertEqual(len(template["roles"]), len(set(template["roles"])), key)
            cats = template["groups"]
            self.assertEqual(len(cats), len({group["name"] for group in cats}), key)
            self.assertTrue(any(ch["name"] == "choose-roles"
                                for group in cats for ch in group["channels"]), key)
            for group in cats:
                names = [ch["name"] for ch in group["channels"]]
                self.assertEqual(len(names), len(set(names)), (key, group["name"]))
                for channel in group["channels"]:
                    self.assertLessEqual(len(channel["name"]), 100)


if __name__ == "__main__":
    unittest.main()
