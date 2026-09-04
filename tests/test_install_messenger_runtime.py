from __future__ import annotations

import unittest
from pathlib import Path


class InstallMessengerRuntimeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (Path(__file__).resolve().parents[1] / "install_messenger_runtime.sh").read_text(encoding="utf-8")

    def test_systemd_dropin_resets_execstart_to_launcher(self):
        self.assertIn("ExecStart=\nExecStart=$VENV_DIR/bin/python $INSTALL_DIR/messenger_launcher.py", self.text)

    def test_enable_runs_runtime_check_and_webhook_registration(self):
        self.assertIn("runtime_check.py", self.text)
        self.assertIn("register_messenger_webhooks.py", self.text)
        self.assertIn("set_env MESSENGER_RUNTIME_ENABLED 1", self.text)

    def test_failed_registration_keeps_endpoint_alive_for_safe_retry(self):
        self.assertIn("Runtime запущен, но регистрация webhook не прошла", self.text)
        self.assertIn("endpoint оставлен активным", self.text)

    def test_no_restart_cannot_register_dead_endpoint(self):
        self.assertIn("--no-restart при --enable требует --no-register", self.text)


if __name__ == "__main__":
    unittest.main()
