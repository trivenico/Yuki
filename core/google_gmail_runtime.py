"""Trusted Gmail tool composition for Yuki's guarded agent runtime.

This factory joins the send handler, host capability classification, bounded
model schema, durable approval boundary, and provider reconciliation. OAuth
credentials and transports are injected as already-constructed trusted clients;
this module never reads environment variables or initializes secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.google_gmail_readonly_client import GmailReadOnlyClient
from core.google_gmail_send_client import GmailSendClient
from core.google_gmail_send_reconciliation import GmailSendReconciliationAdapter
from core.google_gmail_send_tool import (
    GMAIL_SEND_TOOL_NAME,
    GmailSendTool,
    gmail_send_tool_capability,
)
from core.guarded_tool_runtime import GuardedToolRuntime
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_journal import ToolActionJournal
from core.structured_tool_protocol import StructuredToolProtocol
from core.tool_action_reconciliation import (
    ToolActionReconciliationRegistry,
    ToolActionReconciliationService,
)
from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog


@dataclass(frozen=True)
class GmailToolRuntimeBundle:
    """Immutable publication of the guarded runtime and trusted Gmail surfaces."""

    guarded_runtime: GuardedToolRuntime
    tool_protocol: StructuredToolProtocol
    tool_definitions: tuple[dict[str, Any], ...]
    reconciliation_service: ToolActionReconciliationService


def build_gmail_tool_runtime_bundle(
    *,
    existing_tools: Mapping[str, Any],
    existing_capabilities: ToolCapabilityCatalog,
    existing_tool_definitions: Sequence[dict[str, Any]],
    policy: Any,
    action_journal: ToolActionJournal,
    approval_store: SQLiteToolActionApprovalStore,
    proposals: SQLitePendingToolActionProposalStore,
    send_client: GmailSendClient,
    readonly_client: GmailReadOnlyClient,
    message_id_domain: str,
    existing_reconciliation_adapters: Mapping[str, Any] | None = None,
    audit=None,
) -> GmailToolRuntimeBundle:
    """Compose Gmail with existing trusted tools without replacing their policy.

    Every existing handler must already have an explicit capability entry and a
    model schema supplied by the host. The returned runtime enforces action
    contexts, durable journal claims, and single-use durable approvals for all
    side-effecting tools. The Gmail send handler is never callable directly.
    """
    if not isinstance(existing_tools, Mapping):
        raise TypeError("existing_tools must be a trusted mapping")
    if not isinstance(existing_capabilities, ToolCapabilityCatalog):
        raise TypeError("existing_capabilities must be a ToolCapabilityCatalog")
    if not isinstance(existing_tool_definitions, (tuple, list)):
        raise TypeError("existing_tool_definitions must be a sequence")
    if set(existing_tools) != set(existing_capabilities.names()):
        raise ValueError("every existing tool must have exactly one capability")
    if GMAIL_SEND_TOOL_NAME in existing_tools:
        raise ValueError("Gmail send tool is already registered")
    for item in existing_tool_definitions:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise ValueError("existing tool definitions must be trusted function schemas")
        function = item.get("function")
        if not isinstance(function, dict) or function.get("name") not in existing_tools:
            raise ValueError("existing tool schema does not match a registered handler")
    if len({item["function"]["name"] for item in existing_tool_definitions}) != len(existing_tool_definitions):
        raise ValueError("existing tool schemas contain duplicate names")
    if set(item["function"]["name"] for item in existing_tool_definitions) != set(existing_tools):
        raise ValueError("every existing handler must have exactly one model schema")
    for value, expected, label in (
        (action_journal, ToolActionJournal, "action_journal"),
        (approval_store, SQLiteToolActionApprovalStore, "approval_store"),
        (proposals, SQLitePendingToolActionProposalStore, "proposals"),
        (send_client, GmailSendClient, "send_client"),
        (readonly_client, GmailReadOnlyClient, "readonly_client"),
    ):
        if not isinstance(value, expected):
            raise TypeError(label + " has an invalid trusted implementation")
    if not callable(getattr(policy, "authorize_tool", None)):
        raise TypeError("policy must provide authorize_tool(name)")
    if audit is not None and not callable(audit):
        raise TypeError("audit must be callable")
    adapters = dict(existing_reconciliation_adapters or {})
    if GMAIL_SEND_TOOL_NAME in adapters:
        raise ValueError("Gmail reconciliation adapter is already registered")

    send_tool = GmailSendTool(send_client)
    gmail_adapter = GmailSendReconciliationAdapter(
        client=readonly_client, message_id_domain=message_id_domain,
    )
    tools = dict(existing_tools)
    tools[GMAIL_SEND_TOOL_NAME] = send_tool
    capabilities = {
        name: existing_capabilities.resolve(name) for name in existing_capabilities.names()
    }
    capabilities[GMAIL_SEND_TOOL_NAME] = gmail_send_tool_capability()
    catalog = ToolCapabilityCatalog(capabilities)
    guarded = GuardedToolRuntime(
        tools, policy, audit=audit, capabilities=catalog,
        require_action_context=True, action_journal=action_journal,
        approval_store=approval_store,
    )
    adapters[GMAIL_SEND_TOOL_NAME] = gmail_adapter
    reconciliation = ToolActionReconciliationService(
        action_journal,
        ToolActionReconciliationRegistry(adapters),
        proposals=proposals,
    )
    definitions = tuple(dict(item) for item in existing_tool_definitions) + (
        send_tool.definition(),
    )
    return GmailToolRuntimeBundle(
        guarded_runtime=guarded,
        tool_protocol=StructuredToolProtocol(guarded),
        tool_definitions=definitions,
        reconciliation_service=reconciliation,
    )
