import asyncio
import unittest

from api.request_body import RequestBodyTooLarge, read_json_object


class RequestBodyTests(unittest.TestCase):
    def test_reads_json_object_across_asgi_chunks_at_exact_limit(self):
        async def run():
            events = [
                {"type": "http.request", "body": b'{"value":', "more_body": True},
                {"type": "http.request", "body": b'"ok"}', "more_body": False},
            ]
            async def receive():
                return events.pop(0)
            return await read_json_object(receive, len(b'{"value":"ok"}'))
        self.assertEqual(asyncio.run(run()), {"value": "ok"})

    def test_rejects_oversized_chunk_before_extending_buffer(self):
        async def run():
            async def receive():
                return {"type": "http.request", "body": b"x" * 5, "more_body": False}
            with self.assertRaises(RequestBodyTooLarge):
                await read_json_object(receive, 4)
        asyncio.run(run())

    def test_rejects_non_byte_body_chunks(self):
        async def run():
            async def receive():
                return {"type": "http.request", "body": "not bytes", "more_body": False}
            with self.assertRaisesRegex(ValueError, "must be bytes"):
                await read_json_object(receive, 100)
        asyncio.run(run())

    def test_rejects_non_object_json(self):
        async def run():
            async def receive():
                return {"type": "http.request", "body": b"[]", "more_body": False}
            with self.assertRaisesRegex(ValueError, "must be an object"):
                await read_json_object(receive, 10)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
