# FinalKit 3.2.0 代码剖析

本文解释默认无 Claude 账号架构、永久 owner、模型换代、浏览器跨 Windows/WSL 边界和验证逻辑。

## 1. 架构目标

3.2.0 的默认客户端是 WSL 原生 Claude Code。Claude Science 不参与普通安装验收和启动：官方 Science UI 的账号门槛发生在 provider gateway 之前，API key 或 ChatGPT OAuth 不能替代其官方账号。继续把 Start provider 解释为 Start Science，只会稳定地把无 Claude 账号用户送回登录页。

因此默认链路是：

```text
Windows menu
  -> selected Ubuntu WSL user
  -> fkctl selects one owned loopback gateway
  -> Linux Claude Code receives local endpoint/token + real model role map
  -> optional session-only MCP
  -> Windows cmd.exe -> portable Windows Node -> Chrome DevTools MCP
  -> isolated Windows Chrome on 127.0.0.1:9223
```

## 2. 永久 owner

| Owner | 职责 |
|---|---|
| `windows/FinalKit.ps1` | WSL 安装入口、Linux 用户解析、菜单、便携 Windows Node/MCP、隔离 Chrome、Windows/WSL 启动编排 |
| `wsl/install-final-stack.sh` | Ubuntu system/user 安装、官方 Linux Claude Code/Codex CLI、固定 connector、runtime 部署与验证 |
| `wsl/runtime/switch_manager.py` | provider 配置、模型路由、gateway 事务、Claude Code 环境、browser MCP session config、doctor/status/test |
| `wsl/runtime/direct_gateway.py` | DeepSeek/Kimi/GLM 固定上游、认证头、兼容请求/响应、model alias 映射 |
| `wsl/connector-security.patch` | ChatGPT/Codex OAuth connector 的身份、控制面、三档模型和 effort 映射 |
| `wsl/tests/*.py` | 无凭据/无网络契约：route、native client、browser config、Windows entry、runtime owner |

`wsl/fkctl` 只解析真实 Linux home 并 exec manager；CMD 只负责薄入口，不复制业务逻辑。

## 3. Windows 与 WSL 的所有权

```text
Windows user
  %LOCALAPPDATA%\ScienceCodexFinalKit\
    ChromeProfile\
    Node-v24.19.0\
    BrowserMcp\
    browser-mcp.cmd

Ubuntu WSL Linux user
  ~/.local/share/science-codex-finalkit/
    config/model-routes.json
    config/claude-browser-mcp.json
    secrets/*.key
    runtime/*.py
    run/{gateway.json,current-mode,switch.lock}
  ~/.finalkit-client/.codex/auth.json
  ~/.local/bin/{claude,codex,fkctl}
```

API key、Codex token 与模型路由不落 Windows 包目录。Windows MCP 不收到 provider key，只通过 Claude Code 的 stdio MCP 协议交换 browser tool 请求。

## 4. Build 与冷安装

Windows phase：

1. `Invoke-WslCapture` 直接并行读取 `wsl.exe` stdout/stderr 原始字节，兼容 UTF-16LE、UTF-8 和混合本地化错误。
2. 仅在 WSL 系统组件缺失时用 UAC 执行 `wsl --install --no-distribution`。
3. Ubuntu 安装仍由原普通 Windows 用户完成，避免注册到另一个 Administrator 账号。
4. `Resolve-BrowserMcpRuntime` 下载 Node.js 官方 ZIP 与 `SHASUMS256.txt`，精确比对 SHA256 后原子落到当前用户 LocalAppData；再安装固定 `chrome-devtools-mcp@1.2.0`。

WSL system phase 只安装缺少的 Ubuntu 包；已齐全时跳过 apt 网络。user phase 安装/复用 Claude Code、Codex、connector 与 Python 环境。Claude Science 不再下载、不再初始化 profile，也不进入 doctor/smoke 成功判据；已有二进制保持不动。

## 5. Provider gateway

DeepSeek/Kimi/GLM 的 URL、catalog URL 和认证方式固定在 `API_PROVIDERS`。调用者不能把保存的 key 指向任意 URL。直接 gateway：

