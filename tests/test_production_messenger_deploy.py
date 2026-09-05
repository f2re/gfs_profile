from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install_telegram_bot.sh"
DEPLOY = ROOT / "deploy_telegram_bot.sh"
HELPER = ROOT / "install_messenger_runtime.sh"
ENV_EXAMPLE = ROOT / ".env.telegram.example"


class ProductionMessengerDeployTests(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        for path in (INSTALL, DEPLOY, HELPER):
            result = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=f"{path.name}: {result.stderr}")

    def test_fresh_install_uses_launcher_and_runtime_by_default(self) -> None:
        install = INSTALL.read_text(encoding="utf-8")
        env = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("MESSENGER_RUNTIME_ENABLED=1", env)
        self.assertIn("ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/messenger_launcher.py", install)
        self.assertNotIn("ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/telegram_bot.py", install)
        self.assertIn("messenger_config_check.py", install)
        self.assertIn("/ready", install)
        self.assertIn("register_messenger_webhooks.py", install)

    def test_deploy_migrates_old_unit_and_registers_only_after_ready(self) -> None:
        text = DEPLOY.read_text(encoding="utf-8")
        self.assertIn("ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/messenger_launcher.py", text)
        self.assertIn("ensure_runtime_config", text)
        self.assertIn("messenger_config_check.py", text)
        self.assertIn("register_messenger_webhooks.py", text)
        self.assertIn("/ready", text)
        main = text[text.index("main() {") :]
        self.assertLess(main.index("runtime preflight"), main.index("multi-messenger systemd unit"))
        self.assertLess(main.index("multi-messenger systemd unit"), main.index("перезапуск systemd"))
        self.assertLess(main.index('CURRENT_STAGE="readiness"'), main.index('CURRENT_STAGE="регистрация MAX/VK webhook"'))

    def test_deploy_preserves_runtime_state_and_cache(self) -> None:
        text = DEPLOY.read_text(encoding="utf-8")
        self.assertIn("--exclude '.cache_gfs/'", text)
        self.assertIn("--exclude '.env'", text)
        self.assertIn("--exclude '.install-state'", text)
        self.assertIn("MESSENGER_PREFERENCES_DB", text)
        self.assertIn("MAX_CONCURRENT_METEOGRAM", text)

    def test_runtime_health_exposes_platforms_and_shared_limits(self) -> None:
        text = (ROOT / "messenger_runtime.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/health")', text)
        self.assertIn('@app.get("/ready")', text)
        self.assertIn("RESOURCES.snapshot()", text)
        self.assertIn("configure_process_resources()", text)


if __name__ == "__main__":
    unittest.main()
