import tempfile
import unittest
from pathlib import Path
from core.conversation_store import ConversationConflict, ConversationStore

class LeaseTests(unittest.TestCase):
    def test_second_store_cannot_claim_active_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'db.sqlite3'
            store_a = ConversationStore(path)
            store_b = ConversationStore(path)
            conversation = store_a.create_conversation(owner_id='alice')
            cid = conversation['conversation_id']
            store_a.acquire_generation_lease(owner_id='alice', conversation_id=cid, lease_token='a')
            with self.assertRaises(ConversationConflict):
                store_b.acquire_generation_lease(owner_id='alice', conversation_id=cid, lease_token='b')

if __name__ == '__main__':
    unittest.main()
