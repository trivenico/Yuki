import tempfile
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_memory_store import ConversationMemoryStore
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class DenyAll:
    def authorize_tool(self, name):
        return False


class ConversationMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "memory.sqlite3"
        self.memory = ConversationMemoryStore(path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_memory_isolated_by_owner_for_search_list_delete_and_clear(self):
        alice = self.memory.remember(
            owner_id="alice", content="Prefers concise engineering updates", category="preference",
        )
        bob = self.memory.remember(
            owner_id="bob", content="Prefers detailed gardening advice", category="preference",
        )
        self.assertEqual(
            [item["memory_id"] for item in self.memory.search(
                owner_id="alice", query="engineering updates",
            )],
            [alice["memory_id"]],
        )
        self.assertEqual(self.memory.list(owner_id="bob")[0]["memory_id"], bob["memory_id"])
        self.assertFalse(self.memory.delete(owner_id="bob", memory_id=alice["memory_id"]))
        self.assertEqual(self.memory.clear(owner_id="alice"), 1)
        self.assertEqual(self.memory.list(owner_id="alice"), [])
        self.assertEqual(self.memory.list(owner_id="bob")[0]["memory_id"], bob["memory_id"])

    def test_search_ranks_direct_phrase_above_noisier_overlap(self):
        broad = self.memory.remember(
            owner_id="alice",
            content="Engineering notes mention concise updates, concise writing, and concise summaries",
        )
        exact = self.memory.remember(
            owner_id="alice",
            content="Concise engineering updates",
            category="preference",
        )
        matches = self.memory.search(
            owner_id="alice", query="concise engineering updates", limit=2,
        )
        self.assertEqual(matches[0]["memory_id"], exact["memory_id"])
        self.assertEqual({item["memory_id"] for item in matches}, {broad["memory_id"], exact["memory_id"]})
        self.assertGreater(matches[0]["score"], matches[1]["score"])

    def test_search_checks_older_memories_beyond_recent_200(self):
        oldest = self.memory.remember(
            owner_id="alice", content="Unique archival detail: violet telescope",
        )
        for index in range(205):
            self.memory.remember(
                owner_id="alice", content=f"Unrelated recent memory number {index}",
            )
        matches = self.memory.search(
            owner_id="alice", query="violet telescope", limit=5,
        )
        self.assertEqual([item["memory_id"] for item in matches], [oldest["memory_id"]])

    def test_search_ranking_is_deterministic_for_repeated_queries(self):
        self.memory.remember(owner_id="alice", content="Project uses Python and SQLite")
        self.memory.remember(owner_id="alice", content="Project uses Python and PostgreSQL")
        first = self.memory.search(owner_id="alice", query="project database Python")
        second = self.memory.search(owner_id="alice", query="project database Python")
        self.assertEqual(
            [(item["memory_id"], item["score"]) for item in first],
            [(item["memory_id"], item["score"]) for item in second],
        )

    def test_duplicate_inspection_finds_normalized_exact_match_without_mutating(self):
        saved = self.memory.remember(
            owner_id="alice", content="Prefer concise engineering updates",
            category="preference",
        )
        before = self.memory.list(owner_id="alice")
        matches = self.memory.find_duplicates(
            owner_id="alice", content="  PREFER   concise engineering updates!  ",
        )
        self.assertEqual([item["memory_id"] for item in matches], [saved["memory_id"]])
        self.assertTrue(matches[0]["exact"])
        self.assertEqual(matches[0]["similarity"], 1.0)
        self.assertEqual(self.memory.list(owner_id="alice"), before)

    def test_duplicate_inspection_finds_near_match_but_not_low_overlap(self):
        near = self.memory.remember(
            owner_id="alice", content="Prefer concise engineering updates",
        )
        self.memory.remember(
            owner_id="alice", content="Recipes for slow roasted vegetables",
        )
        matches = self.memory.find_duplicates(
            owner_id="alice", content="Prefer concise engineering updates daily",
            threshold=0.8,
        )
        self.assertEqual([item["memory_id"] for item in matches], [near["memory_id"]])
        self.assertFalse(matches[0]["exact"])
        self.assertGreaterEqual(matches[0]["similarity"], 0.8)

    def test_duplicate_inspection_is_owner_scoped_and_ignores_expired_entries(self):
        alice = self.memory.remember(owner_id="alice", content="Private project deployment preference")
        self.memory.remember(owner_id="bob", content="Private project deployment preference")
        expired = self.memory.remember(
            owner_id="alice", content="Old private project deployment preference",
            expires_in_seconds=60,
        )
        with self.memory._connection() as db:
            db.execute(
                "UPDATE conversation_memories SET expires_at = ? WHERE memory_id = ?",
                ("2000-01-01T00:00:00+00:00", expired["memory_id"]),
            )
        matches = self.memory.find_duplicates(
            owner_id="alice", content="Private project deployment preference",
        )
        self.assertEqual([item["memory_id"] for item in matches], [alice["memory_id"]])

    def test_list_can_filter_by_category_without_cross_category_leakage(self):
        preference = self.memory.remember(
            owner_id="alice", content="Concise responses", category="preference",
        )
        note = self.memory.remember(owner_id="alice", content="Project launch date", category="note")
        self.memory.remember(owner_id="bob", content="Concise responses", category="preference")
        self.assertEqual(
            [item["memory_id"] for item in self.memory.list(owner_id="alice", category="preference")],
            [preference["memory_id"]],
        )
        self.assertEqual(
            [item["memory_id"] for item in self.memory.list(owner_id="alice", category="note")],
            [note["memory_id"]],
        )
        with self.assertRaises(ValueError):
            self.memory.list(owner_id="alice", category="system")

    def test_memory_inputs_are_bounded_and_categories_are_allowlisted(self):
        for kwargs in (
            {"owner_id": "", "content": "text"},
            {"owner_id": "owner", "content": " "},
            {"owner_id": "owner", "content": "x" * 2001},
            {"owner_id": "owner", "content": "text", "category": "secret"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.memory.remember(**kwargs)
        with self.assertRaises(ValueError):
            self.memory.search(owner_id="owner", query="valid", limit=True)


class ConversationMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.store = ConversationStore(root / "chat.sqlite3")
        self.memory = ConversationMemoryStore(root / "memory.sqlite3")
        self.observed = []

        def model(*, messages, tools, tool_choice):
            self.observed.append(messages)
            return {"text": "answer", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, DenyAll())),
        )
        self.service = ConversationService(self.store, loop, memory_store=self.memory)
        self.conversation = self.service.create(owner_id="alice")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_explicitly_saved_owner_memory_is_retrieved_as_untrusted_context(self):
        saved = self.service.remember(
            owner_id="alice", content="The project uses Python and SQLite",
            category="note",
        )
        result = self.service.reply(
            owner_id="alice", conversation_id=self.conversation["conversation_id"],
            user_text="What database does the project use?", request_id="memory-1",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        system_prompt = self.observed[0][0]["content"]
        self.assertIn("Python and SQLite", system_prompt)
        self.assertIn("untrusted user memory", system_prompt)
        self.assertEqual(self.service.memories(owner_id="alice")[0]["memory_id"], saved["memory_id"])

    def test_other_owner_memory_is_never_added_to_prompt(self):
        self.memory.remember(owner_id="bob", content="Bob uses PostgreSQL for project database")
        self.memory.remember(owner_id="alice", content="Alice prefers SQLite database")
        result = self.service.reply(
            owner_id="alice", conversation_id=self.conversation["conversation_id"],
            user_text="Which database?", request_id="memory-isolation", expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        system_prompt = self.observed[0][0]["content"]
        self.assertIn("Alice prefers SQLite", system_prompt)
        self.assertNotIn("Bob uses PostgreSQL", system_prompt)


    def test_service_forwards_explicit_memory_expiration(self):
        saved = self.service.remember(
            owner_id="alice", content="Short-lived context",
            expires_in_seconds=3600,
        )
        self.assertIsNotNone(saved["expires_at"])
        self.assertEqual(
            self.memory.list(owner_id="alice")[0]["memory_id"],
            saved["memory_id"],
        )

    def test_memory_is_disabled_unless_store_is_explicitly_injected(self):
        plain = ConversationService(self.store, self.service.agent_loop)
        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            plain.remember(owner_id="alice", content="No implicit memory")
        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            plain.memories(owner_id="alice")


    def test_service_can_clear_only_authenticated_owners_memories(self):
        alice = self.service.remember(owner_id="alice", content="Alice saved context")
        self.memory.remember(owner_id="bob", content="Bob saved context")
        self.assertEqual(self.service.clear_memories(owner_id="alice"), 1)
        self.assertEqual(self.service.memories(owner_id="alice"), [])
        self.assertEqual(self.service.memories(owner_id="bob")[0]["content"], "Bob saved context")
        self.assertFalse(self.service.forget_memory(owner_id="bob", memory_id=alice["memory_id"]))

    def test_unicode_memory_terms_are_searchable(self):
        saved = self.memory.remember(owner_id="alice", content="東京のプロジェクト設定")
        matches = self.memory.search(owner_id="alice", query="東京 プロジェクト")
        self.assertEqual([item["memory_id"] for item in matches], [saved["memory_id"]])

    def test_cjk_search_matches_terms_inside_unspaced_japanese_chinese_and_korean(self):
        saved = self.memory.remember(
            owner_id="alice", content="人工知能の研究を東京で進める 서울 프로젝트"
        )
        matches = self.memory.search(owner_id="alice", query="人工知能 東京 서울")
        self.assertEqual([item["memory_id"] for item in matches], [saved["memory_id"]])


    def test_memory_retrieval_failure_does_not_break_conversation(self):
        self.memory.search = lambda **kwargs: (_ for _ in ()).throw(OSError("memory unavailable"))
        result = self.service.reply(
            owner_id="alice", conversation_id=self.conversation["conversation_id"],
            user_text="Continue without saved context", request_id="memory-fallback",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("untrusted user memory", self.observed[0][0]["content"])


class ConversationMemoryRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "memory.sqlite3"
        self.memory = ConversationMemoryStore(self.path)

    def test_expiring_memory_is_hidden_from_list_and_search_then_pruned(self):
        expiring = self.memory.remember(
            owner_id="alice", content="Temporary project context",
            expires_in_seconds=60,
        )
        permanent = self.memory.remember(
            owner_id="alice", content="Permanent project preference",
        )
        with self.memory._connection() as db:
            db.execute(
                "UPDATE conversation_memories SET expires_at = ? WHERE memory_id = ?",
                ("2000-01-01T00:00:00+00:00", expiring["memory_id"]),
            )
        self.assertEqual(
            [item["memory_id"] for item in self.memory.list(owner_id="alice")],
            [permanent["memory_id"]],
        )
        self.assertEqual(self.memory.search(owner_id="alice", query="Temporary context"), [])
        self.assertEqual(self.memory.prune_expired(owner_id="alice"), 1)
        self.assertEqual(self.memory.prune_expired(owner_id="alice"), 0)

    def test_expiry_pruning_can_be_scoped_to_owner(self):
        alice = self.memory.remember(
            owner_id="alice", content="Alice temporary", expires_in_seconds=60,
        )
        bob = self.memory.remember(
            owner_id="bob", content="Bob temporary", expires_in_seconds=60,
        )
        with self.memory._connection() as db:
            db.execute(
                "UPDATE conversation_memories SET expires_at = ?",
                ("2000-01-01T00:00:00+00:00",),
            )
        self.assertEqual(self.memory.prune_expired(owner_id="alice"), 1)
        self.assertTrue(self.memory.delete(owner_id="bob", memory_id=bob["memory_id"]))
        self.assertFalse(self.memory.delete(owner_id="alice", memory_id=alice["memory_id"]))

    def test_expiration_bounds_reject_bool_and_unreasonable_values(self):
        for value in (True, False, 0, 59, 315_360_001, "3600"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.memory.remember(
                    owner_id="alice", content="bounded", expires_in_seconds=value,
                )

    def test_existing_memory_database_is_migrated_without_losing_rows(self):
        import sqlite3
        legacy = Path(self.tempdir.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.execute("""
            CREATE TABLE conversation_memories (
                memory_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)
        db.execute(
            "INSERT INTO conversation_memories(memory_id, owner_id, category, content) "
            "VALUES ('legacy-id', 'alice', 'note', 'preserve me')"
        )
        db.commit()
        db.close()
        migrated = ConversationMemoryStore(legacy)
        item = migrated.list(owner_id="alice")[0]
        self.assertEqual(item["memory_id"], "legacy-id")
        self.assertIsNone(item["expires_at"])
