import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from aiohttp import ClientSession
from store import Store, level, validate_config
from bot import Community, Features, VoicePanel, TicketPanel, CloseTicket, GiveawayPanel, RolePanel, owned_room, safe_role
from dashboard import start_dashboard

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'db.sqlite'; self.s=Store(self.path)
    def tearDown(self): self.s.db.close(); self.temp.cleanup()
    def test_xp_cooldown_and_level(self):
        for n in range(5): self.s.award(1,2,60,now=100+n*60)
        self.assertEqual(self.s.one('SELECT points FROM xp')['points'],100)
        self.assertEqual(level(100),1)
        self.s.award(1,2,60,now=341)
        self.assertEqual(self.s.one('SELECT points FROM xp')['points'],100)
        self.s.award(9,2,60,now=341)
        self.assertEqual(self.s.one('SELECT points FROM xp WHERE guild=9')['points'],20)
    def test_config_persists_after_restart(self):
        self.s.update(1,{'voice_creator':'1556827796049764373','levels':False})
        self.s.db.close(); self.s=Store(self.path)
        self.assertEqual(self.s.config(1)['voice_creator'],1556827796049764373)
        self.assertFalse(self.s.config(1)['levels'])
        self.assertTrue(self.s.config(2)['levels'])
    def test_invalid_configuration_rejected(self):
        for config in ({'levels':'false'},{'xp_cooldown':0},{'evil':'x'},{'autorole':-1},{'welcome_text':[]},{'logs_channel':True}):
            with self.assertRaises(ValueError): validate_config(config)
    def test_one_open_ticket_per_member(self):
        import sqlite3
        self.s.run('INSERT INTO tickets(channel,guild,owner) VALUES(1,2,3)')
        with self.assertRaises(sqlite3.IntegrityError): self.s.run('INSERT INTO tickets(channel,guild,owner) VALUES(4,2,3)')
        self.s.run('UPDATE tickets SET closed=1 WHERE channel=1')
        self.s.run('INSERT INTO tickets(channel,guild,owner) VALUES(4,2,3)')
    def test_giveaway_entries_unique_and_persisted(self):
        self.s.run('INSERT INTO entries VALUES(1,2)'); self.s.run('INSERT OR IGNORE INTO entries VALUES(1,2)')
        self.s.db.close(); self.s=Store(self.path)
        self.assertEqual(len(self.s.all('SELECT * FROM entries')),1)

