import unittest

from automation.agent_tool_gateway import (
    AgentTool, AgentToolError, SpecialistToolGateway,
)


class SpecialistToolGatewayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.auth = lambda actor, action, agent: actor == "alice" and action in {
            "agent:tool:list", "agent:tool:invoke",
        }
        self.gateway = SpecialistToolGateway([
            AgentTool(
                "project.read", "workspace:read",
                lambda path: self.calls.append(path) or {"path": path, "content": "safe"},
                "Read a file from an approved project workspace.",
            ),
        ], authorize=self.auth)

    def test_only_tools_with_granted_permissions_are_listed(self):
        visible = self.gateway.list_tools(
            actor_id="alice", agent_id="coder",
            permissions=frozenset({"workspace:read"}),
        )
        self.assertEqual([item["name"] for item in visible], ["project.read"])
        self.assertEqual(self.gateway.list_tools(
            actor_id="alice", agent_id="coder", permissions=frozenset(),
        ), ())

    def test_invocation_requires_actor_and_agent_permission(self):
        with self.assertRaises(PermissionError):
            self.gateway.invoke(
                actor_id="alice", agent_id="coder", permissions=frozenset(),
                tool_name="project.read", arguments={"path": "README.md"},
            )
        with self.assertRaises(PermissionError):
            self.gateway.invoke(
                actor_id="mallory", agent_id="coder",
                permissions=frozenset({"workspace:read"}),
                tool_name="project.read", arguments={"path": "README.md"},
            )
        self.assertEqual(self.calls, [])

    def test_authorized_invocation_calls_only_registered_handler(self):
        result = self.gateway.invoke(
            actor_id="alice", agent_id="coder",
            permissions=frozenset({"workspace:read"}),
            tool_name="project.read", arguments={"path": "README.md"},
        )
        self.assertEqual(result["content"], "safe")
        self.assertEqual(self.calls, ["README.md"])

    def test_unknown_tool_and_oversized_arguments_are_rejected(self):
        with self.assertRaises(AgentToolError):
            self.gateway.invoke(
                actor_id="alice", agent_id="coder",
                permissions=frozenset({"workspace:read"}),
                tool_name="shell.exec", arguments={},
            )
        with self.assertRaises(AgentToolError):
            self.gateway.invoke(
                actor_id="alice", agent_id="coder",
                permissions=frozenset({"workspace:read"}),
                tool_name="project.read", arguments={"path": "x" * 20000},
            )
        self.assertEqual(self.calls, [])

    def test_non_json_results_and_oversized_results_are_rejected(self):
        gateway = SpecialistToolGateway([
            AgentTool("bad.result", "workspace:read", lambda: object(), "Bad result"),
            AgentTool("large.result", "workspace:read", lambda: "x" * 40000, "Large result"),
        ], authorize=self.auth)
        for name in ("bad.result", "large.result"):
            with self.subTest(name=name), self.assertRaises(AgentToolError):
                gateway.invoke(
                    actor_id="alice", agent_id="coder",
                    permissions=frozenset({"workspace:read"}),
                    tool_name=name, arguments={},
                )


if __name__ == "__main__":
    unittest.main()
