"""Offline regression tests: no token or live Discord connection required."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands
from developer_edition import DeveloperEdition


def interaction(rules=()):
    guild = SimpleNamespace(
        fetch_automod_rules=AsyncMock(return_value=list(rules)),
        create_automod_rule=AsyncMock(),
    )
    return SimpleNamespace(
        guild=guild, user=SimpleNamespace(id=123),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class DeveloperReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(command_prefix=".", intents=discord.Intents.none())
        self.cog = DeveloperEdition(self.bot)
        await self.bot.add_cog(self.cog)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_global_command_groups_register(self):
        for group, names in {"dev": {"ping", "info", "badges"},
                             "native_automod": {"setup", "status"}}.items():
            registered = self.bot.tree.get_command(group)
            self.assertIsNotNone(registered)
            self.assertEqual({c.name for c in registered.commands}, names)

    async def test_preview_is_read_only(self):
        i = interaction()
        await self.cog.setup_rule.callback(self.cog, i, False, 8)
        i.guild.create_automod_rule.assert_not_awaited()
        i.guild.fetch_automod_rules.assert_not_awaited()

    async def test_existing_rule_is_preserved(self):
        i = interaction([SimpleNamespace(trigger=SimpleNamespace(
            type=discord.AutoModRuleTriggerType.mention_spam))])
        await self.cog.setup_rule.callback(self.cog, i, True, 8)
        i.guild.create_automod_rule.assert_not_awaited()

    async def test_confirm_creates_valid_discord_rule(self):
        i = interaction()
        await self.cog.setup_rule.callback(self.cog, i, True, 8)
        i.guild.create_automod_rule.assert_awaited_once()
        payload = i.guild.create_automod_rule.await_args.kwargs
        self.assertEqual(payload["trigger"].mention_limit, 8)
        self.assertEqual(payload["trigger"].type, discord.AutoModRuleTriggerType.mention_spam)
        self.assertEqual(payload["actions"][0].type, discord.AutoModRuleActionType.block_message)
        self.assertTrue(payload["enabled"])


class ModuleReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_settings_migrate_without_overwriting(self):
        import tempfile
        from pathlib import Path
        from store import Store
        with tempfile.TemporaryDirectory() as root:
            store = Store(str(Path(root) / "test.db"))
            try:
                store.run("INSERT INTO config VALUES(?,?)", (123, '{"levels":false,"language":"fr"}'))
                cfg = store.config(123)
                self.assertFalse(cfg["levels"])
                self.assertEqual(cfg["language"], "fr")
                self.assertTrue(cfg["voice_rooms_enabled"])
                self.assertTrue(cfg["voice_panel_enabled"])
                self.assertTrue(cfg["tickets_enabled"])
                self.assertFalse(cfg["ai_enabled"])
                store.update(123, {"tickets_enabled": False})
                self.assertFalse(store.config(123)["tickets_enabled"])
                self.assertTrue(store.config(456)["tickets_enabled"])
            finally:
                store.db.close()

    async def test_module_values_are_strict_booleans(self):
        from store import validate_config
        with self.assertRaises(ValueError):
            validate_config({"voice_rooms_enabled": "false"})
        self.assertEqual(validate_config({"ai_enabled": True}), {"ai_enabled": True})

    async def test_ai_requires_explicit_server_opt_in_and_owner(self):
        from ai_agent import AIAgent
        cfg = {"ai_enabled": False}
        bot = SimpleNamespace(store=SimpleNamespace(config=lambda _: cfg))
        cog = AIAgent(bot)
        cog.key = "test-not-a-real-key"
        cog.all_guilds = True
        i = SimpleNamespace(guild=SimpleNamespace(id=123, owner_id=456),
                            user=SimpleNamespace(id=456))
        self.assertFalse(cog.authorized(i))
        cfg["ai_enabled"] = True
        self.assertTrue(cog.authorized(i))
        i.user.id = 789
        self.assertFalse(cog.authorized(i))

    async def test_dashboard_rejects_nonowner_ai_write(self):
        from aiohttp import web
        from web_dashboard_v2 import DashboardV2
        service = DashboardV2(SimpleNamespace())
        service._guild = AsyncMock(return_value=(
            {"sid": "session", "actor": "99"}, SimpleNamespace(id=123, owner_id=456)))
        service._json = AsyncMock(return_value={"ai_enabled": True})
        with self.assertRaises(web.HTTPForbidden):
            await service.settings(SimpleNamespace())


class VoiceWelcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_entry_into_selected_room(self):
        from unittest.mock import Mock
        from voice_welcome import should_greet
        room = Mock(spec=discord.VoiceChannel)
        room.id = 123
        other = SimpleNamespace(id=456)
        human = SimpleNamespace(bot=False)
        before = SimpleNamespace(channel=other)
        after = SimpleNamespace(channel=room)
        self.assertTrue(should_greet(human, before, after, 123))
        self.assertFalse(should_greet(human, before, after, 456))
        self.assertFalse(should_greet(human, after, after, 123))
        self.assertFalse(should_greet(SimpleNamespace(bot=True), before, after, 123))
        self.assertFalse(should_greet(human, after, SimpleNamespace(channel=None), 123))
        self.assertFalse(should_greet(human, before, after, 0))

    async def test_voice_welcome_commands_register(self):
        from voice_welcome import VoiceWelcome
        bot = commands.Bot(command_prefix=".", intents=discord.Intents.none())
        try:
            await bot.add_cog(VoiceWelcome(bot))
            group = bot.tree.get_command("voicewelcome")
            self.assertEqual({c.name for c in group.commands}, {"setup", "off", "audio", "reset_audio"})
        finally:
            await bot.close()

    async def test_existing_cached_audio_does_not_call_provider(self):
        from voice_welcome import VoiceWelcome
        from pathlib import Path
        from unittest.mock import patch
        import tempfile
        cog = VoiceWelcome(SimpleNamespace())
        with tempfile.TemporaryDirectory() as root:
            cog.audio = Path(root) / "welcome.mp3"
            cog.audio.write_bytes(b"test cache")
            with patch("voice_welcome.aiohttp.ClientSession") as session:
                await cog.prepare()
                session.assert_not_called()


    async def test_persistent_connection_reused_after_greeting(self):
        from voice_welcome import VoiceWelcome
        from unittest.mock import Mock
        cog = VoiceWelcome(SimpleNamespace())
        channel = Mock()
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=True)
        client = Mock()
        client.is_connected.return_value = True
        client.channel = channel
        client.disconnect = AsyncMock()
        guild = SimpleNamespace(id=123, me=object(), voice_client=client)
        cog.clients[guild.id] = client
        self.assertIs(await cog.ensure_connection(guild, channel), client)
        client.disconnect.assert_not_awaited()

    async def test_empty_voice_room_connects_and_stays(self):
        from voice_welcome import VoiceWelcome
        from unittest.mock import Mock
        cog = VoiceWelcome(SimpleNamespace())
        channel = Mock()
        channel.members = []
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=True)
        client = Mock()
        channel.connect = AsyncMock(return_value=client)
        guild = SimpleNamespace(id=123, me=object(), voice_client=None)
        self.assertIs(await cog.ensure_connection(guild, channel), client)
        self.assertIs(cog.clients[guild.id], client)
        channel.connect.assert_awaited_once_with(timeout=15, reconnect=True, self_deaf=True)


    async def test_offline_audio_is_cached_without_provider(self):
        from voice_welcome import VoiceWelcome
        from pathlib import Path
        from unittest.mock import patch
        import tempfile
        cog = VoiceWelcome(SimpleNamespace())
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "welcome-offline.mp3").write_bytes(b"test bundled clip")
            cog.audio = base / "data" / "welcome.mp3"
            with patch("voice_welcome.__file__", str(base / "voice_welcome.py")), patch("voice_welcome.aiohttp.ClientSession") as session:
                await cog.prepare()
                self.assertEqual(cog.audio.read_bytes(), b"test bundled clip")
                session.assert_not_called()


    async def test_custom_audio_is_guild_scoped(self):
        from voice_welcome import VoiceWelcome
        from pathlib import Path
        import tempfile
        cog = VoiceWelcome(SimpleNamespace())
        with tempfile.TemporaryDirectory() as root:
            cog.audio = Path(root) / "welcome.mp3"
            cog.custom_audio(123).write_bytes(b"custom clip")
            self.assertEqual(cog.playback_path(123), cog.custom_audio(123))
            self.assertEqual(cog.playback_path(456), cog.audio)


if __name__ == "__main__":
    unittest.main()
