"""Official Gemini CLI adapter for account-authenticated, non-interactive prompts.

This adapter never reads browser cookies or API keys. The user must install and
authenticate the official Gemini CLI in the same OS account that runs Yuki.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from providers.base import BaseProvider


class GeminiCLIProvider(BaseProvider):
    """Run Gemini CLI in a fresh temporary working directory with a deadline."""

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
        """Check installation only; this does not guarantee account authentication."""
        return shutil.which(self.executable) is not None

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        if len(prompt) > self.max_prompt_chars:
            raise ValueError(f"Prompt exceeds the {self.max_prompt_chars}-character limit.")
        executable = shutil.which(self.executable)
        if executable is None:
            raise RuntimeError("Gemini CLI is not installed; install and authenticate the official CLI first.")

        # Use a fresh empty working directory, non-interactive prompt mode, and
        # sandbox/plan controls. Never pass through arbitrary user-supplied flags.
        with tempfile.TemporaryDirectory(prefix="yuki-gemini-") as workdir:
            try:
                result = subprocess.run(
                    [executable, "--prompt", prompt, "--sandbox", "--approval-mode", "plan"],
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                    shell=False,
                    env=os.environ.copy(),
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"Gemini CLI exceeded the {self.timeout:g}-second deadline.") from exc
            except OSError as exc:
                raise RuntimeError("Gemini CLI could not be started.") from exc

        output = (result.stdout or "").strip()
        if result.returncode != 0:
            # Do not surface raw stderr, which may contain local paths or secrets.
            raise RuntimeError(f"Gemini CLI failed with exit code {result.returncode}; check its local authentication and logs.")
        if not output:
            raise RuntimeError("Gemini CLI returned an empty response.")
        if len(output) > self.max_output_chars:
            output = output[:self.max_output_chars] + "\n[Response truncated by Yuki.]"
        return output