class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.s=Store(Path(self.temp.name)/'db'); self.b=Community(self.s)
    async def asyncTearDown(self):
        await self.b.close(); self.temp.cleanup()
    async def test_commands_registered_and_moderation_checked(self):
        await self.b.add_cog(Features(self.b))
        commands={c.name:c for c in self.b.tree.get_commands()}
        self.assertEqual(len(commands),22)
        for name in ('setup','kick','ban','purge','giveaway','configure','rolepanel'):
            self.assertTrue(commands[name].checks,name)
        # Ensure every command is explicitly guild-only on serialized payloads.
        for c in commands.values():
            self.assertTrue(c.guild_only,c.name)
    async def test_global_sync_without_server_id(self):
        self.b.tree.sync=AsyncMock()
        with patch.dict(os.environ,{'DISCORD_GUILD_ID':''}): await self.b.sync_public_commands()
        self.b.tree.sync.assert_awaited_once_with()
    async def test_global_sync_also_runs_with_legacy_server_id(self):
        self.b.tree.sync=AsyncMock()
        with patch.dict(os.environ,{'DISCORD_GUILD_ID':'1564283597173690380'}): await self.b.sync_public_commands()
        self.assertEqual(self.b.tree.sync.await_count,2)
        self.assertEqual(self.b.tree.sync.await_args_list[0].kwargs,{})
        self.assertEqual(self.b.tree.sync.await_args_list[1].kwargs['guild'].id,1564283597173690380)
    async def test_persistent_panels(self):
        for v in (VoicePanel(self.b),TicketPanel(self.b),CloseTicket(self.b),GiveawayPanel(self.b,42),RolePanel(self.b,[(8,'Gamer')])):
            self.assertTrue(v.is_persistent())
    async def test_non_owner_cannot_control_room(self):
        c=SimpleNamespace(id=9)
        member=unittest.mock.Mock(spec=discord.Member); member.id=3; member.voice=SimpleNamespace(channel=c)
        self.s.run('INSERT INTO rooms VALUES(9,1,2)')
        with self.assertRaises(ValueError): await owned_room(self.b,SimpleNamespace(user=member))
        member.id=2
        room,_=await owned_room(self.b,SimpleNamespace(user=member)); self.assertEqual(room.id,9)
    async def test_room_cleanup_empty_only(self):
        c=SimpleNamespace(id=9,members=[1],delete=AsyncMock())
        self.s.run('INSERT INTO rooms VALUES(9,1,2)')
        await self.b.clean_room(c); c.delete.assert_not_awaited()
        c.members=[]; await self.b.clean_room(c)
        c.delete.assert_awaited_once(); self.assertIsNone(self.s.one('SELECT * FROM rooms'))
    async def test_giveaway_finishes_once_with_distinct_winners(self):
        m=SimpleNamespace(edit=AsyncMock()); c=SimpleNamespace(fetch_message=AsyncMock(return_value=m))
        g=SimpleNamespace(get_channel=lambda _:c,get_member=lambda uid:object(),id=1)
        self.b.get_guild=lambda _:g; self.b.audit=AsyncMock()
        self.s.run("INSERT INTO giveaways(message,guild,channel,prize,ends,count) VALUES(7,1,8,'Prize',0,2)")
        for uid in range(3): self.s.run('INSERT INTO entries VALUES(7,?)',(uid,))
        await asyncio.gather(self.b.finish_giveaway(7),self.b.finish_giveaway(7))
        r=self.s.one('SELECT * FROM giveaways'); winners=json.loads(r['winners'])
        self.assertEqual(r['state'],'done'); self.assertEqual(len(set(winners)),2); m.edit.assert_awaited_once()
    async def test_giveaway_retries_same_draw_after_failed_delivery(self):
        m=SimpleNamespace(edit=AsyncMock()); c=SimpleNamespace(fetch_message=AsyncMock(side_effect=RuntimeError('offline')))
        g=SimpleNamespace(get_channel=lambda _:c,get_member=lambda uid:object(),id=1)
        self.b.get_guild=lambda _:g; self.b.audit=AsyncMock()
        self.s.run("INSERT INTO giveaways(message,guild,channel,prize,ends,count) VALUES(7,1,8,'Prize',0,1)")
        self.s.run('INSERT INTO entries VALUES(7,42)')
        with self.assertRaises(RuntimeError): await self.b.finish_giveaway(7)
        r=self.s.one('SELECT * FROM giveaways'); self.assertEqual(r['state'],'drawn')
        c.fetch_message=AsyncMock(return_value=m)
        await self.b.finish_giveaway(7)
        self.assertEqual(self.s.one('SELECT * FROM giveaways')['winners'],r['winners'])
    async def test_dashboard_auth_and_real_state(self):
        with patch.dict(os.environ,{'DASHBOARD_KEY':'x'*40,'DASHBOARD_HOST':'127.0.0.1','PORT':'0'}):
            runner=await start_dashboard(self.b)
        try:
            port=runner.addresses[0][1]; url=f'http://127.0.0.1:{port}'
            async with ClientSession() as session:
                async with session.get(url+'/api/state') as r: self.assertEqual(r.status,401)
                async with session.get(url+'/api/state',headers={'Authorization':'Bearer '+'x'*40}) as r:
                    self.assertEqual(r.status,200); data=await r.json(); self.assertEqual(data['guilds'],[])
                    self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
                for path in ('/','/assets/app.js','/assets/style.css'):
                    async with session.get(url+path) as r: self.assertEqual(r.status,200)
                async with session.put(url+'/api/guild/1',data='{}',headers={'Authorization':'Bearer '+'x'*40}) as r: self.assertEqual(r.status,415)
        finally: await runner.cleanup()

class RoleTests(unittest.TestCase):
    def test_dangerous_roles_denied(self):
        class Role:
            managed=False
            def is_default(self): return False
            def __lt__(self,other): return True
        r=Role(); g=SimpleNamespace(me=SimpleNamespace(top_role=object()))
        r.permissions=discord.Permissions(administrator=True); self.assertFalse(safe_role(r,g))
        r.permissions=discord.Permissions(manage_roles=True); self.assertFalse(safe_role(r,g))
        r.permissions=discord.Permissions(view_channel=True); self.assertTrue(safe_role(r,g))

if __name__=='__main__': unittest.main()
