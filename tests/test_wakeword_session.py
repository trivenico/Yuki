import queue
import threading
import unittest

from voice.wakeword_session import WakeWordError, WakeWordSession


class FakeAudioSource:
    def __init__(self, frames=()):
        self.frames = queue.Queue()
        for frame in frames:
            self.frames.put(frame)
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def read(self):
        return self.frames.get(timeout=2)

    def stop(self):
        self.stopped = True
        self.frames.put(None)

    def close(self):
        self.closed = True


class FakeDetector:
    def __init__(self, *, trigger_on=b"wake"):
        self.trigger_on = trigger_on
        self.closed = False
        self.frames_seen = 0

    def process(self, frame):
        self.frames_seen += 1
        return frame == self.trigger_on

    def close(self):
        self.closed = True


class WakeWordSessionTests(unittest.TestCase):
    def make_session(self, source, detector, **kwargs):
        return WakeWordSession(
            audio_source_factory=lambda: source,
            detector_factory=lambda: detector,
            on_wake=kwargs.pop("on_wake", lambda: None),
            on_state=kwargs.pop("on_state", None),
            on_error=kwargs.pop("on_error", None),
        )

    def test_construction_does_not_create_detector_or_open_microphone(self):
        calls = []
        session = WakeWordSession(
            audio_source_factory=lambda: calls.append("source"),
            detector_factory=lambda: calls.append("detector"),
            on_wake=lambda: None,
        )
        self.assertEqual(session.state, "disabled")
        self.assertFalse(session.listening)
        self.assertEqual(calls, [])

    def test_explicit_start_processes_frames_locally_then_triggers_once(self):
        source = FakeAudioSource([b"noise!", b"wake"])
        detector = FakeDetector()
        woke = threading.Event()
        states = []
        session = self.make_session(
            source, detector, on_wake=woke.set, on_state=states.append
        )
        session.start()
        self.assertTrue(woke.wait(2))
        self.assertEqual(session.state, "triggered")
        self.assertTrue(source.started)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)
        self.assertEqual(detector.frames_seen, 2)
        self.assertIn("starting", states)
        self.assertIn("listening", states)
        self.assertIn("triggered", states)
        self.assertTrue(session.stop())
        self.assertEqual(session.state, "disabled")

    def test_close_waits_for_startup_to_release_microphone(self):
        class SlowStartSource(FakeAudioSource):
            def __init__(inner_self):
                super().__init__()
                inner_self.start_entered = threading.Event()
                inner_self.allow_start_to_finish = threading.Event()

            def start(inner_self):
                inner_self.start_entered.set()
                inner_self.allow_start_to_finish.wait(4)
                inner_self.started = True

        source = SlowStartSource()
        detector = FakeDetector()
        session = self.make_session(source, detector)
        start_errors = []

        def start_session():
            try:
                session.start()
            except WakeWordError as exc:
                start_errors.append(exc)

        starter = threading.Thread(target=start_session)
        starter.start()
        self.assertTrue(source.start_entered.wait(2))

        close_result = []
        closer = threading.Thread(target=lambda: close_result.append(session.close()))
        closer.start()
        source.allow_start_to_finish.set()
        closer.join(3)
        starter.join(3)
        self.assertFalse(closer.is_alive())
        self.assertFalse(starter.is_alive())
        self.assertEqual(close_result, [True])
        self.assertEqual(len(start_errors), 1)
        self.assertEqual(session.state, "closed")
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_close_can_be_retried_after_startup_exceeds_first_join_timeout(self):
        class SlowStartSource(FakeAudioSource):
            def __init__(inner_self):
                super().__init__()
                inner_self.start_entered = threading.Event()
                inner_self.allow_start_to_finish = threading.Event()

            def start(inner_self):
                inner_self.start_entered.set()
                inner_self.allow_start_to_finish.wait(2)
                inner_self.started = True

        source = SlowStartSource()
        detector = FakeDetector()
        session = self.make_session(source, detector)
        starter_errors = []

        def start_session():
            try:
                session.start()
            except WakeWordError as exc:
                starter_errors.append(exc)

        starter = threading.Thread(target=start_session)
        starter.start()
        self.assertTrue(source.start_entered.wait(2))
        self.assertFalse(session.stop(timeout=0))
        # close() marks the session terminal even if the first bounded join
        # times out; a subsequent close must still join the startup thread.
        self.assertFalse(session.close())
        source.allow_start_to_finish.set()
        self.assertTrue(session.close())
        starter.join(2)
        self.assertFalse(starter.is_alive())
        self.assertEqual(session.state, "closed")
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_worker_remains_registered_until_wake_callback_finishes(self):
        source = FakeAudioSource([b"wake"])
        detector = FakeDetector()
        callback_entered = threading.Event()
        callback_release = threading.Event()

        def on_wake():
            callback_entered.set()
            callback_release.wait(2)

        session = self.make_session(source, detector, on_wake=on_wake)
        session.start()
        worker = session._thread
        self.assertTrue(callback_entered.wait(2))
        with self.assertRaises(WakeWordError):
            session.start()
        callback_release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(session.state, "triggered")

    def test_stop_interrupts_blocked_audio_read_and_releases_resources(self):
        source = FakeAudioSource()
        detector = FakeDetector()
        session = self.make_session(source, detector)
        session.start()
        self.assertTrue(session.stop(timeout=2))
        self.assertEqual(session.state, "disabled")
        self.assertTrue(source.stopped)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_stop_from_listening_state_callback_is_race_safe(self):
        source = FakeAudioSource()
        detector = FakeDetector()
        states = []
        holder = {}

        def on_state(state):
            states.append(state)
            if state == "listening":
                holder["session"].stop(timeout=2)

        session = self.make_session(source, detector, on_state=on_state)
        holder["session"] = session
        session.start()
        self.assertEqual(session.state, "disabled")
        self.assertIn("listening", states)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_invalid_audio_frame_fails_closed_and_reports_error(self):
        source = FakeAudioSource([object()])
        detector = FakeDetector()
        errors = []
        session = self.make_session(source, detector, on_error=errors.append)
        session.start()
        worker = session._thread
        worker.join(2)
        self.assertEqual(session.state, "failed")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], WakeWordError)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_startup_failure_does_not_leave_session_listening(self):
        errors = []

        def fail_detector():
            raise RuntimeError("model load failed")

        session = WakeWordSession(
            audio_source_factory=lambda: self.fail("source must not open"),
            detector_factory=fail_detector,
            on_wake=lambda: None,
            on_error=errors.append,
        )
        with self.assertRaises(WakeWordError):
            session.start()
        self.assertEqual(session.state, "failed")
        self.assertEqual(len(errors), 1)

    def test_close_is_terminal(self):
        source = FakeAudioSource()
        detector = FakeDetector()
        session = self.make_session(source, detector)
        session.start()
        self.assertTrue(session.close())
        self.assertEqual(session.state, "closed")
        with self.assertRaises(WakeWordError):
            session.start()

    def test_odd_byte_pcm_frames_are_rejected(self):
        source = FakeAudioSource([b"abc"])
        detector = FakeDetector()
        errors = []
        session = self.make_session(source, detector, on_error=errors.append)
        session.start()
        worker = session._thread
        worker.join(2)
        self.assertEqual(session.state, "failed")
        self.assertEqual(len(errors), 1)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_detector_must_return_a_boolean(self):
        source = FakeAudioSource([b"frame!"])
        detector = FakeDetector()
        detector.process = lambda frame: 1
        errors = []
        session = self.make_session(source, detector, on_error=errors.append)
        session.start()
        worker = session._thread
        worker.join(2)
        self.assertEqual(session.state, "failed")
        self.assertEqual(len(errors), 1)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)

    def test_oversized_frames_are_rejected(self):
        source = FakeAudioSource([b"x" * (WakeWordSession.MAX_FRAME_BYTES + 1)])
        detector = FakeDetector()
        errors = []
        session = self.make_session(source, detector, on_error=errors.append)
        session.start()
        worker = session._thread
        worker.join(2)
        self.assertEqual(session.state, "failed")
        self.assertEqual(len(errors), 1)
        self.assertTrue(source.closed)
        self.assertTrue(detector.closed)


if __name__ == "__main__":
    unittest.main()
