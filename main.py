"""Integrated guarded Yuki runtime.

Run with: python main.py
The legacy feature-rich CLI remains available with: python yuki.py
"""
from datetime import datetime
from pathlib import Path

from companion import DEFAULT_PERSONA
from companion.memory import MemoryStore
from companion.proactive import ReminderQueue
from core.agent import YukiCoreAgent
from core.config import load_config
from core.permissions import PermissionGate
from skills.registry import SkillRegistry
from voice.stt import FasterWhisperSTT, STTError
from voice.tts import LocalTTS, TTSError


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


def _confirm(action, reason, risk="medium"):
    from core.permissions import ApprovalRequest
    return _ask_approval(ApprovalRequest(action=action, reason=reason, risk=risk))


def build_agent(config):
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry.defaults(workspace)
    return YukiCoreAgent(registry=registry, permission_gate=PermissionGate(_ask_approval))


def print_help():
    print("""Yuki guarded core
  /help                         Show commands
  /status                       Show configured capabilities
  /skills                       List registered skills
  /skill NAME [ARG]             Request one skill (approval required)
  /memory add TEXT              Save a local note (approval required)
  /memory list                  List recent notes
  /memory delete ID             Delete a note (approval required)
  /memory clear                 Delete all notes (approval required)
  /reminder add ISO_DATETIME | TEXT
                                Add an ISO-8601 reminder with timezone
  /reminders                    List reminders
  /reminders due                Show and mark due reminders delivered
  /voice status                 Show voice adapter status
  /voice speak TEXT             Speak text locally (requires voice opt-in)
  /voice transcribe AUDIO_PATH  Transcribe an audio file (requires voice opt-in)
  /quit                         Exit

Initial skills are read-only: time, system, files. Memory and reminders are local.
Voice uses optional packages; it never opens a microphone. No shell or exploit execution is enabled.
""")


def main():
    config = load_config()
    workspace = Path(config["workspace"])
    agent = build_agent(config)
    memory = MemoryStore(workspace / "yuki_memory.json")
    reminders = ReminderQueue(workspace / "yuki_reminders.json")
    tts = LocalTTS(enabled=config["voice_enabled"], rate=config["tts_rate"])
    stt = FasterWhisperSTT(enabled=config["voice_enabled"], model_size=config["stt_model"])
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
            print(f"AI provider: {config['provider']} (model not connected); voice enabled: {config['voice_enabled']}")
            print(f"Security lab enabled: {config['security_lab_enabled']}")
            print(f"Registered skills: {', '.join(agent.registry.names())}")
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
        elif command.startswith("/memory add "):
            note = command[len("/memory add "):].strip()
            if _confirm("memory:add", "Save the supplied note to local Yuki memory."):
                try:
                    item = memory.add(note)
                    print(f"Yuki > Saved note {item['id']}.")
                except (ValueError, RuntimeError) as exc:
                    print(f"Yuki > Memory was not changed: {exc}")
            else:
                print("Yuki > Memory write cancelled.")
        elif command == "/memory list":
            for item in memory.list():
                print(f"{item['id']} [{item.get('category', 'note')}] {item['created_at']}: {item['text']}")
        elif command.startswith("/memory delete "):
            memory_id = command[len("/memory delete "):].strip()
            if _confirm("memory:delete", f"Delete memory entry {memory_id}."):
                print("Yuki > Deleted." if memory.delete(memory_id) else "Yuki > Entry not found.")
            else:
                print("Yuki > Memory deletion cancelled.")
        elif command == "/memory clear":
            if _confirm("memory:clear", "Permanently delete all local Yuki memory entries.", "high"):
                print(f"Yuki > Deleted {memory.clear()} memory entries.")
            else:
                print("Yuki > Memory clear cancelled.")
        elif command.startswith("/reminder add "):
            payload = command[len("/reminder add "):]
            if "|" not in payload:
                print("Yuki > Use /reminder add 2026-12-31T18:00:00+00:00 | reminder text")
                continue
            timestamp, reminder_text = payload.split("|", 1)
            try:
                due_at = datetime.fromisoformat(timestamp.strip())
                if due_at.tzinfo is None or due_at.utcoffset() is None:
                    raise ValueError("include a timezone offset, such as +00:00")
                item = reminders.add(reminder_text.strip(), due_at)
                print(f"Yuki > Reminder saved ({item['id']}). No scheduled actions will run.")
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Reminder was not saved: {exc}")
        elif command == "/reminders":
            for item in reminders.list():
                state = "delivered" if item.get("delivered") else "pending"
                print(f"{item['id']} [{state}] {item['due_at']}: {item['text']}")
        elif command == "/reminders due":
            due = reminders.due()
            if not due:
                print("Yuki > No reminders are due.")
            for item in due:
                print(f"Yuki reminder: {item['text']} (due {item['due_at']})")
        elif command == "/voice status":
            print(f"Voice opt-in: {config['voice_enabled']}; microphone listening: not implemented")
            print("TTS: optional pyttsx3 adapter; STT: optional faster-whisper audio-file adapter")
        elif command.startswith("/voice speak "):
            text = command[len("/voice speak "):]
            try:
                if not _confirm("voice:speak", "Play the supplied text through local speech output.", "low"):
                    print("Yuki > Voice request cancelled.")
                    continue
                tts.speak(text)
                print("Yuki > Finished speaking.")
            except (TTSError, ValueError) as exc:
                print(f"Yuki > Voice output unavailable: {exc}")
        elif command.startswith("/voice transcribe "):
            audio_path = command[len("/voice transcribe "):].strip()
            try:
                if not _confirm("voice:transcribe", "Transcribe the explicitly supplied audio file locally.", "medium"):
                    print("Yuki > Transcription cancelled.")
                    continue
                result = stt.transcribe_file(audio_path)
                print(f"Yuki transcript ({result['language']}): {result['text']}")
            except (STTError, ValueError) as exc:
                print(f"Yuki > Speech recognition unavailable: {exc}")
        else:
            print("Yuki > This runtime does not have an LLM connected yet. Use /help for available commands.")


if __name__ == "__main__":
    main()
