"""Deterministic evaluation utilities for qualifying model candidates."""

from evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry
from evaluation.model_eval import EvalCase, EvalReport, ModelEvaluator
from evaluation.run_store import EvaluationRun, EvaluationRunConflict, EvaluationRunStore
from evaluation.service import EvaluationCapacityError, EvaluationService, TrustedEvaluationAdapter
from evaluation.benchmark import BenchmarkCase, BenchmarkReport, EvaluationError, evaluate_benchmark, validate_benchmark_report
from evaluation.comparison import CaseDelta, ComparisonError, ModelComparison, compare_evaluations, validate_comparison
from evaluation.builtin_benchmarks import (BUILTIN_BENCHMARK_ID, BUILTIN_BENCHMARK_VERSION, build_builtin_benchmark, ASSISTANT_QUALITY_BENCHMARK_ID, CODING_BENCHMARK_ID, AGENT_RELIABILITY_BENCHMARK_ID, SPECIALIZED_BENCHMARK_VERSION, build_assistant_quality_benchmark, build_coding_engineering_benchmark, build_agent_tool_reliability_benchmark, build_training_baseline_benchmarks)

__all__ = ["BenchmarkDefinition", "BenchmarkRegistry", "EvalCase", "EvalReport", "ModelEvaluator", "EvaluationRun", "EvaluationRunConflict", "EvaluationRunStore", "EvaluationCapacityError", "EvaluationService", "TrustedEvaluationAdapter", "BenchmarkCase", "BenchmarkReport", "EvaluationError", "evaluate_benchmark", "validate_benchmark_report", "CaseDelta", "ComparisonError", "ModelComparison", "compare_evaluations", "validate_comparison", "BUILTIN_BENCHMARK_ID", "BUILTIN_BENCHMARK_VERSION", "build_builtin_benchmark", "ASSISTANT_QUALITY_BENCHMARK_ID", "CODING_BENCHMARK_ID", "AGENT_RELIABILITY_BENCHMARK_ID", "SPECIALIZED_BENCHMARK_VERSION", "build_assistant_quality_benchmark", "build_coding_engineering_benchmark", "build_agent_tool_reliability_benchmark", "build_training_baseline_benchmarks"]
