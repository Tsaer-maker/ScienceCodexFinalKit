# FinalKit 3.2.0：无 Claude 账号的 WSL 多模型工作台

FinalKit 面向普通 Windows 用户，把原生 Linux Claude Code 变成 DeepSeek、Kimi、GLM 和 ChatGPT/Codex 的统一客户端，并可在同一次会话中连接 Windows 隔离 Chrome。默认工作流不启动 Claude Science、不打开 Claude.ai 登录页，也不需要 Claude/Claude.ai 账号。

> 从零安装、日常使用、故障恢复和多用户操作见 [operation.md](operation.md)；源码与安全边界见 [代码剖析](docs/CODE_WALKTHROUGH.zh-CN.md)。

## 核心结论

- 模型配置、API key、Codex OAuth、gateway 和 Claude Code 都属于所选 Ubuntu WSL 的 Linux 用户。
- Windows 负责菜单、WSL 终端、隔离 Chrome，以及与 Chrome 同处 Windows loopback 的 MCP 进程。
- 菜单 `7–10`：启动 WSL Claude Code + Windows 隔离 Chrome + 对应后端。
- 菜单 `19–22`：只启动 WSL Claude Code，不启动浏览器。
- DeepSeek/Kimi/GLM 只需要各自 API key；Codex 只需要 ChatGPT/Codex 官方登录。四种路径都不需要 Claude 账号。
- Codex OAuth 由该 Linux 用户的 `~/.finalkit-client/.codex/auth.json` 独立持有；升级会从旧的 Science 命名目录事务迁移，不打印或复制 token。
- 厂商换模型后运行菜单 `17 Update provider models`，不用 Clear、不用重建 WSL。
- 发行版本号只用于发布识别，不是运行门禁；旧 runtime 的已有能力仍可使用。

Claude Science 0.1.27 是独立的官方 Web 产品，进入其 UI 仍要求它自己的 Claude 账号。FinalKit 3.2.0 因此不再把它放在安装成功判据、普通菜单或 provider 启动链中。旧机器上已有的 `claude-science` 二进制不会被删除，但只标记为可选兼容组件。FinalKit 不复制 HGSX 的专有代码，也不实现虚拟登录、订阅伪造、验证码或用户限制绕过。

## 从零开始

把 ZIP 完整解压到稳定本地磁盘，例如：

```text
C:\Tools\ScienceCodexFinalKit
D:\Tools\ScienceCodexFinalKit
```

不要从 ZIP 内、微信/QQ 临时目录或以后会撤除的 E 盘长期运行。然后：

1. 双击 `SwitchModel.cmd`。
2. 新电脑直接选 `2`；只有确实要删除旧 Ubuntu 时才选 `1`。
3. 用 `3/4/5/6` 配置 DeepSeek/Kimi/GLM/Codex。
4. 先用真实后端测试，再用 `7–10` 进入带浏览器工作台，或用 `19–22` 进入纯 WSL Claude Code。

`Build` 安装或修复 Ubuntu 24.04、原生 Linux Claude Code、Linux Codex CLI、FinalKit gateway 和固定 connector。它还在 `%LOCALAPPDATA%\ScienceCodexFinalKit` 安装经过官方 SHA256 校验的便携 Windows Node.js 与固定版本 Chrome DevTools MCP；不改系统 PATH、不要求全局 Node、不修改 `.wslconfig` 或全局代理。

## 菜单

```text
FinalKit no-account WSL workbench 3.2.0
  1  Clear selected Ubuntu WSL (confirmed + backup)
  2  First install / full repair WSL + Claude Code + Codex + browser tools
  3  Configure DeepSeek
  4  Configure Kimi
  5  Configure GLM
  6  Configure ChatGPT Codex
  7  Start WSL Claude Code + browser + DeepSeek
  8  Start WSL Claude Code + browser + Kimi
  9  Start WSL Claude Code + browser + GLM
  10 Start WSL Claude Code + browser + ChatGPT Codex
  11 Start/check isolated automation Chrome
  12 Status   13 Doctor   14 Stop gateway   15 Stop automation Chrome
  16 Update FinalKit runtime   17 Update provider models   18 Update official tools
  19 Start Claude Code in WSL + DeepSeek
  20 Start Claude Code in WSL + Kimi
  21 Start Claude Code in WSL + GLM
  22 Start Claude Code in WSL + ChatGPT Codex
```

根目录 `Start-Claude-Code-in-WSL.cmd` 提供 19–22 的独立四选一菜单；`windows/40–43-Start-WSL-Claude-Code-*.cmd` 是四个直达入口。`windows/10–13-Start-*.cmd` 对应 7–10，会同时准备浏览器工作流。

## Provider 与模型换代

| 后端 | 默认 main/Opus | 默认 Sonnet | 默认 fast/Haiku | 所需认证 |
|---|---|---|---|---|
| DeepSeek | `deepseek-v4-pro` | `deepseek-v4-pro` | `deepseek-v4-flash` | DeepSeek API key |
| Kimi | `kimi-k3[1m]` | `kimi-k3[1m]` | `kimi-k2.6` | Kimi API key |
| GLM | `glm-5.2` | `glm-5.2` | `glm-4.7-flash` | GLM API key |
| ChatGPT/Codex | `gpt-5.6-sol` | `gpt-5.6-terra` | `gpt-5.6-luna` | ChatGPT/Codex OAuth |

Codex 三档默认 `reasoning.effort=max`。Claude Code 的 `/model` 会显示当前实际模型，例如 `Current model: gpt-5.6-sol`；Opus/Sonnet/Haiku 只作为兼容角色别名，最终路由以 `EFFECTIVE_ROUTE`、`status` 和 gateway 日志为准。

模型的唯一持久 owner 是：

