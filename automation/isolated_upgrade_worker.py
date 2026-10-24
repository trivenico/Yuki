"""Rootless Podman adapter for bounded, network-disabled upgrade test execution.

This adapter is a defense-in-depth boundary, not a claim that containers defeat
all kernel/runtime vulnerabilities. It fails closed unless rootless Podman and an
immutable image digest are configured. Production use still requires an audited
host, patched kernel/runtime, monitoring, and adversarial integration tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
import stat
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import time
import tempfile
import tarfile
from typing import BinaryIO, Callable, Sequence

from automation.authorized_read_tools import ToolAuthorizationError, ToolInputError, ToolPrincipal

_IMAGE_DIGEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$")
_SAFE_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MAX_TIMEOUT = 120
_MAX_OUTPUT = 64 * 1024


@dataclass(frozen=True)
class IsolatedTestRunResult:
    target: str
    exit_code: int
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str


class PodmanIsolatedTestRunner:
    """Execute allow-listed unittest modules in a rootless, hardened Podman container."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        image: str,
        trusted_principal: Callable[[ToolPrincipal], bool],
        approve_run: Callable[[str, str, str], bool],
        audit: Callable[[dict], None],
        allowed_test_modules: Sequence[str],
        timeout_seconds: int = 60,
        podman_binary: str = "podman",
        output_limit_bytes: int = _MAX_OUTPUT,
        memory_limit: str = "512m",
        cpu_limit: str = "1",
        pids_limit: int = 64,
    ) -> None:
        self.root = Path(workspace).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("workspace must be an existing directory.")
        if not isinstance(image, str) or not _IMAGE_DIGEST.fullmatch(image):
            raise ValueError("worker image must be pinned by immutable sha256 digest.")
        if not all(callable(fn) for fn in (trusted_principal, approve_run, audit)):
            raise TypeError("trusted principal, approval and audit callbacks are required.")
        if not isinstance(allowed_test_modules, (tuple, list)) or not allowed_test_modules:
            raise ValueError("explicit allowed_test_modules are required.")
        if any(not isinstance(m, str) or not _SAFE_MODULE.fullmatch(m) for m in allowed_test_modules):
            raise ValueError("test modules must be simple allow-listed Python module names.")
        if len(set(allowed_test_modules)) != len(allowed_test_modules):
            raise ValueError("duplicate test modules are not allowed.")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= _MAX_TIMEOUT:
            raise ValueError("timeout_seconds must be an integer from 1 to 120.")
        if isinstance(output_limit_bytes, bool) or not isinstance(output_limit_bytes, int) or not 1024 <= output_limit_bytes <= _MAX_OUTPUT:
            raise ValueError("output_limit_bytes must be between 1024 and 65536.")
        if memory_limit not in {"256m", "512m", "1g"}:
            raise ValueError("memory_limit must be one of the reviewed fixed limits.")
        if cpu_limit not in {"0.5", "1", "2"}:
            raise ValueError("cpu_limit must be one of the reviewed fixed limits.")
        if isinstance(pids_limit, bool) or pids_limit not in {32, 64, 128}:
            raise ValueError("pids_limit must be one of the reviewed fixed limits.")
        if not isinstance(podman_binary, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", podman_binary):
            raise ValueError("podman_binary must be a simple executable name.")
        self.image = image
        self.trusted_principal = trusted_principal
        self.approve_run = approve_run
        self.audit = audit
        self.allowed_test_modules = tuple(allowed_test_modules)
        self.timeout_seconds = timeout_seconds
        self.podman_binary = podman_binary
        self.output_limit_bytes = output_limit_bytes
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit

    def _record(self, actor: str, outcome: str, digest: str, target: str, **extra: object) -> None:
        event = {"actor_id": actor[:200], "action": "isolated_tests.run", "outcome": outcome,
                 "content_digest": digest, "target": target[:200], "worker_image": self.image}
        event.update(extra)
        try:
            self.audit(event)
        except Exception as exc:
            raise RuntimeError("audit unavailable; isolated test operation denied.") from exc

    def _verify_rootless_runtime(self, binary: str) -> None:
        try:
            result = subprocess.run(
                [binary, "info", "--format", "json"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                check=False, shell=False, env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolAuthorizationError("rootless Podman runtime is unavailable.") from exc
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise ToolAuthorizationError("Podman runtime health check failed.")
        try:
            info = json.loads(result.stdout.decode("utf-8"))
            rootless = info["host"]["security"]["rootless"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise ToolAuthorizationError("unable to verify rootless Podman mode.") from exc
        if rootless is not True:
            raise ToolAuthorizationError("isolated test execution requires rootless Podman.")

    @staticmethod
    def _is_excluded_path(relative: Path) -> bool:
        """Exclude common credential stores and generated/runtime directories from worker input.

        This is defense in depth, not a secret scanner. Callers must stage only a
        reviewed workspace and must never place production credentials in it.
        """
        parts = tuple(part.casefold() for part in relative.parts)
        excluded_dirs = {
            ".git", ".ssh", ".aws", ".azure", ".gnupg", ".config",
            ".venv", "venv", "env", "node_modules", "__pycache__",
            ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        }
        # Include the final component so excluded directories are pruned
        # before os.walk descends into them, not merely filtered file-by-file.
        if any(part in excluded_dirs for part in parts):
            return True
        name = parts[-1] if parts else ""
        sensitive_names = {
            "credentials", "credentials.json", "secrets.json", "secret",
            "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts",
            "authorized_keys", ".netrc", ".npmrc", ".pypirc",
            "service-account.json", "service_account.json",
        }
        if name in sensitive_names or name.startswith(".env"):
            return True
        if name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")):
            return True
        return False

    @staticmethod
    def _open_component(name: str, flags: int, *, dir_fd: int) -> int:
        """Open one child component relative to a verified directory descriptor."""
        return os.open(name, flags, dir_fd=dir_fd)

    def _open_regular_file(self, path: Path) -> tuple[BinaryIO, os.stat_result]:
        """Open a regular file through no-follow directory descriptors.

        Each path component below the resolved workspace root is opened relative
        to an already-open directory descriptor with O_NOFOLLOW. This prevents a
        swapped parent directory or final-component symlink from redirecting the
        read outside the workspace. Platforms lacking these primitives fail closed.
        """
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        dir_fd_supported = os.open in getattr(os, "supports_dir_fd", set())
        if nofollow is None or directory_flag is None or not dir_fd_supported:
            raise ToolInputError("safe descriptor-based worker file access is unsupported on this platform.")
        # Resolve only the parent path to normalize platform aliases such as
        # macOS /var -> /private/var; preserve the leaf so symlinks are rejected,
        # not followed by Path.resolve().
        candidate = path.parent.resolve() / path.name
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ToolInputError("worker file path is outside the workspace root.") from exc
        parts = relative.parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ToolInputError("worker file path is not a safe relative path.")
        root_fd = None
        parent_fd = None
        file_fd = None
        try:
            root_fd = os.open(self.root, os.O_RDONLY | directory_flag | nofollow | getattr(os, "O_CLOEXEC", 0))
            parent_fd = root_fd
            for component in parts[:-1]:
                next_fd = self._open_component(
                    component, os.O_RDONLY | directory_flag | nofollow | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                if parent_fd != root_fd:
                    os.close(parent_fd)
                parent_fd = next_fd
            leaf = parts[-1]
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ToolInputError("only regular files are allowed in worker input.")
            file_fd = self._open_component(
                leaf, os.O_RDONLY | nofollow | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(file_fd)
            after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            identity_before = (before.st_dev, before.st_ino)
            identity_opened = (opened.st_dev, opened.st_ino)
            identity_after = (after.st_dev, after.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or identity_before != identity_opened
                or identity_opened != identity_after
            ):
                raise ToolInputError("worker input changed during safe file open.")
            stream = os.fdopen(file_fd, "rb")
            file_fd = None
            return stream, opened
        except ToolInputError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ToolInputError("unable to safely open worker input file.") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if parent_fd is not None and parent_fd != root_fd:
                os.close(parent_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _workspace_digest(self) -> str:
        """Hash a bounded workspace through verified regular-file descriptors."""
        digest = hashlib.sha256()
        count = 0
        total = 0
        for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            for dirname in dirs:
                if (current_path / dirname).is_symlink():
                    raise ToolInputError("symlink directories are not allowed in the worker workspace.")
            dirs[:] = sorted(
                dirname for dirname in dirs
                if not self._is_excluded_path((current_path / dirname).relative_to(self.root))
            )
            for name in sorted(files):
                path = current_path / name
                relative_path = path.relative_to(self.root)
                if self._is_excluded_path(relative_path):
                    continue
                stream, metadata = self._open_regular_file(path)
                with stream:
                    count += 1
                    if count > 3000:
                        raise ToolInputError("worker workspace exceeds configured digest bounds.")
                    relative = relative_path.as_posix()
                    digest.update(relative.encode("utf-8"))
                    digest.update(b"\\0")
                    size_bytes = 0
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        size_bytes += len(chunk)
                        total += len(chunk)
                        if total > 100 * 1024 * 1024:
                            raise ToolInputError("worker workspace exceeds configured digest bounds.")
                        digest.update(chunk)
                    if size_bytes != metadata.st_size:
                        raise ToolInputError("worker input changed size while hashing.")
                    digest.update(str(size_bytes).encode("ascii"))
                    digest.update(b"\\0")
        return digest.hexdigest()

    def _create_content_addressed_artifact(self, destination: Path) -> tuple[Path, str]:
        """Create a bounded tar artifact from bytes read through verified descriptors."""
        count = 0
        total = 0
        try:
            with tarfile.open(destination, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
                    current_path = Path(current)
                    for dirname in dirs:
                        if (current_path / dirname).is_symlink():
                            raise ToolInputError("symlink directories are not allowed in artifact.")
                    dirs[:] = sorted(
                        dirname for dirname in dirs
                        if not self._is_excluded_path((current_path / dirname).relative_to(self.root))
                    )
                    for name in sorted(files):
                        path = current_path / name
                        relative_path = path.relative_to(self.root)
                        if self._is_excluded_path(relative_path):
                            continue
                        stream, metadata = self._open_regular_file(path)
                        with stream:
                            count += 1
                            if count > 3000:
                                raise ToolInputError("content-addressed artifact exceeds configured bounds.")
                            remaining = 50 * 1024 * 1024 - total
                            if metadata.st_size > remaining:
                                raise ToolInputError("content-addressed artifact exceeds configured bounds.")
                            content = stream.read(metadata.st_size + 1)
                            if len(content) != metadata.st_size or stream.read(1):
                                raise ToolInputError("worker input changed size while packaging.")
                        total += len(content)
                        info = tarfile.TarInfo(name=relative_path.as_posix())
                        info.size = len(content)
                        info.mode = 0o400
                        info.mtime = 0
                        archive.addfile(info, io.BytesIO(content))
            digest = hashlib.sha256()
            with destination.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return destination, digest.hexdigest()
        except (OSError, tarfile.TarError) as exc:
            raise ToolInputError("unable to create content-addressed artifact.") from exc

    def _module_file(self, module: str) -> Path:
        if module not in self.allowed_test_modules:
            raise ToolAuthorizationError("test target is not explicitly allow-listed.")
        relative = Path(*module.split("."))
        candidate = self.root / relative.with_suffix(".py")
        package_init = self.root / relative / "__init__.py"
        source = candidate if candidate.is_file() else package_init
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(self.root)
            cursor = self.root
            for part in source.relative_to(self.root).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ToolInputError("symlink test targets are not allowed.")
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolInputError("test target must resolve inside the staged workspace.") from exc
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise ToolInputError("test target is missing or exceeds the source size limit.")
        return source

    def _ensure_container_removed(self, binary: str, cidfile: Path) -> None:
        if not cidfile.is_file():
            raise RuntimeError("worker container ID unavailable; cleanup cannot be verified.")
        container_id = cidfile.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise RuntimeError("worker container ID is malformed; cleanup cannot be verified.")
        try:
            exists = subprocess.run(
                [binary, "container", "exists", container_id], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False, shell=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            if exists.returncode == 1:
                return
            if exists.returncode != 0:
                raise RuntimeError("unable to verify worker container state.")
            removed = subprocess.run(
                [binary, "rm", "--force", container_id], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False, shell=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            if removed.returncode != 0:
                raise RuntimeError("worker container removal failed.")
            verify = subprocess.run(
                [binary, "container", "exists", container_id], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False, shell=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            if verify.returncode != 1:
                raise RuntimeError("worker container cleanup could not be confirmed.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("worker container cleanup could not be confirmed.") from exc

    def _create_workspace_snapshot(self, destination: Path) -> Path:
        """Copy a bounded workspace using verified file descriptors into a private snapshot."""
        destination.mkdir(mode=0o700)
        count = 0
        total = 0
        for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(self.root)
            target_dir = destination / relative_dir
            target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            for dirname in dirs:
                if (current_path / dirname).is_symlink():
                    raise ToolInputError("symlink directories are not allowed in the worker workspace.")
            dirs[:] = sorted(
                dirname for dirname in dirs
                if not self._is_excluded_path((current_path / dirname).relative_to(self.root))
            )
            for name in sorted(files):
                source = current_path / name
                relative_path = source.relative_to(self.root)
                if self._is_excluded_path(relative_path):
                    continue
                stream, metadata = self._open_regular_file(source)
                with stream:
                    count += 1
                    if count > 3000:
                        raise ToolInputError("worker workspace exceeds configured snapshot bounds.")
                    remaining = 100 * 1024 * 1024 - total
                    if metadata.st_size > remaining:
                        raise ToolInputError("worker workspace exceeds configured snapshot bounds.")
                    target = target_dir / name
                    copied = 0
                    with target.open("xb") as dst:
                        while True:
                            chunk = stream.read(min(1024 * 1024, remaining - copied + 1))
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > remaining:
                                raise ToolInputError("worker workspace exceeds configured snapshot bounds.")
                            dst.write(chunk)
                    if copied != metadata.st_size or stream.read(1):
                        raise ToolInputError("worker input changed size while snapshotting.")
                    total += copied
                target.chmod(0o400)
        for current, dirs, _files in os.walk(destination, topdown=False):
            Path(current).chmod(0o500)
        destination.chmod(0o500)
        return destination

    def run(self, *, principal: ToolPrincipal, test_module: str) -> IsolatedTestRunResult:
        """Run against a private snapshot so the original workspace cannot race approval."""
        with tempfile.TemporaryDirectory(prefix="yuki-worker-snapshot-") as temporary:
            snapshot = Path(temporary) / "workspace"
            try:
                self._create_workspace_snapshot(snapshot)
                runner = PodmanIsolatedTestRunner(
                    snapshot,
                    image=self.image,
                    trusted_principal=self.trusted_principal,
                    approve_run=self.approve_run,
                    audit=self.audit,
                    allowed_test_modules=self.allowed_test_modules,
                    timeout_seconds=self.timeout_seconds,
                    podman_binary=self.podman_binary,
                    output_limit_bytes=self.output_limit_bytes,
                    memory_limit=self.memory_limit,
                    cpu_limit=self.cpu_limit,
                    pids_limit=self.pids_limit,
                )
                return runner._run_snapshot(principal=principal, test_module=test_module)
            finally:
                # TemporaryDirectory must be able to remove read-only snapshot dirs.
                if snapshot.exists():
                    for current, _dirs, _files in os.walk(snapshot, topdown=False):
                        try:
                            Path(current).chmod(0o700)
                        except OSError:
                            pass
                    try:
                        snapshot.chmod(0o700)
                    except OSError:
                        pass

    def _run_snapshot(self, *, principal: ToolPrincipal, test_module: str) -> IsolatedTestRunResult:
        if not isinstance(principal, ToolPrincipal):
            raise ToolAuthorizationError("trusted authenticated principal required.")
        try:
            trusted = self.trusted_principal(principal) is True
        except Exception:
            trusted = False
        if not trusted or not isinstance(principal.actor_id, str) or not principal.actor_id.strip():
            raise ToolAuthorizationError("principal validation failed closed.")
        if not isinstance(principal.permissions, frozenset) or "tests:execute" not in principal.permissions:
            raise ToolAuthorizationError("tests:execute permission is required.")
        source = self._module_file(test_module)
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        workspace_digest = self._workspace_digest()
        with tempfile.TemporaryDirectory(prefix="yuki-worker-artifact-") as artifact_temp:
            artifact_path, artifact_digest = self._create_content_addressed_artifact(Path(artifact_temp) / "workspace.tar")
            run_digest = hashlib.sha256(
                f"{test_module}:{source_digest}:{workspace_digest}:{artifact_digest}:{self.image}:{self.timeout_seconds}:{self.memory_limit}:{self.cpu_limit}:{self.pids_limit}".encode()
            ).hexdigest()
            run_id = "isolated_test_" + run_digest[:24]
            try:
                approved = self.approve_run(principal.actor_id, run_id, run_digest) is True
            except Exception:
                approved = False
            if not approved:
                self._record(principal.actor_id, "denied", run_digest, test_module,
                             artifact_digest=artifact_digest)
                raise ToolAuthorizationError("explicit approval for this isolated artifact digest is required.")
            self._record(principal.actor_id, "requested", run_digest, test_module,
                         artifact_digest=artifact_digest)
            return self._execute_artifact(
                principal=principal, test_module=test_module, run_digest=run_digest,
                source_digest=source_digest, workspace_digest=workspace_digest,
                artifact_digest=artifact_digest, artifact_path=artifact_path,
            )

    @staticmethod
    def _trusted_harness() -> str:
        """Return controller-owned code that verifies and safely unpacks the artifact."""
        return (
                "import hashlib,os,pathlib,sys,tarfile,tempfile,unittest\n"
                "archive=pathlib.Path('/tmp/workspace.tar')\n"
                "expected=sys.argv[1]; target=sys.argv[2]\n"
                "h=hashlib.sha256(archive.read_bytes()).hexdigest()\n"
                "if h != expected: raise SystemExit('artifact digest mismatch')\n"
                "root=pathlib.Path('/work/source'); root.mkdir(parents=True,exist_ok=True)\n"
                "total=0; count=0\n"
                "with tarfile.open(archive,'r:') as tf:\n"
                " members=tf.getmembers()\n"
                " for m in members:\n"
                "  p=pathlib.PurePosixPath(m.name)\n"
                "  if p.is_absolute() or not p.parts or any(x in ('..','') for x in p.parts): raise SystemExit('unsafe archive path')\n"
                "  if not m.isfile() or m.issym() or m.islnk(): raise SystemExit('non-regular archive member')\n"
                "  count+=1; total+=m.size\n"
                "  if count>3000 or total>52428800: raise SystemExit('archive bounds exceeded')\n"
                "  out=root.joinpath(*p.parts); out.parent.mkdir(parents=True,exist_ok=True)\n"
                "  src=tf.extractfile(m)\n"
                "  if src is None: raise SystemExit('missing archive member data')\n"
                "  with src, out.open('xb') as dst:\n"
                "   import shutil; shutil.copyfileobj(src,dst,1048576)\n"
                "  out.chmod(0o400)\n"
                "os.chdir(root); sys.path.insert(0,str(root)); sys.dont_write_bytecode=True\n"
                "suite=unittest.defaultTestLoader.loadTestsFromName(target)\n"
                "result=unittest.TextTestRunner(verbosity=2).run(suite)\n"
                "raise SystemExit(0 if result.wasSuccessful() else 1)\n"
            )
    

    def _build_container_command(
        self, *, binary: str, cidfile: Path, artifact_path: Path,
        artifact_digest: str, test_module: str,
    ) -> list[str]:
        """Build the exact hardened Podman invocation for unit-testable review."""
        return [
            binary, "run", "--rm", "--cidfile", str(cidfile), "--pull=never",
            "--network=none", "--read-only", "--userns=keep-id", "--user=65532:65532",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--entrypoint", "python",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory_limit, "--cpus", self.cpu_limit,
            "--tmpfs", "/work:rw,noexec,nosuid,nodev,size=100m",
            "--mount", f"type=bind,src={artifact_path.parent},dst=/tmp,ro",
            # The artifact directory is mounted read-only at /tmp. Keep all runtime
            # scratch files on the dedicated writable, size-limited /work tmpfs.
            "--env", "TMPDIR=/work",
            "--env", "TMP=/work",
            "--env", "TEMP=/work",
            "--env", "PYTHONIOENCODING=utf-8",
            "--env", "PYTHONUNBUFFERED=1",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            self.image, "-c", self._trusted_harness(), artifact_digest, test_module,
        ]

    @staticmethod
    def _prepare_artifact_for_non_root(path: Path) -> None:
        """Make the private bind-mounted artifact readable to the fixed non-root UID."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = None
        directory_fd = None
        try:
            # Podman mounts the containing directory at /tmp. The fixed non-root
            # UID must be able to traverse it, while the artifact itself stays
            # read-only and the host-created directory remains otherwise private.
            directory_fd = os.open(path.parent, directory_flags)
            os.fchmod(directory_fd, 0o711)
            fd = os.open(path.name, flags, dir_fd=directory_fd)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ToolInputError("worker artifact must be a regular file.")
            os.fchmod(fd, 0o444)
            updated = os.fstat(fd)
            if stat.S_IMODE(updated.st_mode) & 0o444 != 0o444:
                raise ToolInputError("worker artifact is not readable by the non-root worker.")
        except ToolInputError:
            raise
        except OSError as exc:
            raise ToolInputError("unable to prepare artifact for non-root worker access.") from exc
        finally:
            if fd is not None:
                os.close(fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _execute_artifact(
        self, *, principal: ToolPrincipal, test_module: str, run_digest: str,
        source_digest: str, workspace_digest: str, artifact_digest: str, artifact_path: Path,
    ) -> IsolatedTestRunResult:
        binary = shutil.which(self.podman_binary)
        if not binary:
            self._record(principal.actor_id, "runtime_unavailable", run_digest, test_module)
            raise ToolAuthorizationError("Podman executable is unavailable; isolated tests denied.")
        try:
            self._verify_rootless_runtime(binary)
        except Exception:
            self._record(principal.actor_id, "runtime_verification_failed", run_digest, test_module)
            raise

        try:
            self._prepare_artifact_for_non_root(artifact_path)
        except ToolInputError:
            self._record(principal.actor_id, "artifact_permission_failed", run_digest, test_module)
            raise

        control_dir = Path(tempfile.mkdtemp(prefix="yuki-worker-control-"))
        cidfile = control_dir / "container.cid"
        command = self._build_container_command(
            binary=binary, cidfile=cidfile, artifact_path=artifact_path,
            artifact_digest=artifact_digest, test_module=test_module,
        )
        start = time.monotonic()
        stdout = bytearray()
        stderr = bytearray()
        timed_out = False
        output_exceeded = False
        proc = None
        try:
            try:
                proc = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    shell=False, close_fds=True, start_new_session=True,
                    env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
                )
                assert proc.stdout is not None and proc.stderr is not None
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ, stdout)
                    selector.register(proc.stderr, selectors.EVENT_READ, stderr)
                    while selector.get_map() or proc.poll() is None:
                        remaining = self.timeout_seconds - (time.monotonic() - start)
                        if remaining <= 0:
                            timed_out = True
                            break
                        for key, _ in selector.select(min(0.1, remaining)):
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            sink = key.data
                            if len(stdout) + len(stderr) + len(chunk) > self.output_limit_bytes:
                                room = max(0, self.output_limit_bytes - len(stdout) - len(stderr))
                                sink.extend(chunk[:room])
                                output_exceeded = True
                                break
                            sink.extend(chunk)
                        if output_exceeded:
                            break
                if timed_out or output_exceeded:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                # A killed Podman client does not prove the container stopped. Verify
                # the container ID is gone; forcibly remove it when it still exists.
                self._ensure_container_removed(binary, cidfile)
            except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
                if proc is not None and proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                self._record(principal.actor_id, "cleanup_failed", run_digest, test_module)
                raise RuntimeError("isolated worker failed or cleanup could not be verified.") from exc
        finally:
            shutil.rmtree(control_dir, ignore_errors=True)

        duration = round(time.monotonic() - start, 3)
        if timed_out:
            exit_code = 124
            outcome = "timed_out"
        elif output_exceeded:
            exit_code = 125
            outcome = "output_limit_exceeded"
        else:
            exit_code = proc.returncode if proc is not None and proc.returncode is not None else 125
            outcome = "succeeded" if exit_code == 0 else "failed"
        self._record(principal.actor_id, outcome, run_digest, test_module,
                     exit_code=exit_code, timed_out=timed_out, output_exceeded=output_exceeded,
                     duration_seconds=duration, source_digest=source_digest,
                     workspace_digest=workspace_digest, artifact_digest=artifact_digest)
        return IsolatedTestRunResult(test_module, exit_code, timed_out or output_exceeded,
                                     duration, stdout.decode("utf-8", errors="replace"),
                                     stderr.decode("utf-8", errors="replace"))
