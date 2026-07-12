from __future__ import annotations

import re
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

    def test_deploy_lock_is_not_a_predictable_tmp_file(self) -> None:
        text = Path("deploy_telegram_bot.sh").read_text(encoding="utf-8")
        self.assertNotIn('DEPLOY_LOCK="/tmp/${SERVICE_NAME}.deploy.lock"', text)
        self.assertIn("resolve_deploy_lock", text)
        self.assertIn("acquire_deploy_lock", text)
        self.assertIn("rev-parse --git-dir", text)
        self.assertIn("DEPLOY_LOCK_PATH", text)
        self.assertIn("/run/lock/", text)
        self.assertIn("Старый lock в /tmp больше не используется", text)

    def test_optional_stages_do_not_return_previous_failure_status(self) -> None:
        text = Path("deploy_telegram_bot.sh").read_text(encoding="utf-8")
        # `condition || return` returns status 1 and, under `set -e`, silently
        # terminates main. Every optional stage must return zero explicitly.
        self.assertIsNone(re.search(r"\|\|\s*return(?:\s*;|\s*\n)", text))
        self.assertIn('if [[ "$INSTALL_SYSTEM_PACKAGES" -ne 1 ]]; then\n    return 0', text)
        self.assertIn('if [[ "$SKIP_PIP" -eq 1 ]]; then', text)
        self.assertIn('if [[ "$SKIP_TESTS" -eq 1 ]]; then', text)
        self.assertIn('if [[ "$SKIP_COMMANDS" -eq 1 ]]; then', text)

    def test_deploy_has_visible_stages_sync_verification_and_restart_check(self) -> None:
        text = Path("deploy_telegram_bot.sh").read_text(encoding="utf-8")
        self.assertIn("Этап: копирование кода", text)
        self.assertIn("verify_sync", text)
        self.assertIn("--checksum --dry-run --itemize-changes", text)
        self.assertIn('systemctl restart "${SERVICE_NAME}.service"', text)
        self.assertIn("PID сервиса не изменился", text)
        self.assertIn("Deploy завершён", text)

    def test_deploy_requires_root_instead_of_printing_env_permission_errors(self) -> None:
        text = Path("deploy_telegram_bot.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$(id -u)" -ne 0 ]]', text)
        self.assertIn("Deploy изменяет /opt и systemd", text)
        self.assertNotIn('SUDO="sudo"', text)

    def test_env_example_uses_dadata_primary_with_explicit_fallback(self) -> None:
        text = Path(".env.telegram.example").read_text(encoding="utf-8")
        self.assertIn("GEOCODER_PROVIDERS=dadata,local,nominatim", text)
        self.assertIn("DADATA_API_KEY=", text)
        self.assertIn("NOMINATIM_URL=", text)


if __name__ == "__main__":
    unittest.main()
