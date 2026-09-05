from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MessengerSetupScriptTests(unittest.TestCase):
    def test_setup_script_has_valid_bash_syntax(self) -> None:
        subprocess.run(
            ["bash", "-n", str(ROOT / "setup_messenger_bots.sh")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_setup_script_prepares_then_deploys_then_checks_status(self) -> None:
        text = (ROOT / "setup_messenger_bots.sh").read_text(encoding="utf-8")
        prepare = text.index("prepare_messenger_config.py")
        deploy = text.index("deploy_telegram_bot.sh")
        status = text.rindex("register_messenger_webhooks.py\" --status")
        self.assertLess(prepare, deploy)
        self.assertLess(deploy, status)
        self.assertIn("MAX_BOT_TOKEN", text)
        self.assertIn("MAX_WEBHOOK_URL", text)
        self.assertIn("VK_BOT_TOKEN", text)
        self.assertIn("VK_GROUP_ID", text)
        self.assertIn("VK_CALLBACK_URL", text)

    def test_setup_scopes_prepare_and_status_to_selected_platform(self) -> None:
        text = (ROOT / "setup_messenger_bots.sh").read_text(encoding="utf-8")
        self.assertIn('prepare_messenger_config.py" --env-file "$ENV_FILE" --max', text)
        self.assertIn('prepare_messenger_config.py" --env-file "$ENV_FILE" --vk', text)
        self.assertIn('register_messenger_webhooks.py" --status --max', text)
        self.assertIn('register_messenger_webhooks.py" --status --vk', text)
        self.assertIn('set_env MAX_ENABLED "1"', text)
        self.assertIn('set_env VK_ENABLED "1"', text)

    def test_env_example_does_not_enable_platforms_accidentally(self) -> None:
        text = (ROOT / ".env.telegram.example").read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_ENABLED=auto\n", text)
        self.assertIn("MAX_ENABLED=auto\n", text)
        self.assertIn("VK_ENABLED=auto\n", text)
        self.assertIn("MAX_BOT_TOKEN=\n", text)
        self.assertIn("MAX_WEBHOOK_URL=\n", text)
        self.assertIn("VK_BOT_TOKEN=\n", text)
        self.assertIn("VK_CALLBACK_URL=\n", text)


if __name__ == "__main__":
    unittest.main()
