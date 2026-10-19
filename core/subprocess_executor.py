"""Cross-platform subprocess runner with timeout and bounded captured output."""
import subprocess


class SubprocessExecutor:
    def __init__(self, timeout_seconds: int = 3, max_output_bytes: int = 1024 * 1024):
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 30:
            raise ValueError("Timeout must be between 1 and 30 seconds.")
        if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or not 1 <= max_output_bytes <= 2 * 1024 * 1024:
            raise ValueError("Output limit is invalid.")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def run(self, cmd, input_data=None, *, timeout_seconds=None, max_output_bytes=None):
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        output_limit = self.max_output_bytes if max_output_bytes is None else max_output_bytes
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 30:
            raise ValueError("Timeout must be between 1 and 30 seconds.")
        if not isinstance(output_limit, int) or isinstance(output_limit, bool) or not 1 <= output_limit <= 2 * 1024 * 1024:
            raise ValueError("Output limit is invalid.")
        if not isinstance(cmd, (list, tuple)) or not cmd or any(not isinstance(part, str) or not part for part in cmd):
            raise ValueError("Command must be a non-empty argument list.")
        if input_data is not None and not isinstance(input_data, str):
            raise ValueError("Subprocess input must be text.")
        if input_data is not None and len(input_data.encode("utf-8")) > 20_000:
            raise ValueError("Subprocess input exceeds the size limit.")

        # communicate() can buffer arbitrary output. Use a temporary file for each
        # stream, wait with a deadline, then reject oversized output before reading it.
        import os
        import tempfile
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.Popen(
                list(cmd), stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                stdout=stdout_file, stderr=stderr_file, shell=False,
            )
            try:
                if input_data is not None:
                    try:
                        proc.stdin.write(input_data.encode("utf-8"))
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise TimeoutError("Skill process exceeded its time limit.") from None
            stdout_size = stdout_file.tell()
            stderr_size = stderr_file.tell()
            if stdout_size > output_limit or stderr_size > output_limit:
                raise RuntimeError("Skill process output exceeded the configured limit.")
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(output_limit + 1).decode("utf-8", errors="replace")
            stderr = stderr_file.read(output_limit + 1).decode("utf-8", errors="replace")
            return stdout, stderr, proc.returncode
