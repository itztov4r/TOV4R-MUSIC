import unittest
from store import Store
from community_premium import CommunityPremium, VerifyView, MultiRoleView, verification_role
from types import SimpleNamespace

class CommunityPremiumTest(unittest.IsolatedAsyncioTestCase):
    async def test_persistence(self):
        s=Store(':memory:')
        bot=SimpleNamespace(store=s)
        s.update(1,{'welcome_theme':'ocean'})
        self.assertEqual(s.config(2)['welcome_theme'],'ruby')
        with self.assertRaises(ValueError): s.update(1,{'welcome_theme':'invalid'})
        s.run("INSERT INTO template_items VALUES(1,'__verification__','role',123,'role')")
        self.assertTrue(verification_role(bot,1,123))
        self.assertFalse(verification_role(bot,2,123))
        self.assertEqual(VerifyView(bot).children[0].custom_id,'community:verify:v9')
        self.assertEqual(MultiRoleView(bot,[(123,'Test')]).children[0].custom_id,'community:roles:v9')
        self.assertEqual({c.name for c in CommunityPremium.__cog_app_commands__},{'welcome_setup','verify_setup','roles_menu'})
        s.db.close()
