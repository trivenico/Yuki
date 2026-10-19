"""Cross-platform subprocess runner with deadlines and bounded pipe capture."""
import subprocess
import threading
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

        proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        overflow = threading.Event()
        read_errors = []

        def drain(stream, name):
            target = outputs[name]
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    remaining = output_limit + 1 - len(target)
                    if remaining > 0:
                        target.extend(chunk[:remaining])
                    if len(target) > output_limit or len(chunk) > remaining:
                        overflow.set()
                        return
            except (OSError, ValueError) as exc:
                read_errors.append(exc)

        readers = [
            threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for thread in readers:
            thread.start()

        def write_input():
            try:
                if proc.stdin is not None:
                    with proc.stdin:
                        view = memoryview(payload)
                        offset = 0
                        while offset < len(view):
                            offset += proc.stdin.write(view[offset:offset + 4096]) or 0
            except (BrokenPipeError, OSError, ValueError):
                pass

        writer = None
        if proc.stdin is not None:
            writer = threading.Thread(target=write_input, daemon=True)
            writer.start()

        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if overflow.is_set():
                    proc.kill()
                    proc.wait()
                    raise RuntimeError("Skill process output exceeded the configured limit.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    proc.wait()
                    raise TimeoutError("Skill process exceeded its time limit.")
                time.sleep(min(0.01, remaining))
            for thread in readers:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in readers):
                proc.kill()
                raise TimeoutError("Skill output did not close before the deadline.")
            if overflow.is_set():
                raise RuntimeError("Skill process output exceeded the configured limit.")
            if read_errors:
                raise RuntimeError("Unable to read subprocess output safely.")
            return (
                outputs["stdout"].decode("utf-8", errors="replace"),
                outputs["stderr"].decode("utf-8", errors="replace"),
                proc.returncode,
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
