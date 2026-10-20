import tempfile
import unittest

from hackback.exploit_runner import ExploitRunner
from hackback.recon import NmapRecon, ReconError, validate_lab_target
from hackback.sandbox import DockerSandbox, SandboxError
from voice.stt import FasterWhisperSTT, STTError
from voice.tts import LocalTTS, TTSError


class OptionalModuleSafetyTests(unittest.TestCase):
    def test_recon_requires_explicit_target_allowlist(self):
        recon = NmapRecon(enabled=True, approver=lambda _: True)
        with self.assertRaises(ReconError):
            recon.scan_common_tcp("192.168.1.10")

    def test_recon_blocks_public_and_hostname_targets(self):
        with self.assertRaises(ReconError):
            validate_lab_target("example.com")
        with self.assertRaises(ReconError):
            validate_lab_target("8.8.8.8")

    def test_recon_still_requires_approval_for_allowlisted_target(self):
        recon = NmapRecon(enabled=True, allowlisted_targets=["192.168.1.10"])
        with self.assertRaises(ReconError):
            recon.scan_common_tcp("192.168.1.10")

    def test_sandbox_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(directory, allowed_images=["example@sha256:" + "a" * 64])
            with self.assertRaises(SandboxError):
                sandbox.run("example@sha256:" + "a" * 64, ["echo", "hello"])

    def test_metasploit_execution_fails_closed(self):
        runner = ExploitRunner()
        self.assertFalse(runner.status()["available"])
        with self.assertRaises(PermissionError):
            runner.run_module("127.0.0.1", "exploit/example")

    def test_voice_adapters_are_disabled_by_default(self):
        with self.assertRaises(TTSError):
            LocalTTS().speak("hello")
        with self.assertRaises(STTError):
            FasterWhisperSTT().transcribe_file("missing-audio.wav")


if __name__ == "__main__":
    unittest.main()
