#!/usr/bin/env python3
"""Transactional runtime owner for ScienceCodexFinalKit.

The manager keeps one Claude Science data directory and exactly one loopback
backend.  A switch stops Science, replaces the verified backend process,
restarts Science with the matching endpoint, and restores the prior runtime if
any step fails.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path.home() / ".local" / "share" / "science-codex-finalkit"
DEFAULT_GATEWAY_PORT = 9876
DEFAULT_SCIENCE_PORT = 8765
SCIENCE_START_READY_SECONDS = 45.0
NO_PROXY_LOOPBACK = "127.0.0.1,localhost,::1"
LINUX_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCIENCE_LOCAL_ACCOUNT_UUID = "00000000-0000-4000-8000-000000000001"
SCIENCE_LOCAL_ORG_UUID = "00000000-0000-4000-8000-000000000002"
SCIENCE_LOCAL_SESSION_TOKEN = "sk-ant-finalkit-local-session"
API_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "upstream": "https://api.deepseek.com/anthropic",
        "catalog": "https://api.deepseek.com/models",
        "catalog_source": "https://api-docs.deepseek.com/api/list-models",
        "default_model": "deepseek-v4-pro",
        "fast_model": "deepseek-v4-flash",
        "env_prefix": "FINALKIT_DEEPSEEK",
        "profile_id": "deepseek-official-anthropic",
    },
    "kimi": {
        "label": "Kimi",
        "upstream": "https://api.moonshot.ai/anthropic",
        "catalog": "https://api.moonshot.ai/v1/models",
        "catalog_source": "https://platform.moonshot.ai/docs",
        "default_model": "kimi-k3[1m]",
        "fast_model": "kimi-k2.6",
        "env_prefix": "FINALKIT_KIMI",
        "profile_id": "kimi-official-anthropic",
    },
    "glm": {
        "label": "GLM",
        "upstream": "https://open.bigmodel.cn/api/anthropic",
        "catalog": "https://open.bigmodel.cn/api/paas/v4/models",
        "catalog_source": "https://docs.bigmodel.cn/cn/guide/start/model-overview",
        "default_model": "glm-5.2",
        "fast_model": "glm-4.7-flash",
        "env_prefix": "FINALKIT_GLM",
        "profile_id": "glm-official-anthropic",
    },
}
VALID_MODES = set(API_PROVIDERS) | {"codex"}
CODEX_FILE_AUTH_ARGS = ("-c", 'cli_auth_credentials_store="file"')
CODEX_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max", "ultra"}
CLAUDE_ROUTE_ROLES = ("opus", "sonnet", "haiku")
PROVIDER_REASONING_EFFORTS = {
    # ``auto`` preserves the upstream default; ``none`` explicitly disables
    # thinking. The remaining values are the distinct provider-level controls
    # documented for each Anthropic-compatible endpoint.
    "deepseek": ("auto", "none", "high", "max"),
    "kimi": ("auto", "none", "low", "high", "max"),
    "glm": ("auto", "none", "high", "max"),
}
GENERIC_REASONING_EFFORTS = {"auto", "none", "low", "medium", "high", "xhigh", "max", "ultra"}
MAX_CODEX_AUTH_BYTES = 1024 * 1024
MODEL_ROUTE_SCHEMA_VERSION = 2
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]@+=-]{0,199}$")
RUNTIME_CAPABILITIES = (
    "browser-codex-oauth",
    "codex-device-oauth",
    "codex-route-display-names",
    "codex-three-tier-route",
    "codex-tier-test",
    "effective-route-output",
    "model-route-update",
    "persistent-model-routes",
    "per-role-provider-routes",
    "per-role-reasoning",
    "provider-model-discovery",
    "runtime-update-v1",
    "science-linux-only-runtime",
    "native-provider-client",
    "science-isolated-local-identity",
    "science-local-session-admission",
    "shared-official-codex-auth",
    "stdin-codex-auth-import",
)


class FinalKitError(RuntimeError):
    pass


class Paths:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.runtime = self.root / "runtime"
        self.bridge = self.root / "bridge"
        self.config = self.root / "config"
        self.bridge_python = self.bridge / ".venv" / "bin" / "python"
        self.bridge_config = self.bridge / "config.json"
        self.model_routes = self.config / "model-routes.json"
        # Provider clients and official Codex OAuth live outside Science's
        # data HOME. The connector reads this one official cache directly.
        self.client_home = Path.home() / ".finalkit-client"
        # Claude Science creates nested AF_UNIX sockets.  Keep this path short
        # enough for Linux's 108-byte sun_path limit while retaining isolation.
        self.science_home = Path.home() / ".science-finalkit"
        # The official Linux Codex CLI owns the only ChatGPT credential cache.
        # The connector reads and refreshes this same file instead of keeping a
        # second refresh-token chain that can drift or invalidate the first.
        self.codex_auth = self.client_home / ".codex" / "auth.json"
        self.data_dir = self.science_home / ".claude-science"
        self.run = self.root / "run"
        self.logs = self.root / "logs"
        self.secrets = self.root / "secrets"
        self.profiles = self.root / "profiles"
        self.lock = self.run / "switch.lock"
        self.gateway_record = self.run / "gateway.json"
        self.mode = self.run / "current-mode"
        self.gateway_log = self.logs / "gateway.log"
        self.science_boot_log = self.logs / "science-bootstrap.log"
        self.provider_keys = {name: self.secrets / f"{name}.key" for name in API_PROVIDERS}
        self.path_secret = self.secrets / "gateway-path.token"
        self.control_token = self.secrets / "connector-control.token"
        self.instance_id = self.root / "instance.id"
        self.bridge_commit = self.root / "bridge.commit"
        self.versions = self.root / "versions.txt"
        self.direct_gateway = self.runtime / "direct_gateway.py"
        self.science_identity = self.runtime / "science_identity.py"
        self.science = Path.home() / ".local" / "bin" / "claude-science"
        self.claude = Path.home() / ".local" / "bin" / "claude"
        self.codex = Path.home() / ".local" / "bin" / "codex"

    def ensure_private_tree(self) -> None:
        for directory in (
            self.root,
            self.runtime,
            self.config,
            self.client_home,
            self.science_home,
            self.run,
            self.logs,
            self.secrets,
            self.profiles,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)


def atomic_write(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_bytes(path: Path, value: bytes | bytearray, mode: int = 0o600) -> None:
    """Atomically replace one private file without changing its exact bytes."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_nonempty(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FinalKitError(f"{label} is missing: {path}") from exc
    if not value:
        raise FinalKitError(f"{label} is empty: {path}")
    return value


def append_no_proxy(environment: dict[str, str]) -> None:
    for name in ("NO_PROXY", "no_proxy"):
        existing = environment.get(name, "")
        values = [item.strip() for item in existing.split(",") if item.strip()]
        for loopback in NO_PROXY_LOOPBACK.split(","):
            if loopback not in values:
                values.append(loopback)
        environment[name] = ",".join(values)


def private_child_environment(home: Path) -> dict[str, str]:
    keep_exact = {
        "PATH",
        "LANG",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "WSL_DISTRO_NAME",
        "WSL_INTEROP",
        "WSLENV",
        "BROWSER",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in keep_exact or key.startswith("LC_")
    }
    environment["HOME"] = str(home)
    environment["PYTHONUNBUFFERED"] = "1"
    append_no_proxy(environment)
    return environment


def science_child_environment(home: Path) -> dict[str, str]:
    """Return a Linux-only environment for the long-lived Science daemon.

    WSL imports the Windows PATH and caller PWD by default.  Claude Science
    inspects available tools during MCP warm-up, so retaining `/mnt/c` or
    `/mnt/d` entries can block the daemon in DrvFS/9p and take its control
    socket down.  Browser opening is performed by the Windows wrapper, so the
    daemon does not need Windows executables in PATH.
    """

    environment = private_child_environment(home)
    environment["PATH"] = LINUX_SYSTEM_PATH
    environment["PWD"] = str(home)
    environment.pop("OLDPWD", None)
    environment.pop("BROWSER", None)
    environment.pop("WSLENV", None)
    return environment


PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def endpoint_reachable(url: str, environment: dict[str, str]) -> bool:
    """Return whether an HTTPS endpoint is reachable without following redirects."""

    try:
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--connect-timeout",
                "6",
                "--max-time",
                "15",
                "--write-out",
                "%{http_code}",
                url,
            ],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    try:
        status = int(result.stdout.strip())
    except ValueError:
        return False
    # 401/403 prove TLS reachability for protected backends.  3xx is normal
    # for the browser-facing device page.  5xx is not a usable preflight.
    return 200 <= status < 500


def codex_network_environment(home: Path, url: str) -> dict[str, str]:
    """Select inherited proxy or direct networking for only the Codex child.

    A Windows loopback proxy can be visible inside mirrored WSL networking yet
    fail after CONNECT during TLS.  Prefer the user's environment when it
    works; otherwise use a direct child environment only when direct HTTPS has
    been positively verified.  Device-auth POST and browser GET can be treated
    differently by proxies and upstream edge policy.  If the user explicitly
    configured a proxy, preserve it for the official Codex CLI rather than
    overriding it from a different request shape.
    """

    inherited = private_child_environment(home)
    direct = inherited.copy()
    had_proxy = any(direct.get(name) for name in PROXY_VARIABLES)
    for name in PROXY_VARIABLES:
        direct.pop(name, None)

    is_device_api = url.rstrip("/").endswith("/api/accounts/deviceauth/usercode")
    if is_device_api:
        if had_proxy:
            return inherited
        if endpoint_reachable("https://auth.openai.com/codex/device", direct):
            return direct
        raise FinalKitError(
            "Codex device login cannot reach auth.openai.com from WSL. Configure a working "
            "proxy/VPN or restore direct WSL HTTPS, then retry."
        )
    if had_proxy:
        return inherited
    if endpoint_reachable(url, inherited):
        return inherited
    raise FinalKitError(
        "Codex HTTPS preflight failed through both the inherited proxy and direct WSL networking. "
        "Check the Windows proxy/VPN, WSL networking, and access to auth.openai.com/chatgpt.com."
    )


def pipe_with(data: bytes) -> int:
    read_fd, write_fd = os.pipe()
    try:
        view = memoryview(data)
        while view:
            written = os.write(write_fd, view)
            view = view[written:]
    finally:
        os.close(write_fd)
    return read_fd


def process_start_ticks(pid: int) -> int | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        _, separator, rest = value.rpartition(")")
        if not separator:
            return None
        fields = rest.strip().split()
        return int(fields[19])
    except (FileNotFoundError, PermissionError, ValueError, IndexError):
        return None


def process_state(pid: int) -> str | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        _, separator, rest = value.rpartition(")")
        if not separator:
            return None
        fields = rest.strip().split()
        return fields[0] if fields else None
    except (FileNotFoundError, PermissionError):
        return None


def process_is_live(pid: int) -> bool:
    return process_start_ticks(pid) is not None and process_state(pid) != "Z"


def reap_child(pid: int) -> None:
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def process_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except (FileNotFoundError, PermissionError):
        return ""


def process_cmdline_parts(pid: int) -> list[str]:
    try:
        return [
            value.decode("utf-8", errors="replace")
            for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if value
        ]
    except (FileNotFoundError, PermissionError):
        return []


def process_environment(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError):
        return {}
    result: dict[str, str] = {}
    for entry in raw:
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        result[key.decode(errors="replace")] = value.decode(errors="replace")
    return result


class FileLock:
    def __init__(self, path: Path, timeout: float = 15.0):
        self.path = path
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        if self.path.exists() and self.path.is_symlink():
            raise FinalKitError(f"refusing symlink lock path: {self.path}")
        self.handle = open(self.path, "a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise FinalKitError("another FinalKit operation still holds the switch lock")
                time.sleep(0.1)
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()}\n")
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


