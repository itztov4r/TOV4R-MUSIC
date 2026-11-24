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
