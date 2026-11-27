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


if __name__ == "__main__":
    unittest.main()
