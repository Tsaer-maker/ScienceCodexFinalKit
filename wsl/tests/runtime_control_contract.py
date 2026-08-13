#!/usr/bin/env python3
"""Offline contract for Claude Science ownership and stale-control handling.

The test imports the caller-supplied switch manager into an isolated HOME,
replaces process/status probes with deterministic fakes, and verifies stopped,
healthy, stale-socket, lock-conflict, and safe-stop behavior.  It never touches
the host's real processes, WSL service, credentials, or network.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
        paths = module.Paths(home / "finalkit")
        paths.science.parent.mkdir(parents=True, exist_ok=True)
        paths.science.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        paths.science.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        manager = module.RuntimeManager(paths)
        paths.data_dir.mkdir(parents=True, exist_ok=True)

        # The long-lived daemon never inherits Windows drive paths or a caller
        # working directory. FinalKit owns only the local endpoint; Claude
        # Science itself owns its supported account sign-in.
        os.environ["PATH"] = "/mnt/c/Windows/System32:/usr/bin:/mnt/d/Tools"
        os.environ["PWD"] = "/mnt/d/Tools/ScienceCodexFinalKit"
        daemon_environment = manager.science_environment("http://127.0.0.1:9876")
        assert daemon_environment["PATH"] == module.LINUX_SYSTEM_PATH
        assert daemon_environment["PWD"] == str(paths.science_home)
        assert daemon_environment["HOME"] == str(paths.science_home)
        assert "ANTHROPIC_API_KEY" not in daemon_environment
        assert "ANTHROPIC_AUTH_TOKEN" not in daemon_environment
        assert "/mnt/" not in daemon_environment["PATH"]

        original_run = module.subprocess.run
        original_live = module.process_is_live
        original_cmdline = module.process_cmdline_parts
        original_environment = module.process_environment
        original_state = module.process_state
        original_kill = module.os.kill

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

    print("RUNTIME_CONTROL_CONTRACT_OK stopped=usable healthy=owned stale=fail-closed signals=none")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: runtime_control_contract.py /path/to/switch_manager.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
