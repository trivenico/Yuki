"""Bounded adapter for the official Gemini CLI.

Uses the user's own official CLI installation/authentication. Yuki never reads
browser cookies, extracts credentials, or accepts arbitrary executable paths.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile

from providers.base import BaseProvider


class GeminiCLIProvider(BaseProvider):
    """Invoke Gemini CLI with bounded I/O and a minimal child-process environment."""

    # Keep only variables required to locate the executable, resolve the user's
    # CLI auth/config files, use temporary directories, and validate TLS. In
    # particular, do not forward arbitrary application secrets to child tools.
    _ENV_ALLOWLIST = frozenset({
        "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TEMP", "TMP", "TMPDIR",
        "SYSTEMROOT", "WINDIR", "PATHEXT", "LANG", "LC_ALL", "TERM",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    })

    @classmethod
    def _cli_environment(cls, source=None):
        """Return a copy containing only the documented runtime essentials."""
        source = os.environ if source is None else source
        return {key: value for key, value in source.items()
                if key.upper() in cls._ENV_ALLOWLIST and isinstance(value, str)}

    def __init__(self, *, executable="gemini", timeout=45, max_prompt_chars=20000,
                 max_output_chars=50000):
        if executable != "gemini":
            raise ValueError("Only the official 'gemini' executable is supported.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        if isinstance(max_prompt_chars, bool) or not isinstance(max_prompt_chars, int) or not 1 <= max_prompt_chars <= 20000:
            raise ValueError("max_prompt_chars must be between 1 and 20000.")
        if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int) or not 1 <= max_output_chars <= 50000:
            raise ValueError("max_output_chars must be between 1 and 50000.")
        self.executable = executable
        self.timeout = float(timeout)
        self.max_prompt_chars = max_prompt_chars
        self.max_output_chars = max_output_chars

    def is_available(self) -> bool:
        """Check executable installation only, not account authentication."""
        return shutil.which(self.executable) is not None

    @staticmethod
    def _stop_process(process) -> None:
        """Terminate the process group where supported, then reap the child."""
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        if len(prompt) > self.max_prompt_chars:
            raise ValueError(f"Prompt exceeds the {self.max_prompt_chars}-character limit.")
        executable = shutil.which(self.executable)
        if executable is None:
            raise RuntimeError("Gemini CLI is not installed; install and authenticate the official CLI first.")

        # Disk-backed output prevents communicate()/capture_output from retaining
        # an unbounded child-process response in Yuki's memory.
        with tempfile.TemporaryDirectory(prefix="yuki-gemini-") as workdir, \
             tempfile.TemporaryFile(mode="w+b") as stdout_file, \
             tempfile.TemporaryFile(mode="w+b") as stderr_file:
            kwargs = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(
                    [executable, "--prompt", prompt, "--sandbox", "--approval-mode", "plan"],
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    env=self._cli_environment(),
                    **kwargs,
                )
            except OSError as exc:
                raise RuntimeError("Gemini CLI could not be started.") from exc
            try:
                return_code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                self._stop_process(process)
                raise TimeoutError(f"Gemini CLI exceeded the {self.timeout:g}-second deadline.") from exc

            if return_code != 0:
                # Do not surface raw stderr, which can contain secrets or local paths.
                raise RuntimeError(f"Gemini CLI failed with exit code {return_code}; check its local authentication and logs.")
            stdout_file.seek(0)
            raw = stdout_file.read(self.max_output_chars + 1)
            output = raw.decode("utf-8", errors="replace").strip()
        if not output:
            raise RuntimeError("Gemini CLI returned an empty response.")
        if len(output) > self.max_output_chars:
            output = output[:self.max_output_chars] + "\n[Response truncated by Yuki.]"
        return output
