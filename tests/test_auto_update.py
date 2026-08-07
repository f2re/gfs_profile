from __future__ import annotations

import os
import fcntl
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "auto_update_telegram_bot.sh"
INSTALLER = ROOT / "install_auto_update.sh"


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None, check: bool = True):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(args, cwd=cwd, env=merged_env, check=check, text=True, capture_output=True)


class AutoUpdateScriptTests(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        run("bash", "-n", str(UPDATER))
        run("bash", "-n", str(INSTALLER))

    def test_systemd_installer_contract(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("OnUnitInactiveSec=${INTERVAL}s", text)
        self.assertIn("User=root", text)
        self.assertIn("AUTO_UPDATE_BRANCH=$BRANCH", text)
        self.assertIn("systemctl enable --now", text)

    def test_fast_forward_deploy_rollback_and_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = base / "remote.git"
            seed = base / "seed"
            work = base / "work"
            state = base / "state"
            install = base / "install"
            lock = base / "update.lock"
            deploy_log = base / "deploy.log"
            install.mkdir()

            run("git", "init", "--bare", str(remote))
            run("git", "init", "-b", "telegram-bot", str(seed))
            run("git", "config", "user.email", "test@example.com", cwd=seed)
            run("git", "config", "user.name", "Test", cwd=seed)
            (seed / "app.txt").write_text("v1\n", encoding="utf-8")
            run("git", "add", ".", cwd=seed)
            run("git", "commit", "-m", "init", cwd=seed)
            run("git", "remote", "add", "origin", str(remote), cwd=seed)
            run("git", "push", "-u", "origin", "telegram-bot", cwd=seed)
            run("git", "clone", "-b", "telegram-bot", str(remote), str(work))

            (work / "auto_update_telegram_bot.sh").write_text(UPDATER.read_text(encoding="utf-8"), encoding="utf-8")
            deploy = work / "deploy_telegram_bot.sh"
            deploy.write_text(
                "#!/usr/bin/env bash\n"
                "set -e\n"
                "if [[ -f BROKEN ]]; then echo FAIL >> \"${TEST_DEPLOY_LOG}\"; exit 42; fi\n"
                "git rev-parse HEAD >> \"${TEST_DEPLOY_LOG}\"\n",
                encoding="utf-8",
            )
            deploy.chmod(0o755)
            (work / "auto_update_telegram_bot.sh").chmod(0o755)
            run("git", "config", "user.email", "test@example.com", cwd=work)
            run("git", "config", "user.name", "Test", cwd=work)
            run("git", "add", ".", cwd=work)
            run("git", "commit", "-m", "scripts", cwd=work)
            run("git", "push", "origin", "telegram-bot", cwd=work)
            run("git", "pull", "--ff-only", cwd=seed)

            current = run("git", "rev-parse", "HEAD", cwd=work).stdout.strip()
            (install / ".install-state").write_text(f"source_rev={current[:7]}\n", encoding="utf-8")
            env = {
                "TEST_DEPLOY_LOG": str(deploy_log),
                "INSTALL_DIR": str(install),
                "AUTO_UPDATE_REPO_ROOT": str(work),
                "AUTO_UPDATE_REPO_USER": os.environ.get("USER", ""),
                "AUTO_UPDATE_STATE_DIR": str(state),
                "AUTO_UPDATE_LOCK_FILE": str(lock),
                "AUTO_UPDATE_DEPLOY_LOCK_FILE": str(base / "deploy.lock"),
                "AUTO_UPDATE_INNER_DEPLOY_LOCK_FILE": str(base / "inner-deploy.lock"),
                "AUTO_UPDATE_DEPLOY_SCRIPT": str(deploy),
            }

            first = run("bash", str(work / "auto_update_telegram_bot.sh"), cwd=work, env=env)
            self.assertIn("Уже актуально", first.stderr)
            self.assertFalse(deploy_log.exists())

            (seed / "app.txt").write_text("v2\n", encoding="utf-8")
            run("git", "add", ".", cwd=seed)
            run("git", "commit", "-m", "good", cwd=seed)
            good = run("git", "rev-parse", "HEAD", cwd=seed).stdout.strip()
            run("git", "push", cwd=seed)

            # Ручной pull обновил checkout, но не /opt. Timer обязан увидеть
            # рассогласование с install-state/state и выполнить deploy.
            run("git", "-c", "core.hooksPath=/dev/null", "pull", "--ff-only", cwd=work)
            run("bash", str(work / "auto_update_telegram_bot.sh"), cwd=work, env=env)
            self.assertEqual(run("git", "rev-parse", "HEAD", cwd=work).stdout.strip(), good)
            self.assertIn(good, deploy_log.read_text(encoding="utf-8"))

            (seed / "BROKEN").touch()
            run("git", "add", "BROKEN", cwd=seed)
            run("git", "commit", "-m", "bad", cwd=seed)
            bad = run("git", "rev-parse", "HEAD", cwd=seed).stdout.strip()
            run("git", "push", cwd=seed)

            failed = run("bash", str(work / "auto_update_telegram_bot.sh"), cwd=work, env=env, check=False)
            self.assertEqual(failed.returncode, 42)
            self.assertEqual(run("git", "rev-parse", "HEAD", cwd=work).stdout.strip(), good)
            state_text = (state / "auto-update.state").read_text(encoding="utf-8")
            self.assertIn("result=rolled-back", state_text)
            self.assertIn(f"blocked_rev={bad}", state_text)

            before = deploy_log.read_text(encoding="utf-8")
            run("bash", str(work / "auto_update_telegram_bot.sh"), cwd=work, env=env)
            self.assertEqual(deploy_log.read_text(encoding="utf-8"), before)
            self.assertIn("result=blocked-revision", (state / "auto-update.state").read_text(encoding="utf-8"))

            # Новый исправляющий commit должен быть замечен, но updater не имеет
            # права менять checkout, пока штатный deploy-lock занят.
            (seed / "BROKEN").unlink()
            (seed / "app.txt").write_text("v3\n", encoding="utf-8")
            run("git", "add", "-A", cwd=seed)
            run("git", "commit", "-m", "fix", cwd=seed)
            fixed = run("git", "rev-parse", "HEAD", cwd=seed).stdout.strip()
            run("git", "push", cwd=seed)

            deploy_lock_path = Path(env["AUTO_UPDATE_DEPLOY_LOCK_FILE"])
            deploy_lock_path.parent.mkdir(parents=True, exist_ok=True)
            with deploy_lock_path.open("w") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                busy = run("bash", str(work / "auto_update_telegram_bot.sh"), cwd=work, env=env)
                self.assertIn("Deploy lock занят", busy.stderr)
                self.assertEqual(run("git", "rev-parse", "HEAD", cwd=work).stdout.strip(), good)
                self.assertIn("result=deploy-busy", (state / "auto-update.state").read_text(encoding="utf-8"))

            run("bash", str(work / "auto_update_telegram_bot.sh"), cwd=work, env=env)
            self.assertEqual(run("git", "rev-parse", "HEAD", cwd=work).stdout.strip(), fixed)
            self.assertIn(fixed, deploy_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
