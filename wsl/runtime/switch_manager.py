#!/usr/bin/env python3
"""Transactional runtime owner for ScienceCodexFinalKit.

The default runtime is native Claude Code plus exactly one loopback backend.
Claude Science remains an optional compatibility client and is never required
by the no-Claude-account workflow.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
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
NO_PROXY_LOOPBACK = "127.0.0.1,localhost,::1"
LINUX_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
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
CODEX_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
MODEL_ROUTE_SCHEMA_VERSION = 1
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]@+=-]{0,199}$")
RUNTIME_CAPABILITIES = (
    "browser-codex-oauth",
    "codex-device-oauth",
    "codex-route-display-names",
    "codex-three-tier-route",
    "codex-tier-test",
    "effective-route-output",
    "model-route-update",
    "native-browser-mcp",
    "persistent-model-routes",
    "provider-model-discovery",
    "runtime-update-v1",
    "native-provider-client",
    "shared-official-codex-auth",
)


class FinalKitError(RuntimeError):
    pass


def default_gateway_port(uid: int | None = None) -> int:
    """Return a deterministic per-UID loopback port for ordinary Linux users."""

    resolved_uid = os.getuid() if uid is None else uid
    if resolved_uid < 1000:
        return DEFAULT_GATEWAY_PORT
    available = 65535 - DEFAULT_GATEWAY_PORT + 1
    return DEFAULT_GATEWAY_PORT + ((resolved_uid - 1000) % available)


class Paths:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.runtime = self.root / "runtime"
        self.bridge = self.root / "bridge"
        self.config = self.root / "config"
        self.bridge_python = self.bridge / ".venv" / "bin" / "python"
        self.bridge_config = self.bridge / "config.json"
        self.model_routes = self.config / "model-routes.json"
        self.browser_mcp_config = self.config / "claude-browser-mcp.json"
        self.client_home = Path.home() / ".finalkit-client"
        # Legacy Science state is retained only to identify and stop a process
        # left by an older package; no current start or login path writes it.
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
        self.science = Path.home() / ".local" / "bin" / "claude-science"
        self.claude = Path.home() / ".local" / "bin" / "claude"
        self.codex = Path.home() / ".local" / "bin" / "codex"

    def ensure_private_tree(self) -> None:
        for directory in (
            self.root,
            self.runtime,
            self.config,
            self.client_home,
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
        requested_port = os.environ.get("FINALKIT_GATEWAY_PORT", "").strip()
        try:
            self.gateway_port = int(requested_port) if requested_port else default_gateway_port()
        except ValueError as exc:
            raise FinalKitError("FINALKIT_GATEWAY_PORT must be an integer") from exc
        if not 1024 <= self.gateway_port <= 65535:
            raise FinalKitError("FINALKIT_GATEWAY_PORT must be between 1024 and 65535")
        self.science_port = int(os.environ.get("FINALKIT_SCIENCE_PORT", DEFAULT_SCIENCE_PORT))

    def require_runtime(self) -> None:
        required = (
            (self.p.claude, "Claude Code"),
            (self.p.codex, "Linux Codex CLI"),
            (self.p.direct_gateway, "direct gateway"),
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
        return {
            "schema_version": MODEL_ROUTE_SCHEMA_VERSION,
            "providers": {
                name: {
                    "main": str(provider["default_model"]),
                    "fast": str(provider["fast_model"]),
                }
                for name, provider in API_PROVIDERS.items()
            },
            "codex": {
                "opus": "gpt-5.6-sol",
                "sonnet": "gpt-5.6-terra",
                "haiku": "gpt-5.6-luna",
                "reasoning_effort": "max",
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
            if not isinstance(route, dict):
                raise FinalKitError(f"model route for {name} must be an object")
            normalized["providers"][name] = {
                "main": self.validate_model_id(route.get("main"), f"{name} main model"),
                "fast": self.validate_model_id(route.get("fast"), f"{name} fast model"),
            }
        effort_value = codex.get("reasoning_effort")
        if not isinstance(effort_value, str):
            raise FinalKitError("Codex reasoning effort must be a string")
        normalized["codex"] = {
            "opus": self.validate_model_id(codex.get("opus"), "Codex Opus-tier model"),
            "sonnet": self.validate_model_id(codex.get("sonnet"), "Codex Sonnet-tier model"),
            "haiku": self.validate_model_id(codex.get("haiku"), "Codex Haiku-tier model"),
            "reasoning_effort": effort_value.strip().lower(),
        }
        if normalized["codex"]["reasoning_effort"] not in CODEX_REASONING_EFFORTS:
            raise FinalKitError(
                "Codex reasoning effort must be one of: "
                + ", ".join(sorted(CODEX_REASONING_EFFORTS))
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
                            routes["codex"][tier] = str(value)
                elif exact_values != known_broken_defaults:
                    for tier, value in exact_values.items():
                        if value:
                            routes["codex"][tier] = str(value)
            legacy_primary = legacy.get("codex_model")
            if (
                isinstance(legacy_primary, str)
                and legacy_primary
                and legacy_primary != "gpt-5.6-sol"
            ):
                routes["codex"]["opus"] = legacy_primary
            effort = legacy.get("codex_reasoning_effort")
            if isinstance(effort, str) and effort.strip().lower() in CODEX_REASONING_EFFORTS:
                routes["codex"]["reasoning_effort"] = effort.strip().lower()
        # Environment values are a one-time migration source.  Once the file
        # exists, subsequent package updates never overwrite the user's routes.
        for name, provider in API_PROVIDERS.items():
            prefix = str(provider["env_prefix"])
            routes["providers"][name]["main"] = os.environ.get(
                f"{prefix}_MODEL", routes["providers"][name]["main"]
            )
            routes["providers"][name]["fast"] = os.environ.get(
                f"{prefix}_FAST_MODEL", routes["providers"][name]["fast"]
            )
        routes["codex"]["opus"] = os.environ.get(
            "FINALKIT_CODEX_OPUS_MODEL",
            os.environ.get("FINALKIT_CODEX_MODEL", routes["codex"]["opus"]),
        )
        routes["codex"]["sonnet"] = os.environ.get(
            "FINALKIT_CODEX_SONNET_MODEL", routes["codex"]["sonnet"]
        )
        routes["codex"]["haiku"] = os.environ.get(
            "FINALKIT_CODEX_HAIKU_MODEL",
            os.environ.get("FINALKIT_CODEX_FAST_MODEL", routes["codex"]["haiku"]),
        )
        routes["codex"]["reasoning_effort"] = os.environ.get(
            "FINALKIT_CODEX_REASONING_EFFORT", routes["codex"]["reasoning_effort"]
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
        normalized = self.validate_model_routes(payload)
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
        return self.validate_model_routes(payload)

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
            "current_main_available": current["main"] in discovered,
            "current_fast_available": current["fast"] in discovered,
            "models": shown,
            "total_models": len(all_models),
            "truncated": len(all_models) > len(shown),
            "writes_performed": False,
            "generation_request_performed": False,
        }

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
        dry_run: bool = False,
        restart: bool = False,
    ) -> dict[str, Any]:
        if provider not in VALID_MODES:
            raise FinalKitError("provider must be deepseek, kimi, glm, or codex")
        routes = self.model_routes()
        before = json.loads(json.dumps(routes))
        if provider in API_PROVIDERS:
            if opus is not None or sonnet is not None or haiku is not None or effort is not None:
                raise FinalKitError("API providers accept only --main and --fast")
            if main is None and fast is None:
                raise FinalKitError("provide --main and/or --fast")
            target = routes["providers"][provider]
            if main is not None:
                target["main"] = self.validate_model_id(main, f"{provider} main model")
            if fast is not None:
                target["fast"] = self.validate_model_id(fast, f"{provider} fast model")
        else:
            if main is not None or fast is not None:
                raise FinalKitError("Codex accepts --opus, --sonnet, --haiku and --effort")
            if all(value is None for value in (opus, sonnet, haiku, effort)):
                raise FinalKitError("provide at least one of --opus, --sonnet, --haiku or --effort")
            target = routes["codex"]
            if opus is not None:
                target["opus"] = self.validate_model_id(opus, "Codex Opus-tier model")
            if sonnet is not None:
                target["sonnet"] = self.validate_model_id(sonnet, "Codex Sonnet-tier model")
            if haiku is not None:
                target["haiku"] = self.validate_model_id(haiku, "Codex Haiku-tier model")
            if effort is not None:
                effort = effort.strip().lower()
                if effort not in CODEX_REASONING_EFFORTS:
                    raise FinalKitError(
                        "Codex reasoning effort must be one of: "
                        + ", ".join(sorted(CODEX_REASONING_EFFORTS))
                    )
                target["reasoning_effort"] = effort
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
            if affected_active and self.science_lock_process() is not None:
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
                    if previous_science_running:
                        self.science_stop()
                    self.stop_gateway()
                atomic_write(
                    self.p.model_routes,
                    json.dumps(routes, indent=2, ensure_ascii=False) + "\n",
                )
                self.ensure_bridge_config()
                if affected_active:
                    endpoint = self.spawn_gateway(previous_backend)
                    if not self.gateway_health():
                        raise FinalKitError("updated gateway failed its health check")
                    self.write_mode(previous_backend)
                    restarted = True
            except Exception as primary_error:
                rollback_errors: list[str] = []
                if affected_active:
                    if previous_science_running:
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
                    "model_default": route["main"],
                    "model_fast": route["fast"],
                    "instance_id": instance,
                    "profile_id": provider["profile_id"],
                    "offline_smoke": key_override is not None,
                }
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
            if self.science_process_persistently_blocked(pid):
                blocked_owner = owner or {"pid": pid, "owned": True}
                blocked_owner["process_state"] = "D"
                return self.science_control_error(
                    "Claude Science owner is blocked in uninterruptible I/O; its page and control socket are not reliable",
                    blocked_owner,
                )
            if owner and (not owner.get("owned") or int(owner.get("pid", 0) or 0) != pid):
                return self.science_control_error(
                    "Claude Science status PID conflicts with the live operon.lock owner",
                    owner,
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

    def ensure_bridge_config(self) -> None:
        route = self.model_routes()["codex"]
        codex_opus_model = route["opus"]
        codex_sonnet_model = route["sonnet"]
        codex_haiku_model = route["haiku"]
        codex_reasoning_effort = route["reasoning_effort"]
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
            "codex_reasoning_effort": codex_reasoning_effort,
            "codex_model_map": {
                # Claude-compatible clients use these request aliases.  The
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
            main = routes["providers"][mode]["main"]
            fast = routes["providers"][mode]["fast"]
            return (
                f"Opus/Sonnet request aliases -> {provider['label']} {main}; "
                f"Haiku request alias -> {provider['label']} {fast}"
            )
        if mode == "codex":
            opus = routes["codex"]["opus"]
            sonnet = routes["codex"]["sonnet"]
            haiku = routes["codex"]["haiku"]
            effort = routes["codex"]["reasoning_effort"]
            return (
                f"Opus request alias -> ChatGPT Codex {opus} (effort={effort}); "
                f"Sonnet -> {sonnet} (effort={effort}); "
                f"Haiku -> {haiku} (effort={effort})"
            )
        return "unconfigured"

    @staticmethod
    def print_model_alias_note() -> None:
        print(
            "Claude-compatible request aliases are local routing labels; FinalKit's model catalog "
            "and EFFECTIVE_ROUTE expose the configured upstream route."
        )

    def prepare(self) -> None:
        """Create deterministic non-secret runtime configuration."""

        self.require_runtime()
        self.ensure_bridge_config()

    def _switch_locked(self, mode: str) -> str:
        if mode not in VALID_MODES:
            raise FinalKitError("mode must be deepseek, kimi, glm, or codex")
        previous_mode = self.current_mode()
        previous_record = self.read_gateway_record()
        previous_gateway_running = bool(
            previous_record and self.gateway_identity(previous_record) and self.gateway_health(previous_record)
        )

        # No start path owns Claude Science.  If a previous FinalKit package
        # left a fully verified Science process behind, stop it before reusing
        # or switching the provider gateway.
        if self.science_lock_process() is not None:
            science = self.science_status()
            if science.get("control_error"):
                raise FinalKitError(self.science_recovery_message(science))
            if science.get("running"):
                self.science_stop()

        if previous_gateway_running and previous_record and previous_record.get("backend") == mode:
            self.write_mode(mode)
            return str(previous_record["endpoint"])
        previous_backend = (
            str(previous_record.get("backend"))
            if previous_gateway_running and previous_record
            else previous_mode
        )
        self.stop_gateway()
        try:
            endpoint = self.spawn_gateway(mode)
            if not self.gateway_health():
                raise FinalKitError("post-switch runtime identity verification failed")
            self.write_mode(mode)
            return endpoint
        except Exception as primary_error:
            rollback_errors: list[str] = []
            try:
                self.stop_gateway()
            except Exception as exc:
                rollback_errors.append(f"stop gateway: {exc}")
            if previous_backend and previous_gateway_running:
                try:
                    self.spawn_gateway(previous_backend)
                except Exception as exc:
                    rollback_errors.append(f"restore {previous_backend}: {exc}")
            message = f"switch to {mode} failed: {primary_error}"
            if rollback_errors:
                message += "\nrollback incomplete: " + "; ".join(rollback_errors)
            else:
                message += "\nprevious runtime state was restored"
            raise FinalKitError(message) from primary_error

    def switch(self, mode: str) -> str:
        """Backward-compatible gateway-only switch; never start Claude Science."""

        with FileLock(self.p.lock):
            return self._switch_locked(mode)

    def select_gateway(self, mode: str) -> str:
        """Select a backend without requiring or starting Claude Science."""

        with FileLock(self.p.lock):
            return self._switch_locked(mode)

    def stop(self) -> None:
        with FileLock(self.p.lock):
            if self.science_lock_process() is not None:
                self.science_stop()
            self.stop_gateway()

    def configure_provider(self, provider_name: str) -> None:
        if provider_name not in API_PROVIDERS:
            raise FinalKitError(f"unknown API provider: {provider_name}")
        provider = API_PROVIDERS[provider_name]
        first = getpass.getpass(f"{provider['label']} API key (hidden): ")
        if not first or "\n" in first or "\r" in first:
            raise FinalKitError("API key cannot be empty or contain a newline")
        key_path = self.p.provider_keys[provider_name]
        atomic_write(key_path, first)
        first = ""
        print(f"{provider['label']} key saved inside this Linux user's WSL home: {key_path}")

    def configure_codex(self) -> None:
        self.require_runtime()
        with FileLock(self.p.lock):
            self.ensure_bridge_config()
            environment = codex_network_environment(
                self.p.client_home,
                "https://auth.openai.com",
            )
            print("Starting the official Codex browser login in FinalKit's isolated client home.")
            print("No token value will be printed; complete the browser flow opened by Codex.")
            self._replace_codex_auth(
                [str(self.p.codex), *CODEX_FILE_AUTH_ARGS, "login"],
                environment,
                "the official Codex browser login did not complete. "
                "If localhost OAuth is unavailable, run: fkctl configure-codex-device",
            )

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
        environment["ANTHROPIC_AUTH_TOKEN"] = "finalkit-local-token"
        routes = self.model_routes()
        if mode in API_PROVIDERS:
            provider = API_PROVIDERS[mode]
            main = str(routes["providers"][mode]["main"])
            fast = str(routes["providers"][mode]["fast"])
            label = str(provider["label"])
            effort_suffix = ""
        else:
            codex_route = routes["codex"]
            main = str(codex_route["opus"])
            fast = str(codex_route["haiku"])
            label = "ChatGPT Codex"
            effort_suffix = f" {codex_route['reasoning_effort']}"
        sonnet = (
            str(routes["codex"]["sonnet"])
            if mode == "codex"
            else main
        )
        display = lambda model: f"{label} {model}{effort_suffix}"  # noqa: E731
        environment.update(
            {
                "ANTHROPIC_MODEL": main,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": main,
                "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": display(main),
                "ANTHROPIC_DEFAULT_SONNET_MODEL": sonnet,
                "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": display(sonnet),
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": fast,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": display(fast),
                "ANTHROPIC_CUSTOM_MODEL_OPTION": main,
                "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": display(main),
                "CLAUDE_CODE_SUBAGENT_MODEL": sonnet,
                "DISABLE_AUTOUPDATER": "1",
            }
        )
        append_no_proxy(environment)
        result = subprocess.run([str(self.p.claude), *arguments], env=environment, check=False)
        return result.returncode

    def write_browser_mcp_config(self, browser_url: str) -> Path:
        """Write a session-scoped config for a fixed Windows-side MCP launcher."""

        parsed = urllib.parse.urlsplit(browser_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username
            or parsed.password
            or not parsed.port
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise FinalKitError("browser URL must be an HTTP loopback origin with an explicit port")
        config = {
            "mcpServers": {
                "chrome-devtools": {
                    "type": "stdio",
                    "command": "cmd.exe",
                    "args": [
                        "/d",
                        "/c",
                        "%LOCALAPPDATA%/ScienceCodexFinalKit/browser-mcp.cmd",
                    ],
                }
            }
        }
        atomic_write(
            self.p.browser_mcp_config,
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return self.p.browser_mcp_config

    def smoke_local(self) -> str:
        self.require_runtime()
        with FileLock(self.p.lock):
            if self.read_gateway_record():
                record = self.read_gateway_record()
                if not record or not self.gateway_identity(record) or not self.gateway_health(record):
                    raise FinalKitError("an unhealthy runtime is active; stop or repair it before smoke")
                return "active native-client gateway passed the no-cost identity smoke"

            passed: list[str] = []
            for provider_name in API_PROVIDERS:
                endpoint = self.spawn_gateway(provider_name, key_override="finalkit-local-smoke-key")
                try:
                    health = self.gateway_health()
                    if not health or health.get("finalkit_backend") != provider_name:
                        raise FinalKitError(f"local {provider_name} gateway identity verification failed")
                    passed.append(provider_name)
                finally:
                    self.stop_gateway()
            return "local provider gateways + native Claude Code smoke passed: " + ", ".join(passed)

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
        if not isinstance(model_map, dict):
            raise FinalKitError("connector Codex model map is invalid")
        effort = str(config.get("codex_reasoning_effort") or "").strip().lower()
        aliases = tuple(
            (
                f"{family.removeprefix('claude-')} -> {model_map.get(family)} ({effort})",
                alias,
            )
            for family, alias in (
                ("claude-opus", "claude-opus-4-8"),
                ("claude-sonnet", "claude-sonnet-4-5"),
                ("claude-haiku", "claude-haiku-4-5-20251001"),
            )
        )
        if effort not in CODEX_REASONING_EFFORTS or any(
            not isinstance(model_map.get(family), str) or not model_map.get(family)
            for family in ("claude-opus", "claude-sonnet", "claude-haiku")
        ):
            raise FinalKitError("connector Codex three-tier route is incomplete")
        catalog = self.codex_model_catalog(record)
        expected_catalog = {
            alias: f"ChatGPT Codex | {model_map[family]} | {effort}"
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
            "reasoning_effort": effort,
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
        ):
            add(label, path.is_file() and path.stat().st_size > 0, str(path))
            if path.exists():
                add(f"{label} permission 600", stat.S_IMODE(path.stat().st_mode) == 0o600)
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
        effort = str(config.get("codex_reasoning_effort", "")).strip().lower()
        codex_route = routes.get("codex", {}) if isinstance(routes, dict) else {}
        route_ok = (
            routes_ok
            and isinstance(model_map, dict)
            and model_map.get("claude-opus") == codex_route.get("opus")
            and model_map.get("claude-sonnet") == codex_route.get("sonnet")
            and model_map.get("claude-haiku") == codex_route.get("haiku")
            and not config.get("force_model")
            and effort == codex_route.get("reasoning_effort")
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
                "codex_reasoning_effort",
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
        print("[INFO] Claude account: not used by the default native Claude Code workflow")
        record = self.read_gateway_record()
        if record and self.gateway_identity(record) and self.gateway_health(record):
            print(f"[OK] active gateway: {record.get('backend')} (identity verified)")
            print(f"[INFO] effective route: {self.route_description(str(record.get('backend')))}")
            self.print_model_alias_note()
        else:
            print("[INFO] active gateway: stopped")
        print(
            "[INFO] Optional Claude Science client: "
            + ("installed but excluded from default starts" if self.p.science.is_file() else "not installed")
        )
        return 0 if failures == 0 else 1

    def status(self) -> None:
        print(f"FinalKit root:       {self.p.root}")
        print(f"Deployed version:    {self.installed_package_version()}")
        print(f"Native client:       {self.p.claude}")
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
            self.print_model_alias_note()
        elif record:
            print("Gateway:            stale or identity check failed")
        else:
            print("Gateway:            stopped")
        print("Claude account:     not used")
        print(
            "Optional Science:    "
            + ("installed; not used by default" if self.p.science.is_file() else "not installed")
        )

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
    parser = argparse.ArgumentParser(
        prog="fkctl",
        description="FinalKit no-Claude-account WSL client and provider controller",
    )
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("FINALKIT_ROOT", DEFAULT_ROOT)))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    for provider_name in API_PROVIDERS:
        sub.add_parser(f"configure-{provider_name}")
    sub.add_parser("configure-codex")
    sub.add_parser("configure-codex-device")
    sub.add_parser("login-linux-codex")
    claude = sub.add_parser("claude", help="run native Claude Code through a selected FinalKit backend")
    claude.add_argument("mode", choices=sorted(VALID_MODES))
    claude.add_argument("arguments", nargs=argparse.REMAINDER)
    browser_mcp = sub.add_parser(
        "browser-mcp-config",
        help="write an isolated Claude Code MCP config for a loopback Chrome endpoint",
    )
    browser_mcp.add_argument("--browser-url", required=True)
    gateway = sub.add_parser("gateway", help="select a backend without starting Claude Science")
    gateway.add_argument("mode", choices=sorted(VALID_MODES))
    for command in ("start", "switch", "restart", "test"):
        item = sub.add_parser(
            command,
            help=("select a provider gateway without starting Claude Science" if command != "test" else None),
        )
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
    update_models.add_argument("--effort", choices=sorted(CODEX_REASONING_EFFORTS))
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
        elif args.command.startswith("configure-") and args.command.removeprefix("configure-") in API_PROVIDERS:
            manager.configure_provider(args.command.removeprefix("configure-"))
        elif args.command == "configure-codex":
            manager.configure_codex()
        elif args.command == "configure-codex-device":
            manager.configure_codex_device()
        elif args.command == "login-linux-codex":
            manager.login_linux_codex()
        elif args.command == "claude":
            return manager.run_claude_code(args.mode, args.arguments)
        elif args.command == "browser-mcp-config":
            print(manager.write_browser_mcp_config(args.browser_url))
        elif args.command == "gateway":
            manager.select_gateway(args.mode)
            print(f"ACTIVE_MODE={args.mode}")
            print(f"EFFECTIVE_ROUTE={manager.route_description(args.mode)}")
        elif args.command in {"start", "switch", "restart"}:
            mode = args.mode or manager.current_mode()
            if not mode:
                raise FinalKitError("no mode is selected; use deepseek, kimi, glm, or codex")
            endpoint = manager.switch(mode)
            print(f"ACTIVE_MODE={mode}")
            print(f"EFFECTIVE_ROUTE={manager.route_description(mode)}")
            manager.print_model_alias_note()
            print(f"GATEWAY_ENDPOINT={endpoint}")
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
                    print(f"{provider_name}: main={route['main']} fast={route['fast']}")
                route = routes["codex"]
                print(
                    "codex: "
                    f"opus={route['opus']} sonnet={route['sonnet']} "
                    f"haiku={route['haiku']} effort={route['reasoning_effort']}"
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
                print(
                    f"CURRENT main={current['main']} "
                    f"available={str(result['current_main_available']).lower()}"
                )
                print(
                    f"CURRENT fast={current['fast']} "
                    f"available={str(result['current_fast_available']).lower()}"
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
            record = manager.read_gateway_record()
            if not record or not manager.gateway_identity(record) or not manager.gateway_health(record):
                raise FinalKitError("the FinalKit gateway is not running")
            print(record["endpoint"])
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
