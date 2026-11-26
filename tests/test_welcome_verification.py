import unittest
from types import SimpleNamespace
import discord
from store import Store
from community_premium import welcome_name,basic_role

class Role:
    def __init__(self,id,permissions):
        self.id=id; self.permissions=permissions; self.managed=False
    def is_default(self): return False
    def __ge__(self,other): return self.id>=other.id

class WelcomeVerificationTest(unittest.TestCase):
    def test_welcome_name(self):
        for name in ("welcome","👋・bienvenue","tov4r-welcome","accueil"): self.assertTrue(welcome_name(name))
        for name in ("general","général","welcome-chat","general-welcome"): self.assertFalse(welcome_name(name))
    def test_verification_role_permissions(self):
        s=Store(":memory:")
        bot=SimpleNamespace(store=s)
        s.run("INSERT INTO template_items VALUES(1,'__verification__','role',10,'role')")
        role=Role(10,discord.Permissions(view_channel=True,send_messages=True))
        guild=SimpleNamespace(id=1,me=SimpleNamespace(guild_permissions=discord.Permissions(manage_roles=True),top_role=Role(20,discord.Permissions.none())),channels=[])
        self.assertTrue(basic_role(bot,guild,role,verification=True))
        role.permissions=discord.Permissions(administrator=True)
        self.assertFalse(basic_role(bot,guild,role,verification=True))
        role.permissions=discord.Permissions.none()
        s.update(1,{"logs_channel":99})
        guild.channels=[SimpleNamespace(id=99,overwrites_for=lambda role:discord.PermissionOverwrite(view_channel=True))]
        self.assertFalse(basic_role(bot,guild,role,verification=True))
        s.db.close()
