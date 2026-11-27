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

    async def test_public_ai_requires_opt_in_and_never_executes_actions(self):
        from ai_agent import AIAgent
        cfg = {"ai_enabled": False}
        cog = AIAgent(SimpleNamespace(store=SimpleNamespace(config=lambda _: cfg)))
        cog.key = "test-not-a-real-key"
        cog.all_guilds = True
        cog.answer = AsyncMock(return_value="@everyone Example answer")
        cog.plan = AsyncMock()
        i = SimpleNamespace(
            guild=SimpleNamespace(id=123, owner_id=456),
            user=SimpleNamespace(id=789),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        await cog.ask.callback(cog, i, "hello")
        cog.answer.assert_not_awaited()
        cfg["ai_enabled"] = True
        self.assertTrue(cog.chat_authorized(i))
        self.assertFalse(cog.authorized(i))
        await cog.ask.callback(cog, i, "create a role")
        cog.answer.assert_awaited_once_with("create a role")
        cog.plan.assert_not_awaited()
        i.followup.send.assert_awaited_once()
        self.assertTrue(i.followup.send.await_args.kwargs["ephemeral"])
        self.assertFalse(i.followup.send.await_args.kwargs["allowed_mentions"].everyone)

    async def test_public_ai_quota_preserves_admin_capacity(self):
        from ai_agent import AIAgent
        cog = AIAgent(SimpleNamespace())
        i = SimpleNamespace(user=SimpleNamespace(id=10))
        for n in range(3):
            self.assertTrue(cog.reserve_chat(i, 1000 + n * 11))
        self.assertFalse(cog.reserve_chat(i, 1044))
        for n in range(17):
            i.user.id = n + 20
            self.assertTrue(cog.reserve_chat(i, 1100 + n * 11))
        i.user.id = 99
        self.assertFalse(cog.reserve_chat(i, 1500))
        self.assertEqual(len(cog.quota), 20)
        self.assertTrue(cog.reserve_chat(i, 90000))

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


    async def test_voice_welcome_permission_policy(self):
        from voice_welcome import can_manage_voice_welcome
        guild = SimpleNamespace(owner_id=10)
        for user_id, permissions, allowed in [
            (10, discord.Permissions.none(), True),
            (20, discord.Permissions(administrator=True), True),
            (20, discord.Permissions(manage_guild=True), False),
            (20, discord.Permissions.none(), False),
        ]:
            i = SimpleNamespace(guild=guild, user=SimpleNamespace(id=user_id), permissions=permissions)
            self.assertEqual(can_manage_voice_welcome(i), allowed)
        self.assertFalse(can_manage_voice_welcome(SimpleNamespace(guild=None)))

    async def test_non_admin_denied_all_voice_welcome_mutations(self):
        from voice_welcome import VoiceWelcome
        cog = VoiceWelcome(SimpleNamespace())
        for command, args in [(cog.setup, (None, True)), (cog.off, ()),
                              (cog.upload_audio, (None,)), (cog.reset_audio, ())]:
            i = SimpleNamespace(
                guild=SimpleNamespace(owner_id=10),
                user=SimpleNamespace(id=20), permissions=discord.Permissions.none(),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            await command.callback(cog, i, *args)
            i.response.send_message.assert_awaited_once_with(
                "Server owner or Administrator only.", ephemeral=True)

    async def test_administrator_can_preview_voice_welcome(self):
        from voice_welcome import VoiceWelcome
        from unittest.mock import Mock
        cog = VoiceWelcome(SimpleNamespace(store=SimpleNamespace(
            config=lambda _: {"voice_creator": 99}, update=Mock())))
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 123
        channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, connect=True, speak=True)
        i = SimpleNamespace(
            guild=SimpleNamespace(owner_id=10, me=object()), guild_id=456,
            user=SimpleNamespace(id=20), permissions=discord.Permissions(administrator=True),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await cog.setup.callback(cog, i, channel, False)
        self.assertIn("Preview:", i.response.send_message.await_args.args[0])
        cog.bot.store.update.assert_not_called()


    async def test_long_welcome_upload_uses_full_duration_and_guild_file(self):
        from voice_welcome import VoiceWelcome, MAX_AUDIO, MAX_WELCOME_SECONDS, PLAYBACK_TIMEOUT
        from unittest.mock import patch
        from pathlib import Path
        import tempfile
        self.assertEqual(MAX_WELCOME_SECONDS, 300)
        self.assertGreaterEqual(PLAYBACK_TIMEOUT, MAX_WELCOME_SECONDS + 10)
        self.assertEqual(MAX_AUDIO, 64_000_000)
        cog = VoiceWelcome(SimpleNamespace())
        with tempfile.TemporaryDirectory() as root:
            cog.audio = Path(root) / "welcome.mp3"
            i = SimpleNamespace(
                guild=SimpleNamespace(owner_id=1), guild_id=123,
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()))
            file = SimpleNamespace(filename="intro.mp3", size=13_000_000,
                                   read=AsyncMock(return_value=b"x" * 13_000_000))
            async def convert(*args, **kwargs):
                self.assertEqual(args[args.index("-t") + 1], "300")
                Path(args[-1]).write_bytes(b"converted audio")
                return SimpleNamespace(wait=AsyncMock(), returncode=0)
            with patch("voice_welcome.asyncio.create_subprocess_exec", side_effect=convert) as process:
                await cog.upload_audio.callback(cog, i, file)
                process.assert_awaited_once()
            self.assertEqual(cog.custom_audio(123).read_bytes(), b"converted audio")
            self.assertFalse(cog.custom_audio(456).exists())
            self.assertIn("5 minutes", i.followup.send.await_args.args[0])


    async def test_off_interrupts_own_long_playback_before_lock(self):
        import asyncio
        from collections import defaultdict
        from unittest.mock import Mock
        from voice_welcome import VoiceWelcome
        store = SimpleNamespace(update=Mock())
        locks = defaultdict(asyncio.Lock)
        cog = VoiceWelcome(SimpleNamespace(store=store, locks=locks))
        client = Mock()
        client.is_playing.return_value = True
        client.disconnect = AsyncMock()
        guild = SimpleNamespace(owner_id=1, voice_client=client)
        cog.clients[123] = client
        i = SimpleNamespace(guild=guild, guild_id=123,
            user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        lock = locks[("voice_welcome", 123)]
        await lock.acquire()
        task = asyncio.create_task(cog.off.callback(cog, i))
        try:
            await asyncio.sleep(0)
            client.stop.assert_called_once()
            store.update.assert_called_with(123, {"voice_welcome_channel": 0})
        finally:
            lock.release()
            await task
        client.disconnect.assert_awaited_once_with(force=True)


class DashboardV3Tests(unittest.TestCase):
    def test_voice_status_is_actual_and_permission_scoped(self):
        from web_dashboard_v2 import DashboardV2
        from unittest.mock import Mock
        channel = SimpleNamespace(id=123, name="Private welcome")
        voice = Mock()
        voice.is_connected.return_value = True
        voice.channel = channel
        guild = SimpleNamespace(get_channel=lambda cid: channel if cid == 123 else None, voice_client=voice)
        cfg = {"voice_welcome_channel": 123}
        self.assertTrue(DashboardV2.voice_status(guild, cfg, True)["connected"])
        self.assertEqual(DashboardV2.voice_status(guild, cfg, True)["channel_name"], channel.name)
        self.assertIsNone(DashboardV2.voice_status(guild, cfg, False)["channel_name"])
        voice.channel = SimpleNamespace(id=456)
        self.assertFalse(DashboardV2.voice_status(guild, cfg, True)["connected"])
        self.assertFalse(DashboardV2.voice_status(guild, {}, True)["configured"])
        self.assertFalse(DashboardV2.voice_status(guild, {"voice_welcome_channel": 789}, True)["channel_available"])


class AIContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_memory_isolated_reset_and_default_stateless(self):
        from ai_agent import AIAgent
        cog = AIAgent(SimpleNamespace(store=SimpleNamespace(config=lambda _: {"ai_enabled": True})))
        cog.key = "test-not-a-real-key"
        cog.all_guilds = True
        cog.reserve_chat = lambda *args: True
        cog.answer = AsyncMock(return_value="Example answer")
        def member(guild, user):
            return SimpleNamespace(guild=SimpleNamespace(id=guild, owner_id=99),
                user=SimpleNamespace(id=user),
                response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()))
        a, b, c = member(1, 2), member(1, 3), member(4, 2)
        try:
            await cog.ask.callback(cog, a, "First", True)
            self.assertIn((1, 2), cog.memory)
            await cog.ask.callback(cog, b, "Other member", True)
            cog.answer.assert_awaited_with("Other member")
            await cog.ask.callback(cog, c, "Other server", True)
            cog.answer.assert_awaited_with("Other server")
            await cog.ask.callback(cog, a, "Follow-up", True)
            cog.answer.assert_awaited_with("Follow-up", ("First", "Example answer"))
            await cog.ai_clear.callback(cog, a)
            self.assertNotIn((1, 2), cog.memory)
            self.assertIn((1, 3), cog.memory)
            await cog.ask.callback(cog, b, "Stateless")
            cog.answer.assert_awaited_with("Stateless")
            self.assertNotIn((1, 3), cog.memory)
        finally:
            await cog.cog_unload()
        self.assertFalse(cog.memory)

    async def test_reset_during_pending_request_prevents_repopulation(self):
        from ai_agent import AIAgent
        cog = AIAgent(SimpleNamespace())
        key, token = (1, 2), object()
        cog.pending_memory[key] = token
        cog.clear_memory(key)
        cog.remember_exchange(key, token, "Question", "Answer")
        self.assertNotIn(key, cog.memory)

    async def test_provider_input_only_includes_opted_in_exchange(self):
        from ai_agent import AIAgent
        cog = AIAgent(SimpleNamespace())
        cog.response_text = AsyncMock(return_value="Answer")
        await cog.answer("Question")
        self.assertEqual(cog.response_text.await_args.args[0]["input"], "Question")
        await cog.answer("Follow-up", ("Previous", "Reply"))
        payload = cog.response_text.await_args.args[0]
        self.assertFalse(payload["store"])
        self.assertEqual([x["role"] for x in payload["input"]], ["user", "assistant", "user"])
        self.assertEqual(payload["input"][-1]["content"], "Follow-up")


class DashboardAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_endpoint_denies_nonadministrator(self):
        from aiohttp import web
        from web_dashboard_v2 import DashboardV2
        dashboard = DashboardV2(SimpleNamespace())
        dashboard._guild = AsyncMock(return_value=(
            {"sid": "session", "actor": "9", "setup_guilds": set()},
            SimpleNamespace(id=123)))
        with self.assertRaises(web.HTTPForbidden):
            await dashboard.voice_upload(SimpleNamespace())
        with self.assertRaises(web.HTTPForbidden):
            await dashboard.voice_reset(SimpleNamespace())

    async def test_voice_upload_is_guild_scoped_and_rechecks_before_commit(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from aiohttp import web
        from web_dashboard_v2 import DashboardV2
        from voice_welcome import VoiceWelcome
        store = SimpleNamespace(event=Mock())
        bot = SimpleNamespace(store=store)
        cog = VoiceWelcome(bot)
        dashboard = DashboardV2(bot)
        guild = SimpleNamespace(id=123)
        dashboard._voice_access = AsyncMock(return_value=({"actor": "9"}, guild, cog))
        dashboard._recheck_voice_access = AsyncMock()
        async def chunks(size):
            yield b"test"
        async def convert(source, output, fmt):
            self.assertEqual(source.read_bytes(), b"test")
            self.assertEqual(fmt, "mp3")
            output.write_bytes(b"converted")
        cog.convert_audio = AsyncMock(side_effect=convert)
        def request():
            return SimpleNamespace(headers={"X-Audio-Extension": ".mp3", "X-Audio-Consent": "yes"},
                content_type="application/octet-stream", content_length=4,
                content=SimpleNamespace(iter_chunked=chunks))
        with tempfile.TemporaryDirectory() as root:
            cog.audio = Path(root) / "default.mp3"
            cog.custom_audio(123).write_bytes(b"old")
            cog.custom_audio(456).write_bytes(b"other")
            dashboard._recheck_voice_access.side_effect = web.HTTPForbidden()
            with self.assertRaises(web.HTTPForbidden):
                await dashboard.voice_upload(request())
            self.assertEqual(cog.custom_audio(123).read_bytes(), b"old")
            dashboard._recheck_voice_access.side_effect = None
            result = await dashboard.voice_upload(request())
            self.assertEqual(result.status, 200)
            self.assertEqual(cog.custom_audio(123).read_bytes(), b"converted")
            self.assertEqual(cog.custom_audio(456).read_bytes(), b"other")
            invalid = request()
            invalid.content_length = 64_000_001
            with self.assertRaises(web.HTTPBadRequest):
                await dashboard.voice_upload(invalid)
            self.assertEqual(cog.custom_audio(123).read_bytes(), b"converted")
            await dashboard.voice_reset(request())
            self.assertFalse(cog.custom_audio(123).exists())
            self.assertTrue(cog.custom_audio(456).exists())


if __name__ == "__main__":
    unittest.main()
