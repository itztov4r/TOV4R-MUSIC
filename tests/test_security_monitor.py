import unittest
from security_monitor import SecurityMonitor

class SecurityTest(unittest.TestCase):
    def test_join_burst_isolation_and_expiry(self):
        m=SecurityMonitor()
        for x in range(7): self.assertFalse(m.observe(1,'join',100+x))
        self.assertTrue(m.observe(1,'join',108))
        self.assertTrue(m.paused(1,109))
        self.assertFalse(m.paused(2,109))
        self.assertFalse(m.observe(1,'join',110))
        self.assertFalse(m.paused(1,231))
        m.prune(300)
        self.assertFalse(m.events)
    def test_destructive_alert_and_clear(self):
        m=SecurityMonitor()
        for x in range(4): self.assertFalse(m.observe(1,'destructive',100+x))
        self.assertTrue(m.observe(1,'destructive',104))
        self.assertFalse(m.observe(2,'destructive',104))
        m.clear(1)
        self.assertFalse(m.observe(1,'destructive',105))
    def test_bot_alert_throttle(self):
        m=SecurityMonitor()
        self.assertTrue(m.observe(1,'bot',100))
        self.assertFalse(m.observe(1,'bot',101))
        self.assertTrue(m.observe(2,'bot',101))
        self.assertTrue(m.observe(1,'bot',161))
