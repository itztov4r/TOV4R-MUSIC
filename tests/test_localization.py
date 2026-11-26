import unittest
from types import SimpleNamespace
from store import Store
from localization import tr, translate, STRINGS
from bot import TicketPanel, CloseTicket, VoicePanel

class LocalizationTests(unittest.TestCase):
    def test_language_isolation_and_validation(self):
        s=Store(":memory:")
        bot=SimpleNamespace(store=s)
        s.update(1,{"language":"fr"})
        s.update(2,{"language":"darija"})
        self.assertEqual(tr(bot,1,"profile"),"Profil du membre")
        self.assertEqual(tr(bot,2,"profile"),"Profil dyal membre")
        self.assertEqual(tr(bot,3,"profile"),"Member profile")
        self.assertEqual(translate("unknown","profile"),"Member profile")
        with self.assertRaises(ValueError): s.update(1,{"language":"bad"})
        self.assertTrue(all(len(v)==3 and all(v) for v in STRINGS.values()))
        s.db.close()

class PanelLocalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_component_ids_unchanged(self):
        s=Store(":memory:")
        bot=SimpleNamespace(store=s)
        s.update(1,{"language":"fr"})
        ticket=TicketPanel(bot,1)
        select=next(c for c in ticket.children if c.custom_id=="ticket:type:v2")
        self.assertEqual([x.value for x in select.options],["support","report","partnership"])
        self.assertEqual(select.options[0].label,"Assistance")
        self.assertEqual(next(c for c in ticket.children if c.custom_id=="ticket:open").label,"🎫 Ouvrir un ticket")
        self.assertEqual({c.custom_id for c in CloseTicket(bot,1).children},{"ticket:close","ticket:claim:v2"})
        self.assertEqual({c.custom_id for c in VoicePanel(bot,1).children},{c.custom_id for c in VoicePanel(bot).children})
        s.db.close()
