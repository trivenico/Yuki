"""Architecture guard: production MCP tool calls must cross DurableMCPGateway.

This test intentionally scans runtime Python modules rather than only known call sites,
so new integrations cannot quietly call MCPHTTPClient.call_tool directly.
"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = (ROOT / "core" / "durable_mcp_gateway.py").resolve()
CLIENT = (ROOT / "core" / "mcp_client.py").resolve()
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "site-packages"}


def _runtime_python_files():
    for path in ROOT.rglob("*.py"):
        resolved = path.resolve()
        if resolved in {GATEWAY, CLIENT}:
            continue
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if "tests" in path.relative_to(ROOT).parts:
            continue
        yield path


def test_no_runtime_module_calls_mcp_tool_directly():
    violations = []
    for path in _runtime_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            violations.append(f"{path.relative_to(ROOT)}: cannot audit source ({type(exc).__name__})")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr == "call_tool":
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} calls .call_tool() outside the MCP gateway"
                )
    assert not violations, (
        "Route all production MCP tool calls through core.durable_mcp_gateway.DurableMCPGateway:\n"
        + "\n".join(violations)
    )


def test_mcp_gateway_is_the_single_runtime_direct_call_boundary():
    source = GATEWAY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(GATEWAY))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "call_tool"
    ]
    assert len(calls) == 1, "Gateway should contain exactly one underlying MCP call site."
    call = calls[0]
    assert isinstance(call.func.value, ast.Attribute)
    assert isinstance(call.func.value.value, ast.Name)
    assert call.func.value.value.id == "self"
    assert call.func.value.attr == "client"


def test_mcp_gateway_uses_durable_runtime_for_every_public_tool_call():
    source = GATEWAY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(GATEWAY))
    gateway_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DurableMCPGateway"
    )
    call_tool = next(
        node for node in gateway_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "call_tool"
    )
    calls = [
        node for node in ast.walk(call_tool)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "runtime"
    ]
    assert len(calls) == 1, "Public MCP calls must pass through the durable runtime."
    assert any(
        isinstance(arg, ast.Constant) and arg.value == "mcp_call"
        for arg in calls[0].args
    ), "Gateway must use the explicitly registered mcp_call operation."
