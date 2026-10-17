"""Integrated entry point for Yuki's guarded core agent.

Run with: python main.py
The existing legacy terminal interface remains available with: python yuki.py
"""
from pathlib import Path

from companion import DEFAULT_PERSONA
from core.agent import YukiCoreAgent
from core.config import load_config
from core.permissions import PermissionGate
from skills import FileSkill, SystemSkill, TimeSkill


def _ask_approval(request):
    print("\nPermission request")
    print(f"  Action: {request.action}")
    print(f"  Reason: {request.reason}")
    print(f"  Risk:   {request.risk}")
    try:
        answer = input("Approve this single action? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "y"


def build_agent(config):
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    skills = [TimeSkill(), SystemSkill(), FileSkill(workspace)]
    return YukiCoreAgent(skills=skills, permission_gate=PermissionGate(_ask_approval))


def print_help():
    print("""Yuki guarded core
  /help                  Show commands
  /status                Show configured capabilities
  /skills                List registered skills
  /skill NAME [ARG]      Request one skill (approval required)
  /quit                  Exit

Available initial skills are read-only: time, system, files.
No AI model, microphone, voice engine, shell tool, or security scanner is enabled here.
For the original feature-rich prototype, run: python yuki.py
""")


def main():
    config = load_config()
    agent = build_agent(config)
    print(DEFAULT_PERSONA.greeting)
    print("Yuki guarded core is ready. Type /help for commands.")
    while True:
        try:
            command = input("\nYou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nYuki > Goodbye.")
            return
        if not command:
            continue
        if command in {"/quit", "/exit"}:
            print("Yuki > Goodbye.")
            return
        if command == "/help":
            print_help()
        elif command == "/status":
            print(f"Persona: {agent.persona.name}; wake word: {agent.persona.wake_word}")
            print(f"Workspace: {config['workspace']}")
            print("AI provider: not configured; voice: disabled; security lab: not enabled")
        elif command == "/skills":
            for skill in agent.available_skills():
                print(f"- {skill['name']} [{skill['risk']}]: {skill['description']}")
        elif command.startswith("/skill "):
            parts = command.split(maxsplit=2)
            name = parts[1] if len(parts) > 1 else ""
            argument = parts[2] if len(parts) > 2 else ""
            response = agent.run_skill(name, argument)
            print(f"Yuki > {response.message}")
            if response.ok and response.data is not None:
                print(response.data)
        else:
            print("Yuki > This core runtime currently routes explicit /skill requests only. "
                  "Use /help; connect a real model provider in a later phase.")


if __name__ == "__main__":
    main()