- 绑定 `127.0.0.1`；
- 使用不可预测私有 URL path；
- `/health` 返回 instance/backend/profile identity；
- gateway record 保存 PID、Linux start ticks、backend、endpoint、instance；
- 停止前再次核对进程身份；
- key 只由 gateway 读取，不注入 Claude Code。

Claude Code 只收到：

```text
ANTHROPIC_BASE_URL=<private local endpoint>
ANTHROPIC_AUTH_TOKEN=finalkit-local-token
```

## 6. ChatGPT/Codex connector

connector 固定在公开 commit `30b26d7c6f097b186bbd228e93a427a731399960`，只接受已知 FinalKit patch hash 或干净 pinned tree。真实 OAuth owner 是官方 Linux Codex CLI 的：

```text
~/.finalkit-client/.codex/auth.json
```

FinalKit connector 读取并刷新同一个 owner，不复制第二条 refresh-token 链。默认映射：

```text
Opus   -> gpt-5.6-sol   effort=max
Sonnet -> gpt-5.6-terra effort=max
Haiku  -> gpt-5.6-luna  effort=max
```

connector contract 会捕获最终 Responses payload，断言三档 `model` 和 `reasoning.effort`，不连接上游、不读取真实 auth。

## 7. Claude Code 真实模型显示

`run_claude_code` 在每次进程启动前从 `model-routes.json` 读取当前配置，并注入：

```text
ANTHROPIC_MODEL
ANTHROPIC_DEFAULT_OPUS_MODEL(_NAME)
ANTHROPIC_DEFAULT_SONNET_MODEL(_NAME)
ANTHROPIC_DEFAULT_HAIKU_MODEL(_NAME)
ANTHROPIC_CUSTOM_MODEL_OPTION(_NAME)
CLAUDE_CODE_SUBAGENT_MODEL
```

因此 Claude Code `/model` 可显示 `gpt-5.6-sol` 或厂商实际 ID。模型 route 更新不修改 Claude Code 二进制，也不需要 Build；下一次启动自动读取新值。

## 8. 原子模型更新

唯一 owner：

```text
config/model-routes.json
```

`discover-models` 只访问当前 provider 的固定官方 catalog，合法化并去重 `data[].id`，不生成文本、不打印 key、不写配置。`update-models`：

1. 校验 model ID 字符集；
2. 合并新增内置 provider，但保留用户/未来 provider 条目；
3. `--dry-run` 返回完整候选；
4. 写临时文件、flush/fsync、chmod 0600、`os.replace`；
5. 修改活动 backend 时要求显式 `--restart`；失败恢复旧 JSON 与已观察 runtime。

包版本升级不会覆盖已经存在的用户 route。

## 9. 无 Science 依赖的事务切换

`select_gateway(mode)` 是原生客户端路径。gateway port 由 Linux UID 确定（UID 1000 为 9876、UID 1001 为 9877），所以同一发行版的多个普通用户可并行使用。若已有同 backend 健康 gateway，直接 fast-path 复用。切换到其他 backend 时：

- 没有已验证 Science lock owner：完全不调用 Science control；
- 有真实 owned Science 进程：仍先走安全停止，避免遗留进程占用旧 endpoint；
- 停止旧 owned gateway；
- 启动新 gateway 并验证 instance/backend/PID/start ticks/health；
- 最后提交 current-mode；
- 失败恢复旧 gateway。

这解决了旧 Science daemon/control socket 故障连带阻断 DeepSeek/Codex 的问题。

## 10. 浏览器桥为什么在 Windows 跑 MCP

Chrome 只监听 Windows `127.0.0.1:9223`。WSL NAT、mirrored mode、VPN 或企业网络可能让 WSL `127.0.0.1` 与 Windows loopback 不等价；把 Chrome 改为 `0.0.0.0` 又会扩大攻击面。

