import tempfile
import unittest
from pathlib import Path
from store import Store

class VoicePreferencesTest(unittest.TestCase):
    def test_saved_preferences_are_scoped_and_persistent(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db.sqlite'
            s=Store(path)
            s.save_voice_preferences(1,2,'My room',5)
            self.assertIsNone(s.voice_preferences(9,2))
            self.assertIsNone(s.voice_preferences(1,9))
            for name,limit in [('',5),('x'*101,5),('ok',100),('ok',-1)]:
                with self.assertRaises(ValueError):
                    s.save_voice_preferences(1,2,name,limit)
            s.db.close()
            s=Store(path)
            self.assertEqual(s.voice_preferences(1,2),{'name':'My room','user_limit':5})
            s.clear_voice_preferences(1,2)
            self.assertIsNone(s.voice_preferences(1,2))
            s.db.close()
    def test_panel_component_ids(self):
        import asyncio
        from types import SimpleNamespace
        from bot import VoicePanel
        async def check():
            view=VoicePanel(SimpleNamespace())
            ids={x.custom_id for x in view.children}
            self.assertEqual(len(ids),13)
            self.assertTrue({'voice:lock','voice:claim','voice:save','voice:info','voice:forget'}<=ids)
            self.assertTrue(view.is_persistent())
        asyncio.run(check())
