import unittest
from unittest.mock import patch

from yuki_cli import run_cli


class YukiReminderCliTests(unittest.TestCase):
    def test_list_reminders_uses_authenticated_api(self):
        output = []
        with patch("yuki_cli._reminder_request", return_value=({"reminders": []}, 0)) as request:
            code = run_cli(["reminders", "list"], output_fn=output.append)
        self.assertEqual(code, 0)
        request.assert_called_once_with("/v1/companion/reminders")
        self.assertIn("reminders", output[0])

    def test_list_can_include_inactive_reminders(self):
        with patch("yuki_cli._reminder_request", return_value=({"reminders": []}, 0)) as request:
            code = run_cli(
                ["reminders", "list", "--include-inactive"],
                output_fn=lambda _text: None,
            )
        self.assertEqual(code, 0)
        request.assert_called_once_with("/v1/companion/reminders?include_inactive=true")

    def test_add_reminder_posts_text_and_timezone_aware_timestamp(self):
        with patch("yuki_cli._reminder_request", return_value=({"reminder": {}}, 0)) as request:
            code = run_cli([
                "reminders", "add", "--text", "Review CI",
                "--due-at", "2026-11-06T09:30:00+04:00",
            ], output_fn=lambda _text: None)
        self.assertEqual(code, 0)
        request.assert_called_once_with(
            "/v1/companion/reminders", method="POST",
            payload={"text": "Review CI", "due_at": "2026-11-06T09:30:00+04:00"},
        )

    def test_cancel_rejects_malformed_id_without_api_request(self):
        with patch("yuki_cli._reminder_request") as request:
            output = []
            code = run_cli(["reminders", "cancel", "not-an-id"], output_fn=output.append)
        self.assertEqual(code, 2)
        self.assertEqual(output, ["Invalid reminder ID."])
        request.assert_not_called()

    def test_cancel_uses_owner_scoped_api_endpoint(self):
        reminder_id = "a" * 32
        with patch("yuki_cli._reminder_request", return_value=({"cancelled": True}, 0)) as request:
            code = run_cli(
                ["reminders", "cancel", reminder_id],
                output_fn=lambda _text: None,
            )
        self.assertEqual(code, 0)
        request.assert_called_once_with(
            "/v1/companion/reminders/" + reminder_id, method="DELETE",
        )

    def test_reminder_command_does_not_initialize_model_provider(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("reminder commands must not initialize a model provider")
        with patch("yuki_cli._reminder_request", return_value=({"reminders": []}, 0)):
            code = run_cli(
                ["reminders", "list"], provider_factory=forbidden,
                output_fn=lambda _text: None,
            )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
