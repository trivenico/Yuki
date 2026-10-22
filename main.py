"""Integrated guarded Yuki runtime. Optional capabilities require separate dependencies and opt-in."""
from datetime import datetime, timezone
from pathlib import Path
import shlex

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
from core.readiness import build_readiness_report
from core.recovery_bootstrap import recover_workspace_on_startup
from core.production_guard import assert_production_runtime_supported
from core.pending_approvals import SQLitePendingApprovalStore
from core.approval_audit import SQLiteApprovalAuditLog
from core.execution_ledger import SQLiteExecutionLedger
from core.execution_reconciliation import SQLiteExecutionReconciliation
from core.approval_flow import ApprovalCoordinator
from core.durable_skill_runtime import DurableSkillRuntime
from core.durable_operation_runtime import DurableOperationRuntime
from core.durable_memory_runtime import DurableMemoryRuntime
from core.durable_knowledge_runtime import DurableKnowledgeRuntime
from core.durable_reminder_runtime import DurableReminderRuntime
from core.performance_monitor import collect_performance, format_performance_report
from core.performance_history import format_performance_history
from core.token_usage import TokenUsageTracker


def _ask_approval(request):
    print("\nPermission request")
    print(f"  Action: {request.action}\n  Reason: {request.reason}\n  Risk: {request.risk}")
    try:
        return input("Approve this single action? [y/N] ").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def _confirm(action, reason, risk="medium"):
    return _ask_approval(ApprovalRequest(action=action, reason=reason, risk=risk))


def build_agent(config):
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry.defaults(
        workspace, gmail_enabled=config["gmail_enabled"],
        gmail_credentials=workspace / "google_oauth_client.json",
        gmail_token=workspace / "google_token.json",
        web_allowed_hosts=config["web_allowed_hosts"],
        plugin_discovery_enabled=config["plugin_discovery_enabled"],
        trusted_plugin_modules=config["trusted_plugin_modules"],
    )
    return YukiCoreAgent(registry=registry, permission_gate=PermissionGate(_ask_approval))


