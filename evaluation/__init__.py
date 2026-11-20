"""Deterministic evaluation utilities for qualifying model candidates."""

from evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry
from evaluation.model_eval import EvalCase, EvalReport, ModelEvaluator
from evaluation.run_store import EvaluationRun, EvaluationRunConflict, EvaluationRunStore
from evaluation.service import EvaluationCapacityError, EvaluationService, TrustedEvaluationAdapter
from evaluation.benchmark import BenchmarkCase, BenchmarkReport, EvaluationError, evaluate_benchmark, validate_benchmark_report

__all__ = ["BenchmarkDefinition", "BenchmarkRegistry", "EvalCase", "EvalReport", "ModelEvaluator", "EvaluationRun", "EvaluationRunConflict", "EvaluationRunStore", "EvaluationCapacityError", "EvaluationService", "TrustedEvaluationAdapter", "BenchmarkCase", "BenchmarkReport", "EvaluationError", "evaluate_benchmark", "validate_benchmark_report"]