3.2.0 让 MCP 进程也在 Windows 启动：WSL Claude Code 的 stdio server command 是 `cmd.exe`，目标是 `%LOCALAPPDATA%/ScienceCodexFinalKit/browser-mcp.cmd`。WSL 对 Windows 可执行文件的 interop 保留 stdin/stdout，因此 Claude Code 与 MCP 仍用本地 stdio；MCP 与 Chrome 共用 Windows loopback。

Chrome 由 Windows Desktop Shell 持有进程生命周期，而不是依附于一次短命 PowerShell 调用；因此菜单 11 启动脚本退出后，后续 WSL Claude Code 会话仍可连接同一隔离 profile。

WSL 配置只包含固定 launcher：

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "type": "stdio",
      "command": "cmd.exe",
      "args": ["/d", "/c", "%LOCALAPPDATA%/ScienceCodexFinalKit/browser-mcp.cmd"]
    }
  }
}
```

路径使用 `/`，避免 WSL command interop 的 shell 层吞掉反斜杠。菜单 7–10 用 `--mcp-config` 只注入当前 Claude Code 会话；不加 `--strict-mcp-config`，从而不意外屏蔽用户已有的其他 MCP。工具调用仍通过 Claude Code 权限确认。

## 11. Windows 入口语义

- provider action `deepseek|kimi|glm|codex` 与菜单 7–10 调用 `Open-ProviderWorkspace`；
- `Open-ProviderWorkspace` 调用 `Open-NativeClaude -WithBrowser`；
- 菜单 19–22 调用 `Open-NativeClaude`，不准备浏览器；
- 菜单 11 只启动/检查 Chrome；
- 不存在普通 `science` 或 `browser-science` action。

`windows_entry_contract.py` 静态锁定这些语义，并确认 40–43 快捷脚本不使用会破坏参数边界的 `%*`。

## 12. Doctor、Smoke 与状态

Doctor 检查 Ubuntu、Claude Code、Codex、gateway、connector、secret 权限、模型路由和 Codex auth；Claude Science 不再是 FAIL 条件。Status 明确输出：

```text
Claude account:     not used
Optional Science:   installed; not used by default / not installed
```

Smoke 用 fake provider key 启动三个 direct gateway 并核验 identity，不启动 Science，不访问真实 upstream。浏览器 config 的 loopback 与固定 command 结构由 `native_client_contract.py` 在隔离 HOME 验证。

## 13. 安全边界

- 不创建或伪造 Claude Science OAuth/订阅；`science_identity.py` 仅保留用于识别并移除历史 FinalKit 精确虚拟身份，保护未知真实凭据。
- 不复制 HGSX proprietary 包或 Docker 层。
- 不打印 key/token；secret 文件与 config 为 0600。
- 不把 Chrome remote debugging 暴露到非 loopback。
- 不自动接受 browser tool 权限。
- 不以包版本号阻止已有命令；新能力用 `fkctl capabilities` 探测。
- runtime update 的替换集合有精确备份/回滚；auth 与 model-routes 不属于替换集合。

## 14. 测试矩阵

| 测试 | 证明什么 |
|---|---|
| `connector_contract.py` | Codex 三档 catalog、真实 Responses model/effort、重试边界 |
| `model_routes_contract.py` | 迁移、dry-run、原子写入、未来 provider 保留、活动 route 重启授权 |
| `native_client_contract.py` | 无 Claude 账号环境、真实 model name 注入、loopback browser config、Science 不连带阻断 |
| `windows_entry_contract.py` | 7–10 是 browser workspace；19–22 是纯 WSL Claude Code；无默认 Science action |
| `runtime_control_contract.py` | 遗留可选 Science owner 的 stale/lock/PID 安全语义 |
| `science_identity_contract.py` | 旧虚拟身份精确移除、未知/真实凭据不覆盖 |
| `installer_update_contract.sh` | runtime 更新失败精确回滚、成功提交 |

发布前还要做真实 readback：DeepSeek/Codex 各一次回答、`/model`、Windows Chrome endpoint、Claude Code MCP tool 可见、显式授权后 `evaluate location.href`、Status/Doctor、ZIP 内容和 SHA256。
