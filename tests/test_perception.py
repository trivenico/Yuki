import unittest

from core.perception import (
    PerceptionPermissionDenied,
    ScreenContext,
    capture_camera_frame,
    capture_screen,
    get_screen_context,
    require_permission,
)


class PerceptionPermissionTests(unittest.TestCase):
    def test_screen_capture_rejects_disabled_permission_before_importing_backend(self):
        with self.assertRaises(PerceptionPermissionDenied):
            capture_screen(enabled=False)

    def test_camera_capture_rejects_disabled_permission_before_importing_backend(self):
        with self.assertRaises(PerceptionPermissionDenied):
            capture_camera_frame(enabled=False)

    def test_active_window_metadata_requires_opt_in(self):
        with self.assertRaises(PerceptionPermissionDenied):
            get_screen_context(enabled=False)

    def test_permission_requires_literal_true(self):
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(PerceptionPermissionDenied):
                    require_permission(value, "camera")

    def test_screen_context_prompt_discloses_metadata_only_and_incompleteness(self):
        context = ScreenContext(active_window="Editor - project.py", platform="win32")
        prompt = context.as_prompt_context()
        self.assertIn("metadata only", prompt)
        self.assertIn("Editor - project.py", prompt)
        self.assertIn("Ask before taking actions", prompt)

    def test_invalid_camera_index_is_rejected_after_permission_check(self):
        with self.assertRaises(PermissionError):
            capture_camera_frame(enabled=False, camera_index=99)
        with self.assertRaises(ValueError):
            capture_camera_frame(enabled=True, camera_index=99)


if __name__ == "__main__":
    unittest.main()
