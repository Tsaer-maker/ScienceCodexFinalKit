#!/usr/bin/env python3
"""Offline contract for Claude Science ownership and stale-control handling.

The test imports the caller-supplied switch manager into an isolated HOME,
replaces process/status probes with deterministic fakes, and verifies stopped,
healthy, stale-socket, lock-conflict, and safe-stop behavior.  It never touches
the host's real processes, WSL service, credentials, or network.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


PID = 321
CONFLICT_PID = 654


def load_manager(manager_path: Path, home: Path):
    os.environ["HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("finalkit_runtime_control_contract", manager_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import switch manager: {manager_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRun:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]):
        self.results = list(results)
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if not self.results:
            raise AssertionError(f"unexpected subprocess.run call: {args!r}")
        return self.results.pop(0)


def completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(["claude-science"], returncode, stdout, stderr)


def verify(manager_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="finalkit-runtime-control-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        module = load_manager(manager_path, home)
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        terminal_environment = module.private_child_environment(home)
        assert terminal_environment["TERM"] == "xterm-256color"
        assert terminal_environment["COLORTERM"] == "truecolor"
        paths = module.Paths(home / "finalkit")
        paths.science.parent.mkdir(parents=True, exist_ok=True)
        paths.science.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        paths.science.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        manager = module.RuntimeManager(paths)
        paths.data_dir.mkdir(parents=True, exist_ok=True)

        # Auth migration snapshots must distinguish stopped, gateway-only and
        # Science+gateway states so Windows can restore the exact prior shape.
        snapshot_manager = module.RuntimeManager(paths)
        snapshot_manager.require_runtime = lambda: None
        snapshot_manager.current_mode = lambda: "kimi"
        snapshot_manager.science_status = lambda: {"running": False}
        snapshot_manager.read_gateway_record = lambda: None
        stopped_snapshot = snapshot_manager.runtime_state()
        assert stopped_snapshot == {
            "schema": "switchboard-runtime-state-v1",
            "science_running": False,
            "gateway_running": False,
            "mode": "kimi",
        }

        snapshot_record = {
            "backend": "deepseek",
            "endpoint": "http://127.0.0.1:9876",
        }
        snapshot_manager.read_gateway_record = lambda: snapshot_record
        snapshot_manager.gateway_identity = lambda record=None: True
        snapshot_manager.gateway_health = lambda record=None: {"status": "ok"}
        gateway_snapshot = snapshot_manager.runtime_state()
        assert gateway_snapshot["gateway_running"] is True
        assert gateway_snapshot["science_running"] is False
        assert gateway_snapshot["mode"] == "deepseek"

        snapshot_manager.science_status = lambda: {"running": True}
        snapshot_manager.science_status_endpoint_matches = lambda status, endpoint: True
        science_snapshot = snapshot_manager.runtime_state()
        assert science_snapshot["science_running"] is True
        assert science_snapshot["gateway_running"] is True
        assert science_snapshot["mode"] == "deepseek"

        # A one-time Windows -> WSL Codex auth migration is accepted only while
        # this runtime is stopped. The candidate crosses stdin in production,
        # is validated in a temporary HOME, and either commits at mode 0600 or
        # restores the prior bytes and permissions exactly.
        auth_manager = module.RuntimeManager(paths)
        auth_manager.require_runtime = lambda: None
        auth_manager.ensure_bridge_config = lambda: None
        auth_manager.science_status = lambda: {"running": False}
        auth_manager.read_gateway_record = lambda: None
        auth_manager._codex_login_status = lambda environment: True
        imported_secret = "contract-import-secret"
        imported = bytearray(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "access_token": imported_secret,
                        "refresh_token": "contract-import-refresh",
                    },
                }
            ).encode("utf-8")
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            auth_manager.import_codex_auth(imported)
        assert paths.codex_auth.read_bytes() == bytes(imported)
        assert stat.S_IMODE(paths.codex_auth.stat().st_mode) == 0o600
        assert imported_secret not in output.getvalue()

        previous = (
            b'{\n  "auth_mode": "chatgpt", "tokens": '
            b'{"access_token": "old", "refresh_token": "old-refresh"}\n}\n'
        )
        paths.codex_auth.write_bytes(previous)
        paths.codex_auth.chmod(0o640)
        checks = iter((True, False))
        auth_manager._codex_login_status = lambda environment: next(checks)
        try:
            auth_manager.import_codex_auth(imported)
        except module.FinalKitError as exc:
            assert "official Codex login status" in str(exc), exc
        else:
            raise AssertionError("failed final Codex validation unexpectedly committed imported auth")
        assert paths.codex_auth.read_bytes() == previous
        assert stat.S_IMODE(paths.codex_auth.stat().st_mode) == 0o640

        auth_manager.science_status = lambda: {"running": True}
        auth_manager._codex_login_status = lambda environment: (_ for _ in ()).throw(
            AssertionError("live-runtime import reached Codex validation")
        )
        try:
            auth_manager.import_codex_auth(imported)
        except module.FinalKitError as exc:
            assert "stop the active Switchboard" in str(exc), exc
        else:
            raise AssertionError("Codex auth import was allowed while Science was running")
        assert paths.codex_auth.read_bytes() == previous

        # The Windows migration entry is one WSL transaction: snapshot, stop,
        # import and exact restoration all happen during one FileLock hold.
        transaction_state = {
            "schema": "switchboard-runtime-state-v1",
            "science_running": True,
            "gateway_running": True,
            "mode": "deepseek",
        }
        transaction = module.RuntimeManager(paths)
        transaction.require_runtime = lambda: None
        transaction._validate_imported_codex_auth = lambda payload: None
        transaction_calls: list[str] = []
        transaction._runtime_state_locked = lambda: (
            transaction_calls.append("state") or dict(transaction_state)
        )
        transaction.science_stop = lambda: transaction_calls.append("stop-science")
        transaction.stop_gateway = lambda: transaction_calls.append("stop-gateway")
        transaction._import_codex_auth_locked = lambda payload: transaction_calls.append("import")
        transaction._restore_runtime_state_locked = lambda state: transaction_calls.append("restore")
        with contextlib.redirect_stdout(io.StringIO()):
            restored_state = transaction.migrate_codex_auth(imported)
        assert restored_state == transaction_state
        assert transaction_calls == [
            "state", "stop-science", "stop-gateway", "import", "restore", "state"
        ]

        # If a restore fails after auth commit, the same locked transaction
        # stops partial layers, restores exact prior auth bytes/mode, retries
        # the old runtime shape and reports the primary failure.
        transaction_prior = b'{"auth_mode":"chatgpt","tokens":{"access_token":"prior"}}\n'
        paths.codex_auth.write_bytes(transaction_prior)
        paths.codex_auth.chmod(0o640)
        rollback = module.RuntimeManager(paths)
        rollback.require_runtime = lambda: None
        rollback._validate_imported_codex_auth = lambda payload: None
        rollback._runtime_state_locked = lambda: dict(transaction_state)
        rollback.science_stop = lambda: None
        rollback.stop_gateway = lambda: None
        rollback._import_codex_auth_locked = lambda payload: (
            paths.codex_auth.write_bytes(b"committed-new-auth")
        )
        restore_attempts = iter((False, True))

        def restore_with_one_failure(state):
            if not next(restore_attempts):
                raise module.FinalKitError("fixture restore failure")

        rollback._restore_runtime_state_locked = restore_with_one_failure
        try:
            rollback.migrate_codex_auth(imported)
        except module.FinalKitError as exc:
            assert "previous auth bytes and runtime state were restored" in str(exc), exc
        else:
            raise AssertionError("post-commit restore failure was not surfaced")
        assert paths.codex_auth.read_bytes() == transaction_prior
        assert stat.S_IMODE(paths.codex_auth.stat().st_mode) == 0o640

        # Ctrl-C and TERM after auth replacement enter the same rollback path;
        # neither signal may bypass exact auth/runtime restoration.
        for interrupt_name in ("keyboard", "term"):
            paths.codex_auth.write_bytes(transaction_prior)
            paths.codex_auth.chmod(0o640)
            interrupted = module.RuntimeManager(paths)
            interrupted.require_runtime = lambda: None
            interrupted._validate_imported_codex_auth = lambda payload: None
            interrupted._runtime_state_locked = lambda: dict(transaction_state)
            interrupted.science_stop = lambda: None
            interrupted.stop_gateway = lambda: None
            restored_after_interrupt: list[dict] = []
            interrupted._restore_runtime_state_locked = (
                lambda state: restored_after_interrupt.append(dict(state))
            )

            def interrupt_after_replace(payload):
                paths.codex_auth.write_bytes(b"interrupted-new-auth")
                if interrupt_name == "keyboard":
                    raise KeyboardInterrupt()
                os.kill(os.getpid(), signal.SIGTERM)

            interrupted._import_codex_auth_locked = interrupt_after_replace
            try:
                interrupted.migrate_codex_auth(imported)
            except module.FinalKitError as exc:
                assert "migration failed" in str(exc)
            else:
                raise AssertionError(f"{interrupt_name} bypassed migration rollback")
            assert paths.codex_auth.read_bytes() == transaction_prior
            assert stat.S_IMODE(paths.codex_auth.stat().st_mode) == 0o640
            assert restored_after_interrupt == [transaction_state]

        # Official login validation is internally bounded; Windows never has
        # to kill an unbounded WSL child in the middle of this transaction.
        original_run = module.subprocess.run
        timeout_seen: list[int] = []

        class StatusResult:
            returncode = 0

        def bounded_run(*args, **kwargs):
            timeout_seen.append(kwargs.get("timeout"))
            return StatusResult()

        module.subprocess.run = bounded_run
        try:
            status_manager = module.RuntimeManager(paths)
            assert status_manager._codex_login_status({}) is True
        finally:
            module.subprocess.run = original_run
        assert timeout_seen == [module.CODEX_LOGIN_STATUS_TIMEOUT_SECONDS]

        # The standalone WSL login entry is the same isolated credential
        # owner as configure-codex, but it deliberately skips model prompts.
        # The official CLI first logs into a staging HOME, validates there,
        # then validates the committed ~/.finalkit-client cache. It must never
        # read or replace the user's ordinary ~/.codex/auth.json.
        ordinary_auth = home / ".codex" / "auth.json"
        ordinary_auth.parent.mkdir(parents=True, exist_ok=True)
        ordinary_bytes = b'{"tokens":{"access_token":"ordinary-profile"}}\n'
        ordinary_auth.write_bytes(ordinary_bytes)
        login_homes = home / "codex-login-homes.txt"
        paths.codex.parent.mkdir(parents=True, exist_ok=True)
        staged_payload = {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": "isolated-login-access",
                "refresh_token": "isolated-login-refresh",
            },
        }
        paths.codex.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            f"marker = Path({str(login_homes)!r})\n"
            "home = Path(os.environ['HOME'])\n"
            "with marker.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(str(home) + '\\n')\n"
            "auth = home / '.codex' / 'auth.json'\n"
            "if sys.argv[-2:] == ['login', 'status']:\n"
            "    raise SystemExit(0 if auth.is_file() else 1)\n"
            "auth.parent.mkdir(parents=True, exist_ok=True)\n"
            f"auth.write_text({(json.dumps(staged_payload) + chr(10))!r}, encoding='utf-8')\n",
            encoding="utf-8",
        )
        paths.codex.chmod(0o700)
        selected_network_homes: list[Path] = []
        original_codex_network_environment = module.codex_network_environment

        def isolated_network_environment(selected_home: Path, _url: str):
            selected_network_homes.append(Path(selected_home))
            return module.private_child_environment(Path(selected_home))

        module.codex_network_environment = isolated_network_environment
        linux_login = module.RuntimeManager(paths)
        linux_login.ensure_bridge_config = lambda: None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                linux_login.login_linux_codex()
        finally:
            module.codex_network_environment = original_codex_network_environment
        observed_homes = [Path(value) for value in login_homes.read_text(encoding="utf-8").splitlines()]
        assert selected_network_homes == [paths.client_home]
        assert len(observed_homes) == 3, observed_homes
        assert observed_homes[-1] == paths.client_home
        assert all(value == paths.client_home or paths.client_home in value.parents for value in observed_homes)
        assert paths.codex_auth.read_text(encoding="utf-8") == json.dumps(staged_payload) + "\n"
        assert stat.S_IMODE(paths.codex_auth.stat().st_mode) == 0o600
        assert ordinary_auth.read_bytes() == ordinary_bytes
        paths.codex_auth.write_bytes(transaction_prior)
        paths.codex_auth.chmod(0o640)

        # A partial first stop is also recovered inside the same transaction;
        # Windows never guesses whether it should start Codex or another mode.
        partial = module.RuntimeManager(paths)
        partial.require_runtime = lambda: None
        partial._validate_imported_codex_auth = lambda payload: None
        partial._runtime_state_locked = lambda: dict(transaction_state)
        stop_attempts = iter((False, True))

        def stop_with_one_failure():
            if not next(stop_attempts):
                raise module.FinalKitError("fixture partial stop")

        partial.science_stop = stop_with_one_failure
        partial.stop_gateway = lambda: None
        partial._import_codex_auth_locked = lambda payload: (_ for _ in ()).throw(
            AssertionError("partial stop reached auth replacement")
        )
        partial._restore_runtime_state_locked = lambda state: None
        try:
            partial.migrate_codex_auth(imported)
        except module.FinalKitError as exc:
            assert "previous auth bytes and runtime state were restored" in str(exc), exc
        else:
            raise AssertionError("partial stop failure was not surfaced")
        assert paths.codex_auth.read_bytes() == transaction_prior

        # The long-lived daemon never inherits Windows drive paths or a caller
        # working directory. FinalKit supplies a session-only loopback token;
        # real provider credentials remain exclusively inside the gateway.
        os.environ["PATH"] = "/mnt/c/Windows/System32:/usr/bin:/mnt/d/Tools"
        os.environ["PWD"] = "/mnt/d/Tools/ScienceCodexFinalKit"
        daemon_environment = manager.science_environment("http://127.0.0.1:9876")
        assert daemon_environment["PATH"] == module.LINUX_SYSTEM_PATH
        assert daemon_environment["PWD"] == str(paths.science_home)
        assert daemon_environment["HOME"] == str(paths.science_home)
        assert daemon_environment["ANTHROPIC_API_KEY"] == ""
        assert daemon_environment["ANTHROPIC_AUTH_TOKEN"] == "sk-ant-finalkit-local-session"
        assert "/mnt/" not in daemon_environment["PATH"]

        origin, nonce = manager.science_login_target(
            "http://localhost:8765/?nonce=one-time-local-nonce", 8765
        )
        assert origin == "http://127.0.0.1:8765"
        assert nonce == "one-time-local-nonce"
        for invalid in (
            "https://127.0.0.1:8765/?nonce=x",
            "http://example.com:8765/?nonce=x",
            "http://127.0.0.1:9999/?nonce=x",
            "http://127.0.0.1:not-a-port/?nonce=x",
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/?nonce=x&nonce=y",
        ):
            try:
                manager.science_login_target(invalid, 8765)
            except module.FinalKitError:
                pass
            else:
                raise AssertionError(f"unsafe Science login URL was accepted: {invalid}")

        # A gateway-only selection may reuse the provider already serving a
        # live Science instance, but it must never stop or reroute that
        # instance in order to select a different provider for another client.
        guard_manager = module.RuntimeManager(paths)
        gateway_record = {
            "backend": "codex",
            "endpoint": "http://127.0.0.1:9876",
        }
        guard_manager.current_mode = lambda: "codex"
        guard_manager.read_gateway_record = lambda: gateway_record
        guard_manager.gateway_identity = lambda record=None: True
        guard_manager.gateway_health = lambda record=None: {"status": "ok"}
        guard_manager.science_status = lambda: {"running": True}
        guard_manager.science_status_endpoint_matches = (
            lambda status, endpoint: status.get("running") is True
            and endpoint == gateway_record["endpoint"]
        )
        guard_manager.write_mode = lambda mode: None
        guard_manager.science_stop = lambda: (_ for _ in ()).throw(
            AssertionError("gateway-only selection stopped Claude Science")
        )
        guard_manager.stop_gateway = lambda: (_ for _ in ()).throw(
            AssertionError("gateway-only selection stopped the Science gateway")
        )
        guard_manager.spawn_gateway = lambda mode: (_ for _ in ()).throw(
            AssertionError("gateway-only selection rerouted Claude Science")
        )

        reused_endpoint = guard_manager._switch_locked("codex", start_science=False)
        assert reused_endpoint == gateway_record["endpoint"]
        try:
            guard_manager._switch_locked("deepseek", start_science=False)
        except module.FinalKitError as exc:
            assert "will not stop or reroute" in str(exc), exc
        else:
            raise AssertionError("gateway-only provider change was allowed to reroute live Science")

        # A newly started Science transaction carries the bounded startup
        # deadline through the second, user-facing nonce URL instead of
        # treating one transient control-socket miss as a switch failure.
        switch_manager = module.RuntimeManager(paths)
        switch_manager.current_mode = lambda: None
        switch_manager.read_gateway_record = lambda: None
        switch_manager.science_status = lambda: {"running": False}
        switch_manager.science_stop = lambda: None
        switch_manager.stop_gateway = lambda: None
        switch_manager.spawn_gateway = lambda mode: "http://127.0.0.1:9876"
        switch_manager.science_start = lambda endpoint: {"running": True, "pid": PID}
        switch_manager.gateway_health = lambda record=None: {"status": "ok"}
        switch_manager.science_status_endpoint_matches = lambda status, endpoint: True
        switch_manager.write_mode = lambda mode: None
        user_url_deadlines = []
        switch_manager.science_url = lambda **kwargs: (
            user_url_deadlines.append(kwargs.get("startup_deadline"))
            or "http://127.0.0.1:8765/?nonce=user"
        )
        switched_url = switch_manager._switch_locked("codex", start_science=True)
        assert switched_url.endswith("nonce=user")
        assert len(user_url_deadlines) == 1 and user_url_deadlines[0] is not None

        original_run = module.subprocess.run
        original_live = module.process_is_live
        original_cmdline = module.process_cmdline_parts
        original_environment = module.process_environment
        original_state = module.process_state
        original_kill = module.os.kill
        original_sleep = module.time.sleep
        original_monotonic = module.time.monotonic

        live_pids: set[int] = set()

        def fake_live(pid: int) -> bool:
            return pid in live_pids

        def fake_cmdline(pid: int) -> list[str]:
            return [str(paths.science), "serve", "--data-dir", str(paths.data_dir)]

        def fake_environment(pid: int) -> dict[str, str]:
            return {"HOME": str(paths.science_home), "ANTHROPIC_BASE_URL": "http://127.0.0.1:9876"}

        module.process_is_live = fake_live
        module.process_cmdline_parts = fake_cmdline
        module.process_environment = fake_environment
        module.process_state = lambda pid: "S"
        module.os.kill = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runtime control contract must never signal a process")
        )

        try:
            # Normal stopped state remains usable and does not become a failure.
            stopped_run = FakeRun([completed(0, '{"running": false}\n')])
            module.subprocess.run = stopped_run
            stopped = manager.science_status()
            assert stopped == {"running": False}, stopped
            assert Path(stopped_run.calls[0][1]["cwd"]) == paths.science_home

            # A healthy daemon must pass the complete lock/PID/argv/HOME/data-dir identity proof.
            live_pids = {PID}
            (paths.data_dir / "operon.lock").write_text(json.dumps({"pid": PID}), encoding="utf-8")
            module.subprocess.run = FakeRun(
                [completed(0, json.dumps({"running": True, "pid": PID, "port": 8765}))]
            )
            healthy = manager.science_status()
            assert healthy.get("running") is True and not healthy.get("control_error"), healthy
            assert manager.science_status_endpoint_matches(
                healthy, "http://127.0.0.1:9876"
            )

            # One transient control-socket miss for the same fully owned daemon
            # is retried and recovered instead of blocking a healthy Start.
            module.subprocess.run = FakeRun(
                [
                    completed(1, stderr="could not reach daemon control socket"),
                    completed(0, json.dumps({"running": True, "pid": PID, "port": 8765})),
                ]
            )
            transient = manager.science_status()
            assert transient.get("running") is True and transient.get("control_recovered") is True

            # Even a successful official status is rejected when the verified
            # owner is blocked in Linux uninterruptible I/O (D state).
            module.process_state = lambda pid: "D"
            module.subprocess.run = FakeRun(
                [completed(0, json.dumps({"running": True, "pid": PID, "port": 8765}))]
            )
            blocked = manager.science_status()
            assert blocked.get("control_error") == "unavailable", blocked
            assert blocked.get("process_state") == "D", blocked

            # One transient D observation during detached startup is healthy;
            # only a sustained D state is the historical DrvFS/9p stall.
            states = iter(("D", "S"))
            module.process_state = lambda pid: next(states, "S")
            module.subprocess.run = FakeRun(
                [completed(0, json.dumps({"running": True, "pid": PID, "port": 8765}))]
            )
            transient_io = manager.science_status()
            assert transient_io.get("running") is True
            assert not transient_io.get("control_error"), transient_io
            module.process_state = lambda pid: "S"

            # The URL control command can miss the same freshly detached
            # owner's socket even after status first turns healthy. Startup
            # retries that exact transient without weakening steady-state URL.
            url_manager = module.RuntimeManager(paths)
            url_manager.science_lock_process = lambda: {"pid": PID, "owned": True}
            module.subprocess.run = FakeRun(
                [
                    completed(1, stderr="claude-science: could not reach daemon control socket"),
                    completed(0, "http://127.0.0.1:8765/?nonce=fixture\n"),
                ]
            )
            module.time.sleep = lambda _seconds: None
            module.time.monotonic = lambda: 0.0
            assert url_manager.science_url(startup_deadline=1.0).endswith("nonce=fixture")

            # Science 0.1.27 may remain in verified ext4 database I/O for
            # several seconds after serve --detached exits. Only science_start
            # may wait for that exact result, and only until its ready deadline.
            startup_manager = module.RuntimeManager(paths)
            startup_states = iter(
                (
                    {"running": False},
                    {
                        "running": True,
                        "control_error": "unavailable",
                        "owned": True,
                        "pid": PID,
                        "process_state": "D",
                        "detail": (
                            "Claude Science owner is blocked in uninterruptible I/O; "
                            "its page and control socket are not reliable"
                        ),
                    },
                    {"running": True, "owned": True, "pid": PID, "port": 8765},
                    {
                        "running": True,
                        "control_error": "unavailable",
                        "owned": True,
                        "pid": PID,
                        "process_state": "D",
                        "detail": (
                            "Claude Science owner is blocked in uninterruptible I/O; "
                            "its page and control socket are not reliable"
                        ),
                    },
                    {"running": True, "owned": True, "pid": PID, "port": 8765},
                )
            )
            startup_manager.science_status = lambda: next(startup_states)
            startup_manager.ensure_science_identity = lambda: {"action": "existing"}
            session_checks = []
            startup_manager.verify_science_local_session = lambda **_kwargs: (
                session_checks.append("verified") or {"verified": True}
            )
            module.subprocess.run = FakeRun([completed(0)])
            module.time.sleep = lambda _seconds: None
            module.time.monotonic = lambda: 0.0
            started = startup_manager.science_start("http://127.0.0.1:9876")
            assert started.get("pid") == PID
            assert started.get("local_session", {}).get("verified") is True
            assert session_checks == ["verified"], session_checks

            blocked_start_manager = module.RuntimeManager(paths)
            blocked_start_states = iter(
                (
                    {"running": False},
                    {
                        "running": True,
                        "control_error": "unavailable",
                        "owned": True,
                        "pid": PID,
                        "process_state": "D",
                        "detail": (
                            "Claude Science owner is blocked in uninterruptible I/O; "
                            "its page and control socket are not reliable"
                        ),
                    },
                )
            )
            blocked_start_manager.science_status = lambda: next(blocked_start_states)
            blocked_start_manager.ensure_science_identity = lambda: {"action": "existing"}
            blocked_start_manager.verify_science_local_session = lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("permanently blocked startup reached session verification")
            )
            module.subprocess.run = FakeRun([completed(0)])
            monotonic_values = iter((0.0, 0.0, module.SCIENCE_START_READY_SECONDS + 1.0))
            module.time.monotonic = lambda: next(
                monotonic_values, module.SCIENCE_START_READY_SECONDS + 1.0
            )
            try:
                blocked_start_manager.science_start("http://127.0.0.1:9876")
            except module.FinalKitError as exc:
                assert "did not become ready" in str(exc), exc
            else:
                raise AssertionError("permanently blocked Science startup exceeded its deadline")
            module.time.sleep = original_sleep
            module.time.monotonic = original_monotonic

            # A live owned process plus a failed control socket is an explicit error, not stopped.
            module.subprocess.run = FakeRun([completed(1, stderr="non-retryable control failure")])
            stale = manager.science_status()
            assert stale.get("running") is True and stale.get("control_error") == "unavailable", stale
            assert stale.get("pid") == PID and stale.get("owned") is True, stale
            assert "FINALKIT_SCIENCE_CONTROL_UNAVAILABLE" in manager.science_recovery_message(stale)

            # Status and the live lock must identify the same fully owned process.
            live_pids = {PID, CONFLICT_PID}
            (paths.data_dir / "operon.lock").write_text(
                json.dumps({"pid": CONFLICT_PID}), encoding="utf-8"
            )
            module.subprocess.run = FakeRun(
                [completed(0, json.dumps({"running": True, "pid": PID, "port": 8765}))]
            )
            conflict = manager.science_status()
            assert conflict.get("control_error") == "unavailable", conflict
            assert "conflicts" in str(conflict.get("detail")), conflict

            # Official stop failure followed by stale control fails closed and never calls os.kill.
            live_pids = {PID}
            (paths.data_dir / "operon.lock").write_text(json.dumps({"pid": PID}), encoding="utf-8")
            module.subprocess.run = FakeRun(
                [
                    completed(1, stderr="stop socket unavailable"),
                    completed(1, stderr="status socket unavailable"),
                ]
            )
            try:
                manager.science_stop()
            except module.FinalKitError as exc:
                assert "FINALKIT_SCIENCE_CONTROL_UNAVAILABLE" in str(exc), exc
            else:
                raise AssertionError("stale Science stop unexpectedly succeeded")
        finally:
            module.subprocess.run = original_run
            module.process_is_live = original_live
            module.process_cmdline_parts = original_cmdline
            module.process_environment = original_environment
            module.process_state = original_state
            module.os.kill = original_kill
            module.time.sleep = original_sleep
            module.time.monotonic = original_monotonic

    print(
        "RUNTIME_CONTROL_CONTRACT_OK stopped=usable healthy=owned "
        "local-session=loopback-only non-science-switch=guarded "
        "auth-import=atomic+stopped-only migration=one-lock+rollback runtime-snapshot=exact "
        "linux-login=isolated-owner startup-io=bounded stale=fail-closed signals=rollback-safe"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: runtime_control_contract.py /path/to/switch_manager.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
