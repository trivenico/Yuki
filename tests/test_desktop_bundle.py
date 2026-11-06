"""Keep the native desktop's bundled UI synchronized with the canonical web UI."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "ui" / "web"
BUNDLE = ROOT / "desktop" / "dist"


class DesktopBundleTests(unittest.TestCase):
    def test_bundled_html_matches_canonical_shell_with_relative_assets(self):
        source = (WEB / "index.html").read_text(encoding="utf-8")
        expected = source.replace(
            'href="/assets/chat.css"', 'href="assets/chat.css"'
        ).replace(
            'src="/assets/chat.js"', 'src="assets/chat.js"'
        )
        actual = (BUNDLE / "index.html").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_bundled_stylesheet_matches_canonical_stylesheet(self):
        expected = (WEB / "chat.css").read_text(encoding="utf-8")
        actual = (BUNDLE / "assets" / "chat.css").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_bundled_script_matches_canonical_script_and_uses_fixed_api_origin(self):
        source = (WEB / "chat.js").read_text(encoding="utf-8")
        expected = source.replace(
            '  const $ = (id) => document.getElementById(id);',
            '  const API_BASE = "http://127.0.0.1:8765";\n'
            '  const apiUrl = (path) => new URL(path, API_BASE).toString();\n'
            '  const $ = (id) => document.getElementById(id);',
        ).replace(
            'fetch(path, { ...options, headers })',
            'fetch(apiUrl(path), { ...options, headers })',
        ).replace(
            'fetch("/v1/conversations/" + encodeURIComponent(state.active) + "/messages/stream", {',
            'fetch(apiUrl("/v1/conversations/" + encodeURIComponent(state.active) + "/messages/stream"), {',
        ).replace(
            'fetch("/v1/devices/pair", {',
            'fetch(apiUrl("/v1/devices/pair"), {',
        )
        actual = (BUNDLE / "assets" / "chat.js").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)
        self.assertIn('const API_BASE = "http://127.0.0.1:8765"', actual)
        self.assertNotIn('fetch("/v1/', actual)


if __name__ == "__main__":
    unittest.main()
