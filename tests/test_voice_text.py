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
    async def test_bitrate_respects_server_limit(self):
        self.ctx.guild.bitrate_limit=96000
        for arg in ('7','97','abc'):
            with self.assertRaises(commands.BadArgument): await self.call('bitrate',arg)
        await self.call('bitrate','96')
        self.assertEqual(self.room.edit.await_args.kwargs['bitrate'],96000)
    async def test_delete_requires_same_room_confirmation(self):
        self.room.delete=AsyncMock(); self.ctx.channel=SimpleNamespace(id=20)
        await self.call('delete'); await self.call('delete','confirm 999')
        self.room.delete.assert_not_awaited()
        self.assertIsNotNone(self.s.one('SELECT * FROM rooms'))
        await self.call('delete','confirm 10')
        self.room.delete.assert_awaited_once()
        self.assertIsNone(self.s.one('SELECT * FROM rooms'))
    async def test_non_owner_cannot_delete_or_reset(self):
        self.author.id=3
        for action in ('delete','reset','chat','bitrate'):
            with self.assertRaises(commands.BadArgument): await self.call(action,'confirm 10')
    async def test_region_auto(self):
        await self.call('region','auto')
        self.assertIsNone(self.room.edit.await_args.kwargs['rtc_region'])
    async def test_chat_preserves_other_permissions(self):
        self.ctx.guild.me=object()
        self.room.overwrites_for=lambda _:discord.PermissionOverwrite(connect=False)
        await self.call('chat','off')
        ow=self.room.set_permissions.await_args.kwargs['overwrite']
        self.assertFalse(ow.connect); self.assertFalse(ow.send_messages)
    async def test_unreject_preserves_other_overwrites(self):
        from unittest.mock import patch
        target=SimpleNamespace(id=8,bot=False)
        self.room.overwrites_for=lambda _:discord.PermissionOverwrite(connect=False,send_messages=False)
        with patch.object(commands.MemberConverter,'convert',AsyncMock(return_value=target)):
            await self.call('unreject','8')
        ow=self.room.set_permissions.await_args.kwargs['overwrite']
        self.assertIsNone(ow.connect); self.assertFalse(ow.send_messages)
    async def test_members_is_readable_by_room_guest(self):
        self.author.id=3
        await self.call('members'); self.ctx.send.assert_awaited_once()
    async def test_reset_requires_confirmation(self):
        await self.call('reset'); self.room.edit.assert_not_awaited()
    async def test_reset_preserves_role_and_unrelated_member_permissions(self):
        role=Mock(spec=discord.Role); role.id=99
        everyone=Mock(spec=discord.Role); everyone.id=1
        member=Mock(spec=discord.Member); member.id=8
        owner=Mock(spec=discord.Member); owner.id=2
        bot_member=Mock(spec=discord.Member); bot_member.id=50
        self.bot._connection.user=bot_member
        self.ctx.guild.default_role=everyone; self.ctx.guild.bitrate_limit=96000
        role_ow=discord.PermissionOverwrite(manage_channels=True)
        self.room.overwrites={role:role_ow,everyone:discord.PermissionOverwrite(connect=False,send_messages=False),member:discord.PermissionOverwrite(connect=False,send_messages=False),owner:discord.PermissionOverwrite(connect=True)}
        await self.call('reset','confirm 10')
        opts=self.room.edit.await_args.kwargs; ows=opts['overwrites']
        self.assertIs(ows[role],role_ow)
        self.assertTrue(ows[everyone].connect)
        self.assertIsNone(ows[everyone].send_messages)
        self.assertIsNone(ows[member].connect)
        self.assertFalse(ows[member].send_messages)
        self.assertTrue(ows[owner].connect)
        self.assertEqual(opts['user_limit'],0)
