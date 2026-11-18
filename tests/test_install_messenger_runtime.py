from __future__ import annotations

import unittest
import subprocess
import tempfile
from pathlib import Path


class InstallMessengerRuntimeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (Path(__file__).resolve().parents[1] / "install_messenger_runtime.sh").read_text(encoding="utf-8")

    def test_systemd_dropin_resets_execstart_to_launcher(self):
        self.assertIn("ExecStart=\nExecStart=$VENV_DIR/bin/python $INSTALL_DIR/messenger_launcher.py", self.text)

    def test_enable_runs_runtime_check_and_webhook_registration(self):
        self.assertIn("runtime_check.py", self.text)
        self.assertIn("messenger_readiness.py", self.text)
        self.assertIn("register_messenger_commands.py", self.text)
        self.assertIn("register_messenger_webhooks.py", self.text)
        self.assertIn('"$INSTALL_DIR/register_messenger_webhooks.py" --strict', self.text)
        self.assertIn("set_env MESSENGER_RUNTIME_ENABLED 1", self.text)

    def test_failed_registration_keeps_endpoint_alive_for_safe_retry(self):
        self.assertIn("Runtime запущен, но регистрация меню или webhook не прошла", self.text)
        self.assertIn("endpoint оставлен активным", self.text)

    def test_no_restart_cannot_register_dead_endpoint(self):
        self.assertIn("--no-restart при --enable требует --no-register", self.text)

    def test_daemon_reload_failure_stops_conditional_restart(self):
        source = self.text.rsplit("\nvalidate_install\ncase", 1)[0]
        for flags in ([], ["--no-restart"]):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                harness = base / "runtime-harness.sh"
                harness.write_text(
                    'id() { printf 0; }\n'
                    + source
                    + r'''
systemctl() {
  printf '%s\n' "$1" >> "$PWD/trace"
  [[ "$1" != daemon-reload ]]
}
# Production invokes restart_service from if/!, which disables automatic
# errexit within the function. A failed reload must be returned explicitly.
if ! restart_service; then
  exit 0
fi
exit 99
''',
                    encoding="utf-8",
                )
                result = subprocess.run(
                    ["bash", str(harness), *flags], cwd=base,
                    capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((base / "trace").read_text().splitlines(), ["daemon-reload"])


if __name__ == "__main__":
    unittest.main()
