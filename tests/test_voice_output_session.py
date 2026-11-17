import unittest

from voice.output_session import VoiceOutputError, VoiceOutputSession


class FakeSynthesizer:
    def __init__(self):
        self.enabled = True
        self.calls = []
        self.stop_calls = 0

    def speak_stream(self, chunks):
        self.calls.append(list(chunks))
        return {"chunks_spoken": len(self.calls[-1])}

    def stop(self):
        self.stop_calls += 1


class VoiceOutputSessionTests(unittest.TestCase):
    def setUp(self):
        self.synth = FakeSynthesizer()
        self.session = VoiceOutputSession(self.synth)

    def test_construction_never_enables_synthesizer_or_speaks(self):
        self.assertFalse(self.session.enabled)
        self.assertFalse(self.synth.enabled)
        self.assertEqual(self.synth.calls, [])

    def test_requires_both_explicit_enable_and_voice_mode(self):
        with self.assertRaises(VoiceOutputError):
            self.session.speak_reply("Hello", voice_mode_active=True)
        self.session.enable()
        with self.assertRaises(VoiceOutputError):
            self.session.speak_reply("Hello", voice_mode_active=False)
        self.assertEqual(self.synth.calls, [])

    def test_speaks_only_after_explicit_opt_in(self):
        self.session.enable()
        result = self.session.speak_reply("Hello Yuki", voice_mode_active=True)
        self.assertEqual(result["chunks_spoken"], 1)
        self.assertEqual(self.synth.calls, [["Hello Yuki"]])

    def test_reply_and_chunk_limits(self):
        self.session.enable()
        with self.assertRaisesRegex(ValueError, "20000-character"):
            self.session.speak_reply("x" * 20001, voice_mode_active=True)
        result = self.session.speak_reply("x" * 4001, voice_mode_active=True)
        self.assertEqual(result["chunks_spoken"], 3)
        self.assertEqual([len(x) for x in self.synth.calls[-1]], [2000, 2000, 1])

    def test_disable_revokes_permission_and_stops_backend(self):
        self.session.enable()
        self.session.disable()
        self.assertFalse(self.session.enabled)
        self.assertFalse(self.synth.enabled)
        self.assertGreaterEqual(self.synth.stop_calls, 1)
        with self.assertRaises(VoiceOutputError):
            self.session.speak_reply("No", voice_mode_active=True)

    def test_close_is_terminal(self):
        self.session.close()
        with self.assertRaises(VoiceOutputError):
            self.session.enable()
        with self.assertRaises(VoiceOutputError):
            self.session.speak_reply("No", voice_mode_active=True)

    def test_stop_invalidates_remaining_chunks_even_if_backend_keeps_consuming(self):
        class StopAfterFirstChunk(FakeSynthesizer):
            def speak_stream(inner_self, chunks):
                spoken = []
                for chunk in chunks:
                    spoken.append(chunk)
                    if len(spoken) == 1:
                        self.session.stop()
                inner_self.calls.append(spoken)
                return {"chunks_spoken": len(spoken)}

        synth = StopAfterFirstChunk()
        self.session = VoiceOutputSession(synth)
        self.session.enable()
        with self.assertRaises(VoiceOutputError):
            self.session.speak_reply("x" * 4001, voice_mode_active=True)
        self.assertEqual(synth.calls, [])
        self.assertFalse(self.session.speaking)

    def test_stop_failure_is_reported(self):
        class BrokenStop(FakeSynthesizer):
            def stop(inner_self):
                raise OSError("device unavailable")

        session = VoiceOutputSession(BrokenStop())
        with self.assertRaises(VoiceOutputError):
            session.stop()


if __name__ == "__main__":
    unittest.main()
