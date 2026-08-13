#!/usr/bin/env python3
"""Offline contract for no-Claude-account Windows menu/action semantics."""

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
    kit_root = script_path.parent.parent
    native = function_body(source, "Open-NativeClaude", "Open-ProviderWorkspace")
    assert '@("claude", $Mode)' in native
    workspace = function_body(source, "Open-ProviderWorkspace", "Show-NativeClaudeMenu")
    assert "Open-NativeClaude -Mode $Mode -WithBrowser" in workspace

    menu_workspace = {"7": "deepseek", "8": "kimi", "9": "glm", "10": "codex"}
    menu_claude = {"19": "deepseek", "20": "kimi", "21": "glm", "22": "codex"}
    for number, provider in menu_workspace.items():
        assert f'"{number}" {{ Open-ProviderWorkspace {provider} }}' in source
    for number, provider in menu_claude.items():
        assert f'"{number}" {{ Open-NativeClaude {provider} }}' in source
    for provider in menu_workspace.values():
        assert f'"{provider}" {{ Open-ProviderWorkspace {provider} }}' in source
        assert f'"{provider}" {{ Open-Science {provider} }}' not in source
    assert '"11" { Start-BrowserBridge; Show-BrowserStatus; Show-BrowserMcpInfo }' in source
    assert '"browser-start" { Start-BrowserBridge }' in source
    assert "function Start-DesktopProcess" in source
    assert "$shell.ShellExecute" in source
    assert "Start-Process -FilePath $chrome" not in source
    assert "Open-NativeClaude -Mode $claudeMode" in source
    assert "Runtime: WSL distro=$Distro; Linux user=$resolvedUser" in native
    assert '"claude-menu" { Show-NativeClaudeMenu }' in source
    for number, provider in menu_claude.items():
        assert f'Write-Host "  {number} Start Claude Code in WSL' in source
        launcher = kit_root / "windows" / {
            "19": "40-Start-WSL-Claude-Code-DeepSeek.cmd",
            "20": "41-Start-WSL-Claude-Code-Kimi.cmd",
            "21": "42-Start-WSL-Claude-Code-GLM.cmd",
            "22": "43-Start-WSL-Claude-Code-Codex.cmd",
        }[number]
        launcher_source = launcher.read_text(encoding="utf-8-sig").lower()
        assert "-action claude" in launcher_source
        assert f"-remainingargs {provider}" in launcher_source
        assert "%*" not in launcher_source
    root_launcher = (kit_root / "Start-Claude-Code-in-WSL.cmd").read_text(
        encoding="utf-8-sig"
    ).lower()
    assert "-action claude-menu" in root_launcher
    browser_launcher = (kit_root / "windows" / "30-Start-Browser-Bridge.cmd").read_text(
        encoding="utf-8-sig"
    ).lower()
    assert "-action browser-start" in browser_launcher
    assert "browser-science" not in browser_launcher


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: windows_entry_contract.py /path/to/windows/FinalKit.ps1", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    print(
        "WINDOWS_ENTRY_CONTRACT_OK no_claude_account=true "
        "workspace=7-10+provider-actions claude=19-22+40-43+root-menu runtime=wsl"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
