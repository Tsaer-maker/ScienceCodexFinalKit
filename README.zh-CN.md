# Science SwitchModel / FinalKit 3.2.3

ScienceCodexFinalKit 为普通 Windows 用户建立一套可重复安装、按用户隔离的科研 Agent 环境。主运行面位于 Ubuntu 24.04 WSL2：Claude Science 使用本地隔离身份连接 DeepSeek、Kimi、GLM 或 ChatGPT/Codex；原生 Linux Claude Code 可以复用同一组 provider。另有一套完全独立的 Windows Claude profile，可在 Windows 应用内使用三家 API 或 Windows Codex CLI 的 ChatGPT 登录。

新用户不需要修改源码、用户名或盘符。根脚本通过自身位置找到包，Windows 状态写入当前用户的 `%LOCALAPPDATA%/%USERPROFILE%`，Linux 状态写入目标用户的 `/home/<user>`。执行 owner 中没有固定的 `TSA`、`C:\Users\TSA`、`/home/tsa` 或 `D:\Tools` 路径。

> 完整的首次安装、日常命令、更新和故障恢复见 [operation.md](operation.md)。维护者实现与安全模型见 [代码剖析](docs/CODE_WALKTHROUGH.zh-CN.md)。

## 1. 能做什么

| 运行面 | 用途 | 可用 provider | 凭据 owner | 默认端口 |
|---|---|---|---|---|
| WSL Claude Science | 本地科研工作台 | DeepSeek、Kimi、GLM、ChatGPT/Codex | 当前 Linux 用户 | Science `8765`；gateway `9876` |
| WSL 原生 Claude Code | CLI/项目工作 | 同上 | 当前 Linux 用户 | 复用当前或临时启动的 WSL gateway |
| Windows Claude 应用（可选） | 官方 Windows 应用的独立 3P profile | DeepSeek、Kimi、GLM、ChatGPT/Codex | 当前 Windows 用户 | gateway `18987` |
| Windows 隔离 Chrome（可选） | Claude Science 页面自动化与 MCP | 不改变推理 provider | 当前 Windows 用户 | DevTools `9223` |

WSL Claude Science 的本地身份不是 Claude.ai 账号，不需要 Claude.ai 登录。实际推理仍必须拥有至少一种有效上游认证：三家 API key，或官方 Codex CLI 的 ChatGPT 登录。Windows Claude 使用官方 Windows 应用，其应用可用性、登录和计划要求仍由 Anthropic 决定；FinalKit 只管理独立的第三方推理 profile。

FinalKit 不依赖 Docker，不复制 HGSX 的专有代码、镜像、凭据或许可证内容，也不尝试绕过验证码、订阅、配额、地区、组织或付费边界。它独立实现单入口切换、loopback gateway、进程身份核验、原子配置、失败回滚和按用户隔离。

## 2. 适用环境

### 必需

