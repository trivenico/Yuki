"""Integrated guarded Yuki runtime. Optional capabilities require separate dependencies and opt-in."""

from datetime import datetime, timezone
import argparse
from pathlib import Path
import shlex
import signal

from companion import DEFAULT_PERSONA
from companion.memory import MemoryStore
from companion.proactive import ReminderQueue
from companion.evolution import DailyEvolutionCycle
from core.agent import YukiCoreAgent
from core.config import load_config
from core.permissions import ApprovalRequest, PermissionGate
from core.planner import Planner
from core.vector_memory import VectorMemory
from defense.firewall import FirewallManager, FirewallError
from defense.ids import PortScanDetector, PacketSniffer, IDSError
from hackback.exploit_runner import ExploitRunner
from hackback.recon import NmapRecon, ReconError
from hackback.sandbox import DockerSandbox, SandboxError
from knowledge.indexer import KnowledgeIndexer
from skills.registry import SkillRegistry
from voice.stt import FasterWhisperSTT, STTError
from voice.tts import LocalTTS, TTSError
from voice.wakeword import WakeWordDetector, WakeWordError
from voice.speaker_verification import SpeakerVerifier, SpeakerVerificationError
from providers.factory import create_provider
from providers.hybrid import HybridProvider
from yuki_agent import YukiAgent
from core.task_agent import ApprovedTaskAgent
from core.task_store import TaskStore
from core.task_runtime import DurableTaskRunner
from core.observability import configure_logging, log_event
import logging
import time


def _ask_approval(request):
    print("\nPermission request")
    print(f"  Action: {request.action}\n  Reason: {request.reason}\n  Risk: {request.risk}")
    try:
        return input("Approve this single action? [y/N] ").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def _confirm(action, reason, risk="medium"):
    return _ask_approval(ApprovalRequest(action=action, reason=reason, risk=risk))


def build_agent(config, *, approval_callback=None):
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry.defaults(
        workspace,
        gmail_enabled=config["gmail_enabled"],
        gmail_credentials=workspace / "google_oauth_client.json",
        gmail_token=workspace / "google_token.json",
        web_allowed_hosts=config["web_allowed_hosts"],
        plugin_discovery_enabled=config["plugin_discovery_enabled"],
        trusted_plugin_modules=config["trusted_plugin_modules"],
    )
    # Keep production approval fail-closed; tests may inject an explicit decision
    # callback so runtime smoke tests never depend on interactive stdin.
    approver = _ask_approval if approval_callback is None else approval_callback
    if not callable(approver):
        raise TypeError("approval_callback must be callable.")
    return YukiCoreAgent(registry=registry, permission_gate=PermissionGate(approver))


def print_help():
    print("""Yuki guarded core
  /model status                  Show Ollama model detection status
  /model list                    List models already installed in local Ollama
  /model use MODEL_NAME          Select an already installed local model
  /help                          Show commands
  /status                        Show capability status
  /skills                        List registered skills
  /ask PROMPT                    Ask the configured AI provider (no tools executed)
  /task GOAL                      Bounded model-guided task; approve each skill call
  /tasks                          Show recent durable task history
  /ask-cloud TASK | PROMPT       Explicitly send an allow-listed task prompt to cloud
  /skill NAME [ARG]              Request one registered skill
  /plan GOAL                     Propose a bounded plan (no automatic execution)
  /memory add TEXT               Save a local note (approval required)
  /memory list | /memory search QUERY
  /memory delete ID | /memory clear
  /knowledge index RELATIVE_PATH Index a text file inside workspace (approval required)
  /reminder add ISO_DATETIME | TEXT
  /reminders | /reminders due
  /evolution status | /evolution record CATEGORY | SUMMARY
  /evolution run | /evolution review PROPOSAL_ID accept|reject
  /voice status
  /voice record SECONDS          Explicitly record and transcribe short audio
  /voice speak TEXT
  /voice transcribe AUDIO_PATH
  /voice wakeword status         Show custom detector readiness, no background listener
  /voice speaker-check PATH      Currently fails closed without a verified model
  /security status
  /security recon PRIVATE_IP     Authorized limited Nmap scan
  /sandbox status
  /sandbox run IMAGE@sha256:DIGEST | PROGRAM ARGS
  /defense status
  /defense sniff                 Bounded packet capture; alert only
  /defense block IP              Requires approval; dry-run by default
  /defense remove RULE_ID        Remove a Yuki-created rule (approval required)
  /metasploit status             Execution is not implemented
  /dashboard                     Open optional Textual supervision dashboard
  /quit

No model-directed tool execution or autonomous project editing is enabled. AI answers do not execute tools.
""")

