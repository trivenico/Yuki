"""Read-only Yuki runtime doctor; checks configuration and local provider readiness.

This command does not download models, start services, send prompts, or execute skills.
"""
from __future__ import annotations
import argparse
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass
from typing import Callable

@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str

@dataclass(frozen=True)
class DoctorReport:
    python: str
    platform: str
    provider: str
    checks: tuple[Check, ...]
    @property
    def ready_for_basic_chat(self) -> bool:
        return all(item.status in {"pass", "warning"} for item in self.checks if item.name in {"python", "configuration", "provider_adapter", "provider_available"}) and any(item.name == "provider_available" and item.status == "pass" for item in self.checks)
    def to_dict(self):
        result = asdict(self)
        result["ready_for_basic_chat"] = self.ready_for_basic_chat
        return result

def run_doctor(*, config_loader: Callable | None = None, provider_factory: Callable | None = None, environ=None) -> DoctorReport:
    env = os.environ if environ is None else environ
    checks: list[Check] = [Check("python", "pass" if sys.version_info >= (3, 10) else "fail", f"Python {platform.python_version()} (requires 3.10+)")]
    provider_name = "unknown"
    if config_loader is None:
        from core.config import load_config
        config_loader = load_config
    if provider_factory is None:
        from providers.factory import create_provider
        provider_factory = create_provider
    try:
        config = config_loader()
        provider_name = str(config.get("provider", "mock")).strip().lower()
        checks.append(Check("configuration", "pass", "Configuration loaded and validated."))
    except Exception as exc:
        checks.append(Check("configuration", "fail", f"Configuration failed ({type(exc).__name__}); details withheld."))
        return DoctorReport(platform.python_version(), platform.platform(), provider_name, tuple(checks))
    try:
        provider = provider_factory(config, environ=env)
        if not callable(getattr(provider, "generate", None)) or not callable(getattr(provider, "is_available", None)):
            raise TypeError("provider contract mismatch")
        checks.append(Check("provider_adapter", "pass", "Configured provider adapter constructed and exposes required methods."))
    except Exception as exc:
        checks.append(Check("provider_adapter", "fail", f"Provider construction failed ({type(exc).__name__}); details withheld."))
        return DoctorReport(platform.python_version(), platform.platform(), provider_name, tuple(checks))
    try:
        available = provider.is_available() is True
    except Exception as exc:
        checks.append(Check("provider_available", "fail", f"Provider availability check failed ({type(exc).__name__}); details withheld."))
    else:
        if available:
            checks.append(Check("provider_available", "pass", "Provider reports available; inference round-trip not tested."))
        else:
            checks.append(Check("provider_available", "fail", "Provider reports unavailable. Check local service/model configuration."))
    checks.append(Check("inference_round_trip", "not_run", "No prompt was sent; basic chat is not fully verified until a user-approved test prompt succeeds."))
    checks.append(Check("agent_tools", "not_run", "Skill execution and approval paths are not exercised by this diagnostic."))
    return DoctorReport(platform.python_version(), platform.platform(), provider_name, tuple(checks))

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)
    report = run_doctor()
    data = report.to_dict()
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"Yuki runtime doctor — provider: {report.provider}")
        for item in report.checks:
            print(f"[{item.status.upper()}] {item.name}: {item.detail}")
        print("BASIC CHAT: " + ("READY (provider available; inference still untested)" if report.ready_for_basic_chat else "NOT READY / NOT VERIFIED"))
    return 0 if report.ready_for_basic_chat else 1

if __name__ == "__main__":
    raise SystemExit(main())
