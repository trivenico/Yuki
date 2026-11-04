"""Authenticated streaming encryption for Yuki backup archives.

Format v1: 8-byte magic, 12-byte random nonce, AES-256-GCM ciphertext,
then the 16-byte authentication tag. The complete header is authenticated
as additional data. Keys are raw 32-byte files stored outside backup folders.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import secrets
import stat
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"YUKIBAK1"
NONCE_BYTES = 12
KEY_BYTES = 32
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_CIPHERTEXT_BYTES = 20 * 1024**3


class BackupCryptoError(RuntimeError):
    """Raised when backup encryption or authentication fails."""


def is_encrypted_file(path: str | Path) -> bool:
    candidate = Path(path)
    try:
        with candidate.open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError as exc:
        raise BackupCryptoError("backup_file_unreadable") from exc


def _load_key(key_file: str | Path) -> bytes:
    path = Path(key_file).expanduser()
    if path.is_symlink():
        raise BackupCryptoError("backup_key_must_not_be_symlink")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise BackupCryptoError("backup_key_must_be_regular_file")
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupCryptoError("backup_key_permissions_too_open")
        key = path.read_bytes()
    except BackupCryptoError:
        raise
    except OSError as exc:
        raise BackupCryptoError("backup_key_unreadable") from exc
    if len(key) != KEY_BYTES:
        raise BackupCryptoError("backup_key_must_be_32_bytes")
    return key


def create_key_file(key_file: str | Path) -> Path:
    """Create a random 256-bit key without overwriting an existing file."""
    path = Path(key_file).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise BackupCryptoError("backup_key_destination_already_exists")
    if not path.parent.is_dir():
        raise BackupCryptoError("backup_key_parent_missing")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(secrets.token_bytes(KEY_BYTES))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise BackupCryptoError("backup_key_destination_already_exists") from exc
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise BackupCryptoError("backup_key_creation_failed") from exc
    return path


def _publish_new_file(temporary: Path, destination: Path) -> None:
    """Atomically publish without replacing an existing destination."""
    try:
        os.link(temporary, destination)
        temporary.unlink()
    except FileExistsError as exc:
        raise BackupCryptoError("backup_destination_already_exists") from exc
    except OSError as exc:
        raise BackupCryptoError("backup_atomic_publish_failed") from exc


def encrypt_file(
    source_file: str | Path,
    destination_file: str | Path,
    key_file: str | Path,
) -> dict[str, int]:
    source = Path(source_file).expanduser().resolve(strict=True)
    destination_raw = Path(destination_file).expanduser()
    if destination_raw.is_symlink():
        raise BackupCryptoError("backup_destination_must_not_be_symlink")
    destination = destination_raw.resolve(strict=False)
    key_path = Path(key_file).expanduser().resolve(strict=True)
    if not source.is_file():
        raise BackupCryptoError("backup_source_must_be_file")
    if source == destination or source == key_path or destination == key_path:
        raise BackupCryptoError("backup_paths_must_be_distinct")
    try:
        key_path.relative_to(destination.parent)
    except ValueError:
        pass
    else:
        raise BackupCryptoError("backup_key_must_be_outside_backup_directory")
    if destination.exists():
        raise BackupCryptoError("backup_destination_already_exists")
    if not destination.parent.is_dir():
        raise BackupCryptoError("backup_destination_parent_missing")
    source_size = source.stat().st_size
    if source_size <= 0 or source_size > MAX_CIPHERTEXT_BYTES:
        raise BackupCryptoError("backup_source_size_invalid")
    key = _load_key(key_path)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = MAGIC + nonce
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    temporary = destination.parent / (".yuki-encrypt-" + secrets.token_hex(12) + ".tmp")
    written = 0
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with source.open("rb") as input_handle, os.fdopen(descriptor, "wb") as output:
            output.write(header)
            while True:
                chunk = input_handle.read(CHUNK_BYTES)
                if not chunk:
                    break
                encrypted = encryptor.update(chunk)
                output.write(encrypted)
                written += len(chunk)
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        _publish_new_file(temporary, destination)
    except BackupCryptoError:
        raise
    except OSError as exc:
        raise BackupCryptoError("backup_encryption_failed") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {"plaintext_bytes": written, "encrypted_bytes": destination.stat().st_size}


def decrypt_file(
    source_file: str | Path,
    destination_file: str | Path,
    key_file: str | Path,
) -> dict[str, int]:
    source = Path(source_file).expanduser().resolve(strict=True)
    destination_raw = Path(destination_file).expanduser()
    if destination_raw.is_symlink():
        raise BackupCryptoError("restore_destination_must_not_be_symlink")
    destination = destination_raw.resolve(strict=False)
    key_path = Path(key_file).expanduser().resolve(strict=True)
    if source == destination or source == key_path or destination == key_path:
        raise BackupCryptoError("restore_paths_must_be_distinct")
    if destination.exists():
        raise BackupCryptoError("restore_destination_already_exists")
    if not destination.parent.is_dir():
        raise BackupCryptoError("restore_destination_parent_missing")
    encrypted_size = source.stat().st_size
    minimum_size = len(MAGIC) + NONCE_BYTES + TAG_BYTES + 1
    if encrypted_size < minimum_size or encrypted_size > MAX_CIPHERTEXT_BYTES + len(MAGIC) + NONCE_BYTES + TAG_BYTES:
        raise BackupCryptoError("encrypted_backup_size_invalid")
    key = _load_key(key_path)
    temporary = destination.parent / (".yuki-decrypt-" + secrets.token_hex(12) + ".tmp")
    plaintext_bytes = 0
    try:
        with source.open("rb") as input_handle:
            header = input_handle.read(len(MAGIC) + NONCE_BYTES)
            if len(header) != len(MAGIC) + NONCE_BYTES or header[:len(MAGIC)] != MAGIC:
                raise BackupCryptoError("encrypted_backup_format_invalid")
            nonce = header[len(MAGIC):]
            input_handle.seek(-TAG_BYTES, os.SEEK_END)
            tag = input_handle.read(TAG_BYTES)
            ciphertext_remaining = encrypted_size - len(header) - TAG_BYTES
            input_handle.seek(len(header))
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while ciphertext_remaining:
                    chunk = input_handle.read(min(CHUNK_BYTES, ciphertext_remaining))
                    if not chunk:
                        raise BackupCryptoError("encrypted_backup_truncated")
                    ciphertext_remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    plaintext_bytes += len(plaintext)
                    output.write(plaintext)
                try:
                    final = decryptor.finalize()
                except Exception as exc:
                    raise BackupCryptoError("encrypted_backup_authentication_failed") from exc
                if final:
                    output.write(final)
                    plaintext_bytes += len(final)
                output.flush()
                os.fsync(output.fileno())
        if plaintext_bytes <= 0 or plaintext_bytes > MAX_CIPHERTEXT_BYTES:
            raise BackupCryptoError("decrypted_backup_size_invalid")
        _publish_new_file(temporary, destination)
    except BackupCryptoError:
        raise
    except OSError as exc:
        raise BackupCryptoError("backup_decryption_failed") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {"plaintext_bytes": plaintext_bytes, "encrypted_bytes": encrypted_size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    key_parser = commands.add_parser("generate-key", help="Create a new random 32-byte key file")
    key_parser.add_argument("key_file")
    for name in ("encrypt", "decrypt"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("destination")
        command.add_argument("--key-file", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate-key":
            created = create_key_file(args.key_file)
            print(f"Backup key created: {created}")
            print("Store this key securely outside Yuki and the backup directory. It cannot be recovered if lost.")
        elif args.command == "encrypt":
            result = encrypt_file(args.source, args.destination, args.key_file)
            print(f"Encrypted backup created: {args.destination} ({result['encrypted_bytes']} bytes).")
        else:
            result = decrypt_file(args.source, args.destination, args.key_file)
            print(f"Backup authenticated and decrypted: {args.destination} ({result['plaintext_bytes']} bytes).")
    except (OSError, BackupCryptoError) as exc:
        print(f"Backup crypto operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
