from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import requests

import messenger_readiness as readiness


def health(*, enabled=("telegram", "max")):
    return {
        "status": "ok", "runtime": "multi-messenger",
        "platform_status": {
            name: {"requested": name in enabled, "ready": name in enabled,
                   "state": "ready" if name in enabled else "off", "reason": ""}
            for name in ("telegram", "max", "vk")
        },
    }


class MessengerReadinessTests(unittest.TestCase):
    def test_all_requested_platforms_ready_and_disabled_ignored(self):
        for enabled in (("telegram",), ("max",), ("vk",), ("telegram", "max", "vk"), ()):
            with self.subTest(enabled=enabled):
                readiness.validate_readiness(health(enabled=enabled))

    def test_http_200_degraded_runtime_is_not_successful_deployment(self):
        payload = health()
        payload["status"] = "degraded"
        payload["platform_status"]["telegram"].update(ready=False, state="degraded", reason="polling failed")
        response = Mock(status_code=200)
        response.json.return_value = payload
        with self.assertRaisesRegex(readiness.ReadinessError, "TELEGRAM не готова"):
            readiness.check_runtime_ready("http://127.0.0.1:8081/ready", getter=Mock(return_value=response))

    def test_requested_failure_is_rejected_even_if_top_level_status_is_ok(self):
        payload = health()
        payload["platform_status"]["max"].update(ready=False, state="degraded")
        with self.assertRaises(readiness.ReadinessError):
            readiness.validate_readiness(payload)

    def test_wrong_service_partial_schema_and_starting_are_rejected(self):
        for payload in ({}, {"status": "ok"}, {"runtime": "multi-messenger", "status": "starting"},
                        {**health(), "runtime": "other"},
                        {**health(), "platform_status": {"telegram": {"requested": True, "ready": True}}},
                        {**health(), "status": "degraded"}):
            with self.subTest(payload=payload):
                with self.assertRaises(readiness.ReadinessError):
                    readiness.validate_readiness(payload)

    def test_successful_http_probe_obeys_timeout(self):
        response = Mock(status_code=200)
        response.json.return_value = health()
        getter = Mock(return_value=response)
        readiness.check_runtime_ready("http://127.0.0.1:8081/ready", timeout=1.5, getter=getter)
        getter.assert_called_once_with("http://127.0.0.1:8081/ready", timeout=1.5)

    def test_non_200_and_invalid_json_are_rejected(self):
        response = Mock(status_code=503)
        with self.assertRaisesRegex(readiness.ReadinessError, "503"):
            readiness.check_runtime_ready("http://localhost/ready", getter=Mock(return_value=response))
        response.status_code = 200
        response.json.side_effect = ValueError("invalid")
        with self.assertRaisesRegex(readiness.ReadinessError, "JSON"):
            readiness.check_runtime_ready("http://localhost/ready", getter=Mock(return_value=response))

    def test_cli_failure_does_not_print_tokens_from_runtime_reasons(self):
        payload = health()
        payload["platform_status"]["telegram"].update(
            ready=False, state="degraded", reason="https://api.telegram.org/botsecret/getUpdates",
        )
        response = Mock(status_code=200)
        response.json.return_value = payload
        output = io.StringIO()
        with patch("sys.argv", ["messenger_readiness.py", "http://localhost/ready"]), \
             patch.object(readiness.requests, "get", return_value=response), redirect_stdout(output):
            result = readiness.main()
        self.assertEqual(result, 1)
        self.assertNotIn("secret", output.getvalue())
        self.assertIn("TELEGRAM", output.getvalue())

    def test_network_failure_does_not_expose_url(self):
        getter = Mock(side_effect=requests.ConnectionError("http://user:secret@localhost/ready"))
        with self.assertRaises(readiness.ReadinessError) as error:
            readiness.check_runtime_ready("http://localhost/ready", getter=getter)
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
