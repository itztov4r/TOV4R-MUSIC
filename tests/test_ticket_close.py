import tempfile
import unittest
from pathlib import Path
from store import Store

class TicketCloseTest(unittest.TestCase):
    def test_queue_is_guild_scoped_and_persistent(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"test.db"
            s=Store(p)
            s.run("INSERT INTO tickets(channel,guild,owner) VALUES(?,?,?)",(11,22,33))
            self.assertFalse(s.queue_ticket_close(99,11,100))
            self.assertTrue(s.queue_ticket_close(22,11,100))
            self.assertFalse(s.queue_ticket_close(22,11,200))
            s.db.close()
            s=Store(p)
            self.assertEqual(s.one("SELECT due FROM ticket_deletions WHERE guild=22 AND channel=11")["due"],100)
            self.assertEqual(s.one("SELECT closed FROM tickets WHERE channel=11")["closed"],1)
            s.db.close()
