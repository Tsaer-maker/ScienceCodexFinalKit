#!/usr/bin/env python3
"""Offline contract for persistent, upgrade-safe provider model routes.

The test imports the caller-supplied manager under an isolated HOME.  It never
touches the installed FinalKit runtime, credentials, processes, or network.
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


def load_manager(manager_path: Path, home: Path):
    os.environ["HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("finalkit_model_routes_contract", manager_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import switch manager: {manager_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(manager_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="finalkit-model-routes-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        module = load_manager(manager_path, home)
        paths = module.Paths(home / "finalkit")
        manager = module.RuntimeManager(paths)

        # The known 3.0.3 draft accidentally routed Sonnet to Sol and Haiku to
        # Terra.  It is a package default, not a user choice, so migration must
        # repair it to the current three-tier defaults.
        paths.bridge.mkdir(parents=True, exist_ok=True)
        paths.bridge_config.write_text(
            json.dumps(
                {
                    "codex_model": "gpt-5.6-sol",
                    "codex_model_map": {
                        "claude-opus-4-8": "gpt-5.6-sol",
                        "claude-sonnet-4-5": "gpt-5.6-sol",
                        "claude-haiku-4-5": "gpt-5.6-terra",
                    },
                    "force_model": "gpt-5.6-sol",
                }
            ),
            encoding="utf-8",
        )
        routes = manager.model_routes()
        assert routes["codex"] == {
            "opus": "gpt-5.6-sol",
            "sonnet": "gpt-5.6-terra",
            "haiku": "gpt-5.6-luna",
            "reasoning_effort": "max",
        }, routes
        assert stat.S_IMODE(paths.model_routes.stat().st_mode) == 0o600

        # Provider discovery is a read-only official catalog lookup.  The key
        # is passed only to the fixed-endpoint fetcher, no route file changes,
        # and no generation request is made.
        paths.provider_keys["deepseek"].write_text("fixture-secret\n", encoding="utf-8")
        paths.provider_keys["deepseek"].chmod(0o600)
        fetched: dict[str, str] = {}

        def fake_catalog(provider: str, api_key: str):
            fetched.update(provider=provider, api_key=api_key)
            return {
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-pro"},
                    {"id": "deepseek-v4-flash"},
                    {"id": "deepseek-v4-pro"},
                    {"id": "bad model with spaces"},
                ],
            }

        manager.fetch_provider_model_payload = fake_catalog
        discovery_before = paths.model_routes.read_bytes()
        discovery = manager.discover_provider_models("deepseek")
        assert fetched == {"provider": "deepseek", "api_key": "fixture-secret"}
        assert discovery["models"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
        assert discovery["current_main_available"] and discovery["current_fast_available"]
        assert discovery["writes_performed"] is False
        assert discovery["generation_request_performed"] is False
        assert paths.model_routes.read_bytes() == discovery_before

        # A preview is deterministic and does not mutate the owning file.
        original = paths.model_routes.read_bytes()
        preview = manager.update_model_routes(
            "deepseek", main="deepseek-v5-pro", fast="deepseek-v5-flash", dry_run=True
        )
        assert preview["changed"] and preview["dry_run"]
        assert preview["routes"]["providers"]["deepseek"]["main"] == "deepseek-v5-pro"
        assert paths.model_routes.read_bytes() == original

        # A healthy runtime for another provider is not interrupted.  The
        # requested provider's route is committed for its next start.
        manager.read_gateway_record = lambda: {"backend": "deepseek"}
        manager.gateway_identity = lambda record=None: True
        manager.gateway_health = lambda record=None: {"status": "ok"}
        unrelated = manager.update_model_routes("glm", main="glm-6", fast="glm-6-flash")
        assert unrelated["changed"] and not unrelated["restart_required"]
        manager.read_gateway_record = lambda: None

        # A stopped-runtime update is atomic and survives regeneration of the
        # connector's derived compatibility config.
        updated = manager.update_model_routes(
            "codex",
            opus="gpt-6-sol",
            sonnet="gpt-6-terra",
            haiku="gpt-6-luna",
            effort="xhigh",
        )
        assert updated["changed"] and not updated["runtime_restarted"]
        manager.ensure_bridge_config()
        persisted = manager.model_routes()
        assert persisted["codex"]["opus"] == "gpt-6-sol"
        derived = json.loads(paths.bridge_config.read_text(encoding="utf-8"))
        assert derived["codex_model_map"] == {
            "claude-opus": "gpt-6-sol",
            "claude-sonnet": "gpt-6-terra",
            "claude-haiku": "gpt-6-luna",
        }
        assert derived["codex_reasoning_effort"] == "xhigh"
        assert derived["force_model"] == ""

        # Changing the active provider is fail-closed unless the caller gives
        # explicit restart authority; no configuration is written on refusal.
        active_bytes = paths.model_routes.read_bytes()
        manager.read_gateway_record = lambda: {"backend": "codex"}
        manager.gateway_identity = lambda record=None: True
        manager.gateway_health = lambda record=None: {"status": "ok"}
        try:
            manager.update_model_routes("codex", opus="gpt-6.1-sol")
        except module.FinalKitError as exc:
            assert "--restart" in str(exc)
        else:
            raise AssertionError("active route update did not require explicit restart")
        assert paths.model_routes.read_bytes() == active_bytes
        manager.read_gateway_record = lambda: None

        # Future package defaults must not replace an existing user-owned
        # route.  Re-instantiating the manager models a later package update.
        manager_again = module.RuntimeManager(module.Paths(paths.root))
        assert manager_again.model_routes() == persisted

        # A package may add another built-in provider later, while an older or
        # vendor-specific route may already be present.  Validation merges new
        # defaults and preserves unknown well-formed provider entries.
        custom = manager_again.model_routes()
        custom["providers"]["future-vendor"] = {
            "main": "future/model@3+pro",
            "fast": "future-3-flash",
        }
        module.atomic_write(paths.model_routes, json.dumps(custom) + "\n")
        merged = manager_again.model_routes()
        assert merged["providers"]["future-vendor"]["main"] == "future/model@3+pro"
        assert set(module.API_PROVIDERS).issubset(merged["providers"])

        try:
            manager.update_model_routes("glm", main="bad model with spaces")
        except module.FinalKitError as exc:
            assert "model ID" in str(exc)
        else:
            raise AssertionError("invalid model ID was accepted")
        malformed = manager.model_routes()
        del malformed["providers"]["deepseek"]["main"]
        try:
            manager.validate_model_routes(malformed)
        except module.FinalKitError as exc:
            assert "string model ID" in str(exc)
        else:
            raise AssertionError("missing model ID was normalized to a string")

        # Stable CLI surfaces for scripts: compact JSON on stdout, a no-write
        # preview, and conventional nonzero failure for invalid input.
        environment = os.environ.copy()
        environment["HOME"] = str(home)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        cli = [sys.executable, str(manager_path), "--root", str(paths.root)]
        shown = subprocess.run(
            [*cli, "models", "--json"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert shown.returncode == 0, shown.stderr
        assert json.loads(shown.stdout)["providers"]["future-vendor"]["main"] == "future/model@3+pro"
        cli_before = paths.model_routes.read_bytes()
        cli_preview = subprocess.run(
            [
                *cli,
                "update-models",
                "kimi",
                "--main",
                "kimi-k4",
                "--dry-run",
                "--json",
            ],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert cli_preview.returncode == 0, cli_preview.stderr
        assert json.loads(cli_preview.stdout)["routes"]["providers"]["kimi"]["main"] == "kimi-k4"
        assert paths.model_routes.read_bytes() == cli_before
        cli_discovery = subprocess.run(
            [*cli, "discover-models", "deepseek", "--json"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        # The subprocess has no injected network fixture.  It must fail
        # conventionally without printing the saved key or mutating routes.
        assert cli_discovery.returncode == 1
        assert "fixture-secret" not in cli_discovery.stdout + cli_discovery.stderr
        assert paths.model_routes.read_bytes() == cli_before
        cli_invalid = subprocess.run(
            [*cli, "update-models", "kimi", "--main", "bad model"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert cli_invalid.returncode == 1
        assert "ERROR:" in cli_invalid.stderr

    print("MODEL_ROUTES_CONTRACT_OK")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: model_routes_contract.py /path/to/switch_manager.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
