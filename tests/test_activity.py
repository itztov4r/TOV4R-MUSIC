import tempfile
import unittest
from pathlib import Path
from store import Store

class ActivityTest(unittest.TestCase):
    def test_activity_isolation_and_existing_xp(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'db.sqlite')
            self.assertFalse(s.config(1)['voice_xp'])
            s.award(1,2,60,now=100)
            s.count_message(1,2)
            self.assertEqual(s.award_voice(1,2,30,now=100),0)
            self.assertEqual(s.award_voice(1,2,30,now=130),5)
            self.assertEqual(s.one('SELECT points,last FROM xp WHERE guild=1 AND user=2'),{'points':25,'last':100})
            self.assertIsNone(s.one('SELECT * FROM activity WHERE guild=9'))
            for _ in range(60): s.award_voice(1,2,60,now=200)
            self.assertEqual(s.one('SELECT points FROM xp WHERE guild=1 AND user=2')['points'],320)
            self.assertEqual(s.award_voice(1,2,60,now=86400),5)
            self.assertEqual(s.one('SELECT messages FROM activity WHERE guild=1 AND user=2')['messages'],1)
            s.db.close()