def _handle_model_command(command, config, ai):
    """Inspect/select installed Ollama models; never download a model."""
    parts = shlex.split(command)
    if len(parts) == 2 and parts[1] == "status":
        try:
            provider = create_provider({**config, "provider": "ollama"})
            available = provider.is_available()
            print(f"Yuki > Ollama: {'available' if available else 'not ready'}")
            if available:
                print(f"Yuki > Selected local model: {provider.model}")
            else:
                print("Yuki > Start Ollama and install a model, or run /model list.")
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Yuki > Model detection failed: {exc}")
            return True
    if len(parts) == 2 and parts[1] == "list":
        try:
            from providers.ollama import OllamaProvider
            provider = OllamaProvider(
                base_url=config["ollama_base_url"],
                model="",
            )
            models = provider.list_models()
            if not models:
                print("Yuki > No installed Ollama models found. No model was downloaded.")
            else:
                print("Yuki > Installed local Ollama models:")
                for name in models:
                    print(f"  - {name}")
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Yuki > Could not list local models: {exc}")
            return True
    if len(parts) >= 3 and parts[1] == "use":
        selected = " ".join(parts[2:]).strip()
        try:
            from providers.ollama import OllamaProvider
            provider = OllamaProvider(
                base_url=config["ollama_base_url"],
                model="",
            )
            if selected not in provider.list_models():
                print("Yuki > That model is not installed locally. I will not download it.")
                return True
            config["provider"] = "ollama"
            config["ollama_model"] = selected
            ai.provider = create_provider(config)
            print(f"Yuki > Selected installed local model: {selected}")
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Yuki > Could not select local model: {exc}")
            return True
    print("Yuki > Usage: /model status | /model list | /model use MODEL_NAME")
    return True


