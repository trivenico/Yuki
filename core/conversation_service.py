"""Application service joining persisted conversations to the guarded agent loop.

The service accepts an authenticated owner_id from its caller. It never derives
identity from request payloads and never constructs an unguarded tool runtime.
"""
from __future__ import annotations

import sys
import threading
import hashlib
import json
import uuid
from typing import Any, Sequence

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_store import ConversationConflict, ConversationStore


class ConversationService:
    LEASE_TTL_SECONDS = 3600
    LEASE_RENEW_INTERVAL_SECONDS = 1200

    """Synchronous conversation orchestration with durable per-conversation leases.

    Independent conversations may execute concurrently. A database lease
    coordinates workers that share the same conversation store.
    """

    def __init__(
        self, store: ConversationStore, agent_loop: AgentExecutionLoop,
        *, agent_loop_for_owner=None, provider_outcome_reconciler=None,
    ):
        if not isinstance(store, ConversationStore):
            raise TypeError("store must be a ConversationStore.")
        if not isinstance(agent_loop, AgentExecutionLoop):
            raise TypeError("agent_loop must be an AgentExecutionLoop.")
        self.store = store
        if agent_loop_for_owner is not None and not callable(agent_loop_for_owner):
            raise TypeError("agent_loop_for_owner must be callable.")
        self.agent_loop = agent_loop
        self.agent_loop_for_owner = agent_loop_for_owner
        if provider_outcome_reconciler is not None and not callable(
            getattr(provider_outcome_reconciler, "reconcile", None)
        ):
            raise TypeError("provider_outcome_reconciler must implement reconcile.")
        self.provider_outcome_reconciler = provider_outcome_reconciler

    def create(self, *, owner_id: str, title: str = "New conversation") -> dict[str, Any]:
        return self.store.create_conversation(owner_id=owner_id, title=title)

    def tool_executions(
        self, *, owner_id: str, conversation_id: str, request_id: str,
    ) -> list[dict[str, Any]]:
        """Return sanitized execution metadata for the authenticated conversation owner."""
        self.store.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        return self.store.list_tool_executions(
            owner_id=owner_id, conversation_id=conversation_id, request_id=request_id.strip()
        )

    def reconcile_provider_outcome(
        self, *, owner_id: str, conversation_id: str,
        request_id: str, call_id: str,
    ) -> dict[str, Any]:
        """Perform one explicitly configured read-only provider lookup."""
        self.store.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        if self.provider_outcome_reconciler is None:
            raise LookupError("provider outcome reconciliation is not configured.")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 64:
            raise ValueError("call_id must be non-empty text of at most 64 characters.")
        return self.provider_outcome_reconciler.reconcile(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id.strip(), call_id=call_id.strip(),
        )

    def provider_outcome_evidence(
        self, *, owner_id: str, conversation_id: str,
        request_id: str, call_id: str,
    ) -> list[dict[str, Any]]:
        """Return redacted provider verification evidence to the authenticated owner."""
        self.store.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 64:
            raise ValueError("call_id must be non-empty text of at most 64 characters.")
        return self.store.list_provider_outcome_evidence(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id.strip(), call_id=call_id.strip(),
        )

    def review_tool_execution(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        call_id: str, action: str, reason: str,
    ) -> dict[str, Any]:
        self.store.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        return self.store.review_tool_execution(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, call_id=call_id, action=action, reason=reason,
        )

    def tool_execution_reviews(
        self, *, owner_id: str, conversation_id: str,
        request_id: str, call_id: str,
    ) -> list[dict[str, Any]]:
        self.store.get_conversation(owner_id=owner_id, conversation_id=conversation_id)
        return self.store.list_tool_execution_reviews(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, call_id=call_id,
        )

    def list(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_conversations(owner_id=owner_id, limit=limit)

    def history(
        self, *, owner_id: str, conversation_id: str, limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_messages(
            owner_id=owner_id, conversation_id=conversation_id,
            limit=limit, after_sequence=after_sequence,
        )

    def reply(self, *, owner_id: str, conversation_id: str, user_text: str, request_id: str, expected_version: int, tools: Sequence[dict[str, Any]], system_text: str | None = None, on_text_delta=None, cancellation_requested=None) -> dict[str, Any]:
        token = str(uuid.uuid4())
        ttl = self.LEASE_TTL_SECONDS
        interval = self.LEASE_RENEW_INTERVAL_SECONDS
        if type(ttl) is not int or not 5 <= ttl <= 3600:
            raise RuntimeError("conversation lease TTL configuration is invalid.")
        if type(interval) not in (int, float) or not 0 < interval < ttl:
            raise RuntimeError("conversation lease renewal interval must be positive and shorter than its TTL.")
        self.store.acquire_generation_lease(
            owner_id=owner_id, conversation_id=conversation_id,
            lease_token=token, ttl_seconds=ttl,
        )
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def renew_while_running():
            while not stop_heartbeat.wait(interval):
                try:
                    self.store.renew_generation_lease(
                        owner_id=owner_id, conversation_id=conversation_id,
                        lease_token=token, ttl_seconds=ttl,
                    )
                except Exception:
                    # Fail closed: never allow execution to commit after the
                    # heartbeat cannot prove that this worker still owns lease.
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=renew_while_running,
            name="yuki-conversation-lease",
            daemon=True,
        )
        heartbeat_started = False

        def combined_cancellation_requested():
            if lease_lost.is_set():
                return True
            return bool(cancellation_requested and cancellation_requested())

        try:
            heartbeat.start()
            heartbeat_started = True
            result = self._reply_unleased(
                owner_id=owner_id, conversation_id=conversation_id,
                user_text=user_text, request_id=request_id,
                expected_version=expected_version, tools=tools,
                system_text=system_text, on_text_delta=on_text_delta,
                cancellation_requested=combined_cancellation_requested,
                lease_token=token, lease_lost_event=lease_lost,
            )
            return result
        finally:
            stop_heartbeat.set()
            if heartbeat_started:
                heartbeat.join(timeout=max(1.0, min(float(interval), 5.0)))
            active_error = sys.exc_info()[1]
            try:
                self.store.release_generation_lease(
                    owner_id=owner_id, conversation_id=conversation_id,
                    lease_token=token,
                )
            except Exception as cleanup_error:
                if active_error is None:
                    raise
                # Preserve the execution/lease-loss exception while retaining
                # cleanup diagnostics for logs and debuggers.
                add_note = getattr(active_error, "add_note", None)
                if callable(add_note):
                    add_note(
                        "conversation lease cleanup also failed: "
                        + type(cleanup_error).__name__
                    )

    def _reply_unleased(
        self, *, owner_id: str, conversation_id: str, user_text: str,
        request_id: str, expected_version: int,
        tools: Sequence[dict[str, Any]], system_text: str | None = None,
        on_text_delta=None, cancellation_requested=None, lease_token: str | None = None,
        lease_lost_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 20_000:
            raise ValueError("user_text must be non-empty text of at most 20000 characters.")
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")

        starting_version = self.store.get_conversation(
            owner_id=owner_id, conversation_id=conversation_id
        )["version"]
        recovered_request_ids = set()
        # Reconcile durable running records before accepting retries. Never
        # re-execute a model/tool sequence after a process crash: side effects
        # may already have happened before the crash.
        pending = self.store.list_interrupted_generations(
            owner_id=owner_id, conversation_id=conversation_id, limit=100
        )
        for generation in pending:
            # A matching request ID here is a prior durable attempt, not the
            # current call: new generation rows are created only after this
            # recovery pass. Reconcile it before considering idempotent replay.
            recovered_request_ids.add(generation["request_id"])
            self.store.mark_started_tool_executions_uncertain(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=generation["request_id"], lease_token=lease_token,
            )
            conversation_now = self.store.get_conversation(
                owner_id=owner_id, conversation_id=conversation_id
            )
            messages_now = self.store.list_messages(
                owner_id=owner_id, conversation_id=conversation_id, limit=200
            )
            user_message = next(
                (item for item in messages_now if item["message_id"] == generation["user_message_id"]),
                None,
            )
            assistant_already_persisted = self.store.has_message(
                owner_id=owner_id, conversation_id=conversation_id,
                idempotency_key="assistant:" + generation["request_id"],
            )
            if assistant_already_persisted:
                self.store.finish_generation(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=generation["request_id"], status="completed", lease_token=lease_token,
                )
            elif user_message is not None:
                self.store.append_message(
                    owner_id=owner_id, conversation_id=conversation_id,
                    role="assistant",
                    content="This response was interrupted before completion. Please submit a new request; Yuki will not automatically repeat potentially side-effecting actions.",
                    idempotency_key="assistant:" + generation["request_id"],
                    expected_version=conversation_now["version"], lease_token=lease_token,
                )
                self.store.finish_generation(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=generation["request_id"], status="interrupted", lease_token=lease_token,
                )
            else:
                # A missing user-message row is an invariant violation, but
                # must not leave the durable generation permanently running.
                # Any started tool records were already marked uncertain above.
                self.store.finish_generation(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=generation["request_id"], status="interrupted",
                    lease_token=lease_token,
                )
        # Process at most one bounded recovery page per request. If the page
        # is full, conservatively require another request to drain the backlog
        # rather than starting fresh model work while older generations remain.
        if len(pending) == 100:
            raise ConversationConflict(
                "conversation recovery backlog remains; retry after the current batch is reconciled."
            )
        # Check ownership and stale versions before invoking a model.
        conversation = self.store.get_conversation(
            owner_id=owner_id, conversation_id=conversation_id
        )
        if (recovered_request_ids and request_id not in recovered_request_ids
                and expected_version == starting_version):
            # Recovery appended the missing assistant turn. Advance this new
            # request's optimistic version by exactly those reconciled writes.
            expected_version = conversation["version"]
        existing_messages = self.store.list_messages(
            owner_id=owner_id, conversation_id=conversation_id, limit=200
        )
        if conversation["version"] != expected_version:
            # A retry with the same request ID is allowed to retrieve its
            # already persisted answer; all other stale writes fail closed.
            duplicate = self.store.append_message(
                owner_id=owner_id, conversation_id=conversation_id,
                role="user", content=user_text, idempotency_key=request_id,
                expected_version=expected_version,
            )
            if duplicate["created"]:
                raise ConversationConflict("conversation version changed.")
            return self._replay_or_conflict(
                owner_id=owner_id, conversation_id=conversation_id,
                user_message=duplicate["message"],
            )

        if existing_messages and existing_messages[-1]["role"] == "user":
            raise ConversationConflict(
                "the previous turn has no persisted assistant response; reconcile it before sending another."
            )

        # Resolve the execution loop from the authenticated owner identity.
        # The caller cannot supply an owner ID through the conversation payload.
        agent_loop = self.agent_loop
        if self.agent_loop_for_owner is not None:
            agent_loop = self.agent_loop_for_owner(owner_id)
            if not isinstance(agent_loop, AgentExecutionLoop):
                raise TypeError("agent_loop_for_owner must return an AgentExecutionLoop.")

        user_result = self.store.append_message(
            owner_id=owner_id, conversation_id=conversation_id,
            role="user", content=user_text, idempotency_key=request_id,
            expected_version=expected_version, generation_request_id=request_id,
        )
        if not user_result["created"]:
            return self._replay_or_conflict(
                owner_id=owner_id, conversation_id=conversation_id,
                user_message=user_result["message"],
            )

        history = [
            {"role": item["role"], "content": item["content"]}
            for item in existing_messages[-126:]
        ]
        status, answer, model_turns, tool_calls = "completed", "", 0, 0
        self.store.update_generation_checkpoint(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, checkpoint={"phase": "model_running"},
            lease_token=lease_token,
        )
        try:
            kwargs = {"tools": list(tools), "history": history}
            if system_text is not None:
                kwargs["system_text"] = system_text
            def before_tool_call(call, argument):
                digest = hashlib.sha256(argument.encode("utf-8")).hexdigest()
                scope = json.dumps(
                    ["yuki-tool-v1", owner_id, conversation_id, request_id, call.call_id],
                    ensure_ascii=False, separators=(",", ":"),
                )
                idempotency_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
                provider_support = agent_loop.tool_protocol.supports_idempotency(call.name)
                claim = self.store.begin_tool_execution(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, call_id=call.call_id, tool_name=call.name,
                    arguments_sha256=digest, lease_token=lease_token,
                    idempotency_key=idempotency_key, provider_idempotency=provider_support,
                )
                if not claim["created"]:
                    return claim["status"]
                return {"status": "claimed", "idempotency_key": idempotency_key}

            def on_tool_error(call, argument, outcome):
                # A handler exception cannot prove that an external provider did
                # not commit its side effect before the error reached us.
                self.store.finish_tool_execution(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, call_id=call.call_id,
                    lease_token=lease_token, status=outcome,
                )

            def after_tool_call(call, argument, output):
                if not self.store.finish_tool_execution(
                    owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, call_id=call.call_id,
                    lease_token=lease_token, status="completed",
                ):
                    raise ConversationConflict(
                        "tool side effect finished but its durable journal could not be finalized."
                    )

            result = agent_loop.run(
                user_text, on_text_delta=on_text_delta,
                cancellation_requested=cancellation_requested,
                before_tool_call=before_tool_call, after_tool_call=after_tool_call,
                on_tool_error=on_tool_error,
                **kwargs,
            )
            status = result.status
            model_turns = result.model_turns
            tool_calls = result.tool_calls
            if result.status == "completed":
                answer = result.text
            elif result.status == "cancelled":
                answer = "Generation was stopped at your request."
            elif result.status == "interrupted":
                answer = (
                    "A previous attempt may already have performed a tool action. "
                    "Yuki blocked automatic replay; review the action status before retrying."
                )
            else:
                answer = (
                    "I couldn't complete that request within the current execution limits. "
                    "Please try a smaller or more specific request."
                )
        except Exception:
            status = "failed"
            answer = "I couldn't complete that request because the AI service or a guarded operation failed."

        if lease_lost_event is not None and lease_lost_event.is_set():
            raise ConversationConflict(
                "generation lease renewal failed; this worker cannot commit a response."
            )

        assistant_result = self.store.append_message(
            owner_id=owner_id, conversation_id=conversation_id,
            role="assistant", content=answer,
            idempotency_key=f"assistant:{request_id}",
            expected_version=user_result["version"], lease_token=lease_token,
        )
        self.store.finish_generation(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id,
            status=status if status in {"completed", "failed", "cancelled", "interrupted"} else "failed",
            lease_token=lease_token,
        )
        return {
            "conversation_id": conversation_id,
            "status": status,
            "answer": assistant_result["message"]["content"],
            "version": assistant_result["version"],
            "model_turns": model_turns,
            "tool_calls": tool_calls,
            "replayed": False,
        }

    def _replay_or_conflict(self, *, owner_id: str, conversation_id: str, user_message: dict):
        following = self.store.list_messages(
            owner_id=owner_id, conversation_id=conversation_id,
            after_sequence=user_message["sequence"], limit=1,
        )
        if following and following[0]["role"] == "assistant":
            return {
                "conversation_id": conversation_id,
                "status": "replayed",
                "answer": following[0]["content"],
                "version": self.store.get_conversation(
                    owner_id=owner_id, conversation_id=conversation_id
                )["version"],
                "model_turns": 0,
                "tool_calls": 0,
                "replayed": True,
            }
        raise ConversationConflict(
            "this request was already recorded but has no completed assistant response."
        )
