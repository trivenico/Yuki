"""Packet capture must use durable approval for capture and alert responses."""
import ast
import unittest
from pathlib import Path


class PacketCaptureDurableApprovalTests(unittest.TestCase):
    def test_packet_capture_and_alert_firewall_action_are_registered(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        registrations = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "DurableOperationRuntime":
                continue
            for keyword in node.keywords:
                if keyword.arg == "operations" and isinstance(keyword.value, ast.Dict):
                    registrations.update(
                        key.value for key in keyword.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )
        self.assertIn("packet_capture", registrations)
        self.assertIn("firewall_block", registrations)

        packet_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_packet_capture"
        )
        nested = [
            node for node in ast.walk(packet_fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "durable_operations"
        ]
        self.assertTrue(nested, "packet alert callback must route firewall changes through durable approval")


if __name__ == "__main__":
    unittest.main()
