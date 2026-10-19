"""Environment variable scrubbing helper for subprocesses and plugins."""
import os


SENSITIVE_PATTERNS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")


def sanitize_env(env: dict = None) -> dict:
    source = dict(env if env is not None else os.environ)
    scrubbed = {}
    for k, v in source.items():
        upper_key = k.upper()
        if not any(pattern in upper_key for pattern in SENSITIVE_PATTERNS):
            scrubbed[k] = v
    return scrubbed
