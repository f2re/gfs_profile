from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_REV = "a" * 40
NEW_REV = "b" * 40


class DeployTransactionTests(unittest.TestCase):
    """Execute the real shell orchestration and gates without touching systemd/API.

    Only OS/package operations and external Python commands are replaced. The
    actual deploy main, restart/readiness, menu/webhook gates and atomic state
    writer run in a temporary directory. No bot token or host /opt is used.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.install = self.base / "install"
        self.install.mkdir()
        (self.install / ".env").write_text(
            "MESSENGER_RUNTIME_ENABLED=1\nGEOCODER_PROVIDERS=local\n",
            encoding="utf-8",
        )
        self.state = self.install / ".install-state"
        self.previous_state = (
            f"source_rev={OLD_REV}\ndeploy_status=complete\n"
            "installed_at=2026-01-01T00:00:00Z\n"
            "last_deployed_at=2026-01-02T00:00:00Z\n"
        )
        self.trace = self.base / "trace"
        self.pid = self.base / "pid"
        self.script = self.base / "deploy-harness.sh"
        source = (ROOT / "deploy_telegram_bot.sh").read_text(encoding="utf-8")
        self.assertTrue(source.rstrip().endswith('main "$@"'))
        # Load definitions but defer main until external boundaries are replaced.
        source = source.rsplit('\nmain "$@"', 1)[0]
        self.script.write_text(
            'id() { if [[ "${1:-}" == -u ]]; then printf 0; else command id "$@"; fi; }\n'
            + source
            + r'''
trace() { printf '%s\n' "$1" >> "$TEST_TRACE"; }
print_status() { :; }
require_ready_install() { trace prerequisites; }
confirm() { return 0; }
acquire_deploy_lock() { trace lock; }
install_system_packages() { :; }
ensure_geocoder_config() { :; }
ensure_runtime_config() { :; }
backup_admin_db() { :; }
copy_project() { trace sync; }
restore_admin_db() { :; }
verify_sync() { trace verify; }
ensure_venv_and_deps() { trace dependencies; }
run_tests() {
  [[ "$SKIP_TESTS" -eq 0 ]] || return 0
  trace tests
  [[ "$TEST_FAIL_PHASE" != tests ]]
}
validate_release() { trace preflight; [[ "$TEST_FAIL_PHASE" != preflight ]]; }
prepare_basemap_cache() { trace basemap; }
write_service_unit() { trace unit; }
git_rev() { printf '%s' "$TEST_NEW_REV"; }
sleep() { :; }
journalctl() { :; }
chown() { [[ "$TEST_FAIL_PHASE" != state ]]; }
systemctl() {
  case "$1" in
    show) cat "$TEST_PID" ;;
    restart)
      trace restart
      [[ "$TEST_FAIL_PHASE" != restart ]] || return 1
      printf 201 > "$TEST_PID"
      ;;
    daemon-reload|is-active|status) return 0 ;;
    *) printf 'Unexpected systemctl: %s\n' "$*" >&2; return 98 ;;
  esac
}
run_with_env() {
  case "${2:-}" in
    */messenger_readiness.py) trace ready; [[ "$TEST_FAIL_PHASE" != readiness ]] ;;
    */register_messenger_commands.py)
      trace "commands${3:+:$3}"
      [[ "$TEST_FAIL_PHASE" != commands ]]
      ;;
    */register_messenger_webhooks.py)
      trace webhooks
      [[ "${3:-}" == --strict ]] || return 97
      [[ "$TEST_FAIL_PHASE" != webhooks ]]
      ;;
    *) printf 'Unexpected Python call: %s\n' "$*" >&2; return 98 ;;
  esac
}
main
''',
            encoding="utf-8",
        )

    def deploy(self, *flags: str, fail_phase: str = "") -> subprocess.CompletedProcess[str]:
        self.state.write_text(self.previous_state, encoding="utf-8")
        self.trace.write_text("", encoding="utf-8")
        self.pid.write_text("200", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            INSTALL_DIR=str(self.install),
            TEST_TRACE=str(self.trace),
            TEST_PID=str(self.pid),
            TEST_FAIL_PHASE=fail_phase,
            TEST_NEW_REV=NEW_REV,
            SERVICE_NAME="gfs-fixture-only",
        )
        return subprocess.run(
            ["bash", str(self.script), "--yes", *flags],
            cwd=self.base,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def values(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.state.read_text().splitlines())

    def events(self) -> list[str]:
        return self.trace.read_text().splitlines()

    def test_complete_deploy_waits_for_runtime_and_verified_menus(self) -> None:
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        order = self.events()
        for first, second in zip(
            ("tests", "preflight", "unit", "restart", "ready", "commands"),
            ("preflight", "unit", "restart", "ready", "commands", "webhooks"),
        ):
            self.assertLess(order.index(first), order.index(second))
        state = self.values()
        self.assertEqual(state["source_rev"], NEW_REV)
        self.assertEqual(state["deploy_status"], "complete")
        self.assertEqual(state["pending_source_rev"], "")
        self.assertEqual(state["menu_sync"], "ok")
        self.assertEqual(state["webhook_sync"], "ok")
        self.assertEqual(state["installed_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(list(self.install.glob(".install-state.*")), [])

    def test_pre_restart_failure_keeps_confirmed_revision_and_process(self) -> None:
        for phase in ("tests", "preflight"):
            with self.subTest(phase=phase):
                result = self.deploy(fail_phase=phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("restart", self.events())
                self.assertNotIn("commands", self.events())
                self.assertEqual(self.state.read_text(), self.previous_state)

    def test_restart_and_readiness_failure_cannot_publish_menu_or_revision(self) -> None:
        for phase in ("restart", "readiness"):
            with self.subTest(phase=phase):
                result = self.deploy(fail_phase=phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("commands", self.events())
                self.assertEqual(self.state.read_text(), self.previous_state)

    def test_failed_menu_registration_is_not_a_successful_release(self) -> None:
        result = self.deploy(fail_phase="commands")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commands", self.events())
        self.assertNotIn("webhooks", self.events())
        self.assertNotIn("Deploy завершён:", result.stderr)
        self.assertEqual(self.state.read_text(), self.previous_state)

    def test_failed_strict_webhook_registration_preserves_confirmed_revision(self) -> None:
        result = self.deploy(fail_phase="webhooks")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commands", self.events())
        self.assertIn("webhooks", self.events())
        self.assertEqual(self.state.read_text(), self.previous_state)

    def test_no_restart_does_not_register_new_menu_against_old_runtime(self) -> None:
        result = self.deploy("--no-restart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue({"restart", "ready", "commands", "webhooks"}.isdisjoint(self.events()))
        state = self.values()
        self.assertEqual(state["source_rev"], OLD_REV)
        self.assertEqual(state["pending_source_rev"], NEW_REV)
        self.assertEqual(state["deploy_status"], "partial")
        self.assertEqual(state["last_deployed_at"], "2026-01-02T00:00:00Z")

    def test_explicit_skips_are_recorded_as_partial(self) -> None:
        for flag in ("--skip-pip", "--skip-tests", "--skip-commands", "--skip-webhooks"):
            with self.subTest(flag=flag):
                result = self.deploy(flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                state = self.values()
                self.assertEqual(state["source_rev"], OLD_REV)
                self.assertEqual(state["pending_source_rev"], NEW_REV)
                self.assertEqual(state["deploy_status"], "partial")

    def test_state_write_failure_does_not_truncate_last_success(self) -> None:
        result = self.deploy(fail_phase="state")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text(), self.previous_state)

    def test_telegram_only_runtime_does_not_register_max_menu(self) -> None:
        (self.install / ".env").write_text("MESSENGER_RUNTIME_ENABLED=0\nMAX_BOT_TOKEN=fixture\n")
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("commands:--telegram", self.events())
        self.assertNotIn("webhooks", self.events())
        self.assertEqual(self.values()["deploy_status"], "complete")


class InstallerRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.install = self.base / "install"
        self.install.mkdir()
        (self.install / ".env").write_text("MESSENGER_RUNTIME_ENABLED=1\n")
        self.state = self.install / ".install-state"
        self.previous_state = f"source_rev={OLD_REV}\ndeploy_status=complete\n"
        self.state.write_text(self.previous_state)
        self.trace = self.base / "trace"
        self.script = self.base / "installer-harness.sh"
        source = (ROOT / "install_telegram_bot.sh").read_text(encoding="utf-8")
        self.assertTrue(source.rstrip().endswith('main "$@"'))
        self.script.write_text(
            'id() { if [[ "${1:-}" == -u ]]; then printf 0; else command id "$@"; fi; }\n'
            + source.rsplit('\nmain "$@"', 1)[0]
            + r'''
trace() { printf '%s\n' "$1" >> "$TEST_TRACE"; }
sleep() { :; }
journalctl() { :; }
git() { printf '%s' "$TEST_NEW_REV"; }
run_root() { if [[ "$1" == chown ]]; then return 0; fi; "$@"; }
systemctl() { trace "$1"; }
run_with_env() {
  case "${2:-}" in
    */messenger_readiness.py) trace ready ;;
    */register_messenger_commands.py)
      trace commands
      [[ "$TEST_FAIL_PHASE" != commands ]]
      ;;
    */register_messenger_webhooks.py)
      trace webhooks
      [[ "${3:-}" == --strict ]] || return 97
      [[ "$TEST_FAIL_PHASE" != webhooks ]]
      ;;
    *) return 98 ;;
  esac
}
start_service
wait_runtime_ready
register_commands
register_webhooks
write_state
''',
            encoding="utf-8",
        )

    def install_run(self, *flags: str, fail_phase: str = "") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            INSTALL_DIR=str(self.install),
            TEST_TRACE=str(self.trace),
            TEST_NEW_REV=NEW_REV,
            TEST_FAIL_PHASE=fail_phase,
            SERVICE_NAME="gfs-installer-fixture-only",
        )
        return subprocess.run(
            ["bash", str(self.script), "--yes", *flags], cwd=self.base,
            env=env, text=True, capture_output=True, timeout=10, check=False,
        )

    def test_reinstall_restarts_existing_service_before_menu_registration(self) -> None:
        result = self.install_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.trace.read_text().splitlines()
        self.assertEqual(events, ["enable", "restart", "is-active", "ready", "commands", "webhooks"])
        state = dict(line.split("=", 1) for line in self.state.read_text().splitlines())
        self.assertEqual(state["source_rev"], NEW_REV)
        self.assertEqual(state["deploy_status"], "complete")

    def test_menu_failure_keeps_previous_state_and_does_not_register_webhooks(self) -> None:
        result = self.install_run(fail_phase="commands")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("webhooks", self.trace.read_text().splitlines())
        self.assertEqual(self.state.read_text(), self.previous_state)

    def test_webhook_failure_does_not_confirm_installation(self) -> None:
        result = self.install_run(fail_phase="webhooks")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text(), self.previous_state)

    def test_no_start_is_partial_and_does_not_contact_messenger_apis(self) -> None:
        result = self.install_run("--no-start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.trace.exists())
        state = dict(line.split("=", 1) for line in self.state.read_text().splitlines())
        self.assertEqual(state["source_rev"], OLD_REV)
        self.assertEqual(state["pending_source_rev"], NEW_REV)
        self.assertEqual(state["deploy_status"], "partial")


class EnvironmentCommandTests(unittest.TestCase):
    def test_invalid_env_or_workdir_cannot_fall_through_to_registration(self) -> None:
        scripts = (
            ("deploy_telegram_bot.sh", "run_with_env"),
            ("install_telegram_bot.sh", "run_with_env"),
            ("install_messenger_runtime.sh", "run_python_env"),
        )
        for name, function in scripts:
            for failure in ("env", "workdir"):
                with self.subTest(script=name, failure=failure), tempfile.TemporaryDirectory() as raw:
                    base = Path(raw)
                    env_file = base / ".env"
                    env_file.write_text("false\n" if failure == "env" else "TEST_SETTING=ok\n")
                    marker = base / "registration-ran"
                    source = (ROOT / name).read_text(encoding="utf-8")
                    if name == "install_messenger_runtime.sh":
                        source = source.rsplit("\nvalidate_install\ncase", 1)[0]
                    else:
                        source = source.rsplit('\nmain "$@"', 1)[0]
                    harness = base / "environment-harness.sh"
                    harness.write_text(
                        'id() { if [[ "${1:-}" == -u ]]; then printf 0; else command id "$@"; fi; }\n'
                        + source
                        + '\nrun_user() { shift; "$@"; }\n'
                        + 'runuser() { shift 3; "$@"; }\n'
                        + 'ENV_FILE="$TEST_ENV_FILE"\n'
                        + 'INSTALL_DIR="$TEST_WORKDIR"\n'
                        + f"{function} bash -c 'printf touched > \"$TEST_MARKER\"'\n"
                    )
                    env = os.environ.copy()
                    env.update(
                        TEST_ENV_FILE=str(env_file),
                        TEST_WORKDIR=str(base if failure == "env" else base / "missing"),
                        TEST_MARKER=str(marker),
                    )
                    result = subprocess.run(
                        ["bash", str(harness)], cwd=base, env=env, text=True,
                        capture_output=True, timeout=10, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(marker.exists(), "Registration ran after invalid environment")


if __name__ == "__main__":
    unittest.main()
