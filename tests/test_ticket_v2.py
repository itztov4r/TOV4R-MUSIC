import tempfile
import unittest
from pathlib import Path
from store import Store

class TicketV2Test(unittest.TestCase):
    def test_kind_claim_and_guild_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'db.sqlite')
            s.run('INSERT INTO tickets(channel,guild,owner) VALUES(?,?,?)',(11,22,33))
            s.set_ticket_kind(99,11,'report')
            self.assertIsNone(s.one('SELECT * FROM ticket_details'))
            s.set_ticket_kind(22,11,'report')
            self.assertEqual(s.one('SELECT kind FROM ticket_details')['kind'],'report')
            self.assertIsNone(s.claim_ticket(99,11,44))
            self.assertEqual(s.claim_ticket(22,11,44),44)
            self.assertEqual(s.claim_ticket(22,11,55),44)
            self.assertTrue(s.queue_ticket_close(22,11,100))
            self.assertIsNone(s.claim_ticket(22,11,66))
            s.run('INSERT INTO tickets(channel,guild,owner) VALUES(?,?,?)',(12,22,33))
            self.assertEqual(s.claim_ticket(22,12,55),55)
            self.assertEqual(s.one('SELECT kind FROM ticket_details WHERE channel=12')['kind'],'support')
            s.db.close()
    def test_persistent_component_ids(self):
        from types import SimpleNamespace
        from bot import TicketPanel, CloseTicket
        import asyncio
        async def check():
            bot=SimpleNamespace()
            self.assertEqual({x.custom_id for x in TicketPanel(bot).children},{'ticket:open','ticket:type:v2'})
            self.assertEqual({x.custom_id for x in CloseTicket(bot).children},{'ticket:close','ticket:claim:v2'})
        asyncio.run(check())
