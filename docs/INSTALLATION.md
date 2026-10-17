# Install and run Yuki

## Requirements

- Python 3.10 or newer.
- Git, if you are cloning the repository.
- No AI API key is required for the current mock-provider prototype.
- No third-party Python packages are currently required.

## Windows

1. Install Python 3.10+ from https://www.python.org/downloads/ if it is not already installed. During setup, enable **Add Python to PATH**.
2. Download the repository ZIP from GitHub and extract it, or clone the repository.
3. Open the extracted `Yuki` folder.
4. Run `install_windows.bat`.
5. When setup passes, run `run_yuki_windows.bat`.

The setup script creates `.venv`, checks the Python version, processes `requirements.txt`, and runs the unit tests. It stops with a non-zero exit code if a required setup step or test fails.

## macOS or Linux

From the repository directory:

```sh
sh install_unix.sh
sh run_yuki_unix.sh
```

## Expected successful setup output

Exact test names and counts may change as the project grows. A successful run should end with a unittest summary similar to:

```text
----------------------------------------------------------------------
Ran <number> tests in <duration>s

OK

==========================================
 Setup completed successfully.
 Start Yuki with: run_yuki_windows.bat
==========================================
```

The number and duration are intentionally placeholders, not a claim about a run on your computer. The installer prints the real results from your environment.

## Expected first launch

```text
Yuki terminal prototype. Type /help for commands; /quit to exit.
Safety note: tools are allow-listed; audit records contain metadata, not command arguments.

You >
```

Try `/help`, `/status`, `/dashboard`, `/task add Build my first project`, `/task list`, and `/quit`.

## Diagnostics and tests

Windows:

```bat
.venv\Scripts\python.exe scripts\doctor.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

macOS/Linux:

```sh
.venv/bin/python scripts/doctor.py
.venv/bin/python -m unittest discover -s tests -v
```

## Important limitations

The current provider is an offline mock: it does not generate real AI answers, build a software project autonomously, or call an online AI service. Voice activation, speech recognition, text-to-speech, browser control, and cybersecurity lab automation are not implemented. Those features must be added and tested in later phases. Do not use Yuki for production or consequential security actions yet.
