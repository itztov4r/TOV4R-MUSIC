import unittest
from store import Store
from automod import content_reason,incident,escalation,AutoMod,AutoModCommands
from types import SimpleNamespace
class AutoModTest(unittest.TestCase):
    def test_filters(self):
        s=Store(":memory:")
        cfg=s.config(1)
        self.assertIsNone(content_reason(cfg,"https://example.org"))
        cfg["anti_links"]=True
        self.assertEqual(content_reason(cfg,"https://example.org"),"External link")
        cfg["blocked_words"]="blocked"
        self.assertEqual(content_reason(cfg,"BLOCKED"),"Blocked word")
        cfg["security_enabled"]=True
        self.assertEqual(content_reason(cfg,"hello",8),"Mass mentions")
        s.db.close()
    def test_scope_cooldown_dismissal(self):
        s=Store(":memory:")
        iid,n=incident(s,1,2,3,4,"Message flood",100)
        self.assertEqual(n,1)
        self.assertIsNone(incident(s,1,2,3,5,"Message flood",110))
        self.assertIsNone(incident(s,1,2,3,4,"Message flood",140))
        self.assertEqual(incident(s,2,2,3,4,"Message flood",140)[1],1)
        s.run("UPDATE automod_incidents SET dismissed=1 WHERE id=?",(iid,))
        self.assertEqual(incident(s,1,2,3,6,"Message flood",140)[1],1)
        self.assertEqual([escalation(x) for x in (1,2,3,5)],[0,0,1,5])
        s.db.close()
    def test_flood_and_repetition(self):
        s=Store(":memory:")
        a=AutoMod(SimpleNamespace())
        m=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=2),content="Repeated long message",raw_mentions=[],raw_role_mentions=[],mention_everyone=False)
        for t in range(3): self.assertIsNone(a.reason(m,s.config(1),now=t))
        self.assertEqual(a.reason(m,s.config(1),now=3),"Repeated messages")
        self.assertIsNone(a.reason(m,s.config(1),edited=True,now=4))
        self.assertEqual({c.name for c in AutoModCommands.__cog_app_commands__},{"mod_history","mod_dismiss"})
        s.db.close()
