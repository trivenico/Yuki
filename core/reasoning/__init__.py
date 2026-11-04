"""Reasoning context assembly primitives for Yuki.

This package prepares bounded, provenance-bearing input for a reasoner. It
does not perform inference, execute tools, or alter memory/model weights.
"""
from core.reasoning.context_builder import ReasoningContextBuilder

__all__ = ["ReasoningContextBuilder"]
\nfrom providers.reasoning_agent_adapter import ReasoningAgentAdapter\n