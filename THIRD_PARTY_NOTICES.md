# Third-party notices

Science SwitchModel / FinalKit installs or integrates the following components at runtime.

## Claude Science

- Source/installer: `https://claude.ai/install-claude-science.sh`
- Vendor: Anthropic
- FinalKit does not redistribute the Claude Science binary in this package.

## Claude Code

- Source/installer: `https://claude.ai/install.sh`
- Vendor: Anthropic
- Installed as the native Linux stable channel for each WSL user.
- FinalKit does not redistribute the Claude Code binary in this package.

## OpenAI Codex CLI

- Source/installer: `https://chatgpt.com/codex/install.sh`
- Vendor: OpenAI
- FinalKit does not redistribute the Codex CLI binary in this package.

## claude-science-codex-connector

- Source: `https://github.com/haoyuan-sjtu/claude-science-codex-connector.git`
- Pinned commit: `30b26d7c6f097b186bbd228e93a427a731399960`
- Upstream license: MIT
- FinalKit applies `wsl/connector-security.patch` locally after verifying the exact commit.
- The connector is used only for the ChatGPT/Codex account backend. DeepSeek, Kimi, and GLM use FinalKit's independent direct gateway.

## Node.js

- Source: `https://nodejs.org/dist/v24.19.0/`
- Version: `v24.19.0` LTS (Krypton)
- License: Node.js project license terms
- The official archive is downloaded at installation time and checked against the official `SHASUMS256.txt` entry. It is not redistributed in this package.

## Chrome DevTools MCP

- Source: `https://github.com/ChromeDevTools/chrome-devtools-mcp`
- Package: `chrome-devtools-mcp@1.2.0`
- License: Apache-2.0
- Installed per WSL user from npm. It is optional at runtime and is never connected to the user's default Chrome profile by FinalKit.

## HGSX

- Audited local release: HGSX Windows Offline AILAB r7, dated 2026-08-07.
- `ailab-switch` metadata declares a proprietary license.
- No HGSX code, binary, Docker layer, credential, or licensed content is included in FinalKit.
- FinalKit independently implements general mechanisms such as transactional switching, process identity checks, private local endpoints, and rollback.

## Python dependencies

Exact connector dependency versions are recorded in `wsl/requirements.lock`. They are downloaded from the configured Python package index during installation and remain inside the connector's isolated WSL virtual environment.

## Provider services

DeepSeek, Kimi/Moonshot, GLM/BigModel, Anthropic, ChatGPT, and Codex are external services governed by their respective terms, account permissions, quotas, and billing. FinalKit does not bypass service authorization, captcha, user limits, subscriptions, or rate limits.
