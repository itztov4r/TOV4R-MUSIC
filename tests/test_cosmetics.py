import unittest
import tempfile
from pathlib import Path
from store import Store
from cosmetics import CATALOG, STARTER_CREDITS

class CosmeticsTest(unittest.TestCase):
    def test_wallet_purchase_equip_isolation_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db.sqlite'
            s=Store(path)
            self.assertEqual(s.cosmetic_profile(1,2),{'theme':'','badge':''})
            self.assertEqual(s.cosmetic_account(1,2)['credits'],STARTER_CREDITS)
            self.assertFalse(s.cosmetic_equip(1,2,'theme','ruby'))
            self.assertEqual(s.cosmetic_buy(1,2,'ruby'),'purchased')
            self.assertEqual(s.cosmetic_buy(1,2,'ruby'),'already_owned')
            self.assertEqual(s.cosmetic_account(1,2)['credits'],STARTER_CREDITS-100)
            self.assertTrue(s.cosmetic_equip(1,2,'theme','ruby'))
            self.assertEqual(s.cosmetic_account(2,2)['credits'],STARTER_CREDITS)
            self.assertEqual(s.cosmetic_account(1,3)['items'],[])
            with self.assertRaises(ValueError): s.cosmetic_buy(1,2,'unknown')
            with self.assertRaises(ValueError): s.cosmetic_equip(1,2,'badge','ruby')
            s.db.close()
            s=Store(path)
            self.assertEqual(s.cosmetic_profile(1,2)['theme'],'ruby')
            for k in CATALOG: s.cosmetic_buy(1,2,k)
            self.assertEqual(s.cosmetic_account(1,2)['credits'],0)
            self.assertTrue(s.cosmetic_equip(1,2,'theme',''))
            self.assertEqual(s.cosmetic_profile(1,2)['theme'],'')
            s.db.close()
