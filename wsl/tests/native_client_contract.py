#!/usr/bin/env python3
"""Offline contract for no-account Claude Code routes and browser MCP config."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

sys.dont_write_bytecode = True


def load_manager(manager_path: Path, home: Path):
    os.environ["HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("finalkit_native_client_contract", manager_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import switch manager: {manager_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(manager_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="finalkit-native-client-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        module = load_manager(manager_path, home)
        paths = module.Paths(home / "finalkit")
        manager = module.RuntimeManager(paths)
        paths.ensure_private_tree()
        assert module.default_gateway_port(1000) == 9876
        assert module.default_gateway_port(1001) == 9877
        assert 1024 <= module.default_gateway_port(65534) <= 65535
        assert paths.codex_auth == home / ".finalkit-client" / ".codex" / "auth.json"
        assert not paths.science_home.exists()
        assert not hasattr(manager, "science_start")
        assert not hasattr(manager, "science_url")
        assert not hasattr(manager, "init_profile")

        config_path = manager.write_browser_mcp_config("http://127.0.0.1:9223")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        server = config["mcpServers"]["chrome-devtools"]
        assert server == {
            "type": "stdio",
            "command": "cmd.exe",
            "args": [
                "/d",
                "/c",
                "%LOCALAPPDATA%/ScienceCodexFinalKit/browser-mcp.cmd",
            ],
        }
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

        for invalid in (
            "https://127.0.0.1:9223",
            "http://example.com:9223",
            "http://127.0.0.1",
            "http://127.0.0.1:9223/path",
            "http://user@127.0.0.1:9223",
        ):
            try:
                manager.write_browser_mcp_config(invalid)
            except module.FinalKitError:
                pass
            else:
                raise AssertionError(f"unsafe browser origin accepted: {invalid}")

        calls: list[tuple[list[str], dict[str, str]]] = []
        manager.select_gateway = lambda mode: None
        manager.read_gateway_record = lambda: {
            "backend": "deepseek",
            "endpoint": "http://127.0.0.1:9876/private",
        }
        manager.gateway_identity = lambda record=None: True
        manager.gateway_health = lambda record=None: {"status": "ok"}
        original_run = module.subprocess.run
        module.subprocess.run = lambda argv, env, check: (
            calls.append((list(argv), dict(env))) or type("Result", (), {"returncode": 0})()
        )
        try:
            assert manager.run_claude_code("deepseek", ["--version"]) == 0
        finally:
            module.subprocess.run = original_run
        argv, environment = calls[0]
        assert argv == [str(paths.claude), "--version"]
        assert environment["ANTHROPIC_AUTH_TOKEN"] == "finalkit-local-token"
        assert environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "deepseek-v4-pro"
        assert environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-v4-flash"
        assert environment["ANTHROPIC_DEFAULT_OPUS_MODEL_NAME"] == "DeepSeek deepseek-v4-pro"

        # Switching a native client must not touch a broken optional Science
        # control plane when no verified Science lock owner exists.
        manager.current_mode = lambda: "deepseek"
        manager.read_gateway_record = lambda: {
            "backend": "deepseek",
            "endpoint": "http://127.0.0.1:9876/old",
        }
        manager.gateway_identity = lambda record=None: True
        manager.gateway_health = lambda record=None: {"status": "ok"}
        manager.science_lock_process = lambda: None
        manager.science_status = lambda: (_ for _ in ()).throw(
            AssertionError("native switch consulted optional Science status")
        )
        manager.science_stop = lambda: (_ for _ in ()).throw(
            AssertionError("native switch tried to stop absent Science")
        )
        manager.stop_gateway = lambda: None
        manager.spawn_gateway = lambda mode: "http://127.0.0.1:9876/new"
        manager.write_mode = lambda mode: None
        assert manager._switch_locked("kimi").endswith("/new")

        # Legacy fkctl start/switch/restart compatibility must also be
        # gateway-only.  A user following an older command must never be sent
        # into a Claude Science account flow.
        switch_calls: list[str] = []
        manager._switch_locked = lambda mode: (
            switch_calls.append(mode) or "http://127.0.0.1:9876/new"
        )
        assert manager.switch("codex").endswith("/new")
        assert switch_calls == ["codex"]
        parser = module.build_parser()
        assert parser.parse_args(["start", "deepseek"]).command == "start"
        with redirect_stderr(io.StringIO()):
            try:
                parser.parse_args(["init-profile"])
            except SystemExit:
                pass
            else:
                raise AssertionError("legacy Science profile initializer is still publicly callable")

    print("NATIVE_CLIENT_CONTRACT_OK no_claude_account=true browser_mcp=loopback-only")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: native_client_contract.py /path/to/switch_manager.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