class RuntimeManager:
    def __init__(self, paths: Paths):
        self.p = paths
        self.p.ensure_private_tree()
        self.gateway_port = int(os.environ.get("FINALKIT_GATEWAY_PORT", DEFAULT_GATEWAY_PORT))
        self.science_port = int(os.environ.get("FINALKIT_SCIENCE_PORT", DEFAULT_SCIENCE_PORT))

    def require_runtime(self) -> None:
        required = (
            (self.p.science, "Claude Science"),
            (self.p.claude, "Claude Code"),
            (self.p.codex, "Linux Codex CLI"),
            (self.p.direct_gateway, "direct gateway"),
            (self.p.science_identity, "Science local identity helper"),
            (self.p.bridge_python, "connector Python"),
            (self.p.bridge / "proxy.py", "connector proxy"),
        )
        for path, label in required:
            if not path.is_file():
                raise FinalKitError(f"{label} is missing: {path}")

    def instance(self) -> str:
        return read_nonempty(self.p.instance_id, "instance id")

    def current_mode(self) -> str | None:
        try:
            mode = self.p.mode.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return mode if mode in VALID_MODES else None

    def write_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            raise FinalKitError(f"invalid mode: {mode}")
        atomic_write(self.p.mode, mode + "\n")

    @staticmethod
    def default_model_routes() -> dict[str, Any]:
        providers: dict[str, dict[str, str]] = {}
        for name, provider in API_PROVIDERS.items():
            providers[name] = {
                "model_opus": str(provider["default_model"]),
                "reasoning_opus": "auto",
                "model_sonnet": str(provider["default_model"]),
                "reasoning_sonnet": "auto",
                "model_haiku": str(provider["fast_model"]),
                "reasoning_haiku": "auto",
            }
        return {
            "schema_version": MODEL_ROUTE_SCHEMA_VERSION,
            "providers": providers,
            "codex": {
                "model_opus": "gpt-5.6-sol",
                "reasoning_opus": "max",
                "model_sonnet": "gpt-5.6-terra",
                "reasoning_sonnet": "max",
                "model_haiku": "gpt-5.6-luna",
                "reasoning_haiku": "max",
            },
        }

    @staticmethod
    def validate_model_id(value: Any, label: str) -> str:
        if not isinstance(value, str):
            raise FinalKitError(f"{label} must be a string model ID")
        model = value.strip()
        if not MODEL_ID_PATTERN.fullmatch(model):
            raise FinalKitError(
                f"{label} must be a 1-200 character model ID containing only letters, digits, . _ : / [ ] @ + = or -"
            )
        return model

    @staticmethod
    def validate_reasoning_effort(value: Any, label: str, allowed: set[str]) -> str:
        if not isinstance(value, str):
            raise FinalKitError(f"{label} must be a string")
        effort = value.strip().lower()
        if effort not in allowed:
            raise FinalKitError(f"{label} must be one of: {', '.join(sorted(allowed))}")
        return effort

    def normalize_role_route(
        self,
        route: Any,
        label: str,
        allowed_efforts: set[str],
    ) -> dict[str, str]:
        if not isinstance(route, dict):
            raise FinalKitError(f"model route for {label} must be an object")
        normalized: dict[str, str] = {}
        for role in CLAUDE_ROUTE_ROLES:
            normalized[f"model_{role}"] = self.validate_model_id(
                route.get(f"model_{role}"), f"{label} {role} model"
            )
            normalized[f"reasoning_{role}"] = self.validate_reasoning_effort(
                route.get(f"reasoning_{role}"),
                f"{label} {role} reasoning",
                allowed_efforts,
            )
        return normalized

    def migrate_model_routes_payload(self, payload: Any) -> dict[str, Any]:
        """Migrate the former main/fast/shared-effort shape without losing choices."""

        if not isinstance(payload, dict):
            raise FinalKitError("model route config must be a JSON object")
        schema = payload.get("schema_version")
        if schema == MODEL_ROUTE_SCHEMA_VERSION:
            # An early 3.2.3 preview used the longer reasoning_effort_* names.
            # Accept it once and rewrite to the compact model/reasoning schema.
            compact = json.loads(json.dumps(payload))
            route_objects = []
            providers = compact.get("providers")
            if isinstance(providers, dict):
                route_objects.extend(route for route in providers.values() if isinstance(route, dict))
            if isinstance(compact.get("codex"), dict):
                route_objects.append(compact["codex"])
            for route in route_objects:
                for role in CLAUDE_ROUTE_ROLES:
                    model_name = f"model_{role}"
                    if model_name not in route and role in route:
                        route[model_name] = route[role]
                    route.pop(role, None)
                    long_name = f"reasoning_effort_{role}"
                    short_name = f"reasoning_{role}"
                    if short_name not in route and long_name in route:
                        route[short_name] = route[long_name]
                    route.pop(long_name, None)
            return self.validate_model_routes(compact)
        if schema != 1:
            raise FinalKitError(
                f"model route schema must be 1 or {MODEL_ROUTE_SCHEMA_VERSION}; found {schema!r}"
            )
        providers = payload.get("providers")
        codex = payload.get("codex")
        if not isinstance(providers, dict) or not isinstance(codex, dict):
            raise FinalKitError("model route config requires providers and codex objects")
        migrated = self.default_model_routes()
        for name, old_route in providers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise FinalKitError(f"invalid provider route name: {name!r}")
            if not isinstance(old_route, dict):
                raise FinalKitError(f"model route for {name} must be an object")
            main = old_route.get("main")
            fast = old_route.get("fast")
            shared_effort = old_route.get("reasoning_effort", "auto")
            migrated["providers"][name] = {
                "model_opus": main,
                "reasoning_opus": shared_effort,
                "model_sonnet": main,
                "reasoning_sonnet": shared_effort,
                "model_haiku": fast,
                "reasoning_haiku": shared_effort,
            }
        shared_codex_effort = codex.get("reasoning_effort", "max")
        migrated["codex"] = {
            "model_opus": codex.get("opus"),
            "reasoning_opus": shared_codex_effort,
            "model_sonnet": codex.get("sonnet"),
            "reasoning_sonnet": shared_codex_effort,
            "model_haiku": codex.get("haiku"),
            "reasoning_haiku": shared_codex_effort,
        }
        return self.validate_model_routes(migrated)

    def validate_model_routes(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise FinalKitError("model route config must be a JSON object")
        if payload.get("schema_version") != MODEL_ROUTE_SCHEMA_VERSION:
            raise FinalKitError(
                f"model route schema must be {MODEL_ROUTE_SCHEMA_VERSION}; found {payload.get('schema_version')!r}"
            )
        providers = payload.get("providers")
        codex = payload.get("codex")
        if not isinstance(providers, dict) or not isinstance(codex, dict):
            raise FinalKitError("model route config requires providers and codex objects")
        normalized = self.default_model_routes()
        # Preserve well-formed future provider entries even when an older
        # runtime cannot start them yet.  Newly added built-in providers are
        # filled from package defaults without replacing existing choices.
        for name, route in providers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise FinalKitError(f"invalid provider route name: {name!r}")
            allowed = set(PROVIDER_REASONING_EFFORTS.get(name, tuple(GENERIC_REASONING_EFFORTS)))
            normalized["providers"][name] = self.normalize_role_route(
                route, name, allowed
            )
        normalized["codex"] = self.normalize_role_route(
            codex, "Codex", set(CODEX_REASONING_EFFORTS)
        )
        return normalized

    def legacy_model_routes(self) -> dict[str, Any]:
        """Migrate only user-visible legacy choices; never read or copy credentials."""

        routes = self.default_model_routes()
        try:
            legacy = json.loads(self.p.bridge_config.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            legacy = {}
        if isinstance(legacy, dict):
            model_map = legacy.get("codex_model_map")
            if isinstance(model_map, dict):
                family_values = {
                    "opus": model_map.get("claude-opus"),
                    "sonnet": model_map.get("claude-sonnet"),
                    "haiku": model_map.get("claude-haiku"),
                }
                exact_values = {
                    "opus": model_map.get("claude-opus-4-8"),
                    "sonnet": model_map.get("claude-sonnet-4-5"),
                    "haiku": model_map.get("claude-haiku-4-5"),
                }
                known_broken_defaults = {
                    "opus": "gpt-5.6-sol",
                    "sonnet": "gpt-5.6-sol",
                    "haiku": "gpt-5.6-terra",
                }
                if any(family_values.values()):
                    for tier, value in family_values.items():
                        if value:
                            routes["codex"][f"model_{tier}"] = str(value)
                elif exact_values != known_broken_defaults:
                    for tier, value in exact_values.items():
                        if value:
                            routes["codex"][f"model_{tier}"] = str(value)
            legacy_primary = legacy.get("codex_model")
            if (
                isinstance(legacy_primary, str)
                and legacy_primary
                and legacy_primary != "gpt-5.6-sol"
            ):
                routes["codex"]["model_opus"] = legacy_primary
            effort = legacy.get("codex_reasoning_effort")
            if isinstance(effort, str) and effort.strip().lower() in CODEX_REASONING_EFFORTS:
                for role in CLAUDE_ROUTE_ROLES:
                    routes["codex"][f"reasoning_{role}"] = effort.strip().lower()
        # Environment values are a one-time migration source.  Once the file
        # exists, subsequent package updates never overwrite the user's routes.
        for name, provider in API_PROVIDERS.items():
            prefix = str(provider["env_prefix"])
            legacy_main = os.environ.get(f"{prefix}_MODEL")
            legacy_fast = os.environ.get(f"{prefix}_FAST_MODEL")
            legacy_effort = os.environ.get(f"{prefix}_REASONING_EFFORT")
            for role in CLAUDE_ROUTE_ROLES:
                model_fallback = legacy_fast if role == "haiku" else legacy_main
                routes["providers"][name][f"model_{role}"] = os.environ.get(
                    f"{prefix}_{role.upper()}_MODEL",
                    model_fallback or routes["providers"][name][f"model_{role}"],
                )
                routes["providers"][name][f"reasoning_{role}"] = os.environ.get(
                    f"{prefix}_{role.upper()}_REASONING_EFFORT",
                    legacy_effort
                    or routes["providers"][name][f"reasoning_{role}"],
                )
        routes["codex"]["model_opus"] = os.environ.get(
            "FINALKIT_CODEX_OPUS_MODEL",
            os.environ.get("FINALKIT_CODEX_MODEL", routes["codex"]["model_opus"]),
        )
        routes["codex"]["model_sonnet"] = os.environ.get(
            "FINALKIT_CODEX_SONNET_MODEL", routes["codex"]["model_sonnet"]
        )
        routes["codex"]["model_haiku"] = os.environ.get(
            "FINALKIT_CODEX_HAIKU_MODEL",
            os.environ.get("FINALKIT_CODEX_FAST_MODEL", routes["codex"]["model_haiku"]),
        )
        shared_codex_effort = os.environ.get("FINALKIT_CODEX_REASONING_EFFORT")
        for role in CLAUDE_ROUTE_ROLES:
            routes["codex"][f"reasoning_{role}"] = os.environ.get(
                f"FINALKIT_CODEX_{role.upper()}_REASONING_EFFORT",
                shared_codex_effort or routes["codex"][f"reasoning_{role}"],
            )
        return self.validate_model_routes(routes)

    def ensure_model_routes(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.p.model_routes.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = self.legacy_model_routes()
            atomic_write(
                self.p.model_routes,
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            )
            return payload
        except (json.JSONDecodeError, OSError) as exc:
            raise FinalKitError(f"model route config is unreadable: {self.p.model_routes}") from exc
        normalized = self.migrate_model_routes_payload(payload)
        if normalized != payload:
            atomic_write(
                self.p.model_routes,
                json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
            )
        return normalized

    def model_routes(self) -> dict[str, Any]:
        return self.ensure_model_routes()

    def model_routes_read_only(self) -> dict[str, Any]:
        """Read current routes without migration, normalization, or filesystem writes."""

        try:
            payload = json.loads(self.p.model_routes.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.default_model_routes()
        except (json.JSONDecodeError, OSError) as exc:
            raise FinalKitError(f"model route config is unreadable: {self.p.model_routes}") from exc
        return self.migrate_model_routes_payload(payload)

    def fetch_provider_model_payload(self, provider: str, api_key: str) -> Any:
        """Read one fixed official catalog endpoint without generating tokens.

        Catalog URLs are package-owned constants rather than user input, so a
        saved provider key cannot be redirected to an arbitrary host.  Try a
        direct connection first, then the inherited proxy only for a transport
        failure; HTTP authentication/permission failures are final.
        """

        definition = API_PROVIDERS[provider]
        request = urllib.request.Request(
            str(definition["catalog"]),
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "ScienceCodexFinalKit/provider-model-discovery",
            },
        )
        transport_errors: list[str] = []
        openers = (
            ("direct network", urllib.request.build_opener(urllib.request.ProxyHandler({}))),
            ("configured network", urllib.request.build_opener()),
        )
        for label, opener in openers:
            try:
                with opener.open(request, timeout=20) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise FinalKitError(
                    f"{definition['label']} official model catalog returned HTTP {exc.code}; "
                    "check that this Linux user's API key is valid and allowed to list models"
                ) from exc
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
                transport_errors.append(f"{label}: {type(exc).__name__}")
        raise FinalKitError(
            f"{definition['label']} official model catalog could not be reached "
            f"({'; '.join(transport_errors)})"
        )

    def discover_provider_models(self, provider: str, *, limit: int = 100) -> dict[str, Any]:
        if provider not in API_PROVIDERS:
            raise FinalKitError("official catalog discovery supports deepseek, kimi, or glm")
        if not 1 <= limit <= 500:
            raise FinalKitError("model discovery limit must be between 1 and 500")
        api_key = read_nonempty(
            self.p.provider_keys[provider],
            f"{API_PROVIDERS[provider]['label']} API key; run fkctl configure-{provider}",
        )
        try:
            payload = self.fetch_provider_model_payload(provider, api_key)
        finally:
            api_key = ""
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise FinalKitError("official model catalog returned an unexpected JSON shape")
        discovered: set[str] = set()
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            value = item.get("id")
            try:
                discovered.add(self.validate_model_id(value, "catalog model"))
            except FinalKitError:
                continue
        all_models = sorted(discovered, key=str.casefold)
        if not all_models:
            raise FinalKitError("official model catalog returned no usable model IDs")
        current = dict(self.model_routes_read_only()["providers"][provider])
        shown = all_models[:limit]
        return {
            "ok": True,
            "provider": provider,
            "label": str(API_PROVIDERS[provider]["label"]),
            "catalog_url": str(API_PROVIDERS[provider]["catalog"]),
            "catalog_source": str(API_PROVIDERS[provider]["catalog_source"]),
            "discovered_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "current": current,
            "current_availability": {
                role: current[f"model_{role}"] in discovered for role in CLAUDE_ROUTE_ROLES
            },
            "models": shown,
            "total_models": len(all_models),
            "truncated": len(all_models) > len(shown),
            "writes_performed": False,
            "generation_request_performed": False,
        }

    def codex_local_model_catalog(self) -> list[dict[str, Any]]:
        """Read model/reasoning capabilities owned by this Linux Codex CLI."""

        cache_path = self.p.codex_auth.parent / "models_cache.json"
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeError):
            return []
        raw_models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            return []
        catalog: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_models:
            if not isinstance(item, dict) or str(item.get("visibility", "")) == "hide":
                continue
            try:
                slug = self.validate_model_id(item.get("slug"), "Codex cache model")
            except FinalKitError:
                continue
            if slug in seen:
                continue
            seen.add(slug)
            levels: list[dict[str, str]] = []
            level_names: set[str] = set()
            raw_levels = item.get("supported_reasoning_levels")
            if isinstance(raw_levels, list):
                for raw_level in raw_levels:
                    if isinstance(raw_level, str):
                        effort = raw_level
                        description = ""
                    elif isinstance(raw_level, dict):
                        effort = str(raw_level.get("effort") or "")
                        description = re.sub(r"[\r\n]+", " ", str(raw_level.get("description") or "")).strip()
                    else:
                        continue
                    effort = effort.strip().lower()
                    if effort not in CODEX_REASONING_EFFORTS or effort in level_names:
                        continue
                    level_names.add(effort)
                    levels.append({"reasoning": effort, "description": description[:500]})
            default_reasoning = str(item.get("default_reasoning_level") or "").strip().lower()
            if default_reasoning not in level_names:
                default_reasoning = levels[0]["reasoning"] if levels else ""
            catalog.append(
                {
                    "model": slug,
                    "reasoning": levels,
                    "default_reasoning": default_reasoning,
                }
            )
        return catalog

    @staticmethod
    def _catalog_entry(catalog: list[dict[str, Any]], model: str) -> dict[str, Any] | None:
        return next((entry for entry in catalog if entry.get("model") == model), None)

    @staticmethod
    def _prompt_compact(label: str, seed: str) -> str:
        value = input(f"{label} [{seed}]: ").strip()
        return value or seed

    def configure_model_routes_interactive(self, provider: str) -> dict[str, Any]:
        """Prompt for three independent Model/Reasoning pairs using compact labels."""

        if provider not in VALID_MODES:
            raise FinalKitError("provider must be deepseek, kimi, glm, or codex")
        routes = self.model_routes_read_only()
        target = routes["codex"] if provider == "codex" else routes["providers"][provider]
        available: list[str] = []
        codex_catalog: list[dict[str, Any]] = []
        if provider == "codex":
            codex_catalog = self.codex_local_model_catalog()
            available = [str(entry["model"]) for entry in codex_catalog]
            if available:
                print("Models: " + ", ".join(available))
                for entry in codex_catalog:
                    choices = ", ".join(
                        str(level["reasoning"]) for level in entry.get("reasoning", [])
                    ) or "cache did not declare"
                    print(f"  {entry['model']}: Reasoning={choices}")
            else:
                print("Models: local Codex cache unavailable; packaged routes remain editable.")
        else:
            try:
                discovery = self.discover_provider_models(provider, limit=500)
                available = [str(value) for value in discovery["models"]]
                print("Models: " + ", ".join(available))
            except FinalKitError as exc:
                print(f"Models: official catalog unavailable ({exc}); current/package values remain editable.")
            print("Reasoning: " + ", ".join(PROVIDER_REASONING_EFFORTS[provider]))

        selected_models: dict[str, str] = {}
        selected_reasoning: dict[str, str] = {}
        print("Configure each Claude tier (only Model and Reasoning are stored):")
        for role in CLAUDE_ROUTE_ROLES:
            role_label = role.title()
            model = self._prompt_compact(
                f"{role_label} Model", str(target[f"model_{role}"])
            )
            model = self.validate_model_id(model, f"{role_label} Model")
            if available and model not in available:
                raise FinalKitError(
                    f"{role_label} Model {model!r} is not in the current account catalog"
                )
            selected_models[role] = model

            current_reasoning = str(target[f"reasoning_{role}"])
            if provider == "codex":
                entry = self._catalog_entry(codex_catalog, model)
                supported = {
                    str(level.get("reasoning")) for level in entry.get("reasoning", [])
                } if entry else set()
                seed = current_reasoning
                if supported and seed not in supported:
                    default_value = str(entry.get("default_reasoning") or "")
                    seed = default_value if default_value in supported else sorted(supported)[0]
                if supported:
                    print(f"{role_label} Reasoning: " + ", ".join(sorted(supported)))
                reasoning = self._prompt_compact(f"{role_label} Reasoning", seed)
                reasoning = self.validate_reasoning_effort(
                    reasoning, f"{role_label} Reasoning", set(CODEX_REASONING_EFFORTS)
                )
                if supported and reasoning not in supported:
                    raise FinalKitError(
                        f"{role_label} Model {model} does not support Reasoning={reasoning}; "
                        f"choose: {', '.join(sorted(supported))}"
                    )
            else:
                reasoning = self._prompt_compact(
                    f"{role_label} Reasoning", current_reasoning
                )
                reasoning = self.validate_reasoning_effort(
                    reasoning,
                    f"{role_label} Reasoning",
                    set(PROVIDER_REASONING_EFFORTS[provider]),
                )
            selected_reasoning[role] = reasoning

        return self.update_model_routes(
            provider,
            opus=selected_models["opus"],
            sonnet=selected_models["sonnet"],
            haiku=selected_models["haiku"],
            effort_opus=selected_reasoning["opus"],
            effort_sonnet=selected_reasoning["sonnet"],
            effort_haiku=selected_reasoning["haiku"],
        )

    def update_model_routes(
        self,
        provider: str,
        *,
        main: str | None = None,
        fast: str | None = None,
        opus: str | None = None,
        sonnet: str | None = None,
        haiku: str | None = None,
        effort: str | None = None,
        effort_opus: str | None = None,
        effort_sonnet: str | None = None,
        effort_haiku: str | None = None,
        dry_run: bool = False,
        restart: bool = False,
    ) -> dict[str, Any]:
        if provider not in VALID_MODES:
            raise FinalKitError("provider must be deepseek, kimi, glm, or codex")
        routes = self.model_routes()
        before = json.loads(json.dumps(routes))
        # Keep --main/--fast/--effort as backwards-compatible shorthands while
        # making every persisted route independent. Explicit role values win
        # only when they agree with a supplied shorthand.
        if main is not None:
            if opus is not None and opus != main:
                raise FinalKitError("--main conflicts with --opus")
            if sonnet is not None and sonnet != main:
                raise FinalKitError("--main conflicts with --sonnet")
            opus = opus or main
            sonnet = sonnet or main
        if fast is not None:
            if haiku is not None and haiku != fast:
                raise FinalKitError("--fast conflicts with --haiku")
            haiku = haiku or fast
        role_efforts = {
            "opus": effort_opus,
            "sonnet": effort_sonnet,
            "haiku": effort_haiku,
        }
        if effort is not None:
            for role, role_effort in role_efforts.items():
                if role_effort is not None and role_effort.lower() != effort.lower():
                    raise FinalKitError(f"--reasoning conflicts with --reasoning-{role}")
                role_efforts[role] = role_effort or effort
        values = (opus, sonnet, haiku, *role_efforts.values())
        if all(value is None for value in values):
            raise FinalKitError(
                "provide at least one role Model or Reasoning value"
            )
        if provider in API_PROVIDERS:
            target = routes["providers"][provider]
            allowed_efforts = set(PROVIDER_REASONING_EFFORTS[provider])
            label = API_PROVIDERS[provider]["label"]
        else:
            target = routes["codex"]
            allowed_efforts = set(CODEX_REASONING_EFFORTS)
            label = "Codex"
        for role, model in (("opus", opus), ("sonnet", sonnet), ("haiku", haiku)):
            if model is not None:
                target[f"model_{role}"] = self.validate_model_id(
                    model, f"{label} {role} model"
                )
            role_effort = role_efforts[role]
            if role_effort is not None:
                target[f"reasoning_{role}"] = self.validate_reasoning_effort(
                    role_effort,
                    f"{label} {role} reasoning",
                    allowed_efforts,
                )
        routes = self.validate_model_routes(routes)
        changed = routes != before
        restarted = False
        restart_required = False
        if changed:
            record = self.read_gateway_record()
            active = bool(record and self.gateway_identity(record) and self.gateway_health(record))
            previous_backend = str(record.get("backend")) if active and record else ""
            restart_required = active and previous_backend == provider
        if changed and not dry_run:
            affected_active = restart_required
            if affected_active and not restart:
                raise FinalKitError(
                    "an active FinalKit runtime is using the old route; rerun with --restart or stop it first; no config was written"
                )
            previous_science_running = False
            if affected_active:
                science = self.science_status()
                if science.get("control_error"):
                    raise FinalKitError(self.science_recovery_message(science))
                previous_science_running = bool(science.get("running"))
            previous_routes = self.p.model_routes.read_text(encoding="utf-8")
            previous_bridge = (
                self.p.bridge_config.read_text(encoding="utf-8")
                if self.p.bridge_config.is_file()
                else None
            )
            try:
                if affected_active:
                    self.science_stop()
                    self.stop_gateway()
                atomic_write(
                    self.p.model_routes,
                    json.dumps(routes, indent=2, ensure_ascii=False) + "\n",
                )
                self.ensure_bridge_config()
                if affected_active:
                    endpoint = self.spawn_gateway(previous_backend)
                    if previous_science_running:
                        self.science_start(endpoint)
                    if not self.gateway_health():
                        raise FinalKitError("updated gateway failed its health check")
                    if previous_science_running and not self.science_endpoint_matches(endpoint):
                        raise FinalKitError("updated Claude Science endpoint identity check failed")
                    self.write_mode(previous_backend)
                    restarted = True
            except Exception as primary_error:
                rollback_errors: list[str] = []
                if affected_active:
                    try:
                        self.science_stop()
                    except Exception as exc:
                        rollback_errors.append(f"stop updated Science: {exc}")
                    try:
                        self.stop_gateway()
                    except Exception as exc:
                        rollback_errors.append(f"stop updated gateway: {exc}")
                try:
                    atomic_write(self.p.model_routes, previous_routes)
                    if previous_bridge is None:
                        self.p.bridge_config.unlink(missing_ok=True)
                    else:
                        atomic_write(self.p.bridge_config, previous_bridge)
                except Exception as exc:
                    rollback_errors.append(f"restore route files: {exc}")
                if affected_active:
                    try:
                        endpoint = self.spawn_gateway(previous_backend)
                        if previous_science_running:
                            self.science_start(endpoint)
                    except Exception as exc:
                        rollback_errors.append(f"restore {previous_backend}: {exc}")
                message = f"model route update failed: {primary_error}; previous route files were restored"
                if rollback_errors:
                    message += "; rollback incomplete: " + "; ".join(rollback_errors)
                raise FinalKitError(message) from primary_error
        return {
            "provider": provider,
            "changed": changed,
            "dry_run": dry_run,
            "restart_required": restart_required,
            "runtime_restarted": restarted,
            "routes": routes,
        }

    def read_gateway_record(self) -> dict[str, Any] | None:
        try:
            record = json.loads(self.p.gateway_record.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return record if isinstance(record, dict) else None

    def gateway_identity(self, record: dict[str, Any] | None = None) -> bool:
        record = record or self.read_gateway_record()
        if not record:
            return False
        try:
            pid = int(record["pid"])
            expected_start = int(record["start_ticks"])
            backend = str(record["backend"])
        except (KeyError, TypeError, ValueError):
            return False
        if process_start_ticks(pid) != expected_start:
            return False
        if process_state(pid) == "Z":
            return False
        command = process_cmdline(pid)
        marker = self.p.direct_gateway if backend in API_PROVIDERS else self.p.bridge / "proxy.py"
        return str(marker) in command

    def local_json(self, url: str, *, timeout: float = 2.0, payload: dict | None = None) -> dict:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = None
        method = "GET"
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            method = "POST"
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        with opener.open(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise FinalKitError("local endpoint returned non-object JSON")
        return parsed

    def verify_gateway_science_identity(self, endpoint: str) -> None:
        """Prove that a provider gateway exposes the local Science identity contract."""

        base = endpoint.rstrip("/")
        user = self.local_json(base + "/v1/userinfo")
        org = self.local_json(base + "/v1/organization")
        token = self.local_json(base + "/v1/oauth/session")
        api_user = self.local_json(base + "/api/oauth/profile")
        api_orgs = self.local_json(base + "/api/organizations")
        api_token = self.local_json(base + "/api/oauth/session")
        checks = (
            user.get("id") == SCIENCE_LOCAL_ACCOUNT_UUID,
            user.get("organization_uuid") == SCIENCE_LOCAL_ORG_UUID,
            user.get("email") == "virtual@localhost.invalid",
            org.get("id") == SCIENCE_LOCAL_ORG_UUID,
            token.get("access_token") == SCIENCE_LOCAL_SESSION_TOKEN,
            token.get("refresh_token") == "",
            api_user.get("id") == SCIENCE_LOCAL_ACCOUNT_UUID,
            api_user.get("organization_uuid") == SCIENCE_LOCAL_ORG_UUID,
            isinstance(api_orgs.get("organizations"), list),
            any(
                isinstance(item, dict) and item.get("id") == SCIENCE_LOCAL_ORG_UUID
                for item in api_orgs.get("organizations", [])
            ),
            api_token.get("access_token") == SCIENCE_LOCAL_SESSION_TOKEN,
            api_token.get("refresh_token") == "",
        )
        if not all(checks):
            raise FinalKitError("gateway local Science identity contract verification failed")

    def gateway_health(self, record: dict[str, Any] | None = None) -> dict[str, Any] | None:
        record = record or self.read_gateway_record()
        if not record:
            return None
        try:
            health = self.local_json(str(record["endpoint"]).rstrip("/") + "/health")
        except (KeyError, OSError, ValueError, urllib.error.URLError, FinalKitError):
            return None
        if (
            health.get("status") != "ok"
            or health.get("finalkit_instance") != self.instance()
            or health.get("finalkit_backend") != record.get("backend")
        ):
            return None
        if record.get("backend") == "codex" and health.get("control_protected") is not True:
            return None
        return health

    def port_in_use(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            return probe.connect_ex(("127.0.0.1", self.gateway_port)) == 0

    def tail_gateway_log(self, lines: int = 80) -> str:
        try:
            content = self.p.gateway_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return ""
        return "\n".join(content[-lines:])

    def spawn_gateway(self, mode: str, *, key_override: str | None = None) -> str:
        self.require_runtime()
        if mode not in VALID_MODES:
            raise FinalKitError("mode must be deepseek, kimi, glm, or codex")

        existing = self.read_gateway_record()
        if (
            existing
            and existing.get("backend") == mode
            and self.gateway_identity(existing)
            and self.gateway_health(existing)
        ):
            return str(existing["endpoint"])
        if existing and self.gateway_identity(existing):
            raise FinalKitError("a different verified FinalKit gateway is still running")
        if self.port_in_use():
            raise FinalKitError(
                f"127.0.0.1:{self.gateway_port} is owned by another process; FinalKit will not take it over"
            )
        self.p.gateway_record.unlink(missing_ok=True)

        instance = self.instance()
        log_handle = open(self.p.gateway_log, "a", encoding="utf-8")
        os.chmod(self.p.gateway_log, 0o600)
        inherited_fds: list[int] = []
        try:
            if mode in API_PROVIDERS:
                provider = API_PROVIDERS[mode]
                route = self.model_routes()["providers"][mode]
                path_secret = read_nonempty(self.p.path_secret, "gateway path secret")
                api_key = key_override or read_nonempty(
                    self.p.provider_keys[mode], f"{provider['label']} API key"
                )
                config = {
                    "host": "127.0.0.1",
                    "port": self.gateway_port,
                    "path_secret": path_secret,
                    "provider": mode,
                    "upstream": provider["upstream"],
                    "instance_id": instance,
                    "profile_id": provider["profile_id"],
                    "offline_smoke": key_override is not None,
                }
                for role in CLAUDE_ROUTE_ROLES:
                    config[f"model_{role}"] = route[f"model_{role}"]
                    config[f"reasoning_{role}"] = route[f"reasoning_{role}"]
                config_fd = pipe_with(json.dumps(config, separators=(",", ":")).encode("utf-8"))
                key_fd = pipe_with(api_key.encode("utf-8"))
                inherited_fds.extend((config_fd, key_fd))
                command = [
                    sys.executable,
                    str(self.p.direct_gateway),
                    "--config-fd",
                    str(config_fd),
                    "--key-fd",
                    str(key_fd),
                ]
                environment = private_child_environment(self.p.client_home)
                endpoint = f"http://127.0.0.1:{self.gateway_port}/{path_secret}"
                cwd = self.p.runtime
            else:
                if not self.codex_auth_configured():
                    raise FinalKitError("ChatGPT Codex auth is not configured; run: fkctl configure-codex")
                self.ensure_bridge_config()
                control = read_nonempty(self.p.control_token, "connector control token")
                control_fd = pipe_with(control.encode("utf-8"))
                instance_fd = pipe_with(instance.encode("utf-8"))
                inherited_fds.extend((control_fd, instance_fd))
                command = [str(self.p.bridge_python), str(self.p.bridge / "proxy.py")]
                environment = codex_network_environment(
                    self.p.client_home, "https://chatgpt.com/backend-api/codex"
                )
                environment.update(
                    {
                        "CLAUDE_SCIENCE_PROXY_DIR": str(self.p.bridge),
                        "PROXY_HOST": "127.0.0.1",
                        "PROXY_PORT": str(self.gateway_port),
                        "FINALKIT_CONTROL_FD": str(control_fd),
                        "FINALKIT_INSTANCE_FD": str(instance_fd),
                        "FINALKIT_BACKEND": "codex",
                    }
                )
                endpoint = f"http://127.0.0.1:{self.gateway_port}"
                cwd = self.p.bridge

            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                pass_fds=tuple(inherited_fds),
            )
        finally:
            log_handle.close()
            for descriptor in inherited_fds:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

        start_ticks = None
        for _ in range(40):
            start_ticks = process_start_ticks(process.pid)
            if start_ticks is not None:
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
        if start_ticks is None:
            raise FinalKitError("gateway process exited before its identity could be recorded")

        record = {
            "pid": process.pid,
            "start_ticks": start_ticks,
            "backend": mode,
            "endpoint": endpoint,
            "instance": instance,
            "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        atomic_write(self.p.gateway_record, json.dumps(record, indent=2) + "\n")
        for _ in range(120):
            if self.gateway_identity(record) and self.gateway_health(record):
                return endpoint
            if process.poll() is not None:
                break
            time.sleep(0.2)

        try:
            self.stop_gateway()
        except FinalKitError:
            pass
        detail = self.tail_gateway_log()
        raise FinalKitError("gateway failed identity-aware health" + (f"\n{detail}" if detail else ""))

    def stop_gateway(self) -> None:
        record = self.read_gateway_record()
        if not record:
            return
        try:
            pid = int(record["pid"])
        except (KeyError, TypeError, ValueError):
            self.p.gateway_record.unlink(missing_ok=True)
            return
        alive = process_is_live(pid)
        if alive and not self.gateway_identity(record):
            raise FinalKitError(f"refusing to stop PID {pid}: process identity no longer matches FinalKit")
        if alive:
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 6.0
            while process_is_live(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if process_is_live(pid):
                os.kill(pid, signal.SIGKILL)
                deadline = time.monotonic() + 2.0
                while process_is_live(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
            if process_is_live(pid):
                raise FinalKitError(f"verified gateway PID {pid} did not stop")
        reap_child(pid)
        self.p.gateway_record.unlink(missing_ok=True)

    def science_environment(self, endpoint: str) -> dict[str, str]:
        environment = science_child_environment(self.p.science_home)
        for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
            environment.pop(name, None)
        environment["ANTHROPIC_BASE_URL"] = endpoint
        environment["ANTHROPIC_AUTH_TOKEN"] = SCIENCE_LOCAL_SESSION_TOKEN
        # Science distinguishes an explicitly selected third-party provider
        # (empty API-key slot + auth token) from a missing credential source.
        # This empty sentinel is not a provider key and never reaches storage.
        environment["ANTHROPIC_API_KEY"] = ""
        append_no_proxy(environment)
        return environment

    def ensure_science_identity(self, *, check_only: bool = False) -> dict[str, Any]:
        """Create/reuse only FinalKit's identity; never overwrite real credentials."""

        result = subprocess.run(
            [
                str(self.p.bridge_python),
                str(self.p.science_identity),
                "check" if check_only else "ensure",
                "--data-dir",
                str(self.p.data_dir),
            ],
            env=science_child_environment(self.p.science_home),
            cwd=self.p.science_home,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise FinalKitError(
                (result.stderr or result.stdout).strip()
                or "FinalKit could not establish the isolated Claude Science local identity"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FinalKitError("Science local identity helper returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise FinalKitError("Science local identity helper did not verify its result")
        if not check_only and payload.get("schema") != "science-local-v2":
            raise FinalKitError(
                "the isolated Science profile contains unknown or real credentials; "
                "FinalKit preserved them and refused to replace them with its local identity"
            )
        return payload

    def science_process_identity(self, pid: int) -> bool:
        if not process_is_live(pid):
            return False
        arguments = process_cmdline_parts(pid)
        if len(arguments) < 2 or arguments[0] != str(self.p.science) or arguments[1] != "serve":
            return False
        try:
            data_index = arguments.index("--data-dir")
            data_dir = arguments[data_index + 1]
        except (ValueError, IndexError):
            return False
        environment = process_environment(pid)
        return (
            data_dir == str(self.p.data_dir)
            and environment.get("HOME") == str(self.p.science_home)
        )

    def science_lock_process(self) -> dict[str, Any] | None:
        try:
            record = json.loads((self.p.data_dir / "operon.lock").read_text(encoding="utf-8"))
            pid = int(record["pid"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            return None
        if not process_is_live(pid):
            return None
        return {
            "pid": pid,
            "owned": self.science_process_identity(pid),
            "process_state": process_state(pid) or "unknown",
        }

    @staticmethod
    def science_control_error(detail: str, owner: dict[str, Any] | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "running": bool(owner and owner.get("owned")),
            "control_error": "unavailable",
            "detail": detail.strip()[:500] or "Claude Science control socket is unavailable",
        }
        if owner:
            result.update(owner)
        return result

    @staticmethod
    def science_process_persistently_blocked(pid: int) -> bool:
        """Require repeated D-state observations before declaring a stall.

        A newly detached Science process can enter Linux D state for a very
        short filesystem read and recover normally.  The historical DrvFS/9p
        failure stays in D across observations.  Treating one sample as final
        caused a healthy first Start to roll back, so sample the exact verified
        PID over a small bounded window.
        """

        for delay in (0.0, 0.15, 0.35):
            if delay:
                time.sleep(delay)
            if process_state(pid) != "D":
                return False
        return True

    @staticmethod
    def science_startup_io_pending(status: dict[str, Any]) -> bool:
        """Recognize only the owned D-state result produced by our status path.

        Claude Science 0.1.27 can spend several seconds in uninterruptible ext4
        database I/O immediately after ``serve --detached`` returns. That state
        is safe to wait for only inside the bounded readiness loop and only after
        the normal PID/argv/HOME/data-dir/lock checks identify this exact owner.
        Steady-state status, doctor, and stop remain fail-closed.
        """

        return (
            status.get("control_error") == "unavailable"
            and status.get("running") is True
            and status.get("owned") is True
            and str(status.get("process_state") or "").upper() == "D"
            and "owner is blocked in uninterruptible I/O"
            in str(status.get("detail") or "")
        )

    def _science_status_once(self) -> dict[str, Any]:
        if not self.p.science.is_file():
            return {"running": False}
        environment = science_child_environment(self.p.science_home)
        try:
            result = subprocess.run(
                [str(self.p.science), "status", "--data-dir", str(self.p.data_dir)],
                env=environment,
                cwd=self.p.science_home,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            status = self.science_control_error(
                "Claude Science status timed out", self.science_lock_process()
            )
            status["control_error"] = "timeout"
            return status
        owner = self.science_lock_process()
        if result.returncode != 0:
            return self.science_control_error(result.stderr or result.stdout, owner)
        try:
            status_value = json.loads(result.stdout)
        except json.JSONDecodeError:
            return self.science_control_error("Claude Science returned invalid status JSON", owner)
        if not isinstance(status_value, dict):
            return self.science_control_error("Claude Science returned a non-object status", owner)
        if status_value.get("running"):
            try:
                pid = int(status_value.get("pid", 0) or 0)
            except (TypeError, ValueError):
                pid = 0
            if not self.science_process_identity(pid):
                return self.science_control_error(
                    "Claude Science status PID failed FinalKit owner identity verification",
                    owner or ({"pid": pid, "owned": False, "process_state": process_state(pid) or "unknown"} if pid else None),
                )
            if owner and (not owner.get("owned") or int(owner.get("pid", 0) or 0) != pid):
                return self.science_control_error(
                    "Claude Science status PID conflicts with the live operon.lock owner",
                    owner,
                )
            if self.science_process_persistently_blocked(pid):
                blocked_owner = owner or {"pid": pid, "owned": True}
                blocked_owner["process_state"] = "D"
                return self.science_control_error(
                    "Claude Science owner is blocked in uninterruptible I/O; its page and control socket are not reliable",
                    blocked_owner,
                )
        elif owner:
            return self.science_control_error(
                "Claude Science reported stopped while its lock still identifies a live process",
                owner,
            )
        return status_value

    def science_status(self) -> dict[str, Any]:
        """Read daemon status with bounded recovery from a transient socket miss.

        Claude Science 0.1.27 can briefly fail one control-socket request while
        the owned daemon is still alive and its next request succeeds.  Retry
        only transport/parse failures for the same fully owned lock process.
        Identity conflicts remain fail-closed on their first observation.
        """

        status = self._science_status_once()
        if not status.get("control_error"):
            return status
        detail = str(status.get("detail") or "")
        retryable = (
            status.get("owned") is True
            and str(status.get("process_state") or "").upper() != "D"
            and (
                status.get("control_error") == "timeout"
                or "could not reach daemon control socket" in detail.lower()
                or "invalid status json" in detail.lower()
            )
            and "conflicts" not in detail.lower()
            and "identity verification" not in detail.lower()
        )
        if not retryable:
            return status
        first_detail = detail
        for delay in (0.2, 0.5, 1.0):
            time.sleep(delay)
            retried = self._science_status_once()
            if not retried.get("control_error"):
                retried["control_recovered"] = True
                retried["control_recovered_from"] = first_detail[:200]
                return retried
            retry_detail = str(retried.get("detail") or "")
            if "conflicts" in retry_detail.lower() or "identity verification" in retry_detail.lower():
                return retried
            status = retried
        return status

    @staticmethod
    def science_recovery_message(status: dict[str, Any]) -> str:
        pid = status.get("pid", "unknown")
        state = status.get("process_state", "unknown")
        detail = status.get("detail", "control socket unavailable")
        return (
            "FINALKIT_SCIENCE_CONTROL_UNAVAILABLE: "
            f"{detail} (PID {pid}, state {state}). FinalKit did not force-kill a daemon "
            "after its official control path failed. "
            "From Windows run: wsl.exe --terminate <your Ubuntu distro>; then run menu 16 "
            "Update FinalKit runtime and start the provider again. Use menu 2 only when the "
            "runtime is missing or the full stack is damaged. Do not use Clear."
        )

    def science_stop(self) -> None:
        if not self.p.science.is_file():
            return
        environment = science_child_environment(self.p.science_home)
        stop_detail = ""
        try:
            result = subprocess.run(
                [str(self.p.science), "stop", "--data-dir", str(self.p.data_dir)],
                env=environment,
                cwd=self.p.science_home,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                stop_detail = (result.stderr or result.stdout).strip()
        except subprocess.TimeoutExpired:
            stop_detail = "Claude Science stop timed out"
        status = self.science_status()
        if status.get("control_error"):
            if stop_detail and not status.get("detail"):
                status["detail"] = stop_detail
            raise FinalKitError(self.science_recovery_message(status))
        if status.get("running"):
            raise FinalKitError(
                "Claude Science remained running after its official stop command; "
                "FinalKit did not kill it without a complete owner proof."
            )
        if stop_detail:
            print(
                f"[WARN] Claude Science stop returned an error, but post-stop status confirms it is stopped: {stop_detail}",
                file=sys.stderr,
            )

    def science_start(self, endpoint: str, *, ensure_identity: bool = True) -> dict[str, Any]:
        current = self.science_status()
        if current.get("control_error"):
            raise FinalKitError(self.science_recovery_message(current))
        if current.get("running"):
            pid = int(current.get("pid", 0) or 0)
            if process_environment(pid).get("ANTHROPIC_BASE_URL") == endpoint:
                if ensure_identity:
                    current["local_session"] = self.verify_science_local_session()
                return current
            raise FinalKitError("Claude Science is running with a different backend endpoint")

        if ensure_identity:
            identity = self.ensure_science_identity()
            if identity.get("action") == "created":
                print(
                    "Created FinalKit's local-only Science identity inside the isolated profile."
                )
            elif str(identity.get("action", "")).startswith("migrated-"):
                print("Migrated an exact legacy FinalKit identity to the current local-only shape.")
        environment = self.science_environment(endpoint)
        with open(self.p.science_boot_log, "a", encoding="utf-8") as log_handle:
            os.chmod(self.p.science_boot_log, 0o600)
            result = subprocess.run(
                [
                    str(self.p.science),
                    "serve",
                    "--data-dir",
                    str(self.p.data_dir),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.science_port),
                    "--no-browser",
                    "--detached",
                    "--no-auto-update",
                ],
                env=environment,
                cwd=self.p.science_home,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                timeout=60,
                check=False,
            )
        if result.returncode != 0:
            raise FinalKitError(
                f"Claude Science start returned {result.returncode}; see {self.p.science_boot_log}"
            )

        ready_deadline = time.monotonic() + SCIENCE_START_READY_SECONDS
        local_session: dict[str, Any] | None = None
        while time.monotonic() < ready_deadline:
            current = self.science_status()
            if current.get("control_error"):
                if self.science_startup_io_pending(current):
                    time.sleep(0.25)
                    continue
                raise FinalKitError(self.science_recovery_message(current))
            if current.get("running") and int(current.get("port", 0) or 0) == self.science_port:
                pid = int(current.get("pid", 0) or 0)
                if process_environment(pid).get("ANTHROPIC_BASE_URL") == endpoint:
                    if ensure_identity:
                        if local_session is None:
                            local_session = self.verify_science_local_session(
                                startup_deadline=ready_deadline
                            )
                            # Admission can trigger one final ext4 database
                            # write. Require another fully owned healthy status
                            # before declaring startup stable, while retaining
                            # the same bounded startup-only D-state tolerance.
                            time.sleep(0.25)
                            continue
                        current["local_session"] = local_session
                    return current
            time.sleep(0.25)
        raise FinalKitError(f"Claude Science did not become ready; see {self.p.science_boot_log}")

    def science_url(self, *, startup_deadline: float | None = None) -> str:
        environment = science_child_environment(self.p.science_home)
        while True:
            result = subprocess.run(
                [str(self.p.science), "url", "--data-dir", str(self.p.data_dir)],
                env=environment,
                cwd=self.p.science_home,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                break
            detail = result.stderr.strip() or result.stdout.strip()
            transient = "could not reach daemon control socket" in detail.lower()
            owner = self.science_lock_process() if transient else None
            if (
                startup_deadline is not None
                and time.monotonic() < startup_deadline
                and owner
                and owner.get("owned") is True
            ):
                time.sleep(0.25)
                continue
            raise FinalKitError(detail or "could not create a Claude Science login URL")
        for line in reversed(result.stdout.splitlines()):
            value = line.strip()
            if value.startswith("http://") or value.startswith("https://"):
                return value
        raise FinalKitError("Claude Science did not return a login URL")

    @staticmethod
    def science_login_target(login_url: str, expected_port: int) -> tuple[str, str]:
        parsed = urllib.parse.urlsplit(login_url)
        try:
            actual_port = parsed.port
        except ValueError as exc:
            raise FinalKitError("Claude Science returned a malformed login URL port") from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or actual_port != expected_port
        ):
            raise FinalKitError("Claude Science returned a non-loopback login URL")
        try:
            values = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        except ValueError as exc:
            raise FinalKitError("Claude Science returned a malformed login URL") from exc
        nonce = values.get("nonce", [])
        if len(nonce) != 1 or not nonce[0]:
            raise FinalKitError("Claude Science login URL did not contain one nonce")
        return f"http://127.0.0.1:{expected_port}", nonce[0]

    def verify_science_local_session(
        self, *, startup_deadline: float | None = None
    ) -> dict[str, Any]:
        """Exercise Science's loopback nonce gate without touching browser state.

        The initial ``Sign in`` document is the daemon's local CSRF/session
        boundary, not proof that a Claude.ai account is required.  This probe
        obtains its own one-time URL, submits the nonce into an in-memory cookie
        jar, then requires both the authenticated ``/api/me`` surface and the
        workbench document.  The separate URL returned to Windows remains
        unused and must still be accepted by the user's browser.
        """

        login_url = self.science_url(startup_deadline=startup_deadline)
        origin, nonce = self.science_login_target(login_url, self.science_port)
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies)
        )
        form = urllib.parse.urlencode({"nonce": nonce, "dest": "/"}).encode("ascii")
        request = urllib.request.Request(
            origin + "/api/auth/nonce",
            data=form,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise FinalKitError(
                        f"Claude Science local nonce admission returned HTTP {response.status}"
                    )
            with opener.open(origin + "/api/me", timeout=10) as response:
                if response.status != 200:
                    raise FinalKitError(
                        f"Claude Science local identity returned HTTP {response.status}"
                    )
                profile = json.loads(response.read().decode("utf-8"))
            with opener.open(origin + "/", timeout=10) as response:
                if response.status != 200:
                    raise FinalKitError(
                        f"Claude Science workbench returned HTTP {response.status}"
                    )
                document = response.read().decode("utf-8", errors="replace")
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            raise FinalKitError(
                f"Claude Science local session admission failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(profile, dict):
            raise FinalKitError("Claude Science /api/me returned a non-object profile")
        title = re.search(r"<title>\s*Claude Science\s*</title>", document, flags=re.IGNORECASE)
        if title is None:
            raise FinalKitError("Claude Science did not admit the local session to its workbench")
        return {"admitted": True, "profile": "local", "cookies": len(cookies)}

    @staticmethod
    def science_status_endpoint_matches(status_value: dict[str, Any], endpoint: str) -> bool:
        if status_value.get("control_error") or not status_value.get("running"):
            return False
        pid = int(status_value.get("pid", 0) or 0)
        return process_environment(pid).get("ANTHROPIC_BASE_URL") == endpoint

    def science_endpoint_matches(self, endpoint: str) -> bool:
        return self.science_status_endpoint_matches(self.science_status(), endpoint)

    def ensure_bridge_config(self) -> None:
        route = self.model_routes()["codex"]
        codex_opus_model = route["model_opus"]
        codex_sonnet_model = route["model_sonnet"]
        codex_haiku_model = route["model_haiku"]
        codex_reasoning_map = {
            "claude-opus": route["reasoning_opus"],
            "claude-sonnet": route["reasoning_sonnet"],
            "claude-haiku": route["reasoning_haiku"],
        }
        config = {
            "deepseek_api_key": "",
            "openai_api_key": "",
            "custom_api_key": "",
            "openai_auth_mode": "codex_device",
            "deepseek_base_url": "https://api.deepseek.com",
            "openai_base_url": "https://api.openai.com",
            "custom_base_url": "",
            "codex_auth_base_url": "https://auth.openai.com",
            "codex_device_url": "https://auth.openai.com/codex/device",
            "codex_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "codex_backend_url": "https://chatgpt.com/backend-api/codex",
            "codex_model": codex_opus_model,
            "codex_reasoning": route["reasoning_opus"],
            "codex_reasoning_map": codex_reasoning_map,
            "codex_model_map": {
                # Claude Science owns these UI compatibility IDs.  The
                # connector maps every current/future version suffix within a
                # family to FinalKit's real three-tier ChatGPT Codex route.
                "claude-opus": codex_opus_model,
                "claude-sonnet": codex_sonnet_model,
                "claude-haiku": codex_haiku_model,
            },
            "default_backend": "openai",
            # An upstream force_model overrides every entry in model_map.  It
            # must stay empty or the Haiku/fast mapping can never take effect.
            "force_model": "",
            "deepseek_model_map": {},
            "openai_model_map": {},
            "custom_model_map": {},
            "deepseek_model_pattern": "(?!)",
            "openai_model_pattern": ".*",
            "custom_model_pattern": "(?!)",
            "reasoning_content_policy": "fallback",
            "inline_image_policy": "preserve",
            "proxy_host": "127.0.0.1",
            "proxy_port": self.gateway_port,
        }
        atomic_write(self.p.bridge_config, json.dumps(config, indent=2, ensure_ascii=False) + "\n")

    def installed_package_version(self) -> str:
        try:
            for line in self.p.versions.read_text(encoding="utf-8").splitlines():
                if line.startswith("package_version="):
                    return line.partition("=")[2].strip() or "unknown"
        except FileNotFoundError:
            pass
        return "unknown"

    def route_description(self, mode: str, routes: dict[str, Any] | None = None) -> str:
        routes = routes or self.model_routes()
        if mode in API_PROVIDERS:
            provider = API_PROVIDERS[mode]
            route = routes["providers"][mode]
            return "; ".join(
                f"{role.title()}: Model={route[f'model_{role}']}, Reasoning={route[f'reasoning_{role}']}"
                for role in CLAUDE_ROUTE_ROLES
            ) + f" ({provider['label']})"
        if mode == "codex":
            route = routes["codex"]
            return "; ".join(
                f"{role.title()}: Model={route[f'model_{role}']}, Reasoning={route[f'reasoning_{role}']}"
                for role in CLAUDE_ROUTE_ROLES
            ) + " (ChatGPT Codex)"
        return "unconfigured"

    @staticmethod
    def print_science_model_note() -> None:
        print(
            "Science model IDs: Claude-compatible aliases; FinalKit's model catalog "
            "and EFFECTIVE_ROUTE expose the configured upstream route."
        )

    def prepare(self) -> None:
        """Create deterministic non-secret runtime configuration."""

        self.require_runtime()
        self.ensure_bridge_config()

    def _switch_locked(self, mode: str, *, start_science: bool = True) -> str:
        if mode not in VALID_MODES:
            raise FinalKitError("mode must be deepseek, kimi, glm, or codex")
        previous_mode = self.current_mode()
        previous_record = self.read_gateway_record()
        previous_gateway_running = bool(
            previous_record and self.gateway_identity(previous_record) and self.gateway_health(previous_record)
        )

        # Native Claude Code can reuse an already healthy matching gateway
        # without consulting or touching Claude Science.  This keeps provider
        # use available even when the optional Science control socket or
        # account session needs repair.
        if (
            not start_science
            and previous_gateway_running
            and previous_record
            and previous_record.get("backend") == mode
        ):
            self.write_mode(mode)
            return str(previous_record["endpoint"])

        previous_science_status = self.science_status()
        if previous_science_status.get("control_error"):
            raise FinalKitError(self.science_recovery_message(previous_science_status))
        previous_science_running = bool(previous_science_status.get("running"))

        # A non-Science client may reuse the backend already serving Science,
        # but it must never stop or restart the live Science daemon merely to
        # select another provider.  Provider changes for Science remain an
        # explicit ``fkctl start <mode>`` operation with its normal rollback.
        if not start_science and previous_science_running:
            if (
                previous_gateway_running
                and previous_record
                and previous_record.get("backend") == mode
                and self.science_status_endpoint_matches(
                    previous_science_status, str(previous_record["endpoint"])
                )
            ):
                self.write_mode(mode)
                return str(previous_record["endpoint"])
            raise FinalKitError(
                "Claude Science is running; a non-Science gateway selection will not "
                "stop or reroute it. Use 'fkctl start <mode>' to explicitly switch "
                "Science, or stop Science first."
            )

        if previous_science_running:
            if not previous_gateway_running or not previous_record:
                raise FinalKitError(
                    "Claude Science is running without a healthy owned gateway; run fkctl stop before switching"
                )
            if not self.science_status_endpoint_matches(
                previous_science_status, str(previous_record["endpoint"])
            ):
                raise FinalKitError(
                    "Claude Science and the owned gateway have different endpoints; run fkctl stop before switching"
                )
        previous_backend = (
            str(previous_record.get("backend"))
            if previous_gateway_running and previous_record
            else previous_mode
        )

        if (
            previous_mode == mode
            and previous_gateway_running
            and previous_record
            and previous_record.get("backend") == mode
        ):
            if start_science and self.science_status_endpoint_matches(
                previous_science_status, str(previous_record["endpoint"])
            ):
                admission_deadline = time.monotonic() + SCIENCE_START_READY_SECONDS
                self.verify_science_local_session(startup_deadline=admission_deadline)
                return self.science_url(startup_deadline=admission_deadline)
            if not start_science:
                return str(previous_record["endpoint"])

        self.science_stop()
        self.stop_gateway()
        try:
            endpoint = self.spawn_gateway(mode)
            started_science: dict[str, Any] | None = None
            if start_science:
                started_science = self.science_start(endpoint)
            if not self.gateway_health() or (
                start_science
                and not self.science_status_endpoint_matches(started_science or {}, endpoint)
            ):
                raise FinalKitError("post-switch runtime identity verification failed")
            self.write_mode(mode)
            if start_science:
                return self.science_url(
                    startup_deadline=time.monotonic() + SCIENCE_START_READY_SECONDS
                )
            return endpoint
        except Exception as primary_error:
            rollback_errors: list[str] = []
            try:
                self.science_stop()
            except Exception as exc:  # rollback must attempt every owned layer
                rollback_errors.append(f"stop Science: {exc}")
            try:
                self.stop_gateway()
            except Exception as exc:
                rollback_errors.append(f"stop gateway: {exc}")
            if previous_backend and previous_gateway_running:
                try:
                    old_endpoint = self.spawn_gateway(previous_backend)
                    if previous_science_running:
                        self.science_start(old_endpoint)
                except Exception as exc:
                    rollback_errors.append(f"restore {previous_backend}: {exc}")
            message = f"switch to {mode} failed: {primary_error}"
            if rollback_errors:
                message += "\nrollback incomplete: " + "; ".join(rollback_errors)
            else:
                message += "\nprevious runtime state was restored"
            raise FinalKitError(message) from primary_error

    def switch(self, mode: str) -> str:
        with FileLock(self.p.lock):
            return self._switch_locked(mode)

    def select_gateway(self, mode: str) -> str:
        """Select a backend without requiring or starting Claude Science."""

        with FileLock(self.p.lock):
            return self._switch_locked(mode, start_science=False)

    def stop(self) -> None:
        with FileLock(self.p.lock):
            self.science_stop()
            self.stop_gateway()

    def init_profile(self) -> None:
        self.require_runtime()
        with FileLock(self.p.lock):
            key_file = self.p.data_dir / "encryption.key"
            if not key_file.is_file() or key_file.stat().st_size == 0:
                science_status = self.science_status()
                if science_status.get("control_error"):
                    raise FinalKitError(self.science_recovery_message(science_status))
                if science_status.get("running") or self.read_gateway_record():
                    raise FinalKitError("stop the active FinalKit runtime before initializing its profile")
                endpoint = self.spawn_gateway("deepseek", key_override="finalkit-local-smoke-key")
                try:
                    # A first Science boot creates encryption.key.  Once that
                    # bootstrap completes, the helper can remove only exact
                    # obsolete FinalKit identities and otherwise leaves Claude
                    # Science account state untouched.
                    self.science_start(endpoint, ensure_identity=False)
                    for _ in range(160):
                        if key_file.is_file() and key_file.stat().st_size > 0:
                            break
                        time.sleep(0.25)
                finally:
                    self.science_stop()
                    self.stop_gateway()
            if not key_file.is_file() or key_file.stat().st_size == 0:
                raise FinalKitError(f"Claude Science did not create {key_file}")
            identity = self.ensure_science_identity()
            print(
                "Science local identity is ready "
                f"({identity.get('schema', 'unknown')}; {identity.get('action', 'verified')})."
            )

    def configure_provider(self, provider_name: str) -> None:
        if provider_name not in API_PROVIDERS:
            raise FinalKitError(f"unknown API provider: {provider_name}")
        provider = API_PROVIDERS[provider_name]
        first = getpass.getpass(f"{provider['label']} API key (hidden): ")
        if not first or "\n" in first or "\r" in first:
            raise FinalKitError("API key cannot be empty or contain a newline")
        key_path = self.p.provider_keys[provider_name]
        with FileLock(self.p.lock):
            atomic_write(key_path, first)
            first = ""
            print(f"{provider['label']} key saved inside this Linux user's WSL home: {key_path}")
            result = self.configure_model_routes_interactive(provider_name)
        print("Route: " + self.route_description(provider_name, result["routes"]))

    def configure_codex(self) -> None:
        self.require_runtime()
        with FileLock(self.p.lock):
            self.ensure_bridge_config()
            environment = codex_network_environment(
                self.p.client_home,
                "https://auth.openai.com",
            )
            print("Starting the official Codex browser login in Claude Science's isolated home.")
            print("No token value will be printed; complete the browser flow opened by Codex.")
            self._replace_codex_auth(
                [str(self.p.codex), *CODEX_FILE_AUTH_ARGS, "login"],
                environment,
                "the official Codex browser login did not complete. "
                "If localhost OAuth is unavailable, run: fkctl configure-codex-device",
            )
            result = self.configure_model_routes_interactive("codex")
        print("Route: " + self.route_description("codex", result["routes"]))

    def configure_codex_device(self) -> None:
        """Explicit beta fallback for headless or blocked localhost OAuth."""

        self.require_runtime()
        with FileLock(self.p.lock):
            self.ensure_bridge_config()
            environment = codex_network_environment(
                self.p.client_home,
                "https://auth.openai.com/api/accounts/deviceauth/usercode",
            )
            print("Starting the official Codex device-code login (beta).")
            print("Device login must be enabled in the ChatGPT account or workspace settings.")
            self._replace_codex_auth(
                [str(self.p.codex), *CODEX_FILE_AUTH_ARGS, "login", "--device-auth"],
                environment,
                "the official Codex device login did not complete. Use fkctl configure-codex "
                "for the default browser flow, or enable device login in ChatGPT settings",
            )
            result = self.configure_model_routes_interactive("codex")
        print("Route: " + self.route_description("codex", result["routes"]))

    @staticmethod
    def _validate_imported_codex_auth(payload: bytes | bytearray) -> None:
        """Admit only an official ChatGPT token chain without exposing its values."""

        if not payload or len(payload) > MAX_CODEX_AUTH_BYTES:
            raise FinalKitError("the imported Codex auth payload is empty or exceeds 1 MiB")
        try:
            value = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinalKitError("the imported Codex auth payload is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict) or value.get("auth_mode") != "chatgpt":
            raise FinalKitError("the imported auth must be an official Codex ChatGPT login")
        tokens = value.get("tokens")
        if not isinstance(tokens, dict) or not all(
            isinstance(tokens.get(name), str) and bool(tokens.get(name))
            for name in ("access_token", "refresh_token")
        ):
            raise FinalKitError("the imported ChatGPT auth token chain is incomplete")

    def import_codex_auth(self, payload: bytearray) -> None:
        """One-time stdin import into the WSL-owned official Codex cache.

        The caller must stop Science and its gateway first. The candidate is
        validated in a temporary HOME, then atomically replaces the WSL cache.
        A failed final validation restores the prior file byte-for-byte. Once
        committed, the Linux Codex CLI and connector independently own future
        refreshes and re-login; there is no recurring Windows synchronization.
        """

        self.require_runtime()
        self._validate_imported_codex_auth(payload)
        with FileLock(self.p.lock):
            science_status = self.science_status()
            if science_status.get("control_error"):
                raise FinalKitError(self.science_recovery_message(science_status))
            if science_status.get("running") or self.read_gateway_record():
                raise FinalKitError(
                    "stop the active FinalKit Science/gateway before importing Codex auth"
                )

            self.ensure_bridge_config()
            auth = self.p.codex_auth
            auth.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            auth.parent.chmod(0o700)
            previous = bytearray(auth.read_bytes()) if auth.is_file() else None
            previous_mode = stat.S_IMODE(auth.stat().st_mode) if auth.is_file() else 0o600
            replaced = False
            try:
                with tempfile.TemporaryDirectory(
                    prefix=".codex-import.", dir=self.p.client_home
                ) as scratch:
                    staging_home = Path(scratch)
                    staged_auth = staging_home / ".codex" / "auth.json"
                    atomic_write_bytes(staged_auth, payload)
                    staging_environment = private_child_environment(staging_home)
                    if not self._codex_login_status(staging_environment):
                        raise FinalKitError(
                            "official Codex login status did not validate the imported auth"
                        )
                    os.replace(staged_auth, auth)
                    replaced = True
                    auth.chmod(0o600)

                self._finalize_codex_auth(private_child_environment(self.p.client_home))
            except Exception:
                if replaced:
                    if previous is None:
                        auth.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(auth, previous, previous_mode)
                raise
            finally:
                if previous is not None:
                    for index in range(len(previous)):
                        previous[index] = 0

        print(
            "One-time Codex auth import committed. WSL now owns its independent "
            "refresh and re-login lifecycle."
        )

    def _replace_codex_auth(
        self,
        command: list[str],
        environment: dict[str, str],
        failure_message: str,
    ) -> None:
        """Validate a staged official login before atomically replacing the cache."""
        auth = self.p.codex_auth
        auth.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        auth.parent.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix=".codex-login.", dir=self.p.client_home) as scratch:
            staging_home = Path(scratch)
            staging_environment = environment.copy()
            staging_environment["HOME"] = str(staging_home)
            login = subprocess.run(command, env=staging_environment, check=False)
            if login.returncode != 0:
                raise FinalKitError(failure_message)

            staged_auth = staging_home / ".codex" / "auth.json"
            if not self._codex_auth_file_configured(staged_auth):
                raise FinalKitError("the official Codex login cache is missing or incomplete")
            staged_auth.chmod(0o600)
            if not self._codex_login_status(staging_environment):
                raise FinalKitError("official Codex login status did not validate the staged auth cache")

            previous = auth.read_text(encoding="utf-8") if auth.is_file() else None
            os.replace(staged_auth, auth)
            auth.chmod(0o600)
            try:
                self._finalize_codex_auth(environment)
            except Exception:
                if previous is None:
                    auth.unlink(missing_ok=True)
                else:
                    atomic_write(auth, previous)
                raise
            finally:
                previous = None

    def codex_auth_configured(self) -> bool:
        """Return whether the official CLI cache has a usable credential shape.

        ChatGPT OAuth normally also has a refresh token. Enterprise access-token
        login is valid without one, so an access token is the common minimum.
        """
        return self._codex_auth_file_configured(self.p.codex_auth)

    @staticmethod
    def _codex_auth_file_configured(path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        return bool(isinstance(tokens, dict) and tokens.get("access_token"))

    def _finalize_codex_auth(self, environment: dict[str, str]) -> None:
        if not self.codex_auth_configured():
            raise FinalKitError("the official Codex login cache is missing or incomplete")
        self.p.codex_auth.chmod(0o600)
        if not self._codex_login_status(environment):
            raise FinalKitError("official Codex login status did not validate the isolated auth cache")
        self.ensure_bridge_config()
        print("ChatGPT Codex account auth is configured. Run: fkctl start codex")

    def _codex_login_status(self, environment: dict[str, str]) -> bool:
        status = subprocess.run(
            [str(self.p.codex), *CODEX_FILE_AUTH_ARGS, "login", "status"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return status.returncode == 0

    def codex_login_status(self) -> bool:
        if not self.codex_auth_configured():
            return False
        try:
            environment = private_child_environment(self.p.client_home)
            return self._codex_login_status(environment)
        except (OSError, subprocess.SubprocessError):
            return False

    def login_linux_codex(self) -> None:
        if not self.p.codex.is_file():
            raise FinalKitError(f"Linux Codex CLI is missing: {self.p.codex}")
        environment = codex_network_environment(
            Path.home(), "https://auth.openai.com"
        )
        result = subprocess.run(
            [str(self.p.codex), *CODEX_FILE_AUTH_ARGS, "login"],
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            raise FinalKitError("Linux Codex browser login did not complete")

    def run_claude_code(self, mode: str, arguments: list[str]) -> int:
        """Run native Linux Claude Code against the selected local FinalKit gateway."""

        self.select_gateway(mode)
        record = self.read_gateway_record()
        if not record or not self.gateway_identity(record) or not self.gateway_health(record):
            raise FinalKitError("the selected gateway is not healthy")
        environment = os.environ.copy()
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
        ):
            environment.pop(name, None)
        environment["ANTHROPIC_BASE_URL"] = str(record["endpoint"])
        environment["ANTHROPIC_AUTH_TOKEN"] = SCIENCE_LOCAL_SESSION_TOKEN
        environment["ANTHROPIC_API_KEY"] = ""
        append_no_proxy(environment)
        result = subprocess.run([str(self.p.claude), *arguments], env=environment, check=False)
        return result.returncode

    def smoke_local(self) -> str:
        self.require_runtime()
        with FileLock(self.p.lock):
            science_status = self.science_status()
            if science_status.get("control_error"):
                raise FinalKitError(self.science_recovery_message(science_status))
            if science_status.get("running") or self.read_gateway_record():
                record = self.read_gateway_record()
                if not record or not self.gateway_identity(record) or not self.gateway_health(record):
                    raise FinalKitError("an unhealthy runtime is active; stop or repair it before smoke")
                self.verify_gateway_science_identity(str(record["endpoint"]))
                if science_status.get("running") and not self.science_endpoint_matches(str(record["endpoint"])):
                    raise FinalKitError("Science/backend endpoint mismatch")
                client_state = "Science running" if science_status.get("running") else "gateway-only"
                return f"active runtime passed the no-cost identity smoke ({client_state})"

            passed: list[str] = []
            for provider_name in API_PROVIDERS:
                endpoint = self.spawn_gateway(provider_name, key_override="finalkit-local-smoke-key")
                try:
                    health = self.gateway_health()
                    if not health or health.get("finalkit_backend") != provider_name:
                        raise FinalKitError(f"local {provider_name} gateway identity verification failed")
                    self.verify_gateway_science_identity(endpoint)
                    passed.append(provider_name)
                finally:
                    self.stop_gateway()
            endpoint = self.spawn_gateway("deepseek", key_override="finalkit-local-smoke-key")
            try:
                self.science_start(endpoint)
                self.verify_gateway_science_identity(endpoint)
                identity = self.ensure_science_identity(check_only=True)
                gateway_ok = bool(self.gateway_health())
                endpoint_ok = self.science_endpoint_matches(endpoint)
                identity_schema = str(identity.get("schema") or "unknown")
                if not gateway_ok or not endpoint_ok or identity_schema != "science-local-v2":
                    raise FinalKitError(
                        "Claude Science/local gateway identity verification failed "
                        f"(gateway={gateway_ok}, endpoint={endpoint_ok}, identity={identity_schema})"
                    )
            finally:
                self.science_stop()
                self.stop_gateway()
            return "local provider gateways + Claude Science smoke passed: " + ", ".join(passed)

    def test_backend(self, mode: str) -> dict[str, Any]:
        self.select_gateway(mode)
        record = self.read_gateway_record()
        if not record:
            raise FinalKitError("gateway record disappeared after switch")
        return self._send_backend_test(record, "claude-opus-4-8", mode)

    def _send_backend_test(
        self, record: dict[str, Any], alias: str, label: str
    ) -> dict[str, Any]:
        response = self.local_json(
            str(record["endpoint"]).rstrip("/") + "/v1/messages",
            timeout=300,
            payload={
                "model": alias,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "Return exactly BACKEND_OK"}],
            },
        )
        if "BACKEND_OK" not in json.dumps(response, ensure_ascii=False):
            raise FinalKitError(f"{label} backend replied, but BACKEND_OK was not present")
        return response

    def codex_model_catalog(self, record: dict[str, Any]) -> list[dict[str, str]]:
        payload = self.local_json(
            str(record["endpoint"]).rstrip("/") + "/v1/models",
            timeout=20,
        )
        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise FinalKitError("connector did not return a model catalog")
        return [model for model in raw_models if isinstance(model, dict)]

    def test_codex_tiers(self) -> dict[str, Any]:
        self.select_gateway("codex")
        record = self.read_gateway_record()
        if not record:
            raise FinalKitError("gateway record disappeared after Codex switch")
        try:
            config = json.loads(self.p.bridge_config.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, TypeError) as exc:
            raise FinalKitError("connector route config is unavailable") from exc
        model_map = config.get("codex_model_map", {})
        reasoning_map = config.get("codex_reasoning_map", {})
        if not isinstance(model_map, dict) or not isinstance(reasoning_map, dict):
            raise FinalKitError("connector Codex route maps are invalid")
        aliases = tuple(
            (
                f"{family.removeprefix('claude-')} -> {model_map.get(family)} ({reasoning_map.get(family)})",
                alias,
            )
            for family, alias in (
                ("claude-opus", "claude-opus-4-8"),
                ("claude-sonnet", "claude-sonnet-4-5"),
                ("claude-haiku", "claude-haiku-4-5-20251001"),
            )
        )
        if any(
            not isinstance(model_map.get(family), str)
            or not model_map.get(family)
            or str(reasoning_map.get(family, "")).lower() not in CODEX_REASONING_EFFORTS
            for family in ("claude-opus", "claude-sonnet", "claude-haiku")
        ):
            raise FinalKitError("connector Codex three-tier route is incomplete")
        catalog = self.codex_model_catalog(record)
        expected_catalog = {
            alias: f"ChatGPT Codex | {model_map[family]} | reasoning={reasoning_map[family]}"
            for family, alias in (
                ("claude-opus", "claude-opus-4-8"),
                ("claude-sonnet", "claude-sonnet-4-5"),
                ("claude-haiku", "claude-haiku-4-5-20251001"),
            )
        }
        actual_catalog = {
            str(model.get("id")): str(model.get("display_name"))
            for model in catalog
        }
        catalog_ids = [str(model.get("id")) for model in catalog]
        if (
            len(catalog) != len(expected_catalog)
            or len(set(catalog_ids)) != len(catalog_ids)
            or actual_catalog != expected_catalog
        ):
            raise FinalKitError("connector model catalog does not match the configured Codex routes")
        results = {
            label: self._send_backend_test(record, alias, label)
            for label, alias in aliases
        }
        return {
            "route": self.route_description("codex"),
            "reasoning": reasoning_map,
            "model_catalog": catalog,
            "tiers": results,
        }

    def doctor(self) -> int:
        checks: list[tuple[str, bool, str]] = []

        def add(label: str, ok: bool, detail: str = "") -> None:
            checks.append((label, ok, detail))

        add("Ubuntu 24.04", Path("/etc/os-release").read_text(errors="replace").find('VERSION_ID="24.04"') >= 0)
        installed_metadata = self.installed_package_version()
        candidate_metadata = os.environ.get("FINALKIT_CANDIDATE_VERSION", "").strip()
        if candidate_metadata and candidate_metadata != installed_metadata:
            print(
                f"[INFO] distribution metadata before verification: {installed_metadata}; "
                f"candidate runtime under test: {candidate_metadata} (neither is a runtime gate)"
            )
        else:
            print(f"[INFO] distribution metadata: {installed_metadata} (not a runtime gate)")
        add("Claude Science", os.access(self.p.science, os.X_OK), str(self.p.science))
        add("Claude Code", os.access(self.p.claude, os.X_OK), str(self.p.claude))
        add("Linux Codex CLI", os.access(self.p.codex, os.X_OK), str(self.p.codex))
        add("direct provider gateway", self.p.direct_gateway.is_file(), str(self.p.direct_gateway))
        add("pinned connector Python", os.access(self.p.bridge_python, os.X_OK), str(self.p.bridge_python))
        for command in ("bwrap", "socat", "curl", "jq", "git"):
            add(command, shutil.which(command) is not None)
        for label, path in (
            ("instance id", self.p.instance_id),
            ("gateway path secret", self.p.path_secret),
            ("connector control token", self.p.control_token),
            ("Science encryption key", self.p.data_dir / "encryption.key"),
        ):
            add(label, path.is_file() and path.stat().st_size > 0, str(path))
            if path.exists():
                add(f"{label} permission 600", stat.S_IMODE(path.stat().st_mode) == 0o600)
        try:
            science_identity = self.ensure_science_identity(check_only=True)
            science_identity_ok = science_identity.get("schema") == "science-local-v2"
        except FinalKitError as exc:
            science_identity_ok = False
            science_identity = {"error": str(exc)}
        add(
            "isolated Science local identity",
            science_identity_ok,
            str(science_identity.get("schema") or science_identity.get("error") or self.p.data_dir),
        )
        if self.codex_auth_configured():
            add("official isolated Codex login", self.codex_login_status(), str(self.p.codex_auth))
            add("Codex auth permission 600", stat.S_IMODE(self.p.codex_auth.stat().st_mode) == 0o600)
        routes: dict[str, Any] = {}
        try:
            routes = self.model_routes()
            routes_ok = True
        except FinalKitError:
            routes_ok = False
        add("persistent model routes", routes_ok, str(self.p.model_routes))
        if self.p.model_routes.exists():
            add(
                "model route permission 600",
                stat.S_IMODE(self.p.model_routes.stat().st_mode) == 0o600,
            )
        config: dict[str, Any] = {}
        try:
            loaded_config = json.loads(self.p.bridge_config.read_text(encoding="utf-8"))
            if not isinstance(loaded_config, dict):
                raise TypeError("connector config must be an object")
            config = loaded_config
            config_ok = True
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            config_ok = False
        add("connector config JSON", config_ok, str(self.p.bridge_config))
        model_map = config.get("codex_model_map", {})
        reasoning_map = config.get("codex_reasoning_map", {})
        codex_route = routes.get("codex", {}) if isinstance(routes, dict) else {}
        route_ok = (
            routes_ok
            and isinstance(model_map, dict)
            and isinstance(reasoning_map, dict)
            and model_map.get("claude-opus") == codex_route.get("model_opus")
            and model_map.get("claude-sonnet") == codex_route.get("model_sonnet")
            and model_map.get("claude-haiku") == codex_route.get("model_haiku")
            and reasoning_map.get("claude-opus") == codex_route.get("reasoning_opus")
            and reasoning_map.get("claude-sonnet") == codex_route.get("reasoning_sonnet")
            and reasoning_map.get("claude-haiku") == codex_route.get("reasoning_haiku")
            and not config.get("force_model")
        )
        route_detail = (
            self.route_description("codex", routes)
            if routes_ok
            else f"invalid or unreadable: {self.p.model_routes}"
        )
        add("connector Codex three-tier route", route_ok, route_detail)
        try:
            proxy_text = (self.p.bridge / "proxy.py").read_text(encoding="utf-8")
            patch_ok = all(marker in proxy_text for marker in (
                "FINALKIT_CONTROL_FD",
                "finalkit_backend",
                "codex_reasoning_map",
                "mapped_from_alias",
                "Expose the real Codex route behind Claude-compatible model IDs",
            ))
        except FileNotFoundError:
            patch_ok = False
        add("connector security patch", patch_ok)
        if self.p.bridge_commit.is_file() and (self.p.bridge / ".git").is_dir():
            expected = self.p.bridge_commit.read_text(encoding="utf-8").strip()
            actual = subprocess.run(
                ["git", "-C", str(self.p.bridge), "rev-parse", "HEAD"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.strip()
            add("connector pinned commit", bool(expected and actual == expected), actual)
        else:
            add("connector pinned commit", False)

        science = self.science_status()
        add(
            "Claude Science control",
            not bool(science.get("control_error")),
            self.science_recovery_message(science)
            if science.get("control_error")
            else ("running" if science.get("running") else "stopped"),
        )

        failures = 0
        for label, ok, detail in checks:
            print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
            failures += 0 if ok else 1
        for name, provider in API_PROVIDERS.items():
            print(
                f"[INFO] {provider['label']} auth: "
                f"{'configured' if self.p.provider_keys[name].is_file() else 'not configured'}"
            )
        print(f"[INFO] ChatGPT Codex auth: {'configured' if self.codex_auth_configured() else 'not configured'}")
        if science_identity.get("schema") == "science-local-v2":
            print("[INFO] Claude Science identity: FinalKit local-only profile; no Claude account used")
        elif science_identity.get("schema") == "science-credentials-preserved":
            print("[INFO] Claude Science identity: unknown/real credentials preserved; direct start is blocked")
        record = self.read_gateway_record()
        if record and self.gateway_identity(record) and self.gateway_health(record):
            print(f"[OK] active gateway: {record.get('backend')} (identity verified)")
            print(f"[INFO] effective route: {self.route_description(str(record.get('backend')))}")
            self.print_science_model_note()
        else:
            print("[INFO] active gateway: stopped")
        if science.get("control_error"):
            print("[INFO] Claude Science: control unavailable; see the failed control check above")
        else:
            print(f"[INFO] Claude Science: {'running' if science.get('running') else 'stopped'}")
        return 0 if failures == 0 else 1

    def status(self) -> None:
        print(f"FinalKit root:       {self.p.root}")
        print(f"Deployed version:    {self.installed_package_version()}")
        print(f"Science data:       {self.p.data_dir}")
        print(f"Committed mode:     {self.current_mode() or 'unconfigured'}")
        for name, provider in API_PROVIDERS.items():
            print(
                f"{provider['label'] + ' auth:':<20}"
                f"{'configured' if self.p.provider_keys[name].is_file() else 'not configured'}"
            )
        print(f"ChatGPT Codex auth: {'configured' if self.codex_auth_configured() else 'not configured'}")
        record = self.read_gateway_record()
        health = self.gateway_health(record) if record and self.gateway_identity(record) else None
        if health and record:
            endpoint = (
                f"http://127.0.0.1:{self.gateway_port}/<private>"
                if record.get("backend") in API_PROVIDERS
                else str(record["endpoint"])
            )
            print(f"Gateway:            healthy ({record.get('backend')}, PID {record.get('pid')})")
            print(f"Gateway endpoint:   {endpoint}")
            print(f"Effective route:    {self.route_description(str(record.get('backend')))}")
            self.print_science_model_note()
        elif record:
            print("Gateway:            stale or identity check failed")
        else:
            print("Gateway:            stopped")
        science = self.science_status()
        if science.get("control_error"):
            print(
                "Claude Science:     control unavailable "
                f"(PID {science.get('pid', 'unknown')}, state {science.get('process_state', 'unknown')})"
            )
            print(f"Recovery:           {self.science_recovery_message(science)}")
        elif science.get("running"):
            print(f"Claude Science:     running (PID {science.get('pid')}, port {science.get('port')})")
            if record and health:
                print(
                    "Runtime identity:    "
                    + (
                        "matched"
                        if self.science_status_endpoint_matches(science, str(record["endpoint"]))
                        else "MISMATCH"
                    )
                )
        else:
            print("Claude Science:     stopped")
        try:
            identity = self.ensure_science_identity(check_only=True)
            if identity.get("schema") == "science-local-v2":
                print("Science identity:   FinalKit local-only; no Claude account used")
            elif identity.get("schema") == "science-local-missing":
                print("Science identity:   not initialized; run update-runtime or init-profile")
            else:
                print("Science identity:   unknown/real credentials preserved; direct start is blocked")
        except FinalKitError as exc:
            print(f"Science identity:   credential audit failed ({exc})")

    def capabilities(self) -> None:
        """Machine-readable command support; package versions never gate use."""

        print(json.dumps({"capabilities": RUNTIME_CAPABILITIES}, sort_keys=True))

    def show_log(self, which: str, lines: int) -> None:
        if which == "gateway":
            path = self.p.gateway_log
        elif which == "science":
            path = self.p.science_boot_log
        else:
            raise FinalKitError("log must be gateway or science")
        if not path.is_file():
            raise FinalKitError(f"log does not exist yet: {path}")
        print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fkctl", description="ScienceCodexFinalKit controller")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("FINALKIT_ROOT", DEFAULT_ROOT)))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("init-profile")
    for provider_name in API_PROVIDERS:
        sub.add_parser(f"configure-{provider_name}")
    sub.add_parser("configure-codex")
    sub.add_parser("configure-codex-device")
    sub.add_parser(
        "import-codex-auth",
        help="one-time import of an official ChatGPT Codex auth JSON from stdin",
    )
    sub.add_parser("login-linux-codex")
    claude = sub.add_parser("claude", help="run native Claude Code through a selected FinalKit backend")
    claude.add_argument("mode", choices=sorted(VALID_MODES))
    claude.add_argument("arguments", nargs=argparse.REMAINDER)
    gateway = sub.add_parser("gateway", help="select a backend without starting Claude Science")
    gateway.add_argument("mode", choices=sorted(VALID_MODES))
    for command in ("start", "switch", "restart", "test"):
        item = sub.add_parser(command)
        item.add_argument("mode", choices=sorted(VALID_MODES), nargs="?" if command == "restart" else None)
    sub.add_parser("smoke")
    sub.add_parser("test-codex-tiers")
    models = sub.add_parser("models", help="show persistent provider model routes")
    models.add_argument("--json", action="store_true", dest="as_json")
    discover_models = sub.add_parser(
        "discover-models",
        help="read the configured account's official callable model catalog without generation",
    )
    discover_models.add_argument("provider", choices=sorted(API_PROVIDERS))
    discover_models.add_argument("--limit", type=int, default=100)
    discover_models.add_argument("--json", action="store_true", dest="as_json")
    update_models = sub.add_parser(
        "update-models",
        help="atomically update one provider's persistent model routes",
    )
    update_models.add_argument("provider", choices=sorted(VALID_MODES))
    update_models.add_argument("--main")
    update_models.add_argument("--fast")
    update_models.add_argument("--opus")
    update_models.add_argument("--sonnet")
    update_models.add_argument("--haiku")
    update_models.add_argument(
        "--reasoning", "--effort", dest="effort", choices=sorted(GENERIC_REASONING_EFFORTS)
    )
    for role in CLAUDE_ROUTE_ROLES:
        update_models.add_argument(
            f"--reasoning-{role}",
            f"--effort-{role}",
            dest=f"effort_{role}",
            choices=sorted(GENERIC_REASONING_EFFORTS),
        )
    update_models.add_argument("--dry-run", action="store_true")
    update_models.add_argument("--restart", action="store_true")
    update_models.add_argument("--json", action="store_true", dest="as_json")
    sub.add_parser("url")
    sub.add_parser("status")
    sub.add_parser("capabilities")
    sub.add_parser("doctor")
    sub.add_parser("stop")
    logs = sub.add_parser("logs")
    logs.add_argument("which", choices=("gateway", "science"))
    logs.add_argument("--lines", type=int, default=120)
    return parser


def main() -> int:
    os.umask(0o077)
    args = build_parser().parse_args()
    manager = RuntimeManager(Paths(args.root))
    try:
        if args.command == "prepare":
            manager.prepare()
            print("PREPARE_OK")
        elif args.command == "init-profile":
            manager.init_profile()
            print("PROFILE_OK")
        elif args.command.startswith("configure-") and args.command.removeprefix("configure-") in API_PROVIDERS:
            manager.configure_provider(args.command.removeprefix("configure-"))
        elif args.command == "configure-codex":
            manager.configure_codex()
        elif args.command == "configure-codex-device":
            manager.configure_codex_device()
        elif args.command == "import-codex-auth":
            if sys.stdin.isatty():
                raise FinalKitError("import-codex-auth accepts auth JSON only through stdin")
            payload = bytearray(sys.stdin.buffer.read(MAX_CODEX_AUTH_BYTES + 1))
            try:
                manager.import_codex_auth(payload)
            finally:
                for index in range(len(payload)):
                    payload[index] = 0
        elif args.command == "login-linux-codex":
            manager.login_linux_codex()
        elif args.command == "claude":
            return manager.run_claude_code(args.mode, args.arguments)
        elif args.command == "gateway":
            manager.select_gateway(args.mode)
            print(f"ACTIVE_MODE={args.mode}")
            print(f"EFFECTIVE_ROUTE={manager.route_description(args.mode)}")
        elif args.command in {"start", "switch", "restart"}:
            mode = args.mode or manager.current_mode()
            if not mode:
                raise FinalKitError("no mode is selected; use deepseek, kimi, glm, or codex")
            url = manager.switch(mode)
            print(f"ACTIVE_MODE={mode}")
            print(f"EFFECTIVE_ROUTE={manager.route_description(mode)}")
            manager.print_science_model_note()
            print(url)
        elif args.command == "smoke":
            print(manager.smoke_local())
            print("SMOKE_OK")
        elif args.command == "test":
            response = manager.test_backend(args.mode)
            print(json.dumps(response, ensure_ascii=False, indent=2))
            print(f"BACKEND_OK mode={args.mode}")
        elif args.command == "test-codex-tiers":
            response = manager.test_codex_tiers()
            print(json.dumps(response, ensure_ascii=False, indent=2))
            print("CODEX_TIERS_OK actual configured routes verified")
        elif args.command == "models":
            routes = manager.model_routes()
            if args.as_json:
                print(json.dumps(routes, ensure_ascii=False, sort_keys=True))
            else:
                for provider_name in API_PROVIDERS:
                    route = routes["providers"][provider_name]
                    print(
                        f"{provider_name}: "
                        + " ".join(
                            f"{role}=({route[f'model_{role}']}, {route[f'reasoning_{role}']})"
                            for role in CLAUDE_ROUTE_ROLES
                        )
                    )
                route = routes["codex"]
                print(
                    "codex: "
                    + " ".join(
                        f"{role}=({route[f'model_{role}']}, {route[f'reasoning_{role}']})"
                        for role in CLAUDE_ROUTE_ROLES
                    )
                )
                print(f"CONFIG={manager.p.model_routes}")
        elif args.command == "discover-models":
            result = manager.discover_provider_models(args.provider, limit=args.limit)
            if args.as_json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                print(
                    f"OFFICIAL_MODEL_CATALOG provider={args.provider} "
                    f"models={result['total_models']} writes=none generation=none"
                )
                for index, model in enumerate(result["models"], start=1):
                    print(f"{index:3d} {model}")
                current = result["current"]
                for role in CLAUDE_ROUTE_ROLES:
                    print(
                        f"CURRENT {role} Model={current[f'model_{role}']} "
                        f"Reasoning={current[f'reasoning_{role}']} "
                        f"available={str(result['current_availability'][role]).lower()}"
                    )
                print(f"SOURCE={result['catalog_source']}")
        elif args.command == "update-models":
            with FileLock(manager.p.lock):
                result = manager.update_model_routes(
                    args.provider,
                    main=args.main,
                    fast=args.fast,
                    opus=args.opus,
                    sonnet=args.sonnet,
                    haiku=args.haiku,
                    effort=args.effort,
                    effort_opus=args.effort_opus,
                    effort_sonnet=args.effort_sonnet,
                    effort_haiku=args.effort_haiku,
                    dry_run=args.dry_run,
                    restart=args.restart,
                )
            if args.as_json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                state = "preview" if args.dry_run else "updated"
                print(f"MODEL_ROUTES_{state.upper()} provider={args.provider}")
                print(manager.route_description(args.provider, result["routes"]))
                if result["changed"] and not args.dry_run and not result["runtime_restarted"]:
                    print("The new route will be used on the next start.")
        elif args.command == "url":
            print(manager.science_url())
        elif args.command == "status":
            manager.status()
        elif args.command == "capabilities":
            manager.capabilities()
        elif args.command == "doctor":
            return manager.doctor()
        elif args.command == "stop":
            manager.stop()
            print("FinalKit runtime stopped.")
        elif args.command == "logs":
            manager.show_log(args.which, max(1, min(args.lines, 2000)))
        return 0
    except (FinalKitError, subprocess.TimeoutExpired, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
