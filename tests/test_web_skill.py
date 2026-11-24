import unittest

from skills.web_skill import WebSkill, WebSkillError


def _record(address):
    return (None, None, None, None, (address, 443))


class _Response:
    status = 200
    def getheader(self, name, default=""):
        return "text/plain; charset=utf-8"
    def read(self, size):
        return b"hello from trusted host"


class _Connection:
    def __init__(self, host, address, **kwargs):
        self.host, self.address = host, address
        self.request_args = None
        self.closed = False
    def request(self, method, target, headers):
        self.request_args = (method, target, headers)
    def getresponse(self):
        return _Response()
    def close(self):
        self.closed = True


class WebSkillTests(unittest.TestCase):
    def test_connects_to_the_exact_public_ip_that_was_validated(self):
        connections = []
        def factory(host, address, **kwargs):
            item = _Connection(host, address, **kwargs)
            connections.append(item)
            return item
        skill = WebSkill(
            allowed_hosts={"docs.example.com"},
            resolver=lambda *args, **kwargs: [_record("8.8.8.8")],
            connection_factory=factory,
        )
        result = skill.execute("https://docs.example.com/guide?q=1")
        self.assertEqual(result["text"], "hello from trusted host")
        self.assertEqual(connections[0].address, "8.8.8.8")
        self.assertEqual(connections[0].request_args[1], "/guide?q=1")
        self.assertTrue(connections[0].closed)

    def test_rejects_mixed_public_and_private_dns_answers(self):
        connections = []
        skill = WebSkill(
            allowed_hosts={"docs.example.com"},
            resolver=lambda *args, **kwargs: [_record("8.8.8.8"), _record("127.0.0.1")],
            connection_factory=lambda *args, **kwargs: connections.append(args),
        )
        with self.assertRaisesRegex(WebSkillError, "non-public"):
            skill.execute("https://docs.example.com/")
        self.assertEqual(connections, [])

    def test_rejects_redirect_responses(self):
        class Redirect(_Response):
            status = 302
        connection = _Connection("docs.example.com", "8.8.8.8")
        connection.getresponse = lambda: Redirect()
        skill = WebSkill(
            allowed_hosts={"docs.example.com"},
            resolver=lambda *args, **kwargs: [_record("8.8.8.8")],
            connection_factory=lambda *args, **kwargs: connection,
        )
        with self.assertRaisesRegex(WebSkillError, "Redirects"):
            skill.execute("https://docs.example.com/")
        self.assertTrue(connection.closed)

    def test_rejects_malformed_port_without_leaking_parser_exception(self):
        skill = WebSkill(allowed_hosts={"docs.example.com"})
        with self.assertRaises(WebSkillError):
            skill.execute("https://docs.example.com:invalid/")
