import unittest
from live_voice import Turn

class LiveVoiceTest(unittest.TestCase):
    def test_silence_does_not_trigger(self):
        t = Turn()
        t.feed(b"\x00" * 3840, 0)
        self.assertFalse(t.ready(2))
    def test_turn_detected_after_pause(self):
        t = Turn()
        frame = b"\x10\x10" * 1920
        for n in range(20):
            t.feed(frame, n * 0.02)
        self.assertFalse(t.ready(0.5))
        self.assertTrue(t.ready(1.5))
        self.assertGreater(len(t.take()), 12000)
        self.assertFalse(t.ready(2))