- Windows 10 版本 2004 / Build 19041 以上，或 Windows 11；这是微软当前 `wsl --install` 的官方前提。[Microsoft WSL 安装说明](https://learn.microsoft.com/windows/wsl/install)
- 可用的 WSL2 与硬件虚拟化；Build 可在首次启用 Windows 组件时申请一次 UAC，并在需要重启时明确停止。
- x64 或 ARM64 Windows/WSL；安装器会按 `uname -m` 选择 Node.js `x64` 或 `arm64` 包。
- 稳定网络，可访问 Ubuntu、Claude、OpenAI/ChatGPT、GitHub、Node.js、PyPI 和 npm；使用哪家 API 还需访问对应供应商。
- 建议至少预留 `20 GB` 可用磁盘空间。完整参考安装约占 `14 GB`，不同 Claude Science/conda/npm 缓存会继续波动。
- 至少一种你有权使用的推理凭据。

### 仅独立 Windows Claude 需要

- 官方 Claude Windows 应用。Anthropic 当前列出的 Windows 系统前提是 Windows 10 以上。[Claude Desktop 安装说明](https://support.claude.com/en/articles/10065433-install-claude-desktop)
- Windows Python `3.10+`，`py -3 --version` 或 `python --version` 能找到它。
- 使用 Codex profile 时，还要安装并登录官方 Windows Codex CLI；三家 API profile 不要求 Codex 登录。

### 不要求

- 不要求 Docker Desktop。
- 不要求预先打开管理员 PowerShell；普通用户运行 Build，只有启用 WSL 系统组件时出现 UAC。
- 不要求固定的 `C:`、`D:` 或 `E:` 安装盘。
- 不要求把 API key 写入环境变量或脚本。
- 只使用 WSL Claude Science 时，不要求安装 Windows Claude 应用或 Windows Python。

## 3. 当前发布状态

3.2.3 当前提交在 GitHub 分支 `fix/science-entry-v3.2.1`，默认 `main` 仍是 3.2.0。需要本说明中 Windows/WSL 独立 profile、逐角色 Model/Reasoning 和一次性 auth 迁移时，请在正式 merge/tag 前明确获取该分支：

```powershell
git clone --branch fix/science-entry-v3.2.1 --single-branch `
  https://github.com/Tsaer-maker/ScienceCodexFinalKit.git
cd ScienceCodexFinalKit
```

若从 GitHub 下载 ZIP，应确认下载的是同一分支，而不是默认 `main`。完整解压到稳定的本地目录；不要直接在 ZIP、聊天软件临时目录、自动清理目录或即将撤除的磁盘中运行。

仓库当前只有第三方许可证说明，没有项目级 `LICENSE`。这不影响维护者和获授权测试者在自己的机器上验证，但在正式公开发行、复制、修改或再分发前，维护者仍需明确选择项目许可证。第三方组件边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 4. 新用户五步安装

### 第一步：以自己的普通 Windows 用户运行 Build

双击：

```text
Build.cmd
```

或在包根目录执行：

```powershell
.\windows\FinalKit.ps1 -Action build
```

默认建立或复用 `Ubuntu-24.04`，Linux 用户名由当前 Windows 用户名安全转换。首次启用 WSL 时允许 UAC；若系统要求重启，重启后登录回同一个普通 Windows 用户，再运行一次 Build。不要从另一个 Administrator 账号安装 Ubuntu，否则发行版会注册给错误的 Windows 用户。

Build 会安装或核验 Ubuntu 24.04、系统依赖、Claude Science、原生 Linux Claude Code、Linux Codex CLI、固定 Node/MCP、FinalKit runtime 和固定提交的 Codex connector。末尾应出现：

```text
BUILD_OK distro=Ubuntu-24.04 linux_user=<user>
SMOKE_OK
```

### 第二步：配置至少一种 WSL provider

打开 `SwitchModel.cmd`，选择菜单 `3–6`，或运行：

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek
.\windows\FinalKit.ps1 -Action configure-kimi
.\windows\FinalKit.ps1 -Action configure-glm
.\windows\FinalKit.ps1 -Action configure-codex
```

三家 API key 通过隐藏提示输入并保存在目标 Linux 用户的 WSL ext4 home，权限 `0600`。Codex 默认运行官方 Linux Codex 浏览器登录；若当前账号/工作区允许 device login，`configure-codex-device` 可作为显式备用。

### 第三步：检查 Model/Reasoning

四个 provider 都分别配置三组短字段：

```text
Opus   Model / Reasoning
Sonnet Model / Reasoning
Haiku  Model / Reasoning
```

Codex 从该操作系统自己的 `models_cache.json` 列出模型和逐模型 Reasoning 能力。包内 Sol/Terra/Luna 只是建议初值；其他用户必须以自己账号的本地模型缓存和配置界面为准。DeepSeek/Kimi/GLM 会在拥有 key 后读取固定官方 endpoint 的账号模型目录；目录可见不等于每种强度都可用，最终仍由真实测试证明。

### 第四步：发送一个最小真实请求

```powershell
.\windows\FinalKit.ps1 -Action test-deepseek
.\windows\FinalKit.ps1 -Action test-kimi
.\windows\FinalKit.ps1 -Action test-glm
.\windows\FinalKit.ps1 -Action test-codex
```

只运行已经配置的那一种。预期为：

```text
BACKEND_OK mode=<provider>
```

真实测试可能产生少量费用或额度消耗；Build/doctor/smoke 的离线契约不会访问模型上游。

### 第五步：启动 Claude Science

双击 `SwitchModel.cmd`，选择菜单 `7–10`，或运行：

```powershell
.\windows\FinalKit.ps1 -Action deepseek
.\windows\FinalKit.ps1 -Action kimi
.\windows\FinalKit.ps1 -Action glm
.\windows\FinalKit.ps1 -Action codex
```

浏览器打开的 `127.0.0.1` 页面可能要求接受一次本地 nonce/cookie 会话。这不是 Claude.ai 登录。启动后验证：

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
```

健康状态应包含：

```text
Gateway: healthy
Claude Science: running
Runtime identity: matched
Science identity: FinalKit local-only; no Claude account used
```

## 5. Provider 与凭据边界

| Provider | WSL 认证 | Windows Claude 认证 | Model/Reasoning 来源 |
|---|---|---|---|
| DeepSeek | Linux 用户私有 API key | Windows DPAPI `CurrentUser` | 官方账号模型目录 + provider 级 Reasoning |
| Kimi | Linux 用户私有 API key | Windows DPAPI `CurrentUser` | 官方账号模型目录 + provider 级 Reasoning |
| GLM | Linux 用户私有 API key | Windows DPAPI `CurrentUser` | 官方账号模型目录 + provider 级 Reasoning |
| ChatGPT/Codex | WSL 官方 Codex CLI 登录 | Windows 官方 Codex CLI 登录 | 每个 OS 自己的 Codex 模型缓存 |

Windows 和 WSL 默认拥有两个独立 Codex auth owner。只有用户主动运行下面入口时，才把当时的 Windows 官方 auth 一次性原子导入 WSL：

```powershell
.\windows\FinalKit.ps1 -Action migrate-windows-codex-auth-to-wsl
```

等价快捷入口：

```text
windows\08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd
```

迁移后不建立同步。Windows 和 WSL 会各自刷新；任一侧失效时，只在那一侧重新登录。不要用 `Get-Content`、剪贴板、命令行参数或聊天手工搬运 `auth.json`。

Reasoning wire 语义：

- DeepSeek：`auto/none/high/max`；非自动档使用 thinking 与 `output_config.effort`。[官方 Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)
- Kimi：`auto/none/low/high/max`；always-thinking 模型可能拒绝 `none`。[Kimi API 文档](https://platform.kimi.ai/docs/api/overview)
- GLM：`auto/none/high/max`；最终能力受具体模型和账号限制。[智谱思考模式](https://docs.bigmodel.cn/cn/guide/capabilities/thinking)
- Codex：只接受本机缓存为所选模型声明的强度；`ultra` 只有模型明确支持时才出现。

## 6. 独立 Windows Claude（可选）

Windows Claude 控制器从不读取、调用或切换 WSL。首次运行：

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-init
.\windows\FinalKit.ps1 -Action windows-claude-status
```

初始化只登记四个 profile，保持官方 `1p`，不索取 key、不登录、不启动 gateway。随后配置一个 profile：

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs kimi
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs glm
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs codex
```

Codex profile 使用 Windows 官方 Codex CLI 的 ChatGPT 登录，不要求另一份 OpenAI API key。OpenAI 当前说明 Codex 可通过现有 ChatGPT 账号登录，具体可用性和用量取决于计划与工作区。[Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)

启动、状态、停止和恢复官方模式：

```powershell
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs codex
.\windows\FinalKit.ps1 -Action windows-claude-status
.\windows\FinalKit.ps1 -Action windows-claude-stop
.\windows\FinalKit.ps1 -Action windows-claude-official
```

未配置 profile 会在启动 Python 或改 Claude 配置前失败。`windows-claude-official` 只恢复 Windows Claude 的官方 `1p`，不查看 WSL。

## 7. 日常使用与独立更新

```powershell
# 状态、诊断、停止 WSL Science/gateway
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action stop

# 只更新 FinalKit runtime；保留 WSL、认证和模型选择
.\windows\FinalKit.ps1 -Action update-runtime

# 交互更新三档 Model/Reasoning
.\windows\FinalKit.ps1 -Action update-models

# 明确联网更新官方客户端与固定工具
.\windows\FinalKit.ps1 -Action update-tools
```

模型换代不需要重新 Build。只有首次安装或 `doctor` 证明组件缺失时才运行完整 Build。网络中断时直接重跑 Build，不要 Clear。

## 8. 多用户与自定义路径

隔离单位是“Windows 用户 + WSL 发行版 + Linux 用户”：

| 层 | 独立内容 |
|---|---|
| Windows 用户 | WSL 注册、Windows Claude profile/DPAPI、Windows Codex、Chrome profile、备份 |
| WSL 发行版 | Ubuntu 系统包与 Linux 文件系统 |
| Linux 用户 | API key、Codex 登录、Science 数据、模型路由、日志和进程 owner |

同一发行版增加另一个 Linux 用户：

```powershell
.\windows\FinalKit.ps1 -Action build -LinuxUser alice
.\windows\FinalKit.ps1 -Action configure-codex -LinuxUser alice
.\windows\FinalKit.ps1 -Action codex -LinuxUser alice
```

自定义发行版名时必须同时给出明确位置：

```powershell
.\windows\FinalKit.ps1 -Action build `
  -Distro Research-Ubuntu-24.04 `
  -DistroLocation D:\WSL\Research-Ubuntu-24.04 `
  -LinuxUser alice
```

不要复制另一个 Windows 用户的 WSL 注册目录、DPAPI blob、Chrome profile 或 Codex auth。

## 9. 安全与故障边界

- WSL API key 和 auth 文件只在目标 Linux home，权限 `0600`。
- Windows 三家 API key 只保存为 DPAPI `CurrentUser` blob；不会进入 Claude JSON、argv、环境变量或 WSL。
- Windows/WSL gateway 都只绑定 loopback，并验证私密 path、token、instance、PID 和 owner。
- provider URL 是源码固定 HTTPS 白名单，不跟随 redirect。
- 停止进程前验证 PID、start ticks、命令行和脚本 owner；未知监听者不会被接管或强杀。
- Claude Science 只在 `~/.science-finalkit` 使用 FinalKit 本地身份；未知或真实 Science 凭据不会被自动覆盖。
- `Clear` 不执行全局 `wsl --shutdown`，只处理精确识别的当前 Windows 用户 Ubuntu；默认先导出 tar。
- `wsl --unregister` 会永久删除发行版数据。新用户、普通 Build 失败、模型未配置或单个 gateway 故障都不应先 Clear。
- Windows Claude 与 WSL Science 的配置、进程、端口和 auth owner 独立；Windows controller 不会静默退回 WSL。

常见故障和精确恢复见 [operation.md 的故障处理](operation.md#10-故障处理)。

## 10. 验证范围

Build 执行七组 WSL 离线契约，覆盖 connector、direct gateway、Science control/identity、模型路由、Windows 入口和 runtime 更新回滚。Windows Claude 另有两组隔离契约，覆盖四 profile、DPAPI、Codex auth owner、工具/SSE、PID/health 和 schema 迁移。

```powershell
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action smoke
```

它们证明本地链路和安全 owner，不证明某个用户的账号、套餐、地区、余额或全部模型都可用。只有对应 `test-*` 返回 `BACKEND_OK` 才证明当前账号当前路由的最小真实请求成功。

参考环境已经验证：

- 包路径可以位于不同本地盘符；
- Linux 用户名和 home 自动解析；
- WSL Claude Science 使用 ChatGPT/Codex 真实请求返回 `BACKEND_OK`；
- Windows Claude Codex profile 使用独立 `127.0.0.1:18987`，Sonnet 不再与 Opus 共用 Model；
- 仓库不包含真实 API key、auth JSON、模型缓存、运行态 profile 或日志。

## 11. 主要入口

| 文件 | 用途 |
|---|---|
| `Build.cmd` / `Install.cmd` | 首次安装或完整修复 |
| `SwitchModel.cmd` | 普通用户菜单 |
| `Clear.cmd` | 可选、破坏性 Ubuntu 清理；默认备份 |
| `operation.md` | 完整操作手册 |
| `windows/FinalKit.ps1` | Windows/WSL 总控制 owner |
| `windows/WindowsClaude.ps1` | 独立 Windows Claude owner |
| `windows/08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd` | 可选一次性 auth 初始化 |
| `windows/40–51` | Windows Claude 初始化、配置、启停、恢复快捷入口 |
| `wsl/install-final-stack.sh` | WSL 安装 owner |
| `wsl/runtime/switch_manager.py` | 路由、身份、切换、回滚、测试 |
| `wsl/runtime/direct_gateway.py` | DeepSeek/Kimi/GLM 固定入口 gateway |
| `docs/CODE_WALKTHROUGH.zh-CN.md` | 维护者代码剖析 |
| `THIRD_PARTY_NOTICES.md` | 第三方版本与许可证 |

## 12. 官方与上游依据

- [Microsoft：安装 WSL](https://learn.microsoft.com/windows/wsl/install)
- [Anthropic：安装 Claude Desktop](https://support.claude.com/en/articles/10065433-install-claude-desktop)
- [Anthropic：设置 Claude Code](https://docs.anthropic.com/en/docs/claude-code/getting-started)
- [OpenAI：使用 ChatGPT 计划登录 Codex](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api)
- [Kimi API overview](https://platform.kimi.ai/docs/api/overview)
- [智谱 Claude 接入](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)
- [`claude-science-codex-connector`](https://github.com/haoyuan-sjtu/claude-science-codex-connector)，固定提交 `30b26d7c6f097b186bbd228e93a427a731399960`
- [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)

第三方组件、固定版本、许可证与 HGSX 排除边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
