"""Explicit inventory of CLI side-effect surfaces and their production controls.

Keep this inventory synchronized with command dispatch in main.py. An entry is
production-ready only after the operation is routed through the durable approval,
audit, and execution-ledger boundary and has integration coverage.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SideEffectSurface:
    command: str
    description: str
    durable_controls_integrated: bool
    integration_tested: bool


# Intentionally conservative: this list describes command families observed in
# the CLI, not a claim that every nested implementation has been exhaustively
# discovered. Unverified entries remain false and block production readiness.
SIDE_EFFECT_SURFACES = (
    SideEffectSurface("/task", "model-guided registered skill execution", True, True),
    SideEffectSurface("/skill", "direct registered skill execution", True, True),
    SideEffectSurface("/memory add|delete|clear", "local memory mutation", True, True),
    SideEffectSurface("/knowledge index", "workspace file indexing", True, False),
    SideEffectSurface("/reminder add", "persistent reminder mutation", True, True),
    SideEffectSurface("/evolution run|review", "proposal execution or acceptance", False, False),
    SideEffectSurface("/voice record|speak|transcribe", "audio capture, synthesis, or file access", False, False),
    SideEffectSurface("/security recon", "network reconnaissance", False, False),
    SideEffectSurface("/sandbox run", "containerized program execution", False, False),
    SideEffectSurface("/defense sniff|block|remove", "packet capture or firewall mutation", False, False),
)


def production_inventory_report() -> dict:
    """Return a stable, machine-readable report for startup checks and tests."""
    outstanding = [
        item.command for item in SIDE_EFFECT_SURFACES
        if not (item.durable_controls_integrated and item.integration_tested)
    ]
    return {
        "surface_count": len(SIDE_EFFECT_SURFACES),
        "ready_count": len(SIDE_EFFECT_SURFACES) - len(outstanding),
        "outstanding": outstanding,
        "production_ready": not outstanding,
    }


# All CLI dispatch predicates in main.py, including read-only commands. Keep this
# set in sync with the AST regression test; adding a command without a policy
# entry fails CI. Prefix entries correspond to str.startswith() predicates.
COMMAND_DISPATCH_PATTERNS = frozenset({
    "/status", "/skills", "/quit", "/exit", "/help", "/readiness", "/performance", "/performance-history", "/devices",
    "/task ", "/ask ", "/ask-cloud ", "/skill ", "/plan ", "/memory add ",
    "/memory list", "/memory search ", "/memory delete ", "/memory clear",
    "/knowledge index ", "/reminder add ", "/reconcile list", "/reconcile resolve ",
    "/reminders", "/reminders due", "/evolution status", "/evolution record ",
    "/evolution run", "/evolution review ", "/voice status", "/voice record ",
    "/voice speak ", "/voice transcribe ", "/voice wakeword status",
    "/voice wakeword listen ", "/voice speaker-check ", "/security status",
    "/security recon ", "/sandbox status", "/sandbox run ", "/defense status",
    "/defense sniff", "/defense block ", "/defense remove ", "/metasploit status",
    "/dashboard", "/self-improvement status", "/self-improvement validate ", "/self-improvement stage ",
})
