import unittest
from store import Store
from community_social import cast_vote,birthday_date,CommunitySocial,SocialView
from types import SimpleNamespace

class SocialTest(unittest.IsolatedAsyncioTestCase):
    async def test_vote_isolation_and_toggle(self):
        s=Store(':memory:')
        s.run("INSERT INTO community_posts VALUES(1,2,3,4,'poll','Title','Body','[\"Yes\",\"No\"]',100,0)")
        self.assertFalse(cast_vote(s,99,1,5,0,50))
        self.assertTrue(cast_vote(s,2,1,5,0,50))
        self.assertTrue(cast_vote(s,2,1,5,1,50))
        self.assertEqual(s.one("SELECT choice FROM community_votes")["choice"],1)
        self.assertTrue(cast_vote(s,2,1,5,1,50))
        self.assertIsNone(s.one("SELECT * FROM community_votes"))
        self.assertFalse(cast_vote(s,2,1,5,0,100))
        self.assertFalse(cast_vote(s,2,1,5,9,50))
        self.assertEqual([x.custom_id for x in SocialView(SimpleNamespace(),["Yes","No"]).children],["social:v9:0","social:v9:1"])
        self.assertEqual(birthday_date(2,29).day,29)
        with self.assertRaises(ValueError): birthday_date(2,30)
        self.assertEqual({c.name for c in CommunitySocial.__cog_app_commands__},{"suggest","poll","event","starboard","community_close","birthday","counters","custom_embed"})
        s.db.close()
