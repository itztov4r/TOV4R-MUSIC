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


if __name__ == "__main__":
    unittest.main()
