import unittest
from voice_ai import valid_question

class TestVoiceAI(unittest.TestCase):
    def test_valid_input(self):
        self.assertTrue(valid_question("Salam", None))
        self.assertTrue(valid_question("", object()))
    def test_reject_invalid_input(self):
        self.assertFalse(valid_question("", None))
        self.assertFalse(valid_question("Hi", object()))
        self.assertFalse(valid_question(" ", None))
        self.assertFalse(valid_question("a" * 501, None))

    def test_multi_guild_owner_only(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from voice_ai import VoiceAI
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test", "TOV4R_AI_GUILD_ID": "123", "TOV4R_AI_ALL_GUILDS": "1"}, clear=True):
            cog = VoiceAI(NS())
            for gid in (123, 456):
                self.assertTrue(cog.permitted(NS(guild=NS(id=gid, owner_id=1), user=NS(id=1))))
                self.assertFalse(cog.permitted(NS(guild=NS(id=gid, owner_id=1), user=NS(id=2))))
            self.assertFalse(cog.permitted(NS(guild=None, user=NS(id=1))))
            cog.all_guilds = False
            self.assertTrue(cog.permitted(NS(guild=NS(id=123, owner_id=1), user=NS(id=1))))
            self.assertFalse(cog.permitted(NS(guild=NS(id=456, owner_id=1), user=NS(id=1))))
            cog.key = ""
            self.assertFalse(cog.permitted(NS(guild=NS(id=123, owner_id=1), user=NS(id=1))))
