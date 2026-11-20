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
import threading

from providers.base import BaseProvider


class GeminiCLIProvider(BaseProvider):
    """Invoke Gemini CLI with bounded I/O and a minimal child-process environment."""

    availability_scope = "executable_installed_only"

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

        # Drain stdout continuously so the child cannot block on a full pipe,
        # but retain only a bounded prefix. stderr is discarded to avoid leaking
        # secrets or allowing unbounded diagnostic output to consume disk.
        with tempfile.TemporaryDirectory(prefix="yuki-gemini-") as workdir:
            kwargs = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(
                    [executable, "--sandbox", "--approval-mode", "plan"],
                    cwd=workdir,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    env=self._cli_environment(),
                    **kwargs,
                )
            except OSError as exc:
                raise RuntimeError("Gemini CLI could not be started.") from exc

            try:
                if process.stdin is None:
                    raise OSError("Gemini CLI input pipe was unavailable.")
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
            except (OSError, ValueError) as exc:
                self._stop_process(process)
                raise RuntimeError("Gemini CLI prompt could not be delivered safely.") from exc

            captured = bytearray()
            reader_errors = []
            capture_limit = self.max_output_chars * 4 + 4

            def drain_stdout():
                stream = process.stdout
                if stream is None:
                    return
                try:
                    while True:
                        chunk = stream.read(8192)
                        if not chunk:
                            break
                        remaining = capture_limit - len(captured)
                        if remaining > 0:
                            captured.extend(chunk[:remaining])
                except Exception:
                    # A partial response is not trustworthy if pipe draining
                    # failed for any ordinary I/O/stream reason; surface a
                    # sanitized error on the caller thread.
                    reader_errors.append(True)
                finally:
                    try:
                        stream.close()
                    except Exception:
                        reader_errors.append(True)

            reader = threading.Thread(target=drain_stdout, name="yuki-gemini-output", daemon=True)
            try:
                reader.start()
            except Exception as exc:
                self._stop_process(process)
                raise RuntimeError("Gemini CLI output reader could not be started.") from exc
            try:
                return_code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                self._stop_process(process)
                reader.join(timeout=5)
                raise TimeoutError(f"Gemini CLI exceeded the {self.timeout:g}-second deadline.") from exc
            except BaseException:
                # Interruptions and unexpected wait failures must not orphan a
                # child process or leave its pipe-draining thread behind.
                self._stop_process(process)
                reader.join(timeout=5)
                raise
            reader.join(timeout=5)
            if reader.is_alive():
                self._stop_process(process)
                raise RuntimeError("Gemini CLI output stream did not close cleanly.")
            if reader_errors:
                raise RuntimeError("Gemini CLI output could not be read completely.")
            if return_code != 0:
                raise RuntimeError(f"Gemini CLI failed with exit code {return_code}; check its local authentication and logs.")
            output = bytes(captured).decode("utf-8", errors="replace").strip()
        if not output:
            raise RuntimeError("Gemini CLI returned an empty response.")
        if len(output) > self.max_output_chars:
            output = output[:self.max_output_chars] + "\n[Response truncated by Yuki.]"
        return output
