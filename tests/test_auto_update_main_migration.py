from __future__ import annotations

import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "auto_update_telegram_bot.sh"


def run(*args: str, cwd: Path | None = None, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env={**os.environ, **(env or {})},
                          capture_output=True, text=True, check=check)


class DeploymentFixture:
    """A real local git remote and deploy contract; no services or network API."""

    def __init__(self, root: Path, *, main=True, shallow=False):
        self.root = root
        self.remote = root / "remote.git"
        self.seed = root / "seed"
        self.work = root / "work"
        self.install = root / "install"
        self.state_dir = root / "state"
        self.log = root / "deploy.log"
        self.install.mkdir()
        run("git", "init", "--bare", str(self.remote))
        run("git", "init", "-b", "telegram-bot", str(self.seed))
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.seed / "app.txt").write_text("v1\n")
        (self.seed / "auto_update_telegram_bot.sh").write_text(UPDATER.read_text())
        (self.seed / "deploy_telegram_bot.sh").write_text(
            '#!/usr/bin/env bash\nset -e\n'
            'rev=$(git rev-parse HEAD)\n'
            'printf "%s\\n" "$rev" >> "$TEST_DEPLOY_LOG"\n'
            'if [[ -f BROKEN ]]; then exit 42; fi\n'
            'if [[ -f INCOMPLETE ]]; then\n'
            '  printf "source_rev=%s\\ndeploy_status=partial\\npending_source_rev=%s\\n" '
            '"$rev" "$rev" > "$INSTALL_DIR/.install-state"\n'
            '  exit 0\nfi\n'
            'if [[ -f NO_ATTESTATION ]]; then exit 0; fi\n'
            'printf "source_rev=%s\\ndeploy_status=complete\\n" "$rev" > "$INSTALL_DIR/.install-state"\n'
        )
        self.initial = self.commit("initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "telegram-bot")
        if main:
            self.git("branch", "main")
            self.git("push", "origin", "main")
        clone = ["git", "clone", "-b", "telegram-bot"]
        if shallow:
            clone.extend(["--depth=1", self.remote.as_uri(), str(self.work)])
        else:
            clone.extend([str(self.remote), str(self.work)])
        run(*clone)
        (self.install / ".install-state").write_text(f"source_rev={self.initial[:7]}\n")
        self.env = {
            "AUTO_UPDATE_BRANCH": "telegram-bot",
            "AUTO_UPDATE_BRANCH_PINNED": "0",
            "AUTO_UPDATE_REPO_ROOT": str(self.work),
            "AUTO_UPDATE_REPO_USER": pwd.getpwuid(os.getuid()).pw_name,
            "AUTO_UPDATE_STATE_DIR": str(self.state_dir),
            "AUTO_UPDATE_LOCK_FILE": str(root / "update.lock"),
            "AUTO_UPDATE_DEPLOY_LOCK_FILE": str(root / "deploy.lock"),
            "AUTO_UPDATE_INNER_DEPLOY_LOCK_FILE": str(root / "inner.lock"),
            "AUTO_UPDATE_DEPLOY_SCRIPT": str(self.work / "deploy_telegram_bot.sh"),
            "INSTALL_DIR": str(self.install),
            "TEST_DEPLOY_LOG": str(self.log),
        }

    def git(self, *args):
        return run("git", *args, cwd=self.seed).stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def publish(self, branch, name, value):
        self.git("checkout", branch)
        (self.seed / name).write_text(value)
        rev = self.commit(name)
        self.git("push", "origin", branch)
        return rev

    def update(self, *args, check=True):
        return run("bash", str(self.work / "auto_update_telegram_bot.sh"), *args,
                   cwd=self.work, env=self.env, check=check)

    def state(self):
        return dict(line.split("=", 1) for line in
                    (self.state_dir / "auto-update.state").read_text().splitlines())

    def local(self, *args):
        return run("git", *args, cwd=self.work).stdout.strip()


class MainMigrationTests(unittest.TestCase):
    def test_legacy_timer_migrates_only_after_merge_and_then_tracks_main(self):
        with tempfile.TemporaryDirectory() as raw:
            f = DeploymentFixture(Path(raw))
            legacy = f.publish("telegram-bot", "app.txt", "v2\n")
            f.update()
            self.assertEqual(f.local("rev-parse", "HEAD"), legacy)
            self.assertEqual(f.state()["effective_branch"], "telegram-bot")
            self.assertEqual(f.state()["tracking_branch"], "")

            f.git("checkout", "main")
            f.git("merge", "--no-ff", "telegram-bot", "-m", "release")
            release = f.git("rev-parse", "HEAD")
            f.git("push", "origin", "main")
            f.update()
            self.assertEqual(f.local("rev-parse", "HEAD"), release)
            self.assertEqual(f.local("branch", "--show-current"), "main")
            self.assertEqual(f.state()["tracking_branch"], "main")

            # A later development commit must not escape into production.
            f.publish("telegram-bot", "development.txt", "not released\n")
            main = f.publish("main", "production.txt", "released\n")
            f.update()
            self.assertEqual(f.local("rev-parse", "HEAD"), main)
            self.assertFalse((f.work / "development.txt").exists())
            self.assertEqual(f.state()["requested_branch"], "telegram-bot")
            status = f.update("--status").stdout
            self.assertIn("requested_branch=telegram-bot", status)
            self.assertIn("effective_branch=main", status)

    def test_same_sha_switches_channel_without_restarting_service(self):
        with tempfile.TemporaryDirectory() as raw:
            f = DeploymentFixture(Path(raw))
            f.update()
            self.assertEqual(f.state()["result"], "up-to-date")
            self.assertEqual(f.state()["tracking_branch"], "main")
            self.assertEqual(f.local("branch", "--show-current"), "main")
            self.assertFalse(f.log.exists())

    def test_missing_main_preserves_legacy_channel(self):
        with tempfile.TemporaryDirectory() as raw:
            f = DeploymentFixture(Path(raw), main=False)
            f.update()
            self.assertEqual(f.state()["effective_branch"], "telegram-bot")
            self.assertEqual(f.state()["tracking_branch"], "")
            self.assertFalse(f.log.exists())

    def test_failed_main_release_does_not_persist_channel_then_recovers(self):
        with tempfile.TemporaryDirectory() as raw:
            f = DeploymentFixture(Path(raw))
            broken = f.publish("main", "BROKEN", "bad\n")
            result = f.update(check=False)
            self.assertEqual(result.returncode, 42, result.stderr)
            self.assertEqual(f.state()["result"], "rolled-back")
            self.assertEqual(f.state()["tracking_branch"], "")
            self.assertEqual(f.state()["blocked_rev"], broken)
            self.assertEqual(f.local("rev-parse", "HEAD"), f.initial)
            before = f.log.read_text()
            f.update()
            self.assertEqual(f.log.read_text(), before)
            (f.seed / "BROKEN").unlink()
            fixed = f.commit("fix")
            f.git("push", "origin", "main")
            f.update()
            self.assertEqual(f.state()["tracking_branch"], "main")
            self.assertEqual(f.state()["deployed_rev"], fixed)

    def test_explicit_custom_or_pinned_branch_wins_over_saved_main(self):
        for branch, pin in (("telegram-bot", "1"), ("staging", "0")):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as raw:
                f = DeploymentFixture(Path(raw))
                if branch == "staging":
                    f.git("branch", branch)
                    f.git("push", "origin", branch)
                f.publish("main", "production.txt", "new\n")
                f.state_dir.mkdir()
                (f.state_dir / "auto-update.state").write_text("tracking_branch=main\n")
                f.env.update(AUTO_UPDATE_BRANCH=branch, AUTO_UPDATE_BRANCH_PINNED=pin)
                f.update()
                self.assertEqual(f.state()["effective_branch"], branch)
                self.assertEqual(f.local("rev-parse", "HEAD"), f.initial)
                self.assertFalse(f.log.exists())

    def test_zero_exit_without_complete_deploy_is_rejected_and_rolled_back(self):
        for marker in ("INCOMPLETE", "NO_ATTESTATION"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as raw:
                f = DeploymentFixture(Path(raw))
                f.publish("main", marker, "partial\n")
                result = f.update(check=False)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("не подтвердил complete", result.stderr)
                self.assertEqual(f.state()["result"], "rolled-back")
                self.assertEqual(f.state()["tracking_branch"], "")
                self.assertEqual(f.local("rev-parse", "HEAD"), f.initial)

    def test_partial_same_sha_forces_complete_deploy(self):
        with tempfile.TemporaryDirectory() as raw:
            f = DeploymentFixture(Path(raw))
            (f.install / ".install-state").write_text(
                f"source_rev={f.initial}\ndeploy_status=partial\npending_source_rev={f.initial}\n")
            f.update()
            self.assertEqual(f.state()["result"], "deployed")
            self.assertEqual(f.log.read_text().strip(), f.initial)

    def test_shallow_single_branch_checkout_can_migrate(self):
        with tempfile.TemporaryDirectory() as raw:
            f = DeploymentFixture(Path(raw), shallow=True)
            self.assertEqual(f.local("rev-parse", "--is-shallow-repository"), "true")
            released = f.publish("main", "production.txt", "new\n")
            f.update()
            self.assertEqual(f.state()["tracking_branch"], "main")
            self.assertEqual(f.local("rev-parse", "HEAD"), released)


if __name__ == "__main__":
    unittest.main()
