# Install and run Yuki

## Requirements

- Python 3.10 or newer.
- Git, if you are cloning the repository.
- PyYAML, installed from `requirements.txt` for YAML configuration.
- No AI API key is required for the mock-provider prototypes.

## Windows

1. Install Python 3.10+ from https://www.python.org/downloads/ if it is not already installed. During setup, enable **Add Python to PATH**.
2. Download the repository ZIP from GitHub and extract it, or clone the repository.
3. Open the extracted `Yuki` folder.
4. Run `install_windows.bat`.
5. For the existing feature-rich prototype, run `run_yuki_windows.bat`.

The setup script creates `.venv`, checks the Python version, processes `requirements.txt`, and runs the unit tests. It stops with a non-zero exit code if a required setup step or test fails.

## macOS or Linux

From the repository directory:

```sh
sh install_unix.sh
sh run_yuki_unix.sh
```

## Run the new guarded core

After dependencies are installed, run:

```sh
python main.py
```

On Windows, you can use `.venv\\Scripts\\python.exe main.py`; on macOS/Linux, use `.venv/bin/python main.py`.

The core provides `/help`, `/status`, `/skills`, `/skill time`, `/skill system`, `/skill files .`, and `/quit`. Each skill invocation prompts for approval and is denied unless you type `y`. The initial skills are read-only. The files skill lists names inside its configured workspace; it does not read or modify file contents.

## Expected successful setup output

Exact test names and counts may change as the project grows. A successful run should end with a unittest summary similar to:

```text
----------------------------------------------------------------------
Ran <number> tests in <duration>s

OK
```

The number and duration are intentionally placeholders, not a claim about a run on your computer.

## Diagnostics and tests

Windows:

```bat
.venv\\Scripts\\python.exe scripts\\doctor.py
.venv\\Scripts\\python.exe -m unittest discover -s tests -v
```

macOS/Linux:

```sh
.venv/bin/python scripts/doctor.py
.venv/bin/python -m unittest discover -s tests -v
```

## Important limitations

The current provider is an offline mock: it does not generate real AI answers, build a software project autonomously, or call an online AI service. Voice activation, speech recognition, text-to-speech, browser control, graphical dashboard, and cybersecurity lab automation are not implemented. The `hackback/` folder describes safe design boundaries only. Those features must be implemented and tested in later phases. Do not use Yuki for production or consequential security actions yet.
