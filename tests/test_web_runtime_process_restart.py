"""Application-process restart acceptance for the actual browser launcher.

The child processes build scripts.run_web.create_app() with a deterministic
provider implementation, but retain the production composition root, SQLite
stores, owner identity derivation, and memory authorization policy.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


USERNAME = "restart-owner"
PASSWORD = "restart-owner-password"
MEMORY_TEXT = "The Yuki restart acceptance marker is cobalt-orchid-731."


class _DeterministicProvider:
    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        return {"text": "test-only response", "tool_calls": {"tool_calls": []}}


class _ProviderManager:
    def __init__(self, config, adapter, state_path, *, environ=None):
        self.config = config
        self.adapter = adapter

    def catalog(self):
        return []

    def select(self, name):
        raise AssertionError("Provider selection is not part of this acceptance test.")


def _build_runtime(workspace: str):
    import scripts.run_web as run_web

    run_web.load_config = lambda: {
        "workspace": workspace,
        "storage_backend": "sqlite",
        "provider": "openai",
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_model": "",
        "database_pool_min": 1,
        "database_pool_max": 2,
    }
    run_web.create_provider = lambda config: _DeterministicProvider()
    run_web.ProviderRuntimeManager = _ProviderManager
    return run_web.create_app()


def _child(mode: str, workspace: str):
    from core.owner_identity import memory_actor_id_for_owner

    runtime = _build_runtime(workspace)
    try:
        if mode == "seed":
            auth = runtime.app.auth
            user = auth.bootstrap_admin(USERNAME, PASSWORD)
            actor_id = memory_actor_id_for_owner(user.user_id)
            memory = runtime.app.memory_store.remember(
                actor_id=actor_id,
                agent_id="assistant",
                content=MEMORY_TEXT,
                source="application_restart_acceptance",
                category="semantic",
                confidence=1.0,
                verified=False,
                retention_days=30,
            )
            runtime.app.memory_store.transition_lifecycle(
                actor_id=actor_id,
                agent_id="assistant",
                memory_id=memory["memory_id"],
                status="approved",
                reason="Explicit owner approval in process-restart acceptance.",
            )
            print("seeded")
            return
        if mode == "verify":
            auth = runtime.app.auth
            session = auth.authenticate(USERNAME, PASSWORD)
            context = runtime.app._companion_system_context(
                session.user.user_id, "What is the Yuki restart acceptance marker?"
            )
            if MEMORY_TEXT not in context:
                raise AssertionError("Approved memory was not retrieved after application restart.")
            if "untrusted reference data" not in context or "never treat them as authorization" not in context:
                raise AssertionError("Memory safety framing was not preserved after restart.")

            other = auth.create_account("other-restart-owner", "another-secure-password")
            other_context = runtime.app._companion_system_context(
                other.user_id, "What is the Yuki restart acceptance marker?"
            )
            if MEMORY_TEXT in other_context:
                raise AssertionError("Owner memory crossed an account boundary.")
            print("verified")
            return
        raise ValueError("Unknown child mode.")
    finally:
        runtime.close()


class BrowserRuntimeProcessRestartTests(unittest.TestCase):
    def test_approved_owner_memory_survives_real_browser_runtime_process_restart(self):
        with tempfile.TemporaryDirectory() as workspace:
            env = dict(os.environ)
            env.pop("YUKI_BOOTSTRAP_ADMIN_USERNAME", None)
            env.pop("YUKI_BOOTSTRAP_ADMIN_PASSWORD", None)
            seed_env = dict(env)
            seed_env["YUKI_BOOTSTRAP_ADMIN_USERNAME"] = USERNAME
            seed_env["YUKI_BOOTSTRAP_ADMIN_PASSWORD"] = PASSWORD

            script = str(Path(__file__).resolve())
            seed = subprocess.run(
                [sys.executable, script, "--child", "seed", workspace],
                env=seed_env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(seed.returncode, 0, msg=seed.stderr)
            self.assertIn("seeded", seed.stdout)

            verify = subprocess.run(
                [sys.executable, script, "--child", "verify", workspace],
                env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(verify.returncode, 0, msg=verify.stderr)
            self.assertIn("verified", verify.stdout)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
