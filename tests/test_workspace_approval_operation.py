import hashlib
import unittest

from automation.approved_workspace_changes import FileChange
from core.tool_policy import argument_digest
from core.workspace_approval_operation import (
    build_workspace_approval_operation,
    verify_workspace_approval_operation,
    workspace_change_payload,
)


class WorkspaceApprovalOperationTests(unittest.TestCase):
    def setUp(self):
        self.change = FileChange(
            "README.md", hashlib.sha256(b"old").hexdigest(), "new approved content"
        )

    def test_envelope_binds_exact_workspace_payload_and_policy_digest(self):
        operation, payload = build_workspace_approval_operation(
            workspace_id="workspace:project-a", changes=[self.change]
        )
        self.assertEqual(operation.capability, "workspace:write")
        self.assertEqual(operation.operation_type, "workspace.write")
        self.assertEqual(operation.target, "workspace:project-a")
        self.assertEqual(operation.payload_sha256, argument_digest(payload))
        self.assertTrue(verify_workspace_approval_operation(
            operation, workspace_id="workspace:project-a", changes=[self.change]
        ))

    def test_content_path_old_digest_or_target_mutation_invalidates_envelope(self):
        operation, _ = build_workspace_approval_operation(
            workspace_id="workspace:project-a", changes=[self.change]
        )
        changed = FileChange(
            "README.md", hashlib.sha256(b"old").hexdigest(), "different content"
        )
        self.assertFalse(verify_workspace_approval_operation(
            operation, workspace_id="workspace:project-a", changes=[changed]
        ))
        self.assertFalse(verify_workspace_approval_operation(
            operation, workspace_id="workspace:project-b", changes=[self.change]
        ))
        renamed = FileChange(
            "docs/README.md", hashlib.sha256(b"old").hexdigest(), "new approved content"
        )
        self.assertFalse(verify_workspace_approval_operation(
            operation, workspace_id="workspace:project-a", changes=[renamed]
        ))

    def test_rejects_duplicate_paths_and_oversized_change_sets(self):
        with self.assertRaises(ValueError):
            workspace_change_payload([self.change, self.change])
        too_large = FileChange(
            "README.md", hashlib.sha256(b"old").hexdigest(), "x" * (256 * 1024 + 1)
        )
        with self.assertRaises(ValueError):
            workspace_change_payload([too_large])

    def test_rejects_invalid_workspace_identity_and_change_types(self):
        with self.assertRaises(ValueError):
            build_workspace_approval_operation(workspace_id="../project", changes=[self.change])
        with self.assertRaises(ValueError):
            workspace_change_payload(["not-a-file-change"])


if __name__ == "__main__":
    unittest.main()
