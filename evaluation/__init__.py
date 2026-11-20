"""Deterministic evaluation utilities for qualifying model candidates."""

from evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry
from evaluation.model_eval import EvalCase, EvalReport, ModelEvaluator
from evaluation.run_store import EvaluationRun, EvaluationRunConflict, EvaluationRunStore
from evaluation.service import EvaluationCapacityError, EvaluationService, TrustedEvaluationAdapter
from evaluation.benchmark import BenchmarkCase, BenchmarkReport, EvaluationError, evaluate_benchmark, validate_benchmark_report
from evaluation.comparison import CaseDelta, ComparisonError, ModelComparison, compare_evaluations, validate_comparison
from evaluation.builtin_benchmarks import BUILTIN_BENCHMARK_ID, BUILTIN_BENCHMARK_VERSION, build_builtin_benchmark

__all__ = ["BenchmarkDefinition", "BenchmarkRegistry", "EvalCase", "EvalReport", "ModelEvaluator", "EvaluationRun", "EvaluationRunConflict", "EvaluationRunStore", "EvaluationCapacityError", "EvaluationService", "TrustedEvaluationAdapter", "BenchmarkCase", "BenchmarkReport", "EvaluationError", "evaluate_benchmark", "validate_benchmark_report", "CaseDelta", "ComparisonError", "ModelComparison", "compare_evaluations", "validate_comparison", "BUILTIN_BENCHMARK_ID", "BUILTIN_BENCHMARK_VERSION", "build_builtin_benchmark"]
