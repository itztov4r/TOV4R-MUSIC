import unittest
from store import Store
from ticket_premium import TicketPremium

class TicketV8Test(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:')
        self.s.run('INSERT INTO tickets(channel,guild,owner) VALUES(11,22,33)')
    def tearDown(self): self.s.db.close()
    def test_priority(self):
        self.assertFalse(self.s.set_ticket_priority(99,11,'high'))
        self.assertTrue(self.s.set_ticket_priority(22,11,'high'))
        with self.assertRaises(ValueError): self.s.set_ticket_priority(22,11,'invalid')
        self.s.queue_ticket_close(22,11,100)
        self.assertFalse(self.s.set_ticket_priority(22,11,'low'))
    def test_reopen(self):
        self.assertTrue(self.s.queue_ticket_close(22,11,100))
        self.assertFalse(self.s.reopen_ticket(99,11,90))
        self.assertFalse(self.s.reopen_ticket(22,11,100))
        self.assertTrue(self.s.reopen_ticket(22,11,99))
        self.assertIsNone(self.s.one('SELECT * FROM ticket_deletions'))
        self.s.queue_ticket_close(22,11,200)
        self.s.run('INSERT INTO tickets(channel,guild,owner) VALUES(12,22,33)')
        self.assertFalse(self.s.reopen_ticket(22,11,190))
    def test_commands(self):
        self.assertEqual({c.name for c in TicketPremium.__cog_app_commands__},{'ticket_add','ticket_remove','ticket_rename','ticket_priority','ticket_control','ticket_history','ticket_stats'})
