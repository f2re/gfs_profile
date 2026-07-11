from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class DadataInstallDeployTests(unittest.TestCase):
    def test_shell_scripts_have_valid_syntax(self) -> None:
        for script in ("install_telegram_bot.sh", "deploy_telegram_bot.sh"):
            result = subprocess.run(["bash", "-n", script], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=f"{script}: {result.stderr}")

    def test_install_requests_and_validates_dadata_key(self) -> None:
        text = Path("install_telegram_bot.sh").read_text(encoding="utf-8")
        self.assertIn("DADATA_API_KEY", text)
        self.assertIn("Secret Key не нужен", text)
        self.assertIn("geocoder_preflight.py", text)
        self.assertIn("GEOCODER_PROVIDERS", text)

    def test_deploy_migrates_old_env_before_restart(self) -> None:
        text = Path("deploy_telegram_bot.sh").read_text(encoding="utf-8")
        configure_at = text.index("ensure_geocoder_config")
        preflight_at = text.index('"$INSTALL_DIR/geocoder_preflight.py"')
        restart_at = text.index("restart_service")
        self.assertLess(configure_at, preflight_at)
        self.assertLess(preflight_at, restart_at)
        self.assertIn("DADATA_API_KEY не задан", text)

    def test_env_example_uses_dadata_primary_with_explicit_fallback(self) -> None:
        text = Path(".env.telegram.example").read_text(encoding="utf-8")
        self.assertIn("GEOCODER_PROVIDERS=dadata,local,nominatim", text)
        self.assertIn("DADATA_API_KEY=", text)
        self.assertIn("NOMINATIM_URL=", text)


if __name__ == "__main__":
    unittest.main()
