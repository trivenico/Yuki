"""Preflight checks for Yuki's external-drive launcher."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from scripts.portable_runtime_manifest import MANIFEST_NAME, RuntimeManifestError, verify_signed_manifest


class PortablePreflightError(RuntimeError):
    """Raised when the selected portable runtime cannot safely start."""


def validate_portable_runtime(
    *,
    version_info: tuple[int, ...] | None = None,
    data_home: str | os.PathLike[str] | None = None,
    bundle_root: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    version = version_info if version_info is not None else sys.version_info[:3]
    if tuple(version[:2]) < (3, 10):
        raise PortablePreflightError("Python 3.10 or newer is required.")

    raw_home = data_home if data_home is not None else env.get("YUKI_HOME", "")
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
        resolved_home = candidate.resolve()
        root = Path(bundle_root).expanduser().resolve(strict=True) if bundle_root is not None else Path(__file__).resolve().parents[1]
        manifest_path = root / MANIFEST_NAME
        require_signature = env.get("YUKI_PORTABLE_REQUIRE_SIGNATURE", "").strip() == "1"
        trusted_key = env.get("YUKI_PORTABLE_TRUSTED_PUBLIC_KEY", "").strip()
        if require_signature and not manifest_path.is_file():
            raise PortablePreflightError(
                "Portable release signature or runtime integrity verification failed."
            )
        if require_signature or manifest_path.exists() or trusted_key:
            if not trusted_key:
                raise PortablePreflightError(
                    "A trusted release public key is required to start this portable runtime."
                )
            try:
                verify_signed_manifest(root, trusted_key)
            except (OSError, ValueError, RuntimeManifestError) as exc:
                raise PortablePreflightError(
                    "Portable release signature or runtime integrity verification failed."
                ) from exc
        return resolved_home
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
