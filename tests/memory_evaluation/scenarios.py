"""Synthetic, deterministic memory retrieval evaluation scenarios.

Fixtures contain opaque IDs and labels only; they do not access user memory,
invoke models, or mutate any persistent store.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalScenario:
    name: str
    query: str
    ranked_ids: tuple[str, ...]
    relevance: tuple[tuple[str, float], ...]
    authorized_ids: frozenset[str]
    active_ids: frozenset[str]

    @property
    def relevance_by_id(self) -> dict[str, float]:
        return dict(self.relevance)

    @property
    def relevant_ids(self) -> frozenset[str]:
        return frozenset(memory_id for memory_id, score in self.relevance if score > 0)


SCENARIOS = (
    RetrievalScenario(
        name="older_relevant_behind_recent_noise",
        query="deployment rollback strategy",
        ranked_ids=("old-deployment", "garden-1", "garden-2", "garden-3"),
        relevance=(("old-deployment", 3.0),),
        authorized_ids=frozenset({"old-deployment", "garden-1", "garden-2", "garden-3"}),
        active_ids=frozenset({"old-deployment", "garden-1", "garden-2", "garden-3"}),
    ),
    RetrievalScenario(
        name="recent_relevant_memory",
        query="current project milestone",
        ranked_ids=("recent-project", "old-project", "unrelated"),
        relevance=(("recent-project", 3.0), ("old-project", 1.0)),
        authorized_ids=frozenset({"recent-project", "old-project", "unrelated"}),
        active_ids=frozenset({"recent-project", "old-project", "unrelated"}),
    ),
    RetrievalScenario(
        name="historical_preference_and_correction",
        query="preferred response detail",
        ranked_ids=("current-preference", "historical-preference"),
        relevance=(("current-preference", 3.0), ("historical-preference", 1.0)),
        authorized_ids=frozenset({"current-preference", "historical-preference"}),
        active_ids=frozenset({"current-preference", "historical-preference"}),
    ),
    RetrievalScenario(
        name="semantic_duplicate_wording",
        query="gradual deployment",
        ranked_ids=("deployment-rollout", "progressive-release"),
        relevance=(("deployment-rollout", 3.0), ("progressive-release", 2.0)),
        authorized_ids=frozenset({"deployment-rollout", "progressive-release"}),
        active_ids=frozenset({"deployment-rollout", "progressive-release"}),
    ),
    RetrievalScenario(
        name="cross_owner_memory_isolation",
        query="private project note",
        ranked_ids=("owner-private",),
        relevance=(("owner-private", 3.0),),
        authorized_ids=frozenset({"owner-private"}),
        active_ids=frozenset({"owner-private"}),
    ),
    RetrievalScenario(
        name="deleted_memory_must_not_leak",
        query="retired project detail",
        ranked_ids=("active-project",),
        relevance=(("active-project", 1.0),),
        authorized_ids=frozenset({"active-project", "deleted-project"}),
        active_ids=frozenset({"active-project"}),
    ),
)
