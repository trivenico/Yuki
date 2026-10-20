"""Bounded subprocess runner for trusted, explicitly registered skill commands.

This is process isolation, not a security sandbox: commands still run with the
current user's OS privileges. Use a container or OS sandbox for untrusted code.
"""
from __future__ import annotations
import json
import os
import signal
import subprocess
import threading
from typing import Any, Sequence

class SubprocessExecutor:
    def __init__(self, *, timeout_seconds: int = 10, max_output_bytes: int = 65536):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120.")
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 1_000_000:
            raise ValueError("max_output_bytes must be between 1 and 1000000.")
        self.timeout_seconds, self.max_output_bytes = timeout_seconds, max_output_bytes

    def run(self, command: Sequence[str], payload: dict[str, Any], *, timeout_seconds: int | None = None,
            max_output_bytes: int | None = None) -> dict[str, Any]:
        if not isinstance(command, (list, tuple)) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ValueError("Command must be a non-empty sequence of strings.")
        timeout = timeout_seconds or self.timeout_seconds
        limit = max_output_bytes or self.max_output_bytes
        if type(timeout) is not int or not 1 <= timeout <= 120 or type(limit) is not int or not 1 <= limit <= 1_000_000:
            raise ValueError("Invalid execution limits.")
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > 65536:
            raise ValueError("Skill input exceeds 64 KiB.")
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
        proc = None
        overflow = threading.Event()
        process_lock = threading.Lock()

        def terminate_process_tree() -> None:
            # POSIX children are placed in a fresh session, so kill the entire
            # process group rather than leaving grandchildren behind. On
            # Windows, terminate the direct child until a Job Object backend exists.
            with process_lock:
                current = proc
            if current is None or current.poll() is not None:
                return
            try:
                if os.name == "posix":
                    os.killpg(current.pid, signal.SIGKILL)
                else:
                    current.kill()
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    current.kill()
                except OSError:
                    pass
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": limit, "stderr": min(limit, 16384)}

        def drain(name: str, pipe) -> None:
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        return
                    remaining = limits[name] - len(output[name])
                    if len(chunk) > remaining:
                        if remaining > 0:
                            output[name].extend(chunk[:remaining])
                        overflow.set()
                        terminate_process_tree()
                        return
                    output[name].extend(chunk)
            finally:
                pipe.close()

        try:
            popen_options = {}
            if os.name == "posix":
                popen_options["start_new_session"] = True
            elif os.name == "nt":
                popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, close_fds=True, **popen_options)
        except OSError as exc:
            raise RuntimeError("Skill process could not be started.") from exc
        readers = [
            threading.Thread(target=drain, args=("stdout", proc.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", proc.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            assert proc.stdin is not None
            try:
                proc.stdin.write(body)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process_tree()
            proc.wait()
            raise RuntimeError("Skill execution timed out.") from exc
        finally:
            if proc.poll() is None:
                terminate_process_tree()
                proc.wait()
            for reader in readers:
                reader.join(timeout=2)
        if overflow.is_set():
            raise RuntimeError("Skill output exceeded configured limits.")
        if proc.returncode != 0:
            raise RuntimeError("Skill process returned a non-zero exit code.")
        try:
            result = json.loads(bytes(output["stdout"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Skill process returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Skill process result must be a JSON object.")
        return result
