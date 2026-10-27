"""Cross-platform desktop shell for the same guarded CLI runtime.

The GUI does not implement a second tool execution path. It launches main.py as
a child process and forwards typed commands/approval answers to that runtime.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_TRANSCRIPT_CHARS = 1_000_000


def build_runtime_command() -> list[str]:
    """Return a shell-free command for the authoritative guarded runtime."""
    return [sys.executable, "-u", str(ROOT / "main.py")]


def run_desktop() -> None:
    """Run the desktop console; operational commands retain CLI approval gates."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:
        raise RuntimeError(
            "Tkinter is unavailable. Install your operating system's Python Tk support, "
            "or use the CLI with: python yuki_cli.py start."
        ) from exc

    root = tk.Tk()
    root.title("Yuki — Desktop")
    root.geometry("980x680")
    root.minsize(680, 420)

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="Yuki Desktop · commands and approvals run through the guarded CLI runtime",
    ).pack(anchor="w", pady=(0, 8))

    output_frame = ttk.Frame(frame)
    output_frame.pack(side="top", fill="both", expand=True)
    transcript = tk.Text(output_frame, wrap="word", state="disabled", undo=False)
    transcript.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=transcript.yview)
    transcript.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")

    entry_row = ttk.Frame(frame)
    entry_row.pack(fill="x", pady=(8, 0))
    command_entry = ttk.Entry(entry_row)
    command_entry.pack(side="left", fill="x", expand=True)
    send_button = ttk.Button(entry_row, text="Send")
    send_button.pack(side="left", padx=(8, 0))

    output_queue: queue.Queue[str | None] = queue.Queue()
    try:
        process = subprocess.Popen(
            build_runtime_command(),
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
    except OSError as exc:
        messagebox.showerror("Yuki could not start", f"Runtime launch failed ({type(exc).__name__}).")
        root.destroy()
        return

    def read_output() -> None:
        stream = process.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            while True:
                char = stream.read(1)
                if char == "":
                    break
                output_queue.put(char)
        finally:
            output_queue.put(None)

    threading.Thread(target=read_output, name="yuki-desktop-output", daemon=True).start()

    def append_output(value: str) -> None:
        transcript.configure(state="normal")
        transcript.insert("end", value)
        current_count = transcript.count("1.0", "end-1c", "chars")
        if current_count and current_count[0] > MAX_TRANSCRIPT_CHARS:
            excess = current_count[0] - MAX_TRANSCRIPT_CHARS
            transcript.delete("1.0", f"1.0+{excess}c")
        transcript.see("end")
        transcript.configure(state="disabled")

    def drain_output() -> None:
        chunks = []
        finished = False
        while True:
            try:
                item = output_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                finished = True
                break
            chunks.append(item)
        if chunks:
            append_output("".join(chunks))
        if finished:
            send_button.configure(state="disabled")
            command_entry.configure(state="disabled")
            append_output("\n[Yuki runtime has exited.]\n")
            return
        root.after(35, drain_output)

    def send_command(_event=None) -> None:
        command = command_entry.get()
        if not command.strip():
            return
        if process.poll() is not None or process.stdin is None:
            append_output("\n[Yuki runtime is not running.]\n")
            return
        try:
            process.stdin.write(command + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            append_output("\n[Could not send input to the Yuki runtime.]\n")
            return
        command_entry.delete(0, "end")
        # A pipe does not echo terminal input, so echo it here for visibility.
        append_output(command + "\n")

    def close_window() -> None:
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("/quit\n")
                    process.stdin.flush()
                process.wait(timeout=1.5)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    process.kill()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        root.destroy()

    send_button.configure(command=send_command)
    command_entry.bind("<Return>", send_command)
    root.protocol("WM_DELETE_WINDOW", close_window)
    command_entry.focus_set()
    root.after(35, drain_output)
    root.mainloop()


if __name__ == "__main__":
    run_desktop()
