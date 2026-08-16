#!/usr/bin/env python3
"""Offline contract for persistent, upgrade-safe provider model routes.

The test imports the caller-supplied manager under an isolated HOME.  It never
touches the installed FinalKit runtime, credentials, processes, or network.
"""

from __future__ import annotations

import importlib.util
import fcntl
import http.server
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

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
            "model_opus": "gpt-5.6-sol",
            "reasoning_opus": "max",
            "model_sonnet": "gpt-5.6-terra",
            "reasoning_sonnet": "max",
            "model_haiku": "gpt-5.6-luna",
            "reasoning_haiku": "max",
        }, routes
        assert routes["schema_version"] == 3
        assert stat.S_IMODE(paths.model_routes.stat().st_mode) == 0o600

        # Schema 1's main/fast/shared-effort shape is upgraded once to the
        # compact per-tier Model/Reasoning owner without losing user choices.
        legacy_routes = {
            "schema_version": 1,
            "providers": {
                name: {
                    "main": route["model_opus"],
                    "fast": route["model_haiku"],
                    "reasoning_effort": "auto",
                }
                for name, route in routes["providers"].items()
            },
            "codex": {
                "opus": "gpt-5.6-sol",
                "sonnet": "gpt-5.6-terra",
                "haiku": "gpt-5.6-luna",
                "reasoning_effort": "high",
            },
        }
        module.atomic_write(paths.model_routes, json.dumps(legacy_routes) + "\n")
        routes = manager.model_routes()
        assert routes["codex"]["reasoning_opus"] == "high"
        assert routes["codex"]["reasoning_sonnet"] == "high"
        assert routes["codex"]["reasoning_haiku"] == "high"

        # Schema 2 admitted provider-wide effort lists.  Upgrade known invalid
        # model/effort pairs to safe auto while preserving models and roles.
        legacy_model_semantics = json.loads(json.dumps(routes))
        legacy_model_semantics["schema_version"] = 2
        legacy_model_semantics["providers"]["kimi"].update(
            model_sonnet="kimi-k2.6", reasoning_sonnet="high"
        )
        legacy_model_semantics["providers"]["glm"].update(
            model_haiku="glm-4.7-flash", reasoning_haiku="max"
        )
        module.atomic_write(paths.model_routes, json.dumps(legacy_model_semantics) + "\n")
        routes = manager.model_routes()
        assert routes["schema_version"] == 3
        assert routes["providers"]["kimi"]["model_sonnet"] == "kimi-k2.6"
        assert routes["providers"]["kimi"]["reasoning_sonnet"] == "auto"
        assert routes["providers"]["glm"]["model_haiku"] == "glm-4.7-flash"
        assert routes["providers"]["glm"]["reasoning_haiku"] == "auto"

        # The WSL configure-codex route editor reads this Linux Codex cache,
        # shows each model's real capabilities, and prompts only Model/Reasoning.
        paths.codex_auth.parent.mkdir(parents=True, exist_ok=True)
        (paths.codex_auth.parent / "models_cache.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.6-sol",
                            "visibility": "list",
                            "default_reasoning_level": "max",
                            "supported_reasoning_levels": [
                                {"effort": "high", "description": "strong"},
                                {"effort": "max", "description": "deepest"},
                            ],
                        },
                        {
                            "slug": "gpt-5.6-terra",
                            "visibility": "list",
                            "default_reasoning_level": "high",
                            "supported_reasoning_levels": ["high", "max"],
                        },
                        {
                            "slug": "gpt-5.6-luna",
                            "visibility": "list",
                            "default_reasoning_level": "low",
                            "supported_reasoning_levels": ["low", "max"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("builtins.input", side_effect=["", "", "", "", "", ""]):
            interactive = manager.configure_model_routes_interactive("codex")
        assert interactive["routes"]["codex"]["model_opus"] == "gpt-5.6-sol"
        assert interactive["routes"]["codex"]["reasoning_haiku"] == "low"
        with mock.patch(
            "builtins.input",
            side_effect=["", "auto", "", "auto", "", "auto"],
        ):
            auto_routes = manager.configure_model_routes_interactive("codex")
        assert auto_routes["routes"]["codex"]["reasoning_opus"] == "auto"
        assert auto_routes["routes"]["codex"]["reasoning_sonnet"] == "auto"
        assert auto_routes["routes"]["codex"]["reasoning_haiku"] == "auto"

        # Provider discovery is a read-only official catalog lookup.  The key
        # is passed only to the fixed-endpoint fetcher, no route file changes,
        # and no generation request is made.
        paths.provider_keys["deepseek"].write_text("fixture-secret\n", encoding="utf-8")
        paths.provider_keys["deepseek"].chmod(0o600)

        # urllib follows redirects by default and would otherwise forward the
        # Authorization header. A loopback-only 302 fixture proves the package
        # refuses a second request, especially to a different host/port.
        catalog_authorization: list[str] = []
        redirected_authorization: list[str] = []

        class RedirectSink(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                redirected_authorization.append(self.headers.get("Authorization", ""))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

            def log_message(self, _format, *_args):
                return

        sink = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectSink)
        sink_thread = threading.Thread(target=sink.serve_forever, daemon=True)
        sink_thread.start()
        sink_port = sink.server_address[1]

        class CatalogRedirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                catalog_authorization.append(self.headers.get("Authorization", ""))
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{sink_port}/models")
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        catalog = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CatalogRedirect)
        catalog_thread = threading.Thread(target=catalog.serve_forever, daemon=True)
        catalog_thread.start()
        original_catalog = module.API_PROVIDERS["deepseek"]["catalog"]
        module.API_PROVIDERS["deepseek"]["catalog"] = (
            f"http://127.0.0.1:{catalog.server_address[1]}/models"
        )
        try:
            try:
                module.RuntimeManager(paths).fetch_provider_model_payload(
                    "deepseek", "redirect-secret"
                )
            except module.FinalKitError as exc:
                assert "refusing to forward" in str(exc), exc
            else:
                raise AssertionError("provider catalog redirect was followed")
        finally:
            module.API_PROVIDERS["deepseek"]["catalog"] = original_catalog
            catalog.shutdown()
            sink.shutdown()
            catalog.server_close()
            sink.server_close()
            catalog_thread.join(timeout=2)
            sink_thread.join(timeout=2)
        assert catalog_authorization == ["Bearer redirect-secret"]
        assert redirected_authorization == []

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
        assert all(discovery["current_availability"].values())
        assert discovery["writes_performed"] is False
        assert discovery["generation_request_performed"] is False
        assert paths.model_routes.read_bytes() == discovery_before

        # A preview is deterministic and does not mutate the owning file.
        original = paths.model_routes.read_bytes()
        preview = manager.update_model_routes(
            "deepseek",
            opus="deepseek-v5-pro",
            sonnet="deepseek-v5-chat",
            haiku="deepseek-v5-flash",
            effort_opus="max",
            effort_sonnet="high",
            effort_haiku="none",
            dry_run=True,
        )
        assert preview["changed"] and preview["dry_run"]
        assert preview["routes"]["providers"]["deepseek"]["model_sonnet"] == "deepseek-v5-chat"
        assert preview["routes"]["providers"]["deepseek"]["reasoning_haiku"] == "none"
        assert paths.model_routes.read_bytes() == original

        # Kimi K3 is always-thinking and therefore rejects none; Kimi K2.6
        # retains the documented provider-wide disable switch.
        try:
            manager.update_model_routes(
                "kimi",
                sonnet="kimi-k3[1m]",
                effort_sonnet="none",
                dry_run=True,
            )
        except module.FinalKitError as exc:
            assert "kimi sonnet reasoning" in str(exc).lower()
        else:
            raise AssertionError("Kimi K3 accepted Reasoning=none")
        kimi_k26 = manager.update_model_routes(
            "kimi",
            sonnet="kimi-k2.6",
            effort_sonnet="none",
            dry_run=True,
        )
        assert kimi_k26["routes"]["providers"]["kimi"]["reasoning_sonnet"] == "none"
        glm_53 = manager.update_model_routes(
            "glm", sonnet="glm-5.3", effort_sonnet="max", dry_run=True
        )
        assert glm_53["routes"]["providers"]["glm"]["reasoning_sonnet"] == "max"
        try:
            manager.update_model_routes(
                "glm", sonnet="glm-5.3", effort_sonnet="none", dry_run=True
            )
        except module.FinalKitError:
            pass
        else:
            raise AssertionError("GLM-5.3 accepted Reasoning=none")

        # A healthy runtime for another provider is not interrupted.  The
        # requested provider's route is committed for its next start.
        manager.read_gateway_record = lambda: {"backend": "deepseek"}
        manager.gateway_identity = lambda record=None: True
        manager.gateway_health = lambda record=None: {"status": "ok"}
        unrelated = manager.update_model_routes(
            "glm", opus="glm-6", sonnet="glm-6-air", haiku="glm-6-flash"
        )
        assert unrelated["changed"] and not unrelated["restart_required"]
        manager.read_gateway_record = lambda: None

        # The real CLI path acquires the same runtime lock. A route update
        # waits behind an auth/runtime transaction, then two concurrent
        # partial role updates serialize and both survive the read/merge/write.
        base = [
            sys.executable,
            str(manager_path),
            "--root",
            str(paths.root),
            "update-models",
            "deepseek",
        ]
        paths.lock.parent.mkdir(parents=True, exist_ok=True)
        with paths.lock.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            blocked = subprocess.Popen(
                base + ["--opus", "deepseek-lock-proof", "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.35)
            assert blocked.poll() is None, "CLI update ignored the runtime lock"
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        blocked_out, blocked_err = blocked.communicate(timeout=20)
        assert blocked.returncode == 0, (blocked_out, blocked_err)

        first = subprocess.Popen(
            base + ["--sonnet", "deepseek-concurrent-sonnet", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second = subprocess.Popen(
            base + ["--haiku", "deepseek-concurrent-haiku", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_out, first_err = first.communicate(timeout=20)
        second_out, second_err = second.communicate(timeout=20)
        assert first.returncode == 0, (first_out, first_err)
        assert second.returncode == 0, (second_out, second_err)
        concurrent = manager.model_routes()["providers"]["deepseek"]
        assert concurrent["model_opus"] == "deepseek-lock-proof"
        assert concurrent["model_sonnet"] == "deepseek-concurrent-sonnet"
        assert concurrent["model_haiku"] == "deepseek-concurrent-haiku"

        # A stopped-runtime update is atomic and survives regeneration of the
        # connector's derived compatibility config.
        updated = manager.update_model_routes(
            "codex",
            opus="gpt-6-sol",
            sonnet="gpt-6-terra",
            haiku="gpt-6-luna",
            effort_opus="max",
            effort_sonnet="high",
            effort_haiku="low",
        )
        assert updated["changed"] and not updated["runtime_restarted"]
        manager.ensure_bridge_config()
        persisted = manager.model_routes()
        assert persisted["codex"]["model_opus"] == "gpt-6-sol"
        derived = json.loads(paths.bridge_config.read_text(encoding="utf-8"))
        assert derived["codex_model_map"] == {
            "claude-opus": "gpt-6-sol",
            "claude-sonnet": "gpt-6-terra",
            "claude-haiku": "gpt-6-luna",
        }
        assert derived["codex_reasoning_map"] == {
            "claude-opus": "max",
            "claude-sonnet": "high",
            "claude-haiku": "low",
        }
        assert derived["codex_reasoning"] == "max"
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

        # Route replacement plus an optional live-runtime restart is one
        # interruption-safe transaction. Inject both Ctrl-C and TERM after
        # stopping the old owner, after writing the new route/config, and
        # after spawning the new gateway; every path must restore exact files
        # and the previous Science+gateway shape before surfacing failure.
        manager.ensure_bridge_config()
        signal_routes = paths.model_routes.read_bytes()
        signal_bridge = paths.bridge_config.read_bytes()
        sigterm_before = signal.getsignal(signal.SIGTERM)
        for interruption in ("keyboard", "term"):
            for phase in ("after_stop", "after_route", "after_new_gateway"):
                paths.model_routes.write_bytes(signal_routes)
                paths.bridge_config.write_bytes(signal_bridge)
                state = {
                    "science": True,
                    "gateway": True,
                    "backend": "deepseek",
                    "mode": "deepseek",
                }
                triggered = [False]
                signal_manager = module.RuntimeManager(paths)
                signal_manager.read_gateway_record = lambda: {
                    "backend": state["backend"],
                    "endpoint": "http://127.0.0.1:9876",
                }
                signal_manager.gateway_identity = lambda record=None: state["gateway"]
                signal_manager.gateway_health = lambda record=None: (
                    {"status": "ok"} if state["gateway"] else None
                )
                signal_manager.science_status = lambda: {"running": state["science"]}
                signal_manager.science_endpoint_matches = lambda endpoint: state["science"]

                def interrupt_once():
                    if triggered[0]:
                        return
                    triggered[0] = True
                    if interruption == "keyboard":
                        raise KeyboardInterrupt(f"fixture {phase}")
                    os.kill(os.getpid(), signal.SIGTERM)

                def stop_science():
                    state["science"] = False

                def stop_gateway():
                    state["gateway"] = False
                    if phase == "after_stop":
                        interrupt_once()

                def spawn_gateway(backend: str):
                    state["gateway"] = True
                    state["backend"] = backend
                    if phase == "after_new_gateway":
                        interrupt_once()
                    return "http://127.0.0.1:9876"

                def start_science(_endpoint: str):
                    state["science"] = True

                def write_mode(mode: str):
                    state["mode"] = mode

                signal_manager.science_stop = stop_science
                signal_manager.stop_gateway = stop_gateway
                signal_manager.spawn_gateway = spawn_gateway
                signal_manager.science_start = start_science
                signal_manager.write_mode = write_mode
                original_ensure_bridge_config = signal_manager.ensure_bridge_config

                def ensure_then_interrupt():
                    original_ensure_bridge_config()
                    if phase == "after_route":
                        interrupt_once()

                signal_manager.ensure_bridge_config = ensure_then_interrupt
                try:
                    signal_manager.update_model_routes(
                        "deepseek",
                        opus=f"deepseek-{interruption}-{phase}",
                        restart=True,
                    )
                except module.FinalKitError as exc:
                    assert "runtime state were restored" in str(exc), exc
                else:
                    raise AssertionError(f"{interruption}/{phase} bypassed route rollback")
                assert triggered[0]
                assert paths.model_routes.read_bytes() == signal_routes
                assert paths.bridge_config.read_bytes() == signal_bridge
                assert state == {
                    "science": True,
                    "gateway": True,
                    "backend": "deepseek",
                    "mode": "deepseek",
                }, (interruption, phase, state)
                assert signal.getsignal(signal.SIGTERM) == sigterm_before

        # Future package defaults must not replace an existing user-owned
        # route.  Re-instantiating the manager models a later package update.
        manager_again = module.RuntimeManager(module.Paths(paths.root))
        assert manager_again.model_routes() == persisted

        # A package may add another built-in provider later, while an older or
        # vendor-specific route may already be present.  Validation merges new
        # defaults and preserves unknown well-formed provider entries.
        custom = manager_again.model_routes()
        custom["providers"]["future-vendor"] = {
            "model_opus": "future/model@3+pro",
            "reasoning_opus": "max",
            "model_sonnet": "future/model@3+chat",
            "reasoning_sonnet": "high",
            "model_haiku": "future-3-flash",
            "reasoning_haiku": "auto",
        }
        module.atomic_write(paths.model_routes, json.dumps(custom) + "\n")
        merged = manager_again.model_routes()
        assert merged["providers"]["future-vendor"]["model_opus"] == "future/model@3+pro"
        assert set(module.API_PROVIDERS).issubset(merged["providers"])

        try:
            manager.update_model_routes("glm", main="bad model with spaces")
        except module.FinalKitError as exc:
            assert "model ID" in str(exc)
        else:
            raise AssertionError("invalid model ID was accepted")
        malformed = manager.model_routes()
        del malformed["providers"]["deepseek"]["model_opus"]
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
        assert json.loads(shown.stdout)["providers"]["future-vendor"]["model_opus"] == "future/model@3+pro"
        cli_before = paths.model_routes.read_bytes()
        cli_preview = subprocess.run(
            [
                *cli,
                "update-models",
                "kimi",
                "--sonnet",
                "kimi-k3[1m]",
                "--reasoning-sonnet",
                "high",
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
        assert json.loads(cli_preview.stdout)["routes"]["providers"]["kimi"]["model_sonnet"] == "kimi-k3[1m]"
        assert json.loads(cli_preview.stdout)["routes"]["providers"]["kimi"]["reasoning_sonnet"] == "high"
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
            [*cli, "update-models", "kimi", "--opus", "bad model"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert cli_invalid.returncode == 1
        assert "ERROR:" in cli_invalid.stderr

    print("MODEL_ROUTES_CONTRACT_OK redirect=fail-closed signals=rollback-safe")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: model_routes_contract.py /path/to/switch_manager.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
