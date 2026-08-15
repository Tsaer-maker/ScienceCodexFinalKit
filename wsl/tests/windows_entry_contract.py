#!/usr/bin/env python3
"""Offline contract for stable Windows menu/action semantics."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def function_body(source: str, name: str, next_name: str) -> str:
    match = re.search(
        rf"(?ms)^function {re.escape(name)} \{{(.*?)^function {re.escape(next_name)} \{{",
        source,
    )
    if not match:
        raise AssertionError(f"cannot isolate PowerShell function {name}")
    return match.group(1)


def verify(script_path: Path) -> None:
    source = script_path.read_text(encoding="utf-8-sig")
    science = function_body(source, "Open-Science", "Open-CurrentScience")
    current_science = function_body(source, "Open-CurrentScience", "Get-ChromePath")
    native = function_body(source, "Open-NativeClaude", "Open-Science")
    assert '@("start", $Mode)' in science
    assert 'Assert-FkctlCapability -Capability "science-isolated-local-identity"' in science
    assert 'Assert-FkctlCapability -Capability "science-isolated-local-identity"' in current_science
    assert 'Assert-FkctlCapability -Capability "science-local-session-admission"' in science
    assert 'Assert-FkctlCapability -Capability "science-local-session-admission"' in current_science
    assert '@("claude", $Mode)' not in science
    assert '@("claude", $Mode)' in native

    menu_science = {"7": "deepseek", "8": "kimi", "9": "glm", "10": "codex"}
    menu_claude = {"19": "deepseek", "20": "kimi", "21": "glm", "22": "codex"}
    for number, provider in menu_science.items():
        assert f'"{number}" {{ Open-Science {provider} }}' in source
    for number, provider in menu_claude.items():
        assert f'"{number}" {{ Open-NativeClaude {provider} }}' in source
    for provider in menu_science.values():
        assert f'"{provider}" {{ Open-Science {provider} }}' in source
    assert 'Invoke-Fkctl (@("claude") + $claudeArguments)' in source

    migration = function_body(
        source, "Invoke-WindowsCodexAuthMigrationToWsl", "Get-UbuntuTargets"
    )
    assert 'Assert-FkctlCapability -Capability "stdin-codex-auth-import"' in migration
    assert 'Invoke-Fkctl @("stop")' in migration
    assert '@($fkctl, "import-codex-auth")' in migration
    assert '-StandardInputBytes $payload.Bytes' in migration
    assert 'Invoke-Fkctl @("start", "codex")' in migration
    assert 'Read-Host "Type MIGRATE' in migration
    assert 'no startup copy occurs' in migration
    assert 'RedirectStandardInput = ($null -ne $StandardInputBytes)' in source
    assert 'ProcessStartInfo.Arguments' not in migration
    assert '"24" { Invoke-WindowsCodexAuthMigrationToWsl }' in source
    assert '"migrate-windows-codex-auth-to-wsl"' in source

    launcher = script_path.parent / "08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd"
    launcher_source = launcher.read_text(encoding="utf-8-sig")
    assert "-Action migrate-windows-codex-auth-to-wsl" in launcher_source
    assert "%*" in launcher_source

    windows_controller = function_body(
        source, "Invoke-WindowsClaudeController", "Get-WindowsClaudeModeArgument"
    )
    assert 'WindowsClaude.ps1' in windows_controller
    assert '& powershell.exe @arguments' in windows_controller
    for forbidden in ("Get-Fkctl", "Invoke-Fkctl", "Get-Wsl", "Invoke-Wsl", "9876"):
        assert forbidden not in windows_controller
    assert '"23" { Invoke-WindowsClaudeController -ControllerAction menu }' in source
    for action in (
        "windows-claude-init",
        "windows-claude-configure",
        "windows-claude",
        "windows-claude-status",
        "windows-claude-stop",
        "windows-claude-official",
    ):
        assert f'"{action}"' in source


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: windows_entry_contract.py /path/to/windows/FinalKit.ps1", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    print(
        "WINDOWS_ENTRY_CONTRACT_OK science=7-10+provider-actions "
        "claude=19-22+claude-action auth-import=explicit-stdin "
        "windows-claude=separate-controller"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
