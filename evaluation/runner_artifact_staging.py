"""Linux sealed-memfd staging for exact candidate artifact bytes.

This is a staging primitive, not a sandbox or trusted runner. It places bounded
artifact bytes in a kernel-sealed anonymous file and verifies the digest before
returning the descriptor. Execution still requires a separately isolated process.
"""
from __future__ import annotations
from dataclasses import dataclass
import fcntl, hashlib, os
from evaluation.execution_receipts import CandidateExecutionManifest

_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_REQUIRED_SEALS = (getattr(fcntl,"F_SEAL_SEAL",0) | getattr(fcntl,"F_SEAL_SHRINK",0)
    | getattr(fcntl,"F_SEAL_GROW",0) | getattr(fcntl,"F_SEAL_WRITE",0))

@dataclass(slots=True)
class SealedRunnerArtifact:
    """Open descriptor to an immutable anonymous artifact; caller must close it."""
    descriptor: int
    artifact_digest: str
    artifact_bytes: int
    def fileno(self):
        if self.descriptor < 0: raise ValueError("artifact descriptor is closed")
        return self.descriptor
    def duplicate_for_subprocess(self) -> int:
        """Return a CLOEXEC duplicate suitable for subprocess.run(pass_fds=(fd,)).

        The caller owns and must close the returned descriptor. Do not put it in
        an environment variable or expose it to unrelated child processes.
        """
        return os.dup(self.fileno())
    def close(self):
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
    def __enter__(self):
        self.fileno()
        return self
    def __exit__(self, exc_type, exc, traceback):
        self.close()

def stage_sealed_artifact(*, manifest: CandidateExecutionManifest,
                          artifact: bytes) -> SealedRunnerArtifact:
    """Verify bytes, seal a memfd against mutation, and re-hash its contents."""
    if not hasattr(os,"memfd_create") or not hasattr(fcntl,"F_ADD_SEALS"):
        raise RuntimeError("sealed artifact staging requires Linux memfd sealing support")
    if _REQUIRED_SEALS == 0: raise RuntimeError("required memfd seals are unavailable")
    if not isinstance(manifest,CandidateExecutionManifest) or not manifest.verify():
        raise ValueError("execution manifest is invalid")
    if not isinstance(artifact,bytes) or len(artifact)>_MAX_ARTIFACT_BYTES:
        raise ValueError("artifact must be bytes within the 8 MiB limit")
    digest=hashlib.sha256(artifact).hexdigest()
    if len(artifact)!=manifest.artifact_bytes or digest!=manifest.artifact_digest:
        raise ValueError("artifact bytes do not match the execution manifest")
    fd=os.memfd_create("yuki-runner-artifact",
        flags=getattr(os,"MFD_CLOEXEC",0)|getattr(os,"MFD_ALLOW_SEALING",0))
    try:
        view=memoryview(artifact)
        while view:
            count=os.write(fd,view)
            if count<=0: raise OSError("artifact staging write made no progress")
            view=view[count:]
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,_REQUIRED_SEALS)
        seals=fcntl.fcntl(fd,fcntl.F_GET_SEALS)
        if seals & _REQUIRED_SEALS != _REQUIRED_SEALS:
            raise RuntimeError("kernel did not apply all required artifact seals")
        os.lseek(fd,0,os.SEEK_SET)
        hasher=hashlib.sha256(); total=0
        while True:
            chunk=os.read(fd,65536)
            if not chunk: break
            hasher.update(chunk); total+=len(chunk)
        if total!=manifest.artifact_bytes or hasher.hexdigest()!=manifest.artifact_digest:
            raise ValueError("sealed artifact failed post-write digest verification")
        os.lseek(fd,0,os.SEEK_SET)
        return SealedRunnerArtifact(fd,digest,total)
    except Exception:
        os.close(fd)
        raise


def verify_sealed_artifact_descriptor(*, descriptor: int,
                                      manifest: CandidateExecutionManifest) -> str:
    """Re-verify a received sealed memfd before a runner consumes it.

    Uses pread so verification does not alter the descriptor's shared file
    offset. This proves byte integrity and kernel seals, not process isolation
    or the identity of the process that supplied the descriptor.
    """
    if type(descriptor) is not int or descriptor < 0:
        raise ValueError("descriptor must be a non-negative file descriptor")
    if not isinstance(manifest, CandidateExecutionManifest) or not manifest.verify():
        raise ValueError("execution manifest is invalid")
    if not hasattr(os, "pread") or not hasattr(fcntl, "F_GET_SEALS"):
        raise RuntimeError("descriptor verification requires Linux pread and memfd seals")
    try:
        info = os.fstat(descriptor)
        seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    except OSError as exc:
        raise ValueError("descriptor is not an accessible sealed memfd") from exc
    import stat
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("artifact descriptor must reference a regular file")
    if info.st_size != manifest.artifact_bytes or info.st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError("descriptor size does not match the execution manifest")
    if seals & _REQUIRED_SEALS != _REQUIRED_SEALS:
        raise ValueError("descriptor is missing required immutable memfd seals")
    hasher = hashlib.sha256()
    offset = 0
    while offset < info.st_size:
        chunk = os.pread(descriptor, min(65536, info.st_size - offset), offset)
        if not chunk:
            raise ValueError("descriptor ended before its declared size")
        hasher.update(chunk)
        offset += len(chunk)
    digest = hasher.hexdigest()
    if digest != manifest.artifact_digest:
        raise ValueError("descriptor bytes do not match the execution manifest")
    return digest
