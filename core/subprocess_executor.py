"""Subprocess runner with deadline enforcement and bounded pipe capture."""
import os
import selectors
import subprocess
import time


class SubprocessExecutor:
    def __init__(self, timeout_seconds: int = 3, max_output_bytes: int = 1024 * 1024):
        self._validate_limits(timeout_seconds, max_output_bytes)
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def _validate_limits(timeout, output_limit):
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise ValueError("Timeout must be greater than 0 and at most 30 seconds.")
        if type(output_limit) is not int or not 1 <= output_limit <= 2 * 1024 * 1024:
            raise ValueError("Output limit is invalid.")

    def run(self, cmd, input_data=None, *, timeout_seconds=None, max_output_bytes=None):
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        output_limit = self.max_output_bytes if max_output_bytes is None else max_output_bytes
        self._validate_limits(timeout, output_limit)
        if not isinstance(cmd, (list, tuple)) or not cmd or any(not isinstance(part, str) or not part for part in cmd):
            raise ValueError("Command must be a non-empty argument list.")
        if input_data is not None and not isinstance(input_data, str):
            raise ValueError("Subprocess input must be text.")
        payload = b"" if input_data is None else input_data.encode("utf-8")
        if len(payload) > 20_000:
            raise ValueError("Subprocess input exceeds the size limit.")

        deadline = time.monotonic() + timeout
        proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        input_offset = 0
        try:
            if proc.stdout is None or proc.stderr is None:
                raise RuntimeError("Unable to capture subprocess output safely.")
            os.set_blocking(proc.stdout.fileno(), False)
            os.set_blocking(proc.stderr.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ, ("out", stdout))
            selector.register(proc.stderr, selectors.EVENT_READ, ("err", stderr))
            if proc.stdin is not None:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, ("in", None))

            while selector.get_map() or proc.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Skill process exceeded its time limit.")
                events = selector.select(min(remaining, 0.05))
                for key, _ in events:
                    kind, target = key.data
                    stream = key.fileobj
                    if kind == "in":
                        if input_offset >= len(payload):
                            selector.unregister(stream)
                            stream.close()
                            continue
                        try:
                            written = os.write(stream.fileno(), payload[input_offset:input_offset + 4096])
                            input_offset += written
                        except BrokenPipeError:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        try:
                            chunk = os.read(stream.fileno(), min(65536, output_limit + 1 - len(target)))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        target.extend(chunk)
                        if len(target) > output_limit:
                            raise RuntimeError("Skill process output exceeded the configured limit.")
                if proc.poll() is not None and not selector.get_map():
                    break
            returncode = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), returncode
        except (TimeoutError, RuntimeError):
            proc.kill()
            proc.wait()
            raise
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise TimeoutError("Skill process exceeded its time limit.") from None
        finally:
            selector.close()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
