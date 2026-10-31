import tempfile
import unittest
from pathlib import Path

from automation.conversation_memory import ConversationMemoryStore


class ConversationMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        grants = {"memory:import", "memory:read", "memory:delete", "memory:delete_all"}
        self.store = ConversationMemoryStore(
            str(Path(self.tmp.name) / "memory.sqlite3"),
            authorize=lambda actor, action, owner: actor == owner and action in grants,
        )

    def test_import_requires_consent_and_is_idempotent(self):
        records = [{"id": "msg-1", "role": "user", "content": "Build Yuki safely."}]
        with self.assertRaises(PermissionError):
            self.store.import_records(
                actor_id="alice", owner_id="alice", source="chatgpt",
                records=records, consent=False,
            )
        self.assertEqual(self.store.import_records(
            actor_id="alice", owner_id="alice", source="chatgpt",
            records=records, consent=True,
        ), 1)
        self.assertEqual(self.store.import_records(
            actor_id="alice", owner_id="alice", source="chatgpt",
            records=records, consent=True,
        ), 0)

    def test_owner_isolation_and_source_attribution(self):
        self.store.import_records(
            actor_id="alice", owner_id="alice", source="claude",
            records=[{"id": "a1", "role": "assistant", "content": "Architecture notes"}],
            consent=True,
        )
        found = self.store.search(actor_id="alice", owner_id="alice", query="Architecture")
        self.assertEqual(found[0].source, "claude")
        self.assertEqual(len(found[0].content_sha256), 64)
        with self.assertRaises(PermissionError):
            self.store.search(actor_id="mallory", owner_id="alice", query="Architecture")
        self.assertEqual(self.store.count(actor_id="alice", owner_id="alice"), 1)

    def test_semantic_search_is_owner_scoped_and_handles_paraphrases(self):
        vectors = {"vehicle": [1.0, 0.0], "I drive a car every day": [1.0, 0.0], "banana bread recipe": [0.0, 1.0]}
        semantic_store = ConversationMemoryStore(
            str(Path(self.tmp.name) / "semantic.sqlite3"),
            authorize=lambda actor, action, owner: actor == owner and action in {"memory:import", "memory:read"},
            embedding_fn=lambda text: vectors.get(text, [0.0, 1.0]),
        )
        semantic_store.import_records(actor_id="alice", owner_id="alice", source="chatgpt", records=[
            {"id": "car", "role": "user", "content": "I drive a car every day"},
            {"id": "food", "role": "user", "content": "banana bread recipe"},
        ], consent=True)
        semantic_store.import_records(actor_id="bob", owner_id="bob", source="chatgpt", records=[
            {"id": "secret", "role": "user", "content": "I drive a car every day"},
        ], consent=True)
        found = semantic_store.semantic_search(actor_id="alice", owner_id="alice", query="vehicle")
        self.assertEqual(found[0].source_record_id, "car")
        self.assertEqual(len(found), 1)
        with self.assertRaises(PermissionError):
            semantic_store.semantic_search(actor_id="mallory", owner_id="alice", query="vehicle")

    def test_semantic_search_falls_back_when_embedder_fails(self):
        semantic_store = ConversationMemoryStore(
            str(Path(self.tmp.name) / "fallback.sqlite3"),
            authorize=lambda actor, action, owner: actor == owner and action in {"memory:read", "memory:import"},
            embedding_fn=lambda _: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        semantic_store.import_records(
            actor_id="alice", owner_id="alice", source="local",
            records=[{"id": "offline-note", "role": "user", "content": "Yuki memory works offline"}],
            consent=True,
        )
        found = semantic_store.semantic_search(actor_id="alice", owner_id="alice", query="Yuki")
        self.assertEqual([item.source_record_id for item in found], ["offline-note"])

    def test_semantic_search_reuses_cached_owner_embeddings_and_ranks_query(self):
        vectors = {"car": [1.0, 0.0], "food": [0.0, 1.0], "vehicle": [1.0, 0.0]}
        calls = []
        def embed(text):
            calls.append(text)
            return vectors[text]
        semantic_store = ConversationMemoryStore(
            str(Path(self.tmp.name) / "cached.sqlite3"),
            authorize=lambda actor, action, owner: actor == owner and action in {"memory:import", "memory:read"},
            embedding_fn=embed,
        )
        semantic_store.import_records(actor_id="alice", owner_id="alice", source="chatgpt", records=[
            {"id": "car", "role": "user", "content": "car"},
            {"id": "food", "role": "user", "content": "food"},
        ], consent=True)
        first = semantic_store.semantic_search(actor_id="alice", owner_id="alice", query="vehicle")
        second = semantic_store.semantic_search(actor_id="alice", owner_id="alice", query="vehicle")
        self.assertEqual(first[0].source_record_id, "car")
        self.assertEqual(second[0].source_record_id, "car")
        self.assertEqual(calls.count("car"), 1)
        self.assertEqual(calls.count("food"), 1)

    def test_embeddings_are_cached_across_searches_and_store_reopen(self):
        calls = []
        def embed(text):
            calls.append(text)
            return [1.0, 0.0] if text in {"vehicle", "car memory"} else [0.0, 1.0]
        database = str(Path(self.tmp.name) / "persistent.sqlite3")
        store = ConversationMemoryStore(
            database, authorize=lambda actor, action, owner: actor == owner and action in {"memory:import", "memory:read"},
            embedding_fn=embed, embedding_model_id="embed-v1",
        )
        store.import_records(actor_id="alice", owner_id="alice", source="chatgpt",
            records=[{"id": "car", "role": "user", "content": "car memory"}], consent=True)
        self.assertEqual(store.semantic_search(actor_id="alice", owner_id="alice", query="vehicle")[0].source_record_id, "car")
        self.assertEqual(calls.count("car memory"), 1)
        reopened = ConversationMemoryStore(
            database, authorize=lambda actor, action, owner: actor == owner and action in {"memory:import", "memory:read"},
            embedding_fn=embed, embedding_model_id="embed-v1",
        )
        reopened.semantic_search(actor_id="alice", owner_id="alice", query="vehicle")
        self.assertEqual(calls.count("car memory"), 1)

    def test_embedding_model_id_versions_cached_vectors(self):
        calls = []
        def embed(text):
            calls.append(text)
            return [1.0, 0.0]
        database = str(Path(self.tmp.name) / "versions.sqlite3")
        auth = lambda actor, action, owner: actor == owner and action in {"memory:import", "memory:read"}
        store = ConversationMemoryStore(database, authorize=auth, embedding_fn=embed, embedding_model_id="v1")
        store.import_records(actor_id="alice", owner_id="alice", source="chatgpt",
            records=[{"id": "one", "role": "user", "content": "remember this"}], consent=True)
        store.semantic_search(actor_id="alice", owner_id="alice", query="query")
        store_v2 = ConversationMemoryStore(database, authorize=auth, embedding_fn=embed, embedding_model_id="v2")
        store_v2.semantic_search(actor_id="alice", owner_id="alice", query="query")
        self.assertEqual(calls.count("remember this"), 2)

    def test_import_rejects_unhashable_role_without_type_error(self):
        with self.assertRaises(ValueError):
            self.store.import_records(actor_id="alice", owner_id="alice", source="chatgpt",
                records=[{"id": "bad-role", "role": ["user"], "content": "hello"}], consent=True)

    def test_delete_and_clear_are_owner_scoped(self):
        self.store.import_records(
            actor_id="alice", owner_id="alice", source="deepseek",
            records=[{"id": "a1", "role": "user", "content": "One"},
                     {"id": "a2", "role": "assistant", "content": "Two"}],
            consent=True,
        )
        found = self.store.search(actor_id="alice", owner_id="alice", query="One")
        self.assertTrue(self.store.delete(actor_id="alice", owner_id="alice", memory_id=found[0].memory_id))
        self.assertEqual(self.store.clear_owner(actor_id="alice", owner_id="alice"), 1)
        self.assertEqual(self.store.count(actor_id="alice", owner_id="alice"), 0)

    def test_invalid_or_oversized_records_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.import_records(
                actor_id="alice", owner_id="alice", source="chatgpt",
                records=[{"id": "bad", "role": "user", "content": "x" * 12001}],
                consent=True,
            )
        with self.assertRaises(ValueError):
            self.store.import_records(
                actor_id="alice", owner_id="alice", source="bad source",
                records=[], consent=True,
            )

    def test_search_treats_like_wildcards_as_literals(self):
        self.store.import_records(
            actor_id="alice", owner_id="alice", source="kimi",
            records=[{"id": "a1", "role": "user", "content": "100% ready"}],
            consent=True,
        )
        self.assertEqual(len(self.store.search(
            actor_id="alice", owner_id="alice", query="100%",
        )), 1)
