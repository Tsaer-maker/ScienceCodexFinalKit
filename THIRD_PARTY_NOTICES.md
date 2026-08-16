# Third-party notices

Claude Codex Switchboard installs or integrates the following components at runtime. Switchboard's original code and documentation use the repository [MIT License](LICENSE); that license does not replace or alter any third-party license below.

## Claude Science

- Vendor: Anthropic
- Installer: `https://claude.ai/install-claude-science.sh`
- Switchboard does not redistribute the Claude Science binary.

## Claude Code

- Vendor: Anthropic
- Installer: `https://claude.ai/install.sh`
- Installed as the native Linux stable channel for each WSL user.
- Switchboard does not redistribute the Claude Code binary.

## OpenAI Codex CLI

- Vendor: OpenAI
- Installer: `https://chatgpt.com/codex/install.sh`
- Switchboard does not redistribute the Codex CLI binary.

## claude-science-codex-connector

- Source: `https://github.com/haoyuan-sjtu/claude-science-codex-connector.git`
- Pinned commit: `30b26d7c6f097b186bbd228e93a427a731399960`
- Upstream license: MIT
- Switchboard applies `wsl/connector-security.patch` only after verifying the exact origin, commit, managed file set, and patch compatibility.
- The connector is used only for the ChatGPT/Codex backend. DeepSeek, Kimi, and GLM use Switchboard's independent direct gateway.

## Optional codex-claude-orchestrator

- Source: `https://github.com/coredo-eu/codex-claude-orchestrator.git`
- Upstream version: `0.3.1`
- Pinned commit: `c996b497c6682f4695b5aa342610527731712c51`
- Upstream license: MIT
- This component is not bundled. It is cloned only when the user explicitly runs `agents-install`.
- Switchboard verifies the exact origin, commit, clean checkout, LICENSE, expected scripts, upstream self-check, and Codex marketplace/plugin identity.
- It is installed only in the selected WSL user's isolated Switchboard Codex home. It does not receive Windows credentials, Claude Science credentials, or provider secret files.

## Node.js

- Source: `https://nodejs.org/dist/v24.19.0/`
- Version: `v24.19.0` LTS
- License: Node.js project license terms
- The official archive is downloaded at installation time and checked against the official `SHASUMS256.txt` entry. It is not redistributed in this package.

## Chrome DevTools MCP

- Source: `https://github.com/ChromeDevTools/chrome-devtools-mcp`
- Package: `chrome-devtools-mcp@1.2.0`
- License: Apache-2.0
- Installed per WSL user from npm.
- It is optional at runtime and is never connected to the user's default Chrome profile automatically.

## HGSX

- Audited local release: HGSX Windows Offline AILAB r7, dated 2026-08-07.
- Its `ailab-switch` metadata declares a proprietary license.
- No HGSX code, binary, Docker layer, credential, model, or licensed content is included.
- Switchboard independently implements general mechanisms such as isolated local Science identity, empty-refresh-token sessions, transactional switching, process identity checks, private loopback endpoints, and rollback.

## Research-only projects

Additional Claude/Codex bridges, switchboards, auth managers, session-transfer tools, and orchestrators were inspected as design evidence. They are not redistributed, installed, imported, or declared as dependencies. Exact repositories, commits, licenses, adopted concepts, and rejection reasons are recorded in [docs/PROJECT_RESEARCH_REPORT.zh-CN.md](docs/PROJECT_RESEARCH_REPORT.zh-CN.md).

This distinction is especially important for repositories with AGPL-3.0, PolyForm Noncommercial, missing, proprietary, or otherwise unverified licenses: their ideas may have informed an independently implemented mechanism, but their code and assets were not copied.

## Python dependencies

Exact connector dependency versions are recorded in `wsl/requirements.lock`. They are downloaded from the configured Python package index during installation and remain inside the connector's isolated WSL virtual environment.

## Provider services

DeepSeek, Kimi/Moonshot, GLM/BigModel, Anthropic, ChatGPT, and Codex are external services governed by their respective terms, account permissions, regional availability, quotas, and billing. Switchboard does not bypass service authorization, captcha, account limits, subscriptions, or rate limits. An official Codex ChatGPT login remains a Codex CLI credential; Switchboard does not represent it as a general OpenAI API key.
