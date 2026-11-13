"""Preflight checks for Yuki's external-drive launcher."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


class PortablePreflightError(RuntimeError):
    """Raised when the selected portable runtime cannot safely start."""


def validate_portable_runtime(
    *,
    version_info: tuple[int, ...] | None = None,
    data_home: str | os.PathLike[str] | None = None,
) -> Path:
    version = version_info if version_info is not None else sys.version_info[:3]
    if tuple(version[:2]) < (3, 10):
        raise PortablePreflightError("Python 3.10 or newer is required.")

    raw_home = data_home if data_home is not None else os.environ.get("YUKI_HOME", "")
    if not str(raw_home).strip():
        raise PortablePreflightError("YUKI_HOME must identify the portable data directory.")

    candidate = Path(raw_home).expanduser()
    if candidate.is_symlink():
        raise PortablePreflightError("YUKI_HOME must not be a symbolic link.")
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        if not candidate.is_dir():
            raise PortablePreflightError("YUKI_HOME is not a directory.")
        # An actual exclusive write catches read-only/remounted media and
        # permission failures more reliably than os.access().
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".yuki-preflight-", dir=candidate,
            delete=True,
        ) as probe:
            probe.write("Yuki portable runtime preflight")
            probe.flush()
        return candidate.resolve()
    except PortablePreflightError:
        raise
    except OSError as exc:
        raise PortablePreflightError(
            "YUKI_HOME is not writable. Check the external drive and its permissions."
        ) from exc


def main() -> int:
    try:
        data_home = validate_portable_runtime()
    except PortablePreflightError as exc:
        print(f"Yuki portable startup check failed: {exc}", file=sys.stderr)
        return 2
    print(f"Yuki portable runtime checks passed. Data directory: {data_home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
