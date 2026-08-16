#!/usr/bin/env python3
"""Offline contract for the optional pinned multi-agent integration."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.dont_write_bytecode = True


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("switchboard_agents_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import agents manager: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(path: Path) -> None:
    module = load_module(path)
    assert module.UPSTREAM_URL == "https://github.com/coredo-eu/codex-claude-orchestrator.git"
    assert module.UPSTREAM_COMMIT == "c996b497c6682f4695b5aa342610527731712c51"
    assert module.UPSTREAM_VERSION == "0.3.1"
    assert module.PLUGIN_SELECTOR == "codex-claude-orchestrator@codex-claude-orchestrator"
    assert "--name" in module.CLAUDE_FLAGS
    assert "--append-system-prompt-file" in module.CLAUDE_FLAGS

    source = path.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "Science-identity=not-mutated" in source
    assert "Windows-auth=not-injected" in source
    assert "FILESYSTEM_BOUNDARY sandbox=false" in source

    previous = dict(os.environ)
    try:
        os.environ["ANTHROPIC_API_KEY"] = "must-not-leak"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "must-not-leak"
        os.environ["CODEX_HOME"] = "/tmp/must-not-leak"
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/must-not-leak"
        os.environ["PATH"] = "/mnt/c/Windows/System32:/usr/bin"
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        with tempfile.TemporaryDirectory(prefix="switchboard-agents-contract-") as temp:
            fixture = Path(temp).resolve()
            isolated_home = fixture / "home"
            isolated_home.mkdir()
            manager = module.AgentsManager(fixture / "root", real_home=isolated_home)
            environment = manager.environment()
            for name in (
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CODEX_HOME",
                "CLAUDE_CONFIG_DIR",
            ):
                assert name not in environment
            assert "/mnt/c" not in environment["PATH"]
            assert environment["HOME"] == str(manager.client_home)
            assert environment["TERM"] == "xterm-256color"
            assert environment["COLORTERM"] == "truecolor"
            assert manager.real_home == isolated_home
            assert manager.client_home.is_relative_to(isolated_home)
            assert manager.checkout.parent == fixture / "root" / "integrations"
            assert not manager.codex.exists()
            assert not manager.claude.exists()
            dependency_names = {name for name, _, _ in manager.dependency_checks()}
            for required in ("git", "zsh", "jq", "flock", "ps", "sed", "awk", "tr", "sha256sum"):
                assert required in dependency_names
            marketplace = {
                "name": module.MARKETPLACE_NAME,
                "root": str(manager.checkout),
            }
            assert manager.marketplace_record_matches(marketplace)
            assert not manager.marketplace_record_matches(
                {"name": module.MARKETPLACE_NAME, "root": str(Path(temp) / "foreign")}
            )
            plugin = {
                "pluginId": module.PLUGIN_SELECTOR,
                "name": module.PLUGIN_NAME,
                "marketplaceName": module.MARKETPLACE_NAME,
                "version": module.UPSTREAM_VERSION + "+codex.test",
                "enabled": True,
                "source": {
                    "source": "local",
                    "path": str(manager.checkout / "plugins" / module.PLUGIN_NAME),
                },
            }
            assert manager.plugin_record_matches(plugin, require_enabled=True)
            plugin["version"] = "0.0.0"
            assert not manager.plugin_record_matches(plugin, require_enabled=True)
            with redirect_stdout(io.StringIO()) as status_output:
                assert manager.print_status() == 0
            assert "MULTI_AGENT_READY=false" in status_output.getvalue()
            with redirect_stdout(io.StringIO()):
                assert manager.print_status(require_ready=True) == 2
    finally:
        os.environ.clear()
        os.environ.update(previous)

    assert module.AgentsManager.contains_named(
        {"installed": [{"name": module.PLUGIN_NAME}]}, module.PLUGIN_NAME
    )
    assert not module.AgentsManager.contains_named(
        {"installed": [{"name": "something-else"}]}, module.PLUGIN_NAME
    )
    print("AGENTS_MANAGER_CONTRACT_OK")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: agents_manager_contract.py /path/to/agents_manager.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
