import unittest

from providers.screen_observation_policy import ScreenObservationGrant, ScreenObservationPolicyError
from providers.screen_transport import (
    SSHScreenTarget,
    ScreenTransportError,
    build_ssh_capture_argv,
    validate_screen_frame,
)


class ScreenTransportTests(unittest.TestCase):
    def setUp(self):
        self.target = SSHScreenTarget(
            host="workstation.example.org", username="yuki",
            identity_file="/home/user/.ssh/yuki_screen",
            known_hosts_file="/home/user/.ssh/known_hosts", port=2222,
        )
        self.grant = ScreenObservationGrant(
            grant_id="g1", owner_id="owner", device_id="device",
            session_id="session_1", issued_at_epoch=10, expires_at_epoch=100,
            allowed_transports=("ssh",),
        )

    def test_builds_strict_host_key_pinned_ssh_command(self):
        argv = build_ssh_capture_argv(self.target, session_id="session_1")
        self.assertEqual(argv[0], "ssh")
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("UserKnownHostsFile=/home/user/.ssh/known_hosts", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-1], "/usr/local/libexec/yuki-screen-capture --session-id session_1 --format png")

    def test_rejects_host_injection_and_arbitrary_session(self):
        with self.assertRaises(ScreenTransportError):
            build_ssh_capture_argv(
                SSHScreenTarget("host;touch /tmp/x", "yuki", "/id", "/known"),
                session_id="s1",
            )
        with self.assertRaises(ScreenTransportError):
            build_ssh_capture_argv(self.target, session_id="x;rm -rf /")

    def test_rejects_unpinned_or_relative_key_paths(self):
        with self.assertRaises(ScreenTransportError):
            build_ssh_capture_argv(
                SSHScreenTarget("host.example", "yuki", "id", "/known"),
                session_id="s1",
            )

    def test_rejects_invalid_port_and_timeout(self):
        with self.assertRaises(ScreenTransportError):
            build_ssh_capture_argv(
                SSHScreenTarget("host.example", "yuki", "/id", "/known", 0),
                session_id="s1",
            )
        with self.assertRaises(ScreenTransportError):
            build_ssh_capture_argv(self.target, session_id="s1", timeout_seconds=120)

    def png(self, width=2, height=3):
        return (
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"
        )

    def test_authorized_ssh_png_frame_is_validated(self):
        frame = validate_screen_frame(
            self.png(), grant=self.grant, owner_id="owner",
            device_id="device", session_id="session_1",
            transport="ssh", now_epoch=20,
        )
        self.assertEqual((frame.width, frame.height), (2, 3))
        self.assertEqual(frame.media_type, "image/png")

    def test_frame_requires_screen_consent_even_with_ssh(self):
        with self.assertRaises(ScreenObservationPolicyError):
            validate_screen_frame(
                self.png(), grant=self.grant, owner_id="owner",
                device_id="device", session_id="wrong",
                transport="ssh", now_epoch=20,
            )

    def test_rejects_non_png_and_excessive_dimensions(self):
        with self.assertRaises(ScreenTransportError):
            validate_screen_frame(
                b"not png", grant=self.grant, owner_id="owner",
                device_id="device", session_id="session_1",
                transport="ssh", now_epoch=20,
            )
        with self.assertRaises(ScreenTransportError):
            validate_screen_frame(
                self.png(16384, 16384), grant=self.grant, owner_id="owner",
                device_id="device", session_id="session_1",
                transport="ssh", now_epoch=20,
            )


if __name__ == "__main__":
    unittest.main()
