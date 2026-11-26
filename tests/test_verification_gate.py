import unittest
from types import SimpleNamespace
import discord
from store import Store
from verification_gate import normalize_overwrite, active, remember, admitted

class VerificationGateTest(unittest.TestCase):
    def test_normalization_preserves_other_permissions(self):
        ow=discord.PermissionOverwrite(view_channel=True,send_messages=False,connect=True)
        self.assertTrue(normalize_overwrite(ow))
        self.assertIsNone(ow.view_channel)
        self.assertFalse(ow.send_messages)
        self.assertTrue(ow.connect)
        private=discord.PermissionOverwrite(view_channel=False)
        self.assertFalse(normalize_overwrite(private))
        self.assertFalse(private.view_channel)
    def test_gate_guild_scope_and_existing_access(self):
        s=Store(":memory:")
        bot=SimpleNamespace(store=s)
        role=SimpleNamespace(id=10)
        guild=SimpleNamespace(id=1,get_role=lambda n:role if n==10 else None)
        member=SimpleNamespace(guild=guild,roles=[],guild_permissions=discord.Permissions.none())
        self.assertTrue(admitted(bot,member))
        remember(bot,1,"access",10,"role"); remember(bot,1,"active",1)
        self.assertTrue(active(bot,1)); self.assertFalse(active(bot,2))
        self.assertFalse(admitted(bot,member))
        member.roles=[role]
        self.assertTrue(admitted(bot,member))
        member.roles=[]; member.guild_permissions=discord.Permissions(administrator=True)
        self.assertTrue(admitted(bot,member))
        s.db.close()
