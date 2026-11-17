import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config


class KokoroConfigTests(unittest.TestCase):
    def _load(self, extra):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            values = {
                "workspace": str(Path(directory) / "workspace"),
                "provider": "mock",
                "ollama_base_url": "http://127.0.0.1:11434",
                **extra,
            }
            lines = []
            for key, value in values.items():
                if isinstance(value, str):
                    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'{key}: "{escaped}"')
                else:
                    lines.append(f"{key}: {value}")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                return load_config(path)

    def test_kokoro_voice_configuration_is_exposed(self):
        config = self._load({
            "tts_backend": "kokoro",
            "kokoro_model_dir": "/models/kokoro",
            "kokoro_lexicon_path": "/models/lexicon.txt",
            "kokoro_voice": "bf_emma",
            "kokoro_speed": 1.15,
        })
        self.assertEqual(config["tts_backend"], "kokoro")
        self.assertEqual(config["kokoro_model_dir"], "/models/kokoro")
        self.assertEqual(config["kokoro_lexicon_path"], "/models/lexicon.txt")
        self.assertEqual(config["kokoro_voice"], "bf_emma")
        self.assertEqual(config["kokoro_speed"], 1.15)

    def test_unknown_kokoro_voice_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "kokoro_voice"):
            self._load({"kokoro_voice": "unknown"})

    def test_kokoro_speed_is_bounded(self):
        with self.assertRaisesRegex(RuntimeError, "kokoro_speed"):
            self._load({"kokoro_speed": 4.0})

    def test_unknown_tts_backend_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "tts_backend"):
            self._load({"tts_backend": "cloud-tts"})


if __name__ == "__main__":
    unittest.main()