def print_help():
    print("""Yuki guarded core
  /help                          Show commands
  /status                        Show capability status
  /readiness                     Detect runtime and local model readiness
  /performance                   Show CPU, RAM, GPU, and recorded token usage
  /skills                        List registered skills
  /ask PROMPT                    Ask the configured AI provider (no tools executed)
  /task GOAL                      Bounded model-guided task; approve each skill call
  /ask-cloud TASK | PROMPT       Explicitly send an allow-listed task prompt to cloud
  /skill NAME [ARG]              Request one registered skill
  /plan GOAL                     Propose a bounded plan (no automatic execution)
  /memory add TEXT               Save a local note (approval required)
  /memory list | /memory search QUERY
  /memory delete ID | /memory clear
  /knowledge index RELATIVE_PATH Index a text file inside workspace (durable approval required)
  /reminder add ISO_DATETIME | TEXT (approval required)
  /reminders | /reminders due
  /reconcile list               List interrupted executions needing operator review
  /reconcile resolve KEY DISPOSITION | EVIDENCE
                                 Record an evidence-based decision; never retries
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


def main():
    config = load_config()
    assert_production_runtime_supported(config)
    workspace = Path(config["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        recovery = recover_workspace_on_startup(workspace)
    except Exception as exc:
        raise RuntimeError("Startup recovery failed; refusing normal runtime startup.") from exc
    if recovery is not None and recovery.pending:
        print(f"Yuki recovery: {recovery.cleaned} cleaned, {recovery.pending} still pending; uncertain jobs will not be replayed.")
    agent = build_agent(config)
    ai = YukiAgent(create_provider(config))

    # Route model-planned and direct skill calls through durable approval state.
    # Production startup remains blocked until every other side-effect command is
    # routed through the same coordinator and integration tests cover those paths.
    pending_store = SQLitePendingApprovalStore(str(workspace / "pending_approvals.sqlite3"))
    audit_log = SQLiteApprovalAuditLog(str(workspace / "approval_audit.sqlite3"))
    execution_ledger = SQLiteExecutionLedger(str(workspace / "execution_ledger.sqlite3"))
    approval_coordinator = ApprovalCoordinator(
        pending_store=pending_store, audit_log=audit_log, execution_ledger=execution_ledger
    )
    reconciliation = SQLiteExecutionReconciliation(
        str(workspace / "execution_reconciliation.sqlite3"),
        ledger=execution_ledger, audit_log=audit_log,
    )

    def confirm_durable_skill(action):
        skill_id = action.get("skill_id", "unknown")
        arguments = action.get("arguments", {})
        argument = arguments.get("argument", "") if isinstance(arguments, dict) else ""
        skill = agent.registry.get(skill_id)
        risk = getattr(skill, "risk", "high")
        return _confirm(
            f"skill:{skill_id}",
            f"Execute registered skill {skill_id!r} with argument {argument!r}.",
            risk,
        )

    durable_agent = DurableSkillRuntime(
        registry=agent.registry, coordinator=approval_coordinator,
        actor="local-owner", confirm=confirm_durable_skill,
    )
    task_agent = ApprovedTaskAgent(ai.provider, durable_agent, max_steps=config["max_agent_steps"])
    token_usage = TokenUsageTracker(workspace / "token_usage.json", session_id="cli-session")
    # Attach usage accounting to active provider adapters without changing routing.
    def attach_usage_tracker(provider, visited=None):
        # Provider wrappers can contain nested local/cloud adapters. Guard cycles
        # and inspect declared/instance attributes only: dynamic mocks and proxy
        # objects may synthesize arbitrary attributes on getattr().
        if provider is None:
            return
        if visited is None:
            visited = set()
        identity = id(provider)
        if identity in visited:
            return
        visited.add(identity)
        try:
            declared = vars(provider)
        except TypeError:
            declared = {}
        if "usage_tracker" in declared or hasattr(type(provider), "usage_tracker"):
            try:
                provider.usage_tracker = token_usage
            except (AttributeError, TypeError):
                pass
        for attr in ("local_provider", "cloud_provider"):
            if attr in declared:
                attach_usage_tracker(declared[attr], visited)
            else:
                class_attr = getattr(type(provider), attr, None)
                if class_attr is not None:
                    try:
                        attach_usage_tracker(getattr(provider, attr), visited)
                    except (AttributeError, TypeError):
                        pass
    attach_usage_tracker(ai.provider)
    memory_path = workspace / "yuki_memory.json"
    memory = MemoryStore(memory_path)
    durable_memory = DurableMemoryRuntime(
        memory=memory, coordinator=approval_coordinator, actor="local-owner",
        confirm=lambda action: _confirm(
            str(action.get("skill_id", "memory:unknown")),
            "Apply approved local memory mutation: " + str(action.get("arguments", {}))[:500],
            "high" if action.get("skill_id") == "memory:clear" else "medium",
        ),
    )
    searchable_memory = VectorMemory(memory_path)
    indexer = KnowledgeIndexer(searchable_memory)
    durable_knowledge = DurableKnowledgeRuntime(indexer=indexer, workspace=workspace, coordinator=approval_coordinator, actor="local-owner", confirm=lambda action: _confirm("knowledge:index", "Read and index approved workspace file: " + str(action.get("arguments", {}))[:500], "medium"))
    planner = Planner(max_steps=config["max_agent_steps"])
    reminders = ReminderQueue(workspace / "yuki_reminders.json")
    durable_reminders = DurableReminderRuntime(
        reminders=reminders, coordinator=approval_coordinator, actor="local-owner",
        confirm=lambda action: _confirm(
            "reminder:add",
            "Save approved reminder: " + str(action.get("arguments", {}))[:500],
            "medium",
        ),
    )
    evolution = DailyEvolutionCycle(workspace)
    tts = LocalTTS(enabled=config["voice_enabled"], rate=config["tts_rate"],
                    backend=config["tts_backend"], model_path=config["piper_model_path"] or None)
    stt = FasterWhisperSTT(enabled=config["voice_enabled"], model_size=config["stt_model"])
    wakeword = WakeWordDetector(config["wakeword_model_path"], enabled=config["wakeword_enabled"]) if config["wakeword_model_path"] else None
    # These high-risk operations are confirmed and audited by the durable coordinator
    # below. Their inner callbacks are bypassed only to avoid duplicate prompts; callers
    # must use DurableOperationRuntime and never invoke these handlers directly.
    recon = NmapRecon(enabled=config["security_lab_enabled"], allowlisted_targets=config["recon_allowlisted_targets"],
                      approver=lambda reason: True)
    sandbox = DockerSandbox(workspace, allowed_images=config["sandbox_allowed_images"],
                            enabled=config["sandbox_enabled"], approver=lambda reason: True)
    firewall = FirewallManager(enabled=config["firewall_enabled"], dry_run=config["firewall_dry_run"],
                               approver=lambda reason: True)
    detector = PortScanDetector()

    def operation_confirmation(action):
        operation = action.get("operation", "unknown")
        arguments = action.get("arguments", {})
        risk = "critical" if operation.startswith("firewall_") else "high"
        return _confirm(
            f"operation:{operation}",
            f"Durably approve operation {operation!r} with arguments {arguments!r}.",
            risk,
        )

    def run_packet_capture(interface, packet_limit, timeout):
        def alert_callback(alert):
            print(f"Yuki alert: suspected port scan from {alert['source_ip']} across {alert['unique_ports']} ports.")
            outcome = durable_operations.execute(
                "firewall_block", {"ip": alert["source_ip"], "seconds": 300}
            )
            if outcome.ok:
                print(outcome.result)
            else:
                print(f"Yuki > Automatic firewall response not applied: {outcome.status}")

        return sniffer.sniff(
            interface=interface, packet_limit=packet_limit, timeout=timeout,
            on_alert=alert_callback,
        )

    def voice_record(seconds):
        output = workspace / "audio" / f"recording-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.wav"
        recorded = stt.record_wav(output, seconds=seconds)
        return stt.transcribe_file(recorded)

    def voice_transcribe(path):
        return stt.transcribe_file(path)

    def voice_speak(text):
        tts.speak(text)
        return {"spoken": True}

    def voice_wakeword_listen(seconds):
        if not wakeword:
            raise WakeWordError("No wake-word model path is configured.")
        wakeword.load()
        detected = wakeword.listen_once(seconds=seconds)
        if not detected["detected"]:
            return {"detected": False, "scores": detected.get("scores", [])}
        output = workspace / "audio" / f"wake-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.wav"
        recorded = stt.record_wav(output, seconds=5)
        transcript = stt.transcribe_file(recorded)
        return {"detected": True, "scores": detected.get("scores", []), "transcript": transcript}

    durable_operations = DurableOperationRuntime(
        coordinator=approval_coordinator,
        operations={
            "recon_scan": lambda target: recon.scan_common_tcp(target),
            "sandbox_run": lambda image, argv: sandbox.run(image, argv),
            "firewall_block": lambda ip, seconds: firewall.block_temporary(ip, seconds=seconds),
            "firewall_remove": lambda rule_id: firewall.remove(rule_id),
            "packet_capture": run_packet_capture,
            "voice_record": voice_record,
            "voice_transcribe": voice_transcribe,
            "voice_speak": voice_speak,
            "voice_wakeword_listen": voice_wakeword_listen,
            "evolution_record": lambda category, summary: evolution.record(category, summary),
            "evolution_run": lambda: evolution.run_cycle(),
            "evolution_review": lambda proposal_id, decision: evolution.review(proposal_id, decision),
        },
        actor="local-owner",
        session_id="cli-session",
        confirm=operation_confirmation,
    )
    sniffer = PacketSniffer(detector, enabled=config["security_lab_enabled"])
    exploit_runner = ExploitRunner()
    speaker_verifier = SpeakerVerifier(enabled=False)

    def dashboard_command(command):
        if command == "/status":
            return (f"Workspace: {workspace}\nSkills: {', '.join(agent.registry.names())}\n"
                    f"Voice enabled: {config['voice_enabled']}\nSecurity lab enabled: {config['security_lab_enabled']}\n"
                    f"Firewall enabled: {config['firewall_enabled']} (dry-run={config['firewall_dry_run']})")
        if command == "/skills":
            return "\n".join(f"{s['name']} [{s['risk']}] — {s['description']}" for s in agent.available_skills())
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
        elif command == "/readiness":
            try:
                report = build_readiness_report().to_dict()
                print(f"Yuki runtime: Python {report['python']} ({report['implementation']}); {report['operating_system']} / {report['architecture']}")
                for name, capability in report["capabilities"].items():
                    print(f"- {name}: {capability['status']} — {capability['detail']}")
            except Exception as exc:
                # Diagnostics must never prevent the guarded core from continuing.
                print(f"Yuki > Readiness diagnostics unavailable ({type(exc).__name__}).")
        elif command == "/performance":
            try:
                provider_state = vars(ai.provider) if ai.provider is not None else {}
                context_data = provider_state.get("last_context_budget")
                if isinstance(getattr(type(ai.provider), "last_context_budget", None), property):
                    context_data = ai.provider.last_context_budget
                print(format_performance_report(collect_performance(
                    token_summary=token_usage.summary(), context_budget_data=context_data
                )))
            except Exception as exc:
                print(f"Yuki > Performance diagnostics unavailable ({type(exc).__name__}).")
        elif command == "/performance-history":
            print(format_performance_history())
        elif command == "/status":
            print(f"Persona: {agent.persona.name}; wake word: {agent.persona.wake_word}")
            print(f"Workspace: {workspace}")
            print(f"Provider: {config['provider']} (configured adapter; availability depends on runtime/model credentials)")
            print(f"Voice: {config['voice_enabled']}; language behavior: {config['language_behavior']}; voice style preference: {config['voice_style']}")
            print(f"Spoken reply policy: {config['spoken_reply_policy']} (end-to-end reply loop not connected)")
            print(f"Security lab: {config['security_lab_enabled']}; sandbox: {config['sandbox_enabled']}")
            print(f"Firewall: {config['firewall_enabled']} (dry-run={config['firewall_dry_run']}); Gmail: {config['gmail_enabled']}")
            print(f"Skills: {', '.join(agent.registry.names())}")
        elif command == "/skills":
            for skill in agent.available_skills():
                print(f"- {skill['name']} [{skill['risk']}]: {skill['description']}")
        elif command.startswith("/task "):
            goal = command[len("/task "):].strip()
            if not goal:
                print("Yuki > Provide a task goal: /task summarize the available time and file skills")
                continue
            print("Yuki > Preparing a bounded plan. Every skill call requires fresh approval.")
            result = task_agent.run(goal)
            print(f"Yuki > {result.message} [status={result.status}, steps={result.steps}]")
        elif command.startswith("/ask "):
            prompt = command[len("/ask "):].strip()
            if not prompt:
                print("Yuki > Provide a prompt: /ask explain a Python decorator")
                continue
            print("Yuki > " + ai.ask(prompt))
        elif command.startswith("/ask-cloud "):
            payload = command[len("/ask-cloud "):]
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
            if not _confirm("ai:cloud_request", f"Send this prompt to the configured cloud provider for task {task!r}? Prompt length: {len(prompt)} characters.", "medium"):
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
            response = durable_agent.run_skill(name, argument)
            print(f"Yuki > {response.message}")
            if response.ok and response.data is not None:
                print(response.data)
        elif command.startswith("/plan "):
            goal = command[len("/plan "):].strip()
            try:
                matches = searchable_memory.search(goal, limit=5)
                plan = planner.propose(goal, memory_context=[m["text"] for m in matches])
                print(f"Plan for: {plan.goal}")
                for number, step in enumerate(plan.steps, 1):
                    print(f"{number}. {step.action}: {step.description} (approval required: {step.requires_approval})")
                if plan.memory_context:
                    print("Relevant local memory:")
                    for item in plan.memory_context:
                        print(f"- {item[:300]}")
                print("Yuki > Proposal only; no actions were executed.")
            except ValueError as exc:
                print(f"Yuki > Cannot plan: {exc}")
        elif command.startswith("/memory add "):
            note = command[len("/memory add "):].strip()
            result = durable_memory.mutate("memory:add", {"text": note})
            print(f"Yuki > {result.message}" + (f" ({result.data})" if result.data else ""))
        elif command == "/memory list":
            for item in memory.list():
                print(f"{item['id']} [{item.get('category', 'note')}] {item['created_at']}: {item['text']}")
        elif command.startswith("/memory search "):
            try:
                for item in searchable_memory.search(command[len("/memory search "):], limit=10):
                    print(f"{item['score']:.3f} {item['id']}: {item['text']}")
            except ValueError as exc:
                print(f"Yuki > Search failed: {exc}")
        elif command.startswith("/memory delete "):
            memory_id = command[len("/memory delete "):].strip()
            result = durable_memory.mutate("memory:delete", {"memory_id": memory_id})
            print(f"Yuki > {result.message}" + (f" ({result.data})" if result.data else ""))
        elif command == "/memory clear":
            result = durable_memory.mutate("memory:clear", {})
            print(f"Yuki > {result.message}" + (f" ({result.data})" if result.data else ""))
        elif command.startswith("/knowledge index "):
            relative_path = command[len("/knowledge index "):].strip()
            result = durable_knowledge.index(relative_path)
            print(f"Yuki > {result.get('message', result.get('status', 'Indexing finished'))}")
            if result.get("ok") and result.get("data") is not None:
                print(result["data"])
        elif command.startswith("/reminder add "):
            payload = command[len("/reminder add "):]
            if "|" not in payload:
                print("Yuki > Use /reminder add 2026-12-31T18:00:00+00:00 | reminder text")
                continue
            timestamp, reminder_text = payload.split("|", 1)
            try:
                due_at = datetime.fromisoformat(timestamp.strip())
                if due_at.tzinfo is None or due_at.utcoffset() is None:
                    raise ValueError("include a timezone offset such as +00:00")
                result = durable_reminders.add(reminder_text.strip(), due_at)
                print(f"Yuki > {result.message}" + (f" ({result.data})" if result.data else ""))
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Reminder was not saved: {exc}")
        elif command == "/reconcile list":
            try:
                items = reconciliation.list_pending()
                if not items:
                    print("Yuki > No executions currently require reconciliation.")
                for item in items:
                    print(f"KEY={item.idempotency_key} STATE={item.state} ACTION_SHA256={item.action_hash} UPDATED={item.updated_at}")
            except Exception as exc:
                print(f"Yuki > Reconciliation listing unavailable: {type(exc).__name__}")
        elif command.startswith("/reconcile resolve "):
            payload = command[len("/reconcile resolve "):]
            if "|" not in payload:
                print("Yuki > Use /reconcile resolve KEY confirmed_completed|confirmed_not_executed|unresolved | evidence")
                continue
            header, evidence = payload.split("|", 1)
            parts = header.strip().split(maxsplit=1)
            if len(parts) != 2:
                print("Yuki > Provide an execution KEY and disposition.")
                continue
            key, disposition = parts
            if not _confirm(
                "execution:reconcile",
                f"Record disposition {disposition!r} for execution {key!r}; this does not retry or change execution state.",
                "high",
            ):
                print("Yuki > Reconciliation cancelled.")
                continue
            try:
                decision = reconciliation.resolve(
                    key, operator="local-owner", disposition=disposition,
                    evidence=evidence.strip(),
                )
                print(f"Yuki > Decision recorded for {decision['idempotency_key']}: {decision['disposition']}. No retry was performed.")
            except (ValueError, OSError, RuntimeError) as exc:
                print(f"Yuki > Reconciliation failed safely: {exc}")
        elif command == "/reminders":
            for item in reminders.list():
                print(f"{item['id']} [{'delivered' if item.get('delivered') else 'pending'}] {item['due_at']}: {item['text']}")
        elif command == "/reminders due":
            due = reminders.due()
            if not due: print("Yuki > No reminders are due.")
            for item in due: print(f"Yuki reminder: {item['text']} (due {item['due_at']})")
        elif command == "/evolution status":
            print(evolution.status())
        elif command.startswith("/evolution record "):
            payload = command[len("/evolution record "):]
            if "|" not in payload:
                print("Yuki > Use /evolution record feedback | concise observation")
                continue
            category, summary = payload.split("|", 1)
            try:
                outcome = durable_operations.execute(
                    "evolution_record", {"category": category.strip(), "summary": summary.strip()}
                )
                if outcome.ok:
                    print(f"Yuki > Recorded local evolution observation {outcome.result['id']}; secret-like strings are redacted.")
                else:
                    print(f"Yuki > Observation was not recorded: {outcome.status}")
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Observation was not recorded: {exc}")
        elif command == "/evolution run":
            try:
                outcome = durable_operations.execute("evolution_run", {})
                if not outcome.ok:
                    print(f"Yuki > Evolution cycle blocked: {outcome.status}")
                    continue
                report = outcome.result
                print(f"Yuki > Reviewed {report['observations_reviewed']} observations; {len(report['proposals'])} proposals await review.")
                for proposal in report["proposals"]:
                    print(f"- {proposal['id']} [{proposal['kind']}] {proposal['title']}: {proposal['detail']}")
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
            try:
                outcome = durable_operations.execute(
                    "evolution_review", {"proposal_id": proposal_id, "decision": decision}
                )
                if outcome.ok:
                    print("Yuki > " + str(outcome.result))
                else:
                    print(f"Yuki > Proposal review blocked: {outcome.status}")
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Proposal was not changed: {exc}")
        elif command == "/voice status":
            print(f"Voice opt-in: {config['voice_enabled']}; recording starts only on explicit request.")
            print(f"Language behavior: {config['language_behavior']}; STT uses automatic detection by default.")
            print(f"Voice style preference: {config['voice_style']} (actual voice depends on installed TTS model).")
            print(f"Spoken reply policy: {config['spoken_reply_policy']} (automatic speaking of conversation replies is not yet wired because no LLM reply loop exists).")
            print(f"TTS backend: {config['tts_backend']}; Piper model configured: {bool(config['piper_model_path'])}")
            print(f"Wake word configured: {wakeword is not None}; continuous listening is not implemented.")
        elif command.startswith("/voice record "):
            try:
                seconds = int(command[len("/voice record "):].strip())
                outcome = durable_operations.execute("voice_record", {"seconds": seconds})
                if not outcome.ok:
                    print(f"Yuki > Recording cancelled or blocked: {outcome.status}")
                    continue
                result = outcome.result
                print(f"Yuki transcript ({result['language']}): {result['text']}")
            except (ValueError, STTError, RuntimeError) as exc:
                print(f"Yuki > Recording/transcription unavailable: {exc}")
        elif command.startswith("/voice speak "):
            try:
                outcome = durable_operations.execute(
                    "voice_speak", {"text": command[len("/voice speak "):]}
                )
                if outcome.ok:
                    print("Yuki > Finished speaking.")
                else:
                    print(f"Yuki > Speech cancelled or blocked: {outcome.status}")
            except (TTSError, ValueError, RuntimeError) as exc:
                print(f"Yuki > Speech unavailable: {exc}")
        elif command.startswith("/voice transcribe "):
            try:
                path = command[len("/voice transcribe "):].strip()
                outcome = durable_operations.execute("voice_transcribe", {"path": path})
                if not outcome.ok:
                    print(f"Yuki > Transcription cancelled or blocked: {outcome.status}")
                    continue
                result = outcome.result
                print(f"Yuki transcript ({result['language']}): {result['text']}")
            except (STTError, ValueError, RuntimeError) as exc:
                print(f"Yuki > Transcription unavailable: {exc}")
        elif command == "/voice wakeword status":
            if not wakeword:
                print("Yuki > No model path configured. Set wakeword_model_path and enable YUKI_WAKEWORD_ENABLED.")
            else:
                try:
                    wakeword.load()
                    print("Yuki > Detector model loaded. No continuous microphone listener is active.")
                except WakeWordError as exc:
                    print(f"Yuki > Wake-word detector unavailable: {exc}")
        elif command.startswith("/voice wakeword listen "):
            try:
                seconds = int(command[len("/voice wakeword listen "):].strip())
                outcome = durable_operations.execute(
                    "voice_wakeword_listen", {"seconds": seconds}
                )
                if not outcome.ok:
                    print(f"Yuki > Wake-word listening cancelled or blocked: {outcome.status}")
                    continue
                result = outcome.result
                if not result["detected"]:
                    print("Yuki > Wake phrase was not detected in this listening window.")
                else:
                    transcript = result["transcript"]
                    print(f"Yuki > Wake phrase detected: {result['scores']}.")
                    print(f"Yuki transcript ({transcript['language']}): {transcript['text']}")
            except (ValueError, WakeWordError, STTError, RuntimeError) as exc:
                print(f"Yuki > Wake-word flow unavailable: {exc}")
        elif command.startswith("/voice speaker-check "):
            try:
                print(speaker_verifier.verify(command[len("/voice speaker-check "):].strip()))
            except SpeakerVerificationError as exc:
                print(f"Yuki > {exc}")
        elif command == "/security status":
            print(f"Security lab: {config['security_lab_enabled']}; allowed private lab IPs: {len(config['recon_allowlisted_targets'])}")
        elif command.startswith("/security recon "):
            target = command[len("/security recon "):].strip()
            if not target:
                print("Yuki > Reconnaissance target is required.")
                continue
            try:
                outcome = durable_operations.execute("recon_scan", {"target": target})
                if not outcome.ok:
                    print(f"Yuki > Recon blocked: {outcome.status}")
                    continue
                result = outcome.result
                print(f"Yuki > Scan finished for {result['target']} (exit {result['returncode']}).")
                print(result["output"])
                if result["errors"]: print(result["errors"])
            except (ReconError, ValueError, RuntimeError) as exc:
                print(f"Yuki > Recon blocked/unavailable: {exc}")
        elif command == "/sandbox status":
            print(f"Sandbox enabled: {config['sandbox_enabled']}; allow-listed images: {len(config['sandbox_allowed_images'])}")
        elif command.startswith("/sandbox run "):
            payload = command[len("/sandbox run "):]
            if "|" not in payload:
                print("Yuki > Use /sandbox run IMAGE@sha256:DIGEST | program arg1 arg2")
                continue
            image, argv_text = payload.split("|", 1)
            try:
                argv = shlex.split(argv_text.strip())
                if not image.strip() or not argv:
                    print("Yuki > Sandbox image and program are required.")
                    continue
                outcome = durable_operations.execute(
                    "sandbox_run", {"image": image.strip(), "argv": argv}
                )
                if not outcome.ok:
                    print(f"Yuki > Sandbox blocked: {outcome.status}")
                    continue
                result = outcome.result
                print(f"Yuki > Sandbox exit {result['returncode']}\n{result['stdout']}\n{result['stderr']}")
            except (SandboxError, ValueError, RuntimeError) as exc:
                print(f"Yuki > Sandbox blocked/unavailable: {exc}")
        elif command == "/defense status":
            print(f"Packet IDS enabled: {config['security_lab_enabled']}; firewall enabled: {config['firewall_enabled']}; dry-run: {config['firewall_dry_run']}")
            print("Packet IDS only raises alerts; it does not automatically block sources.")
        elif command == "/defense sniff":
            try:
                outcome = durable_operations.execute(
                    "packet_capture", {"interface": None, "packet_limit": 500, "timeout": 30}
                )
                if not outcome.ok:
                    print(f"Yuki > Packet capture blocked: {outcome.status}")
                    continue
                alerts = outcome.result or []
                print(f"Yuki > Packet capture finished; {len(alerts)} alert(s) recorded.")
                for alert in alerts:
                    print(f"- {alert['type']}: {alert['source_ip']} ({alert['unique_ports']} ports)")
            except (IDSError, ValueError, RuntimeError) as exc:
                print(f"Yuki > IDS unavailable: {exc}")
        elif command.startswith("/defense block "):
            ip = command[len("/defense block "):].strip()
            if not ip:
                print("Yuki > Firewall address is required.")
                continue
            try:
                outcome = durable_operations.execute("firewall_block", {"ip": ip, "seconds": 300})
                print(outcome.result if outcome.ok else f"Yuki > Firewall action blocked: {outcome.status}")
            except (FirewallError, ValueError, RuntimeError) as exc:
                print(f"Yuki > Firewall action blocked/unavailable: {exc}")
        elif command.startswith("/defense remove "):
            rule_id = command[len("/defense remove "):].strip()
            if not rule_id:
                print("Yuki > Firewall rule ID is required.")
                continue
            try:
                outcome = durable_operations.execute("firewall_remove", {"rule_id": rule_id})
                print(outcome.result if outcome.ok else f"Yuki > Firewall removal blocked: {outcome.status}")
            except (FirewallError, ValueError, RuntimeError) as exc:
                print(f"Yuki > Firewall removal blocked/unavailable: {exc}")
        elif command == "/metasploit status":
            print(exploit_runner.status()["message"])
        elif command == "/dashboard":
            try:
                from ui.dashboard import run_dashboard
                run_dashboard(agent, command_handler=dashboard_command, token_usage=token_usage, provider=ai.provider, reconciliation=reconciliation, operator="local-owner")
            except RuntimeError as exc:
                print(f"Yuki > Dashboard unavailable: {exc}")
        else:
            print("Yuki > No live LLM is connected yet. Use /help for supported commands.")


if __name__ == "__main__":
    main()
