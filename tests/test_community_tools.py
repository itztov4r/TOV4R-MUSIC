import unittest
from types import SimpleNamespace
from store import Store
from community_tools import eligible, save_backup, read_backup, restore_settings

class CommunityToolsTest(unittest.TestCase):
    def setUp(self): self.s=Store(':memory:')
    def tearDown(self): self.s.db.close()
    def test_private_backup_restore_and_limit(self):
        self.s.update(1,{'levels':False,'support_role':99})
        bid=save_backup(self.s,1,{'roles':[],'channels':[]})
        self.s.update(1,{'levels':True,'support_role':55})
        with self.assertRaises(ValueError): read_backup(self.s,2,bid)
        restore_settings(self.s,1,bid)
        self.assertFalse(self.s.config(1)['levels'])
        self.assertEqual(self.s.config(1)['support_role'],55)
        for _ in range(6): save_backup(self.s,1,{})
        self.assertEqual(len(self.s.all('SELECT id FROM community_backups WHERE guild=1')),5)
    def test_eligibility(self):
        member=SimpleNamespace(id=2,bot=False,guild=SimpleNamespace(id=1),roles=[SimpleNamespace(id=5)])
        self.assertFalse(eligible(self.s,member,{'role_id':5,'min_level':1}))
        self.s.run('INSERT INTO xp VALUES(1,2,100,0)')
        self.assertTrue(eligible(self.s,member,{'role_id':5,'min_level':1}))
        self.assertFalse(eligible(self.s,member,{'role_id':6,'min_level':0}))
        member.bot=True
        self.assertFalse(eligible(self.s,member,{'role_id':0,'min_level':0}))
