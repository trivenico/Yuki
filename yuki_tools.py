"""Small, explicitly allow-listed tools available to Yuki."""

import ast
import operator
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent / "yuki_workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

_ALLOWED_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def get_time(argument=""):
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def list_files(argument=""):
    """List file names in Yuki's workspace only."""
    items = sorted(p.name for p in WORKSPACE.iterdir() if p.is_file())
    return "\n".join(items) if items else "The Yuki workspace is empty."


def save_note(text=""):
    """Append a plain-text note to the local workspace."""
    if not isinstance(text, str) or not text.strip():
        return "Please provide note text."
    with (WORKSPACE / "notes.txt").open("a", encoding="utf-8") as handle:
        handle.write(text.strip().replace("\x00", "") + "\n")
    return "Note saved in Yuki's workspace."


def _calculate_node(node):
    if isinstance(node, ast.Expression):
        return _calculate_node(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        if abs(node.value) > 1_000_000_000:
            raise ValueError("Number is too large.")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
        left, right = _calculate_node(node.left), _calculate_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 8:
            raise ValueError("Exponent is too large (maximum absolute exponent: 8).")
        result = _ALLOWED_BINARY[type(node.op)](left, right)
        if isinstance(result, (int, float)) and abs(result) > 1e100:
            raise ValueError("Result is too large.")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_calculate_node(node.operand))
    raise ValueError("Only basic arithmetic is allowed.")


def calculate(expression=""):
    """Evaluate restricted arithmetic, never arbitrary Python."""
    if not isinstance(expression, str) or not expression.strip():
        return "Usage: /tool calculate 12 * (3 + 4)"
    if len(expression) > 200:
        return "Expression is too long."
    try:
        return str(_calculate_node(ast.parse(expression, mode="eval")))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as error:
        return f"Could not calculate: {error}"


TOOLS = {"time": get_time, "list_files": list_files, "save_note": save_note, "calculate": calculate}


def run_tool(name, argument=""):
    """Run only a tool explicitly registered above."""
    if not isinstance(name, str) or name not in TOOLS:
        return "Unknown tool. Available tools: " + ", ".join(sorted(TOOLS))
    try:
        return str(TOOLS[name](argument))
    except Exception:
        return "The tool failed. Check the input and try again."
