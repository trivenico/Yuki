"""Rootless Podman adapter for bounded, network-disabled upgrade test execution.

This adapter is a defense-in-depth boundary, not a claim that containers defeat
all kernel/runtime vulnerabilities. It fails closed unless rootless Podman and an
immutable image digest are configured. Production use still requires an audited
host, patched kernel/runtime, monitoring, and adversarial integration tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import time
import tempfile
import tarfile
from typing import Callable, Sequence

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

    def _workspace_digest(self) -> str:
        """Hash the complete mounted workspace using bounded, deterministic traversal."""
        digest = hashlib.sha256()
        count = 0
        total = 0
        for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            for dirname in dirs:
                if (current_path / dirname).is_symlink():
                    raise ToolInputError("symlink directories are not allowed in the worker workspace.")
            dirs[:] = sorted(dirs)
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink():
                    raise ToolInputError("symlink entries are not allowed in the worker workspace.")
                try:
                    metadata = path.stat()
                    relative = path.relative_to(self.root).as_posix()
                    if not path.is_file():
                        continue
                    count += 1
                    total += metadata.st_size
                    if count > 3000 or total > 100 * 1024 * 1024:
                        raise ToolInputError("worker workspace exceeds configured digest bounds.")
                    digest.update(relative.encode("utf-8"))
                    digest.update(b"\\0")
                    digest.update(str(metadata.st_size).encode("ascii"))
                    digest.update(b"\\0")
                    with path.open("rb") as stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                    digest.update(b"\\0")
                except OSError as exc:
                    raise ToolInputError("unable to hash the complete worker workspace.") from exc
        return digest.hexdigest()

    def _create_content_addressed_artifact(self, destination: Path) -> tuple[Path, str]:
        """Create a bounded tar artifact containing regular files only."""
        count = 0
        total = 0
        try:
            with tarfile.open(destination, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
                    current_path = Path(current)
                    for dirname in dirs:
                        if (current_path / dirname).is_symlink():
                            raise ToolInputError("symlink directories are not allowed in artifact.")
                    dirs[:] = sorted(dirs)
                    for name in sorted(files):
                        path = current_path / name
                        if path.is_symlink():
                            raise ToolInputError("symlink entries are not allowed in artifact.")
                        if not path.is_file():
                            continue
                        size = path.stat().st_size
                        count += 1
                        total += size
                        if count > 3000 or total > 50 * 1024 * 1024:
                            raise ToolInputError("content-addressed artifact exceeds configured bounds.")
                        relative = path.relative_to(self.root).as_posix()
                        info = archive.gettarinfo(str(path), arcname=relative)
                        if not info.isfile():
                            raise ToolInputError("only regular files are allowed in artifact.")
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
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
        """Copy a bounded, symlink-free workspace to a private per-run snapshot."""
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
            dirs[:] = sorted(dirs)
            for name in sorted(files):
                source = current_path / name
                if source.is_symlink():
                    raise ToolInputError("symlink entries are not allowed in the worker workspace.")
                try:
                    metadata = source.stat()
                    if not source.is_file():
                        continue
                    count += 1
                    total += metadata.st_size
                    if count > 3000 or total > 100 * 1024 * 1024:
                        raise ToolInputError("worker workspace exceeds configured snapshot bounds.")
                    target = target_dir / name
                    # Open the source once and copy the bytes actually observed. The
                    # approved digest is calculated from this private snapshot later.
                    with source.open("rb") as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                    target.chmod(0o400)
                except OSError as exc:
                    raise ToolInputError("unable to create immutable worker snapshot.") from exc
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
                "archive=pathlib.Path('/input/workspace.tar')\n"
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

        control_dir = Path(tempfile.mkdtemp(prefix="yuki-worker-control-"))
        cidfile = control_dir / "container.cid"
        command = [
            binary, "run", "--rm", "--cidfile", str(cidfile), "--pull=never",
            "--network=none", "--read-only", "--userns=keep-id",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--entrypoint", "python",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory_limit, "--cpus", self.cpu_limit,
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
            "--tmpfs", "/work:rw,nosuid,nodev,size=100m",
            "--mount", f"type=bind,src={artifact_path.parent},dst=/input,ro",
            "--env", "PYTHONIOENCODING=utf-8",
            "--env", "PYTHONUNBUFFERED=1",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            self.image, "-c", self._trusted_harness(), artifact_digest, test_module,
        ]
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
