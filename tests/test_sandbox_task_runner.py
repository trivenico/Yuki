import tempfile
import unittest
from pathlib import Path
from core.sandbox_policy import SandboxPolicy
from core.sandbox_task_runner import execute_sandbox_task
from core.task_lifecycle import SQLiteTaskLifecycleStore

class SandboxTaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteTaskLifecycleStore(str(Path(self.tmp.name) / 'tasks.db'))

    def test_denied_approval_never_launches_executor(self):
        task = execute_sandbox_task(self.store, idempotency_key='deny-1', task_type='test',
            payload={'operation':'echo'}, policy=SandboxPolicy(), image='python:3.12',
            workspace=self.tmp.name, command=['python', '--version'],
            authorize=lambda request: False, env={})
        self.assertEqual(task.state, 'failed')
        self.assertEqual(task.error_code, 'approval_denied')
        self.assertTrue(self.store.verify_audit_chain())

    def test_existing_terminal_task_is_not_replayed(self):
        first = self.store.create_task(idempotency_key='stable-1', task_type='test', payload={'x':1})
        failed = self.store.transition(first.task_id, expected_state='pending', new_state='failed',
            expected_version=first.version, error_code='test_failure')
        called = []
        result = execute_sandbox_task(self.store, idempotency_key='stable-1', task_type='test',
            payload={'x':1}, policy=SandboxPolicy(), image='python:3.12', workspace=self.tmp.name,
            command=['python', '--version'], authorize=lambda request: called.append(True) or True, env={})
        self.assertEqual(result.state, 'failed')
        self.assertEqual(result.task_id, failed.task_id)
        self.assertEqual(called, [])

if __name__ == '__main__':
    unittest.main()
