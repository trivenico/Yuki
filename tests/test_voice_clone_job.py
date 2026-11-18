import io
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from voice.audio_validation import VoiceSampleValidationError
from voice.clone_job import (
    VoiceCloneJobError,
    VoiceCloneJobCancelled,
    VoiceCloneJobController,
    VoiceCloneJobState,
    VoiceCloneOutput,
)
from voice.profile_registry import VoiceProfileRegistry
from voice.secure_artifact_store import EncryptedVoiceArtifactStore
from voice.clone_process import (
    ProcessIsolatedVoiceCloneExecutor,
    VoiceCloneWorkerCancelled,
    VoiceCloneWorkerTimeout,
)


def wav_sample(seconds=3):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00" * (2 * 24000 * seconds))
    return output.getvalue()


class FakeLocalProvider:
    provider_id = "test-local-provider"
    is_local = True

    def __init__(self, *, cancel_during=False, fail=False):
        self.cancel_during = cancel_during
        self.fail = fail

    def create_clone(self, sample_audio, *, cancel_event, deadline_monotonic):
        if self.cancel_during:
            cancel_event.set()
        if self.fail:
            raise RuntimeError("provider failed")
        return VoiceCloneOutput(b"opaque-clone-artifact", wav_sample())


class BlockingProvider(FakeLocalProvider):
    def __init__(self, started_event=None):
        super().__init__()
        self.started_event = started_event

    def create_clone(self, sample_audio, *, cancel_event, deadline_monotonic):
        if self.started_event is not None:
            self.started_event.set()
        time.sleep(3)
        return VoiceCloneOutput(b"artifact", wav_sample())


class LargeProvider(FakeLocalProvider):
    def create_clone(self, sample_audio, *, cancel_event, deadline_monotonic):
        return VoiceCloneOutput(b"x" * (20 * 1024 * 1024 + 1), wav_sample())


class InvalidPreviewProvider(FakeLocalProvider):
    def create_clone(self, sample_audio, *, cancel_event, deadline_monotonic):
        return VoiceCloneOutput(b"opaque-artifact", b"not-audio")


class FakeRemoteProvider(FakeLocalProvider):
    is_local = False


class VoiceCloneJobControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.authorized = True
        authorize = lambda: self.authorized
        self.store = EncryptedVoiceArtifactStore(root / "artifacts", key=b"\x31" * 32, authorize=authorize)
        self.registry = VoiceProfileRegistry(root / "profiles", authorize=authorize)

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, provider=None):
        return VoiceCloneJobController(provider or FakeLocalProvider(), self.store, self.registry)

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_success_stages_profile_but_never_activates_it(self):
        controller = self.controller()
        result = controller.run(
            wav_sample(), profile_id="new-voice", display_name="New voice",
            source_owner_consent=True,
        )
        self.assertEqual(controller.state, VoiceCloneJobState.PREVIEW_READY)
        self.assertEqual(result.preview_audio, wav_sample())
        self.assertFalse(controller.cancel())
        self.assertIsNone(self.registry.active_profile())
        profile = self.registry.list_profiles()[0]
        self.assertEqual(profile["status"], "staged")
        self.assertEqual(profile["artifact_ref"], result.artifact_ref)
        self.assertEqual(self.store.get(result.artifact_ref), b"opaque-clone-artifact")

    def test_requires_consent_and_valid_sample_before_provider(self):
        controller = self.controller()
        with self.assertRaises(ValueError):
            controller.run(b"not audio", profile_id="new-voice", display_name="New voice", source_owner_consent=True)
        with self.assertRaises(VoiceSampleValidationError):
            controller.run(wav_sample(), profile_id="new-voice", display_name="New voice", source_owner_consent=False)
        self.assertEqual(self.registry.list_profiles(), [])

    def test_rejects_remote_provider(self):
        with self.assertRaises(ValueError):
            self.controller(FakeRemoteProvider())

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_cancellation_after_provider_returns_does_not_stage_profile(self):
        controller = self.controller(FakeLocalProvider(cancel_during=True))
        with self.assertRaises(VoiceCloneJobCancelled):
            controller.run(wav_sample(), profile_id="new-voice", display_name="New voice", source_owner_consent=True)
        self.assertEqual(controller.state, VoiceCloneJobState.CANCELLED)
        self.assertEqual(self.registry.list_profiles(), [])

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_provider_failure_preserves_registry(self):
        controller = self.controller(FakeLocalProvider(fail=True))
        with self.assertRaises(VoiceCloneJobError):
            controller.run(wav_sample(), profile_id="new-voice", display_name="New voice", source_owner_consent=True)
        self.assertEqual(controller.state, VoiceCloneJobState.FAILED)
        self.assertEqual(self.registry.list_profiles(), [])

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_rejects_concurrent_job(self):
        controller = self.controller(BlockingProvider())
        errors = []

        def run():
            try:
                controller.run(wav_sample(), profile_id="first", display_name="First", source_owner_consent=True)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 2
        while controller.active_job_id is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(controller.active_job_id)
        with self.assertRaises(RuntimeError):
            controller.run(wav_sample(), profile_id="second", display_name="Second", source_owner_consent=True)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_rejects_invalid_preview_before_storing_artifact(self):
        controller = self.controller(InvalidPreviewProvider())
        with self.assertRaisesRegex(RuntimeError, "valid bounded PCM WAV"):
            controller.run(wav_sample(), profile_id="bad-preview", display_name="Bad preview", source_owner_consent=True)
        self.assertEqual(self.registry.list_profiles(), [])

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_isolated_worker_is_killed_at_wall_clock_deadline(self):
        executor = ProcessIsolatedVoiceCloneExecutor(poll_interval=0.02, terminate_grace_seconds=0.1)
        started = multiprocessing.get_context("spawn").Event()
        errors = []

        def run():
            try:
                executor.run(
                    BlockingProvider(started), wav_sample(), cancel_event=threading.Event(),
                    deadline_monotonic=time.monotonic() + 2,
                )
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(5), "provider did not start before the test deadline")
        worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], VoiceCloneWorkerTimeout)

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_isolated_worker_is_killed_when_parent_cancels(self):
        executor = ProcessIsolatedVoiceCloneExecutor(poll_interval=0.02, terminate_grace_seconds=0.1)
        cancel = threading.Event()
        started = multiprocessing.get_context("spawn").Event()
        errors = []

        def run():
            try:
                executor.run(
                    BlockingProvider(started), wav_sample(), cancel_event=cancel,
                    deadline_monotonic=time.monotonic() + 10,
                )
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(5), "provider did not start before cancellation")
        cancel.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], VoiceCloneWorkerCancelled)

    def test_worker_limits_reject_unsafe_values(self):
        from voice.clone_process import VoiceCloneWorkerLimits
        with self.assertRaises(ValueError):
            VoiceCloneWorkerLimits(memory_bytes=1)
        with self.assertRaises(ValueError):
            VoiceCloneWorkerLimits(cpu_seconds=0)

    @unittest.skipUnless(os.name in {"posix", "nt"}, "OS worker limits are unavailable on this platform")
    def test_rejects_oversized_provider_output(self):
        controller = self.controller(LargeProvider())
        with self.assertRaises(RuntimeError):
            controller.run(wav_sample(), profile_id="new-voice", display_name="New voice", source_owner_consent=True)
        self.assertEqual(self.registry.list_profiles(), [])


if __name__ == "__main__":
    unittest.main()
