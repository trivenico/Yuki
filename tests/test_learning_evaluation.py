import pytest

from core.learning_evaluation import (
    LearningEvaluationCase,
    evaluate_memory_retrieval,
)


def test_evaluation_reports_hit_precision_recall_and_reciprocal_rank():
    cases = [
        LearningEvaluationCase("case-1", "router firmware", ("memory-a", "memory-b")),
        LearningEvaluationCase("case-2", "worker lease", ("memory-c",)),
    ]

    def retrieve(query, limit):
        assert limit == 2
        if query == "router firmware":
            return ["memory-b", "unrelated"]
        return []

    report = evaluate_memory_retrieval(cases, retrieve, k=2)

    assert report.case_count == 2
    assert report.k == 2
    assert report.hit_rate_at_k == 0.5
    assert report.precision_at_k == 0.25
    assert report.recall_at_k == 0.25
    assert report.mean_reciprocal_rank == 0.25


def test_evaluation_uses_ids_only_and_calls_retriever_once_per_case():
    calls = []
    cases = [LearningEvaluationCase("one", "query", ("expected-id",))]

    def retrieve(query, limit):
        calls.append((query, limit))
        return ["expected-id"]

    report = evaluate_memory_retrieval(cases, retrieve, k=1)

    assert calls == [("query", 1)]
    assert report.hit_rate_at_k == 1.0
    assert report.precision_at_k == 1.0
    assert report.recall_at_k == 1.0
    assert report.mean_reciprocal_rank == 1.0
    assert not hasattr(report, "query")


@pytest.mark.parametrize(
    "cases,retriever,k",
    [
        ([], lambda _q, _k: [], 1),
        ([LearningEvaluationCase("x", "q", ())], lambda _q, _k: [], 1),
        ([LearningEvaluationCase("x", "q", ("a",)), LearningEvaluationCase("x", "q", ("b",))], lambda _q, _k: [], 1),
        ([LearningEvaluationCase("x", "q", ("a",))], lambda _q, _k: ["a", "b"], 1),
        ([LearningEvaluationCase("x", "q", ("a",))], lambda _q, _k: ["a", "a"], 2),
        ([LearningEvaluationCase("x", "q", ("a",))], lambda _q, _k: [""], 1),
        ([LearningEvaluationCase("x", "q", ("a",))], lambda _q, _k: [], True),
    ],
)
def test_evaluation_rejects_invalid_contracts(cases, retriever, k):
    with pytest.raises((TypeError, ValueError)):
        evaluate_memory_retrieval(cases, retriever, k=k)


def test_evaluation_rejects_non_case_and_non_callable_retriever():
    with pytest.raises(TypeError):
        evaluate_memory_retrieval(["not a case"], lambda _q, _k: [])
    with pytest.raises(TypeError):
        evaluate_memory_retrieval(
            [LearningEvaluationCase("x", "q", ("a",))],
            None,
        )
