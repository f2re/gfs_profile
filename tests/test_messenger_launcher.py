from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

import messenger_launcher


class MessengerLauncherTests(unittest.TestCase):
    def test_flag_and_port_validation(self):
        self.assertTrue(messenger_launcher.runtime_enabled("yes"))
        self.assertFalse(messenger_launcher.runtime_enabled("0"))
        self.assertEqual(messenger_launcher.runtime_port("8081"), 8081)
        with self.assertRaises(ValueError):
            messenger_launcher.runtime_port("70000")

    def test_default_path_runs_telegram(self):
        called = []
        fake = types.SimpleNamespace(main=lambda: called.append("telegram"))
        with patch.dict(sys.modules, {"telegram_bot": fake}), patch.dict(os.environ, {"MESSENGER_RUNTIME_ENABLED": "0"}):
            messenger_launcher.main()
        self.assertEqual(called, ["telegram"])

    def test_enabled_path_runs_single_worker_uvicorn(self):
        calls = []
        fake_uvicorn = types.SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs)))
        env = {
            "MESSENGER_RUNTIME_ENABLED": "1",
            "MESSENGER_RUNTIME_HOST": "127.0.0.1",
            "MESSENGER_RUNTIME_PORT": "8089",
        }
        with patch.dict(sys.modules, {"uvicorn": fake_uvicorn}), patch.dict(os.environ, env, clear=False):
            messenger_launcher.main()
        self.assertEqual(calls[0][0], ("messenger_runtime:app",))
        self.assertEqual(calls[0][1]["workers"], 1)
        self.assertEqual(calls[0][1]["port"], 8089)


if __name__ == "__main__":
    unittest.main()