```text
~/.local/share/science-codex-finalkit/config/model-routes.json
```

以后厂商换代：

```powershell
# 交互式读取已配置 DeepSeek/Kimi/GLM 账号的官方模型目录，预览后持久更新
.\windows\FinalKit.ps1 -Action update-models

# 脚本化预览，不写文件
.\windows\FinalKit.ps1 -Action update-models `
  -RemainingArgs deepseek,--main,deepseek-v5-pro,--fast,deepseek-v5-flash,--dry-run,--json

# Codex 三档和推理强度独立更新
.\windows\FinalKit.ps1 -Action update-models `
  -RemainingArgs codex,--opus,gpt-6-sol,--sonnet,gpt-6-terra,--haiku,gpt-6-luna,--effort,max,--restart
```

如果厂商继续让同一 alias 指向新权重（例如 `deepseek-v4-pro` 后端升级到 Pro-0813），调用 ID 不变，无需修改配置。FinalKit 不承诺自动猜测新闻中的模型名称；菜单 17 先读当前账号的官方可调用目录，再由用户确认写入，避免把不可用或错误名称自动投产。

## 启动命令

```powershell
# 默认：WSL Claude Code + 隔离 Chrome + session-only MCP
.\windows\FinalKit.ps1 -Action deepseek
.\windows\FinalKit.ps1 -Action codex

# 不要浏览器，只启动 WSL Claude Code
.\windows\FinalKit.ps1 -Action deepseek -NoBrowser
.\windows\FinalKit.ps1 -Action claude -RemainingArgs codex

# 脚本化非交互调用
.\windows\FinalKit.ps1 -Action claude -RemainingArgs deepseek,-p,"Return exactly OK"
```

WSL 内等价命令：

```bash
fkctl claude deepseek
fkctl claude codex -p 'Return exactly OK'
fkctl status
```

FinalKit 只把本地 loopback token 交给 Claude Code，真实 provider key 留在 WSL `0600` secret 文件中。API key 不写入 `.cmd`、README、项目或 HANDOFF。

## 浏览器桥

菜单 7–10 的数据流是：

```text
WSL Claude Code
  -> session-only MCP config (0600)
  -> WSL 调用 Windows cmd.exe
  -> FinalKit 便携 Windows Node.js + chrome-devtools-mcp@1.2.0
  -> Windows 127.0.0.1:9223
  -> 隔离 Chrome profile
```

MCP 与 Chrome 都在 Windows，因此即使 WSL 当前不是 mirrored networking，也无需把 Chrome 暴露到局域网或改 `.wslconfig`。Chrome 固定使用：

- `127.0.0.1:9223`；
- `%LOCALAPPDATA%\ScienceCodexFinalKit\ChromeProfile`；
- 非默认 `--user-data-dir`；
- session-only `--mcp-config`，不污染 Claude Code 全局 MCP 设置。

浏览器工具仍遵守 Claude Code 权限：读取/点击前会要求允许；只在隔离 profile 登录你明确允许自动化访问的网站。

## 更新、状态与恢复

```powershell
# 只更新 FinalKit runtime；保留 API key、Codex auth、模型路由
.\windows\FinalKit.ps1 -Action update-runtime

# 更新官方 Claude Code/Codex CLI 与固定浏览器依赖
.\windows\FinalKit.ps1 -Action update-tools

.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action stop
.\windows\FinalKit.ps1 -Action browser-stop
```

不要用 Build 处理普通模型换代，不要用 Clear 处理 provider、gateway 或登录故障。Build 是首次安装/完整修复；Clear 会注销并删除目标 WSL 文件系统，默认先导出 tar 到 `%LOCALAPPDATA%\ScienceCodexFinalKit\Backups`。

## 多用户与协作

隔离单位是“Windows 用户 + WSL 发行版 + Linux 用户”。每个 Linux 用户拥有自己的 API key、Codex auth、模型路由、PID/lock 和 gateway；gateway loopback 端口由 Linux UID 稳定分配，避免同一 Ubuntu 的多个普通用户互抢 9876。Windows Codex 与 WSL Claude Code 可通过项目中的 `.science-codex/HANDOFF.md` 协作：

```powershell
.\windows\FinalKit.ps1 -Action init-project -Project D:\path\to\project
.\windows\FinalKit.ps1 -Action windows-review -Project D:\path\to\project
```

HANDOFF 不能存 API key、OAuth token、cookie 或其他秘密。

## 安全与来源边界

- 不绕过 Claude Science 登录、订阅、验证码或用户限制。
- 不复制 HGSX 的专有 Python 包、Docker 层、凭据或虚拟身份机制。
- 仅复用可独立实现的思想：多 provider profile、健康检查、原子模型路由、进程 owner 验证、失败回滚、多用户隔离。
- provider 上游和模型目录使用包内固定 HTTPS 白名单；不允许把 API key 发往任意 URL。
- gateway 绑定 loopback，并带不可预测私有路径与 owner/instance 身份验证。
- Chrome MCP 只绑定 Windows loopback，且只控制隔离 profile。

## 验证

安装/更新会运行离线契约：connector 三档路由、模型持久化、原生客户端环境、浏览器 MCP loopback 配置、Windows 入口语义、runtime owner 与更新回滚。实机验收见 [operation.md](operation.md#安装后的最短验收)。

关键上游：

- [Microsoft WSL 基本命令](https://learn.microsoft.com/windows/wsl/basic-commands)
- [Claude Code 第三方 gateway 配置](https://docs.anthropic.com/en/docs/claude-code/llm-gateway)
- [OpenAI Codex CLI](https://developers.openai.com/codex/cli)
- [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)
- [Node.js 下载与 SHA256 清单](https://nodejs.org/en/download)
