import importlib
import sys
import unittest
from unittest.mock import patch

from companion.application import CompanionApplicationService
from ui.desktop import (
    AuthenticatedDesktopIdentity,
    DesktopStartupError,
    build_desktop_context,
)


class FakeProvider:
    def is_available(self):
        return True

    def generate_chat(self, system, messages):
        return "hello"


class DesktopBootstrapTests(unittest.TestCase):
    def test_package_import_does_not_load_qt(self):
        with patch.dict(sys.modules, {"PySide6": None}):
            module = importlib.reload(importlib.import_module("ui.desktop"))
            self.assertTrue(callable(module.build_desktop_context))
            self.assertNotIn("PySide6.QtWidgets", sys.modules)

    def test_launcher_module_import_does_not_load_qt(self):
        with patch.dict(sys.modules, {"PySide6": None}):
            module = importlib.import_module("ui.desktop.launch")
            self.assertTrue(callable(module.run))
            self.assertNotIn("PySide6.QtWidgets", sys.modules)

    def test_launcher_reports_missing_optional_dependency(self):
        from ui.desktop.launch import run

        with patch.dict(sys.modules, {"PySide6": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements-desktop.txt"):
                run()

    def test_core_requirements_do_not_include_qt(self):
        from pathlib import Path

        requirements = Path("requirements.txt").read_text(encoding="utf-8").casefold()
        self.assertNotIn("pyside6", requirements)
        self.assertNotIn("pyqt", requirements)

    def test_context_uses_shared_companion_service_and_authenticated_identity(self):
        identity = AuthenticatedDesktopIdentity("local-operator", "desktop-session")
        context = build_desktop_context(
            authenticate=lambda: identity,
            provider=FakeProvider(),
        )
        self.assertIsInstance(context.application, CompanionApplicationService)
        self.assertIs(context.identity, identity)
        self.assertEqual(context.identity.actor_id, "local-operator")
        self.assertEqual(context.identity.session_id, "desktop-session")

    def test_identity_claims_are_normalized_and_bounded(self):
        identity = AuthenticatedDesktopIdentity(" operator ", " session ")
        self.assertEqual(identity.actor_id, "operator")
        self.assertEqual(identity.session_id, "session")
        with self.assertRaises(ValueError):
            AuthenticatedDesktopIdentity("x" * 129, "session")
        with self.assertRaises(ValueError):
            AuthenticatedDesktopIdentity("operator\nforged", "session")

    def test_authentication_is_required_and_fails_closed(self):
        with self.assertRaises(DesktopStartupError):
            build_desktop_context(authenticate=None, provider=FakeProvider())
        with self.assertRaises(DesktopStartupError):
            build_desktop_context(
                authenticate=lambda: (_ for _ in ()).throw(PermissionError()),
                provider=FakeProvider(),
            )
        with self.assertRaises(DesktopStartupError):
            build_desktop_context(authenticate=lambda: {"actor_id": "forged"}, provider=FakeProvider())

    def test_invalid_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            AuthenticatedDesktopIdentity("", "session")
        with self.assertRaises(ValueError):
            AuthenticatedDesktopIdentity("operator", "")

    def test_service_composition_errors_are_sanitized(self):
        with self.assertRaises(DesktopStartupError) as raised:
            build_desktop_context(
                authenticate=lambda: AuthenticatedDesktopIdentity("operator", "session"),
                provider=FakeProvider(),
                memory=object(),
            )
        self.assertNotIn("object", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
