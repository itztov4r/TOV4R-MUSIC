import unittest
from ai_agent import validate_plan

class TestAIPlan(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(validate_plan({"action":"create_channel","name":"Gaming Room"}), ("create_channel","gaming-room"))
        self.assertEqual(validate_plan({"action":"create_role","name":"VIP"}), ("create_role","VIP"))
        self.assertEqual(validate_plan({"action":"audit","name":""}), ("audit",""))
    def test_reject_unsafe(self):
        for plan in ({"action":"delete_channel","name":"test"},
                     {"action":"create_role","name":"Administrator"},
                     {"action":"create_channel","name":"@everyone"},
                     {"action":"create_role","name":"VIP","permissions":8},
                     {"action":"create_channel","name":"---"}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                validate_plan(plan)

    def test_multi_guild_owner_only(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from ai_agent import AIAgent
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test", "TOV4R_AI_GUILD_ID": "123", "TOV4R_AI_ALL_GUILDS": "1"}, clear=True):
            cog = AIAgent(NS())
            for gid in (123, 456):
                self.assertTrue(cog.authorized(NS(guild=NS(id=gid, owner_id=1), user=NS(id=1))))
                self.assertFalse(cog.authorized(NS(guild=NS(id=gid, owner_id=1), user=NS(id=2))))
            self.assertFalse(cog.authorized(NS(guild=None, user=NS(id=1))))
            cog.all_guilds = False
            self.assertTrue(cog.authorized(NS(guild=NS(id=123, owner_id=1), user=NS(id=1))))
            self.assertFalse(cog.authorized(NS(guild=NS(id=456, owner_id=1), user=NS(id=1))))
            cog.key = ""
            self.assertFalse(cog.authorized(NS(guild=NS(id=123, owner_id=1), user=NS(id=1))))
