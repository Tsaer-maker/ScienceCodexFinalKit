#!/usr/bin/env python3
"""Optional Codex-to-Claude multi-agent integration for Switchboard.

This owner installs one pinned upstream Codex plugin into Switchboard's
isolated Linux Codex home.  It never reads or writes Windows state, Claude
Science credentials, provider keys, or the user's ordinary Claude/Codex homes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


UPSTREAM_URL = "https://github.com/coredo-eu/codex-claude-orchestrator.git"
UPSTREAM_COMMIT = "c996b497c6682f4695b5aa342610527731712c51"
UPSTREAM_VERSION = "0.3.1"
MARKETPLACE_NAME = "codex-claude-orchestrator"
PLUGIN_NAME = "codex-claude-orchestrator"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
LINUX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
CLAUDE_FLAGS = (
    "--agents",
    "--model",
    "--effort",
    "--name",
    "--session-id",
    "--resume",
    "--settings",
    "--setting-sources",
    "--strict-mcp-config",
    "--append-system-prompt-file",
    "--disallowedTools",
)


class AgentsError(RuntimeError):
    pass


def all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(all_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(all_strings(item))
        return result
    return []


class AgentsManager:
    def __init__(self, root: Path, *, real_home: Path | None = None):
        self.root = root.resolve()
        # ``real_home`` is injectable so the offline contract can prove the
        # complete status path without reading the installed user's Codex or
        # Claude state. Production callers deliberately use the current HOME.
        self.real_home = (real_home if real_home is not None else Path.home()).resolve()
        self.client_home = self.real_home / ".finalkit-client"
        self.integrations = self.root / "integrations"
        self.checkout = self.integrations / MARKETPLACE_NAME
        self.codex = self.real_home / ".local" / "bin" / "codex"
        self.claude = self.real_home / ".local" / "bin" / "claude"
        self.toggle = (
            self.checkout
            / "plugins"
            / PLUGIN_NAME
            / "skills"
            / "claude-pty-agents"
            / "scripts"
            / "toggle-agents.zsh"
        )
        self.disabled_marker = self.client_home / ".codex" / "claude-pty-agents.disabled"

    def environment(self) -> dict[str, str]:
        keep = {
            "LANG",
            "TZ",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
            "WSL_DISTRO_NAME",
            "WSL_INTEROP",
            "TERM",
            "COLORTERM",
            "NO_COLOR",
            "CLICOLOR",
            "CLICOLOR_FORCE",
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in keep or key.startswith("LC_")
        }
        environment["HOME"] = str(self.client_home)
        environment["PATH"] = f"{self.real_home / '.local' / 'bin'}:{LINUX_PATH}"
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        capture: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=self.environment(),
                text=True,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                check=False,
            )
        except OSError as exc:
            raise AgentsError(f"could not run {command[0]}: {exc}") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise AgentsError(
                f"command failed ({result.returncode}): {' '.join(command)}"
                + (f"\n{detail}" if detail else "")
            )
        return result

    def codex_json(self, arguments: list[str]) -> dict[str, Any]:
        result = self.run(
            [str(self.codex), "-c", 'cli_auth_credentials_store="file"', *arguments]
        )
        try:
            value = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentsError(f"Codex returned invalid JSON for {' '.join(arguments)}") from exc
        if not isinstance(value, dict):
            raise AgentsError(f"Codex returned a non-object for {' '.join(arguments)}")
        return value

    def dependency_checks(self) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []
        for name in ("git", "zsh", "jq", "flock", "ps", "sed", "awk", "tr", "sha256sum"):
            resolved = shutil.which(name, path=self.environment()["PATH"])
            checks.append((name, bool(resolved), resolved or "missing; rerun Build.cmd"))
        checks.append(("Linux /proc", Path("/proc/self/stat").is_file(), "/proc/self/stat"))
        checks.append(("Codex CLI", self.codex.is_file(), str(self.codex)))
        checks.append(("Claude Code", self.claude.is_file(), str(self.claude)))
        if self.codex.is_file():
            plugin_help = self.run([str(self.codex), "plugin", "--help"], check=False)
            checks.append(
                (
                    "Codex plugin surface",
                    plugin_help.returncode == 0 and "marketplace" in (plugin_help.stdout or ""),
                    "codex plugin marketplace",
                )
            )
        if self.claude.is_file():
            claude_help = self.run([str(self.claude), "--help"], check=False)
            help_text = (claude_help.stdout or "") + (claude_help.stderr or "")
            missing = [flag for flag in CLAUDE_FLAGS if flag not in help_text]
            checks.append(
                (
                    "Claude worker flags",
                    claude_help.returncode == 0 and not missing,
                    "all required flags" if not missing else "missing " + ", ".join(missing),
                )
            )
        return checks

    def require_dependencies(self) -> None:
        failed = [name for name, ok, _ in self.dependency_checks() if not ok]
        if failed:
            raise AgentsError("multi-agent prerequisites failed: " + ", ".join(failed))

    def git_output(self, arguments: list[str], *, cwd: Path | None = None) -> str:
        return self.run(["git", *arguments], cwd=cwd).stdout.strip()

    def validate_checkout(self) -> None:
        if self.checkout.is_symlink() or not (self.checkout / ".git").is_dir():
            raise AgentsError(f"multi-agent checkout is not an owned Git tree: {self.checkout}")
        origin = self.git_output(["remote", "get-url", "origin"], cwd=self.checkout)
        if origin.rstrip("/") not in {UPSTREAM_URL.rstrip("/"), UPSTREAM_URL.removesuffix(".git")}:
            raise AgentsError(f"unexpected multi-agent origin: {origin}")
        head = self.git_output(["rev-parse", "HEAD"], cwd=self.checkout)
        if head != UPSTREAM_COMMIT:
            raise AgentsError(f"multi-agent checkout is not pinned: {head}")
        if self.git_output(["status", "--porcelain"], cwd=self.checkout):
            raise AgentsError("multi-agent checkout has local changes; refusing to replace them")
        if not (self.checkout / "LICENSE").is_file() or not self.toggle.is_file():
            raise AgentsError("multi-agent checkout is incomplete")

    def install_checkout(self) -> None:
        self.integrations.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.integrations.chmod(0o700)
        if self.checkout.exists() or self.checkout.is_symlink():
            if self.checkout.is_symlink() or not (self.checkout / ".git").is_dir():
                raise AgentsError(f"refusing unknown integration path: {self.checkout}")
            if self.git_output(["status", "--porcelain"], cwd=self.checkout):
                raise AgentsError("multi-agent checkout has local changes; refusing to replace them")
            origin = self.git_output(["remote", "get-url", "origin"], cwd=self.checkout)
            if origin.rstrip("/") not in {UPSTREAM_URL.rstrip("/"), UPSTREAM_URL.removesuffix(".git")}:
                raise AgentsError(f"unexpected multi-agent origin: {origin}")
            probe = self.run(
                ["git", "cat-file", "-e", f"{UPSTREAM_COMMIT}^{{commit}}"],
                cwd=self.checkout,
                check=False,
            )
            if probe.returncode != 0:
                self.run(["git", "fetch", "--no-tags", "origin", UPSTREAM_COMMIT], cwd=self.checkout)
            self.run(["git", "checkout", "--detach", UPSTREAM_COMMIT], cwd=self.checkout)
            self.validate_checkout()
            return

        temporary = Path(tempfile.mkdtemp(prefix=".agents-install.", dir=self.integrations))
        try:
            self.run(["git", "clone", "--no-checkout", UPSTREAM_URL, str(temporary)])
            probe = self.run(
                ["git", "cat-file", "-e", f"{UPSTREAM_COMMIT}^{{commit}}"],
                cwd=temporary,
                check=False,
            )
            if probe.returncode != 0:
                self.run(["git", "fetch", "--no-tags", "origin", UPSTREAM_COMMIT], cwd=temporary)
            self.run(["git", "checkout", "--detach", UPSTREAM_COMMIT], cwd=temporary)
            os.replace(temporary, self.checkout)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        self.validate_checkout()

    def marketplace_state(self) -> dict[str, Any]:
        return self.codex_json(["plugin", "marketplace", "list", "--json"])

    def plugin_state(self, *, available: bool = False) -> dict[str, Any]:
        arguments = ["plugin", "list"]
        if available:
            arguments.append("--available")
        arguments.append("--json")
        return self.codex_json(arguments)

    @staticmethod
    def contains_named(value: dict[str, Any], name: str) -> bool:
        return name in all_strings(value)

    @staticmethod
    def marketplace_record(value: dict[str, Any]) -> dict[str, Any] | None:
        records = value.get("marketplaces")
        if not isinstance(records, list):
            return None
        return next(
            (
                record
                for record in records
                if isinstance(record, dict) and record.get("name") == MARKETPLACE_NAME
            ),
            None,
        )

    @staticmethod
    def plugin_record(value: dict[str, Any], collection: str) -> dict[str, Any] | None:
        records = value.get(collection)
        if not isinstance(records, list):
            return None
        return next(
            (
                record
                for record in records
                if isinstance(record, dict)
                and record.get("pluginId") == PLUGIN_SELECTOR
                and record.get("name") == PLUGIN_NAME
                and record.get("marketplaceName") == MARKETPLACE_NAME
            ),
            None,
        )

    def marketplace_record_matches(self, record: dict[str, Any] | None) -> bool:
        if not record:
            return False
        root = record.get("root")
        if not isinstance(root, str) or not root:
            return False
        try:
            return Path(root).resolve() == self.checkout
        except OSError:
            return False

    def plugin_record_matches(
        self, record: dict[str, Any] | None, *, require_enabled: bool
    ) -> bool:
        if not record:
            return False
        version = str(record.get("version") or "").split("+", 1)[0]
        source = record.get("source")
        source_path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(source_path, str) or not source_path:
            return False
        expected_source = (self.checkout / "plugins" / PLUGIN_NAME).resolve()
        try:
            source_matches = Path(source_path).resolve() == expected_source
        except OSError:
            return False
        enabled_matches = not require_enabled or record.get("enabled") is True
        return version == UPSTREAM_VERSION and source_matches and enabled_matches

    def register_plugin(self) -> None:
        marketplaces = self.marketplace_state()
        marketplace = self.marketplace_record(marketplaces)
        if marketplace and not self.marketplace_record_matches(marketplace):
            raise AgentsError(
                "the expected marketplace name is already bound to an unexpected root"
            )
        if not marketplace:
            self.codex_json(
                ["plugin", "marketplace", "add", str(self.checkout), "--json"]
            )
            marketplace = self.marketplace_record(self.marketplace_state())
        if not self.marketplace_record_matches(marketplace):
            raise AgentsError("Codex did not register the exact pinned marketplace root")

        installed = self.plugin_state()
        installed_plugin = self.plugin_record(installed, "installed")
        if installed_plugin and not self.plugin_record_matches(
            installed_plugin, require_enabled=True
        ):
            self.codex_json(["plugin", "remove", PLUGIN_SELECTOR, "--json"])
            installed_plugin = None
        if not installed_plugin:
            available = self.codex_json(
                [
                    "plugin",
                    "list",
                    "--marketplace",
                    MARKETPLACE_NAME,
                    "--available",
                    "--json",
                ]
            )
            available_plugin = self.plugin_record(available, "available")
            if not self.plugin_record_matches(available_plugin, require_enabled=False):
                raise AgentsError(
                    "pinned marketplace does not expose the expected plugin version/source"
                )
            self.codex_json(["plugin", "add", PLUGIN_SELECTOR, "--json"])
        installed_plugin = self.plugin_record(self.plugin_state(), "installed")
        if not self.plugin_record_matches(installed_plugin, require_enabled=True):
            raise AgentsError("Codex did not install and enable the exact pinned plugin")

    def codex_login_ready(self) -> bool:
        if not self.codex.is_file():
            return False
        result = self.run(
            [
                str(self.codex),
                "-c",
                'cli_auth_credentials_store="file"',
                "login",
                "status",
            ],
            check=False,
        )
        return result.returncode == 0

    def print_status(self, *, require_ready: bool = False) -> int:
        checks = self.dependency_checks()
        for name, ok, detail in checks:
            print(f"CHECK {name}: {'ok' if ok else 'BLOCKED'} ({detail})")

        checkout_ok = False
        head = "missing"
        if self.checkout.exists() and not self.checkout.is_symlink():
            try:
                head = self.git_output(["rev-parse", "HEAD"], cwd=self.checkout)
                self.validate_checkout()
                checkout_ok = True
            except AgentsError as exc:
                head = str(exc)
        print(
            f"UPSTREAM version={UPSTREAM_VERSION} commit={UPSTREAM_COMMIT} "
            f"checkout={'ok' if checkout_ok else 'missing-or-invalid'} detail={head}"
        )

        marketplace_ok = False
        plugin_ok = False
        if self.codex.is_file():
            try:
                marketplace_ok = self.marketplace_record_matches(
                    self.marketplace_record(self.marketplace_state())
                )
                plugin_ok = self.plugin_record_matches(
                    self.plugin_record(self.plugin_state(), "installed"),
                    require_enabled=True,
                )
            except AgentsError:
                pass
        plugin_enabled = plugin_ok and not self.disabled_marker.exists()
        print(f"MARKETPLACE configured={str(marketplace_ok).lower()}")
        print(f"PLUGIN installed={str(plugin_ok).lower()} selector={PLUGIN_SELECTOR}")
        print(f"PLUGIN enabled={str(plugin_enabled).lower()}")
        login_ready = self.codex_login_ready()
        print(f"CODEX_LOGIN configured={str(login_ready).lower()} owner={self.client_home / '.codex' / 'auth.json'}")
        print(
            "AUTH_BOUNDARY Windows-auth=not-injected Science-identity=not-mutated "
            "provider-keys=not-copied"
        )
        print(
            "FILESYSTEM_BOUNDARY sandbox=false project-and-user-visible-mounts=accessible "
            "tool-approvals=inherited"
        )
        print("RUNTIME_THREAD_ID checked-by-upstream-at-worker-launch")

        ready = (
            all(ok for _, ok, _ in checks)
            and checkout_ok
            and marketplace_ok
            and plugin_ok
            and plugin_enabled
            and login_ready
        )
        print(f"MULTI_AGENT_READY={str(ready).lower()}")
        # Ordinary `status` is an inspection command: a clean report with
        # MULTI_AGENT_READY=false is not itself a controller failure. Launch
        # paths use --require-ready so they cannot silently open plain Codex
        # before the optional plugin is installed and enabled.
        return 2 if require_ready and not ready else 0

    def install(self) -> int:
        self.require_dependencies()
        self.client_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.client_home.chmod(0o700)
        self.install_checkout()
        self.run(["zsh", str(self.checkout / "scripts" / "self-check.zsh")], cwd=self.checkout)
        self.register_plugin()
        print(
            f"MULTI_AGENT_INSTALLED version={UPSTREAM_VERSION} commit={UPSTREAM_COMMIT} "
            "auth_changed=false provider_routes_changed=false"
        )
        self.print_status()
        if not self.codex_login_ready():
            print("NEXT: configure WSL Codex login independently or run the one-time Windows auth import.")
        return 0

    def toggle_plugin(self, action: str, *, stop: bool = False) -> int:
        self.validate_checkout()
        command = ["zsh", str(self.toggle), action]
        if stop:
            command.append("--stop")
        self.run(command, capture=False)
        self.print_status()
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fkctl agents",
        description="Optional pinned Codex-to-Claude multi-agent module",
    )
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="network install/update of the pinned upstream plugin")
    status = sub.add_parser("status", help="read-only dependency, plugin, and auth status")
    status.add_argument(
        "--require-ready",
        action="store_true",
        help="return non-zero unless every launch prerequisite is ready",
    )
    sub.add_parser("on", help="enable future worker launches")
    off = sub.add_parser("off", help="disable future worker launches")
    off.add_argument("--stop", action="store_true", help="also stop verified owned workers")
    return parser


def main() -> int:
    os.umask(0o077)
    args = build_parser().parse_args()
    manager = AgentsManager(args.root)
    try:
        if args.command == "install":
            return manager.install()
        if args.command == "status":
            return manager.print_status(require_ready=args.require_ready)
        if args.command == "on":
            return manager.toggle_plugin("on")
        if args.command == "off":
            return manager.toggle_plugin("off", stop=args.stop)
        raise AgentsError(f"unknown command: {args.command}")
    except (AgentsError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
