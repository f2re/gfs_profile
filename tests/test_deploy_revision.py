from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("deploy_telegram_bot.sh", "install_telegram_bot.sh")


class DeployRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "user-owned-checkout"
        self.install = self.base / "install"
        self.install.mkdir()
        (self.install / ".env").write_text("MESSENGER_RUNTIME_ENABLED=1\n")
        self.state = self.install / ".install-state"
        self.previous_state = "source_rev=" + "a" * 40 + "\ndeploy_status=complete\n"
        self.global_config = self.base / "empty-gitconfig"
        self.global_config.write_text("")
        self.env = os.environ.copy()
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.global_config),
            GIT_CONFIG_COUNT="0", INSTALL_DIR=str(self.install),
        )
        self.env.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
        self.env.pop("SUDO_UID", None)
        self.git("init", "-q", str(self.repo))
        (self.repo / "app.txt").write_text("fixture\n")
        self.git("-C", str(self.repo), "add", "app.txt")
        self.git("-C", str(self.repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.sha = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout.strip()

    def git(self, *args: str, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], env=self.env, text=True, capture_output=True, timeout=10,
            check=kwargs.pop("check", True), **kwargs,
        )

    def execute(self, name: str, body: str, *, foreign_owner: bool = False):
        source = (ROOT / name).read_text(encoding="utf-8").rsplit('\nmain "$@"', 1)[0]
        harness = self.repo / "revision-harness.sh"
        harness.write_text(
            'id() { if [[ "${1:-}" == -u ]]; then printf 0; else command id "$@"; fi; }\n'
            + source
            + '\nchown() { :; }\n'
            + 'run_root() { "$@"; }\n'
            + body + "\n",
            encoding="utf-8",
        )
        self.state.write_text(self.previous_state)
        env = self.env.copy()
        if foreign_owner:
            # Git's own regression switch exercises the ownership check without
            # root/chown. No real system user or global trust setting is changed.
            env["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        return subprocess.run(
            ["bash", str(harness)], cwd=self.repo, env=env,
            text=True, capture_output=True, timeout=10, check=False,
        )

    def test_foreign_owned_checkout_yields_exact_sha_without_global_trust(self) -> None:
        foreign_env = {**self.env, "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
        unsafe = subprocess.run(
            ["git", "-c", "safe.directory=", "-C", str(self.repo), "rev-parse", "HEAD"],
            env=foreign_env, text=True, capture_output=True, check=False,
        )
        if unsafe.returncode == 0:
            self.skipTest("Installed Git has no simulated foreign ownership guard")
        self.assertIn("dubious ownership", unsafe.stderr)
        for name in SCRIPTS:
            with self.subTest(script=name):
                result = self.execute(name, "write_state", foreign_owner=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split("=", 1) for line in self.state.read_text().splitlines())
                self.assertEqual(values["source_rev"], self.sha)
                self.assertEqual(values["deploy_status"], "complete")
                self.assertEqual(values["pending_source_rev"], "")
                self.assertEqual(self.global_config.read_text(), "")
                still_unsafe = subprocess.run(
                    ["git", "-c", "safe.directory=", "-C", str(self.repo), "rev-parse", "HEAD"],
                    env=foreign_env, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(still_unsafe.returncode, 0)

    def test_missing_commit_cannot_overwrite_last_confirmed_state(self) -> None:
        self.git("-C", str(self.repo), "update-ref", "-d", "HEAD")
        for name in SCRIPTS:
            with self.subTest(script=name):
                result = self.execute(name, "write_state")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_text(), self.previous_state)
                self.assertEqual(list(self.install.glob(".install-state.*")), [])

    def test_non_sha_git_output_is_rejected(self) -> None:
        for name in SCRIPTS:
            with self.subTest(script=name):
                result = self.execute(name, 'git() { printf unknown; }\nwrite_state')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_text(), self.previous_state)

    def test_unconfirmed_revision_fails_preflight_before_installation_changes(self) -> None:
        for name in (
            "messenger_launcher.py", "messenger_runtime.py", "messenger_config_check.py",
            "messenger_readiness.py", "register_messenger_commands.py", "register_messenger_webhooks.py",
            "telegram_bot.py", "requirements.txt", "runtime_check.py", "geocoder_preflight.py",
            "prepare_basemap_cache.py", "register_telegram_commands.py", ".env.telegram.example",
        ):
            (self.repo / name).touch()
        self.git("-C", str(self.repo), "update-ref", "-d", "HEAD")
        for name in SCRIPTS:
            with self.subTest(script=name):
                preflight = "require_ready_install" if name.startswith("deploy_") else "require_repo_files"
                result = self.execute(
                    name,
                    'id() { return 0; }\n' + preflight + '\nprintf changed > "$STATE_FILE"',
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Не удалось подтвердить commit", result.stderr)
                self.assertEqual(self.state.read_text(), self.previous_state)


if __name__ == "__main__":
    unittest.main()