def _run_doctor(config):
    """Run bounded local readiness checks without starting the interactive runtime."""
    checks = []
    workspace = Path(config["workspace"])
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        checks.append(("workspace", True, str(workspace)))
    except OSError as exc:
        checks.append(("workspace", False, type(exc).__name__))

    try:
        agent = build_agent(config)
        names = agent.registry.names()
        checks.append(("skill_registry", bool(names), f"{len(names)} registered skill(s)"))
        denied = PermissionGate().request("doctor:probe", "verify default approval policy")
        checks.append(("approval_default", denied is False, "no approver must deny execution"))
    except Exception as exc:
        checks.append(("agent_initialization", False, type(exc).__name__))

    try:
        provider = create_provider(config)
        available = provider.is_available()
        checks.append(("provider", bool(available), f"{config['provider']}: {'available' if available else 'unavailable (check provider/model configuration)'}"))
    except Exception as exc:
        checks.append(("provider", False, type(exc).__name__))

    try:
        store = TaskStore(workspace / "yuki_tasks.sqlite3")
        store.status_counts()
        checks.append(("task_store", True, "SQLite task store opened successfully"))
    except Exception as exc:
        checks.append(("task_store", False, type(exc).__name__))

    print("Yuki readiness doctor")
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'WARN'}] {name}: {detail}")
    failed = [name for name, ok, _ in checks if not ok]
    print(f"Result: {len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("This checks startup prerequisites, not every optional integration or production security property.")
    return 0 if not failed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Yuki local-first guarded personal agent")
    parser.add_argument("--check-config", action="store_true", help="validate configuration and exit")
    parser.add_argument("--doctor", action="store_true", help="run bounded local readiness checks and exit")
    parser.add_argument("--version", action="version", version="Yuki 0.1.0")
    args = parser.parse_args(argv)

    configure_logging()
    logger = logging.getLogger("yuki.runtime")
    try:
        config = load_config()
    except (RuntimeError, OSError) as exc:
        print(f"Yuki startup blocked: {exc}")
        return 2
    if args.check_config:
        print("Yuki configuration is valid.")
        print(f"Workspace: {config['workspace']}")
        print(f"Provider: {config['provider']}")
        return 0
    if args.doctor:
        return _run_doctor(config)

    workspace = Path(config["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    agent = build_agent(config)
    ai = YukiAgent(create_provider(config))
    task_agent = ApprovedTaskAgent(ai.provider, agent, max_steps=config["max_agent_steps"])
    task_store = TaskStore(workspace / "yuki_tasks.sqlite3")
    recovered_tasks = task_store.recover_interrupted()
    task_runner = DurableTaskRunner(task_store)
    if recovered_tasks:
        log_event(
            logger,
            logging.WARNING,
            "task.recovery_completed",
            component="task_store",
            status="interrupted",
            recovered_count=recovered_tasks,
        )
        print(
            f"Yuki > Marked {recovered_tasks} unfinished task(s) interrupted; none were replayed."
        )
    # Convert SIGTERM to KeyboardInterrupt so active task lifecycle state is persisted.
    signal.signal(
        signal.SIGTERM,
        lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    memory_path = workspace / "yuki_memory.json"
    memory = MemoryStore(memory_path)
    searchable_memory = VectorMemory(memory_path)
    indexer = KnowledgeIndexer(searchable_memory)
    planner = Planner(max_steps=config["max_agent_steps"])
    reminders = ReminderQueue(workspace / "yuki_reminders.json")
    evolution = DailyEvolutionCycle(workspace)
    tts = LocalTTS(
        enabled=config["voice_enabled"],
        rate=config["tts_rate"],
        backend=config["tts_backend"],
        model_path=config["piper_model_path"] or None,
    )
    stt = FasterWhisperSTT(enabled=config["voice_enabled"], model_size=config["stt_model"])
    wakeword = (
        WakeWordDetector(config["wakeword_model_path"], enabled=config["wakeword_enabled"])
        if config["wakeword_model_path"]
        else None
    )
    recon = NmapRecon(
        enabled=config["security_lab_enabled"],
        allowlisted_targets=config["recon_allowlisted_targets"],
        approver=lambda reason: _confirm("security:recon", reason, "high"),
    )
    sandbox = DockerSandbox(
        workspace,
        allowed_images=config["sandbox_allowed_images"],
        enabled=config["sandbox_enabled"],
        approver=lambda reason: _confirm("sandbox:run", reason, "high"),
    )
    firewall = FirewallManager(
        enabled=config["firewall_enabled"],
        dry_run=config["firewall_dry_run"],
        approver=lambda reason: _confirm("defense:firewall", reason, "high"),
    )
    detector = PortScanDetector()
    sniffer = PacketSniffer(detector, enabled=config["security_lab_enabled"])
    exploit_runner = ExploitRunner()
    speaker_verifier = SpeakerVerifier(enabled=False)

    def dashboard_command(command):
        if command == "/status":
            counts = task_store.status_counts()
            return (
                f"Task counts: queued={counts['queued']}, running={counts['running']}, "
                f"completed={counts['completed']}, failed={counts['failed']}, "
                f"interrupted={counts['interrupted']}\n"
                f"Workspace: {workspace}\nSkills: {', '.join(agent.registry.names())}\n"
                f"Voice enabled: {config['voice_enabled']}\nSecurity lab enabled: {config['security_lab_enabled']}\n"
                f"Firewall enabled: {config['firewall_enabled']} (dry-run={config['firewall_dry_run']})"
            )
        if command == "/skills":
            return "\n".join(
                f"{s['name']} [{s['risk']}] — {s['description']}" for s in agent.available_skills()
            )
        return "Safe commands: /help, /status, /skills. Sensitive actions remain in the approval-gated terminal."

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
        elif command == "/model" or command.startswith("/model "):
            _handle_model_command(command, config, ai)
        elif command == "/status":
            counts = task_store.status_counts()
            print("Tasks: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
            print(f"Persona: {agent.persona.name}; wake word: {agent.persona.wake_word}")
            print(f"Workspace: {workspace}")
            print(
                f"Provider: {config['provider']} (configured adapter; availability depends on runtime/model credentials)"
            )
            print(
                f"Voice: {config['voice_enabled']}; language behavior: {config['language_behavior']}; voice style preference: {config['voice_style']}"
            )
            print(
                f"Spoken reply policy: {config['spoken_reply_policy']} (end-to-end reply loop not connected)"
            )
            print(
                f"Security lab: {config['security_lab_enabled']}; sandbox: {config['sandbox_enabled']}"
            )
            print(
                f"Firewall: {config['firewall_enabled']} (dry-run={config['firewall_dry_run']}); Gmail: {config['gmail_enabled']}"
            )
            print(f"Skills: {', '.join(agent.registry.names())}")
        elif command == "/skills":
            for skill in agent.available_skills():
                print(f"- {skill['name']} [{skill['risk']}]: {skill['description']}")
        elif command == "/tasks":
            for task in task_store.list_tasks(limit=20):
                print(f"- {task['task_id']} [{task['status']}] {task['goal'][:100]}")
        elif command.startswith("/task "):
            goal = command[len("/task ") :].strip()
            if not goal:
                print(
                    "Yuki > Provide a task goal: /task summarize the available time and file skills"
                )
                continue
            print("Yuki > Preparing a bounded plan. Every skill call requires fresh approval.")
            started_at = time.monotonic()
            try:
                task_id, result = task_runner.run(goal, task_agent.run)
            except KeyboardInterrupt:
                log_event(
                    logger,
                    logging.WARNING,
                    "task.interrupted",
                    component="task_runner",
                    status="interrupted",
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
                print(
                    "\nYuki > Shutdown requested. Task will not be replayed; check /tasks for its final status."
                )
                return
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "task.failed",
                    component="task_runner",
                    status="failed",
                    reason_code=type(exc).__name__,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
                print(f"Yuki > Task failed safely ({type(exc).__name__}).")
                continue
            log_event(
                logger,
                logging.INFO,
                "task.finished",
                component="task_runner",
                task_id=task_id,
                status=result.status,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                step_count=result.steps,
            )
            print(
                f"Yuki > {result.message} "
                f"[task={task_id}, status={result.status}, steps={result.steps}]"
            )
        elif command.startswith("/ask "):
            prompt = command[len("/ask ") :].strip()
            if not prompt:
                print("Yuki > Provide a prompt: /ask explain a Python decorator")
                continue
            print("Yuki > " + ai.ask(prompt))
        elif command.startswith("/ask-cloud "):
            payload = command[len("/ask-cloud ") :]
            if "|" not in payload:
                print("Yuki > Use /ask-cloud TASK | PROMPT")
                continue
            task, prompt = payload.split("|", 1)
            task, prompt = task.strip(), prompt.strip()
            provider = ai.provider
            if not isinstance(provider, HybridProvider):
                print("Yuki > Cloud task routing requires provider: hybrid.")
                continue
            if not task or not prompt:
                print("Yuki > Provide both a task name and prompt.")
                continue
            if task.lower() not in provider.cloud_tasks:
                print("Yuki > This task is not in YUKI_CLOUD_TASKS; no data was sent.")
                continue
            if not _confirm(
                "ai:cloud_request",
                f"Send this prompt to the configured cloud provider for task {task!r}? Prompt length: {len(prompt)} characters.",
                "medium",
            ):
                print("Yuki > Cloud request cancelled; no data was sent.")
                continue
            try:
                answer = provider.generate_for(task, prompt)
                print("Yuki > " + answer)
            except Exception as exc:
                print(f"Yuki > Cloud request failed safely ({type(exc).__name__}).")
        elif command.startswith("/skill "):
            parts = command.split(maxsplit=2)
            name, argument = parts[1], parts[2] if len(parts) > 2 else ""
            response = agent.run_skill(name, argument)
            print(f"Yuki > {response.message}")
            if response.ok and response.data is not None:
                print(response.data)
        elif command.startswith("/plan "):
            goal = command[len("/plan ") :].strip()
            try:
                matches = searchable_memory.search(goal, limit=5)
                plan = planner.propose(goal, memory_context=[m["text"] for m in matches])
                print(f"Plan for: {plan.goal}")
                for number, step in enumerate(plan.steps, 1):
                    print(
                        f"{number}. {step.action}: {step.description} (approval required: {step.requires_approval})"
                    )
                if plan.memory_context:
                    print("Relevant local memory:")
                    for item in plan.memory_context:
                        print(f"- {item[:300]}")
                print("Yuki > Proposal only; no actions were executed.")
            except ValueError as exc:
                print(f"Yuki > Cannot plan: {exc}")
        elif command.startswith("/memory add "):
            note = command[len("/memory add ") :].strip()
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
                print(
                    f"{item['id']} [{item.get('category', 'note')}] {item['created_at']}: {item['text']}"
                )
        elif command.startswith("/memory search "):
            try:
                for item in searchable_memory.search(command[len("/memory search ") :], limit=10):
                    print(f"{item['score']:.3f} {item['id']}: {item['text']}")
            except ValueError as exc:
                print(f"Yuki > Search failed: {exc}")
        elif command.startswith("/memory delete "):
            memory_id = command[len("/memory delete ") :].strip()
            if _confirm("memory:delete", f"Delete memory entry {memory_id}."):
                print("Yuki > Deleted." if memory.delete(memory_id) else "Yuki > Entry not found.")
            else:
                print("Yuki > Memory deletion cancelled.")
        elif command == "/memory clear":
            if _confirm("memory:clear", "Permanently delete all local memory entries.", "high"):
                print(f"Yuki > Deleted {memory.clear()} entries.")
            else:
                print("Yuki > Memory clear cancelled.")
        elif command.startswith("/knowledge index "):
            relative_path = command[len("/knowledge index ") :].strip()
            if not _confirm(
                "knowledge:index", f"Read and index {relative_path!r} inside the workspace."
            ):
                print("Yuki > Indexing cancelled.")
                continue
            try:
                print(indexer.index_file(relative_path, workspace=workspace))
            except (ValueError, OSError, RuntimeError) as exc:
                print(f"Yuki > Indexing failed: {exc}")
        elif command.startswith("/reminder add "):
            payload = command[len("/reminder add ") :]
            if "|" not in payload:
                print("Yuki > Use /reminder add 2026-12-31T18:00:00+00:00 | reminder text")
                continue
            timestamp, reminder_text = payload.split("|", 1)
            try:
                due_at = datetime.fromisoformat(timestamp.strip())
                if due_at.tzinfo is None or due_at.utcoffset() is None:
                    raise ValueError("include a timezone offset such as +00:00")
                item = reminders.add(reminder_text.strip(), due_at)
                print(f"Yuki > Reminder saved ({item['id']}); no background actions will run.")
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Reminder was not saved: {exc}")
        elif command == "/reminders":
            for item in reminders.list():
                print(
                    f"{item['id']} [{'delivered' if item.get('delivered') else 'pending'}] {item['due_at']}: {item['text']}"
                )
        elif command == "/reminders due":
            due = reminders.due()
            if not due:
                print("Yuki > No reminders are due.")
            for item in due:
                print(f"Yuki reminder: {item['text']} (due {item['due_at']})")
        elif command == "/evolution status":
            print(evolution.status())
        elif command.startswith("/evolution record "):
            payload = command[len("/evolution record ") :]
            if "|" not in payload:
                print("Yuki > Use /evolution record feedback | concise observation")
                continue
            category, summary = payload.split("|", 1)
            try:
                item = evolution.record(category.strip(), summary.strip())
                print(
                    f"Yuki > Recorded local evolution observation {item['id']}; secret-like strings are redacted."
                )
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Observation was not recorded: {exc}")
        elif command == "/evolution run":
            if not _confirm(
                "evolution:run",
                "Analyze explicitly recorded local observations and create review proposals. No actions or code changes will run.",
            ):
                print("Yuki > Evolution cycle cancelled.")
                continue
            try:
                report = evolution.run_cycle()
                print(
                    f"Yuki > Reviewed {report['observations_reviewed']} observations; {len(report['proposals'])} proposals await review."
                )
                for proposal in report["proposals"]:
                    print(
                        f"- {proposal['id']} [{proposal['kind']}] {proposal['title']}: {proposal['detail']}"
                    )
                print(f"Report saved locally: {evolution.report_path}")
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Evolution cycle failed safely: {exc}")
        elif command.startswith("/evolution review "):
            parts = command.split(maxsplit=3)
            if len(parts) != 4 or parts[3].split()[-1] not in {"accept", "reject"}:
                print("Yuki > Use /evolution review PROPOSAL_ID accept|reject")
                continue
            proposal_id, decision = parts[2], parts[3].strip()
            decision = "accepted_for_manual_followup" if decision == "accept" else "rejected"
            if not _confirm(
                "evolution:review",
                f"Record review decision {decision!r} for proposal {proposal_id}.",
            ):
                print("Yuki > Proposal review cancelled.")
                continue
            try:
                print("Yuki > " + str(evolution.review(proposal_id, decision)))
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Proposal was not changed: {exc}")
        elif command == "/voice status":
            print(
                f"Voice opt-in: {config['voice_enabled']}; recording starts only on explicit request."
            )
            print(
                f"Language behavior: {config['language_behavior']}; STT uses automatic detection by default."
            )
            print(
                f"Voice style preference: {config['voice_style']} (actual voice depends on installed TTS model)."
            )
            print(
                f"Spoken reply policy: {config['spoken_reply_policy']} (automatic speaking of conversation replies is not yet wired because no LLM reply loop exists)."
            )
            print(
                f"TTS backend: {config['tts_backend']}; Piper model configured: {bool(config['piper_model_path'])}"
            )
            print(
                f"Wake word configured: {wakeword is not None}; continuous listening is not implemented."
            )
        elif command.startswith("/voice record "):
            try:
                seconds = int(command[len("/voice record ") :].strip())
                if not _confirm(
                    "voice:record", f"Record up to {seconds} seconds from the microphone.", "medium"
                ):
                    print("Yuki > Recording cancelled.")
                    continue
                output = (
                    workspace
                    / "audio"
                    / f"recording-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.wav"
                )
                recorded = stt.record_wav(output, seconds=seconds)
                result = stt.transcribe_file(recorded)
                print(f"Yuki transcript ({result['language']}): {result['text']}")
            except (ValueError, STTError) as exc:
                print(f"Yuki > Recording/transcription unavailable: {exc}")
        elif command.startswith("/voice speak "):
            try:
                if not _confirm("voice:speak", "Speak the supplied text using local TTS.", "low"):
                    print("Yuki > Speech cancelled.")
                    continue
                tts.speak(command[len("/voice speak ") :])
                print("Yuki > Finished speaking.")
            except (TTSError, ValueError) as exc:
                print(f"Yuki > Speech unavailable: {exc}")
        elif command.startswith("/voice transcribe "):
            try:
                if not _confirm(
                    "voice:transcribe", "Transcribe the selected local audio file.", "medium"
                ):
                    print("Yuki > Transcription cancelled.")
                    continue
                result = stt.transcribe_file(command[len("/voice transcribe ") :].strip())
                print(f"Yuki transcript ({result['language']}): {result['text']}")
            except (STTError, ValueError) as exc:
                print(f"Yuki > Transcription unavailable: {exc}")
        elif command == "/voice wakeword status":
            if not wakeword:
                print(
                    "Yuki > No model path configured. Set wakeword_model_path and enable YUKI_WAKEWORD_ENABLED."
                )
            else:
                try:
                    wakeword.load()
                    print(
                        "Yuki > Detector model loaded. No continuous microphone listener is active."
                    )
                except WakeWordError as exc:
                    print(f"Yuki > Wake-word detector unavailable: {exc}")
        elif command.startswith("/voice wakeword listen "):
            if not wakeword:
                print("Yuki > No wake-word model path is configured.")
                continue
            try:
                seconds = int(command[len("/voice wakeword listen ") :].strip())
                if not _confirm(
                    "voice:wakeword",
                    f"Listen to the microphone for up to {seconds} seconds for the configured wake phrase.",
                    "medium",
                ):
                    print("Yuki > Listening cancelled.")
                    continue
                wakeword.load()
                result = wakeword.listen_once(seconds=seconds)
                if not result["detected"]:
                    print("Yuki > Wake phrase was not detected in this listening window.")
                else:
                    print(
                        f"Yuki > Wake phrase detected: {result['scores']}. Starting a separate 5-second recording for transcription."
                    )
                    output = (
                        workspace
                        / "audio"
                        / f"wake-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.wav"
                    )
                    recorded = stt.record_wav(output, seconds=5)
                    transcript = stt.transcribe_file(recorded)
                    print(f"Yuki transcript ({transcript['language']}): {transcript['text']}")
            except (ValueError, WakeWordError, STTError) as exc:
                print(f"Yuki > Wake-word flow unavailable: {exc}")
        elif command.startswith("/voice speaker-check "):
            try:
                print(speaker_verifier.verify(command[len("/voice speaker-check ") :].strip()))
            except SpeakerVerificationError as exc:
                print(f"Yuki > {exc}")
        elif command == "/security status":
            print(
                f"Security lab: {config['security_lab_enabled']}; allowed private lab IPs: {len(config['recon_allowlisted_targets'])}"
            )
        elif command.startswith("/security recon "):
            try:
                result = recon.scan_common_tcp(command[len("/security recon ") :].strip())
                print(f"Yuki > Scan finished for {result['target']} (exit {result['returncode']}).")
                print(result["output"])
                if result["errors"]:
                    print(result["errors"])
            except (ReconError, ValueError) as exc:
                print(f"Yuki > Recon blocked/unavailable: {exc}")
        elif command == "/sandbox status":
            print(
                f"Sandbox enabled: {config['sandbox_enabled']}; allow-listed images: {len(config['sandbox_allowed_images'])}"
            )
        elif command.startswith("/sandbox run "):
            payload = command[len("/sandbox run ") :]
            if "|" not in payload:
                print("Yuki > Use /sandbox run IMAGE@sha256:DIGEST | program arg1 arg2")
                continue
            image, argv_text = payload.split("|", 1)
            try:
                result = sandbox.run(image.strip(), shlex.split(argv_text))
                print(
                    f"Yuki > Sandbox exit {result['returncode']}\n{result['stdout']}\n{result['stderr']}"
                )
            except (SandboxError, ValueError) as exc:
                print(f"Yuki > Sandbox blocked/unavailable: {exc}")
        elif command == "/defense status":
            print(
                f"Packet IDS enabled: {config['security_lab_enabled']}; firewall enabled: {config['firewall_enabled']}; dry-run: {config['firewall_dry_run']}"
            )
            print("Packet IDS only raises alerts; it does not automatically block sources.")
        elif command == "/defense sniff":

            def alert_callback(alert):
                print(
                    f"Yuki alert: suspected port scan from {alert['source_ip']} across {alert['unique_ports']} ports."
                )
                if _confirm(
                    "defense:block", f"Block suspected source {alert['source_ip']}?", "high"
                ):
                    try:
                        print(firewall.block_temporary(alert["source_ip"], seconds=300))
                    except FirewallError as exc:
                        print(f"Yuki > Block not applied: {exc}")

            if not _confirm(
                "defense:sniff",
                "Capture up to 500 packets for at most 30 seconds on the default interface.",
                "high",
            ):
                print("Yuki > Capture cancelled.")
                continue
            try:
                sniffer.sniff(packet_limit=500, timeout=30, on_alert=alert_callback)
            except IDSError as exc:
                print(f"Yuki > IDS unavailable: {exc}")
        elif command.startswith("/defense block "):
            ip = command[len("/defense block ") :].strip()
            try:
                print(firewall.block_temporary(ip, seconds=300))
            except FirewallError as exc:
                print(f"Yuki > Firewall action blocked/unavailable: {exc}")
        elif command.startswith("/defense remove "):
            rule_id = command[len("/defense remove ") :].strip()
            try:
                print(firewall.remove(rule_id))
            except FirewallError as exc:
                print(f"Yuki > Firewall removal blocked/unavailable: {exc}")
        elif command == "/metasploit status":
            print(exploit_runner.status()["message"])
        elif command == "/dashboard":
            try:
                from ui.dashboard import run_dashboard

                run_dashboard(agent, command_handler=dashboard_command)
            except RuntimeError as exc:
                print(f"Yuki > Dashboard unavailable: {exc}")
        else:
            print("Yuki > No live LLM is connected yet. Use /help for supported commands.")


if __name__ == "__main__":
    raise SystemExit(main())
