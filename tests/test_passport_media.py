import base64
import unittest
from gateway.passport_media import Recording,decode_line


class PassportMediaTests(unittest.TestCase):
    def test_complete_recording_and_duplicate_chunk_rejected(self):
        r=Recording();r.feed({'type':'record.start','turn':1})
        f={'type':'record.chunk','turn':1,'seq':0,'pcm':base64.b64encode(bytes(1024)).decode()}
        for n in range(4):r.feed(dict(f,seq=n))
        self.assertEqual(len(r.feed({'type':'record.end','turn':1,'bytes':4096})),4096)
        r.feed({'type':'record.start','turn':2})
        r.feed(dict(f,turn=2))
        with self.assertRaises(ValueError):r.feed(dict(f,turn=2))
        self.assertIsNone(r.turn)

    def test_wrong_turn_cancel_and_log_noise(self):
        r=Recording();r.feed({'type':'record.start','turn':1})
        self.assertIsNone(r.feed({'type':'record.end','turn':2,'bytes':0}))
        r.feed({'type':'cancel','turn':1});self.assertIsNone(r.turn)
        self.assertIsNone(decode_line(b'normal device log'))
        self.assertEqual(decode_line(b'\rSUMMON1 {"type":"hello"}\n'),{'type':'hello'})
        self.assertIsNone(decode_line(b'SUMMON1 '+bytes(2048)))
