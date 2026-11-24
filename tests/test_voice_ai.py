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
