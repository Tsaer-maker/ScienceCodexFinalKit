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
    native = function_body(source, "Open-NativeClaude", "Open-Science")
    assert '@("start", $Mode)' in science
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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: windows_entry_contract.py /path/to/windows/FinalKit.ps1", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    print("WINDOWS_ENTRY_CONTRACT_OK science=7-10+provider-actions claude=19-22+claude-action")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
