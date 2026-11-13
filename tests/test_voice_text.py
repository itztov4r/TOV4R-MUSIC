import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import discord
from discord.ext import commands
from bot import Community
from store import Store
from voice_commands import VoiceCommands

class TextVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.s=Store(Path(self.tmp.name)/'db'); self.bot=Community(self.s)
        self.cog=VoiceCommands(self.bot); await self.bot.add_cog(self.cog)
        self.room=SimpleNamespace(id=10,name='Room',user_limit=0,members=[],set_permissions=AsyncMock(),edit=AsyncMock(),overwrites_for=lambda _:discord.PermissionOverwrite())
        self.author=SimpleNamespace(id=2,voice=SimpleNamespace(channel=self.room))
        self.ctx=SimpleNamespace(author=self.author,guild=SimpleNamespace(id=1,default_role=object(),get_member=lambda _:None),send=AsyncMock())
        self.s.run('INSERT INTO rooms VALUES(10,1,2)')
    async def asyncTearDown(self):
        await self.bot.close(); self.tmp.cleanup()
    async def call(self,a,arg=''):
        await self.cog.voice.callback(self.cog,self.ctx,a,argument=arg)
    async def test_non_owner_rejected(self):
        self.author.id=3
        with self.assertRaises(commands.BadArgument): await self.call('lock')
        self.room.set_permissions.assert_not_awaited()
    async def test_lock_and_unlock(self):
        await self.call('lock')
        self.assertFalse(self.room.set_permissions.await_args.kwargs['overwrite'].connect)
        await self.call('unlock')
        self.assertTrue(self.room.set_permissions.await_args.kwargs['overwrite'].connect)
    async def test_limit_bounds_and_rename_alias(self):
        for arg in ('-1','100','abc',''):
            with self.assertRaises(commands.BadArgument): await self.call('limit',arg)
        await self.call('limit','0'); self.assertEqual(self.room.edit.await_args.kwargs['user_limit'],0)
        await self.call('rename','جلسة صحاب'); self.assertEqual(self.room.edit.await_args.kwargs['name'],'جلسة صحاب')
    async def test_claim_denied_while_owner_present(self):
        self.room.members=[self.author]; self.ctx.author=SimpleNamespace(id=3,voice=SimpleNamespace(channel=self.room))
        with self.assertRaises(commands.BadArgument): await self.call('claim')
        self.assertEqual(self.s.one('SELECT owner FROM rooms')['owner'],2)
    async def test_claim_persists_owner(self):
        self.author.id=3; await self.call('claim')
        self.assertEqual(self.s.one('SELECT owner FROM rooms')['owner'],3)
    async def test_help_without_voice(self):
        self.author.voice=None; await self.call('help'); self.ctx.send.assert_awaited_once()
    async def test_on_message_routes_prefix_without_xp(self):
        member=Mock(spec=discord.Member);member.bot=False;member.guild_permissions=SimpleNamespace(manage_messages=True)
        self.bot.process_commands=AsyncMock()
        message=SimpleNamespace(guild=SimpleNamespace(id=1),author=member,content='.v lock')
        await self.bot.on_message(message)
        self.bot.process_commands.assert_awaited_once_with(message)
        self.assertEqual(self.s.all('SELECT * FROM xp'),[])
    async def test_command_registered(self):
        self.assertIsNotNone(self.bot.get_command('v'))
        self.assertIs(self.bot.get_command('v'),self.bot.get_command('voice'))
