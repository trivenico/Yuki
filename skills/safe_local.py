"""Small bounded JSON persistence helper for explicitly local Yuki features."""
from __future__ import annotations
import json
from pathlib import Path

MAX_BYTES = 1_000_000

def load_json(path: Path, default):
    try:
        if not path.exists() or path.stat().st_size > MAX_BYTES:
            return default
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value
    except (OSError, ValueError, TypeError):
        return default

def save_json(path: Path, value) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        encoded = json.dumps(value, ensure_ascii=False, indent=2)
        if len(encoded.encode("utf-8")) > MAX_BYTES:
            return False
        temp.write_text(encoded, encoding="utf-8")
        temp.replace(path)
        return True
    except (OSError, ValueError, TypeError):
        return False

def parse_object(argument: str):
    try:
        value = json.loads(argument)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None
