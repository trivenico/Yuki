import unittest

from core.native_intents import IntentKind, NativeIntentParser


class NativeIntentParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = NativeIntentParser()

    def test_help_and_status_aliases(self):
        self.assertEqual(self.parser.parse("/help").kind, IntentKind.HELP)
        self.assertEqual(self.parser.parse("STATUS").kind, IntentKind.STATUS)

    def test_memory_search_preserves_quoted_query(self):
        intent = self.parser.parse('/memory search "project launch notes"')
        self.assertEqual(intent.kind, IntentKind.MEMORY_SEARCH)
        self.assertEqual(intent.arguments, ("project launch notes",))
        self.assertFalse(intent.requires_confirmation)

    def test_memory_remember_requires_confirmation(self):
        intent = self.parser.parse("/memory remember preferred editor is vim")
        self.assertEqual(intent.kind, IntentKind.MEMORY_REMEMBER)
        self.assertTrue(intent.requires_confirmation)

    def test_task_list_and_show(self):
        self.assertEqual(self.parser.parse("/task list").kind, IntentKind.TASK_LIST)
        shown = self.parser.parse("/task show task-123")
        self.assertEqual(shown.kind, IntentKind.TASK_SHOW)
        self.assertEqual(shown.arguments, ("task-123",))

    def test_task_identifier_rejects_injection_characters(self):
        intent = self.parser.parse("/task show ../../etc/passwd")
        self.assertEqual(intent.kind, IntentKind.UNKNOWN)
        self.assertEqual(intent.reason, "valid_task_id_required")

    def test_plan_is_explicit_and_requires_confirmation(self):
        intent = self.parser.parse("/plan prepare a release checklist")
        self.assertEqual(intent.kind, IntentKind.PLAN_CREATE)
        self.assertEqual(intent.arguments, ("prepare a release checklist",))
        self.assertTrue(intent.requires_confirmation)

    def test_unknown_natural_language_is_not_guessed(self):
        intent = self.parser.parse("please delete every file")
        self.assertEqual(intent.kind, IntentKind.UNKNOWN)
        self.assertEqual(intent.reason, "unsupported_command")

    def test_invalid_input_is_bounded_and_fail_closed(self):
        self.assertEqual(self.parser.parse("").reason, "empty_input")
        self.assertEqual(self.parser.parse("x" * 4001).reason, "input_too_long")
        self.assertEqual(self.parser.parse("/memory search \"unterminated").reason, "invalid_quoting")
        self.assertEqual(self.parser.parse("/help\x00").reason, "control_character")

    def test_unsupported_subcommands_do_not_fall_through(self):
        self.assertEqual(self.parser.parse("/memory delete everything").kind, IntentKind.UNKNOWN)
        self.assertEqual(self.parser.parse("/task cancel task-123").kind, IntentKind.UNKNOWN)

    def test_parser_is_pure_and_returns_immutable_results(self):
        intent = self.parser.parse("/status")
        with self.assertRaises(AttributeError):
            intent.kind = IntentKind.HELP


if __name__ == "__main__":
    unittest.main()
