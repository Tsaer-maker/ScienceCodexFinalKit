# Claude Codex Switchboard 3.3.0 操作手册

本文面向普通 Windows 用户。产品概览见 [README.zh-CN.md](README.zh-CN.md)，代码 owner 与安全机制见 [docs/CODE_WALKTHROUGH.zh-CN.md](docs/CODE_WALKTHROUGH.zh-CN.md)。

## 1. 两套独立运行面

WSL 面运行在 Ubuntu 24.04，包含 Claude Science、原生 Linux Claude Code、Linux Codex CLI、四条 provider route 与可选多 Agent 模块。Claude Science 使用 `~/.science-finalkit` 中的本地隔离身份，不要求 Claude.ai 账号；真正推理由当前选中的 API key 或 WSL 官方 Codex CLI ChatGPT 登录负责。

Windows 面运行官方 Windows Claude 应用，包含 `DeepSeek API`、`Kimi API`、`GLM API`、`Codex Login` 四条 profile。Windows controller 不调用 `wsl.exe`，并使用独立端口、runtime、Windows DPAPI 和官方 Windows Codex CLI auth。

## 2. 安装前检查

支持：

- Windows 10 2004+ 或 Windows 11；
- WSL2 与 Ubuntu 24.04；
- x64 或 arm64；
- 普通 Windows 用户；
- 位于本地盘且可映射到 WSL 的包目录。

独立 Windows Claude 功能还要求当前 Windows 用户可调用 **Python 3.10+**（`py.exe -3`、`python.exe` 或 `python3.exe`）。`Build.cmd` 只安装 WSL 内的 Python；它不会替用户静默安装 Windows Python。若只使用 WSL Claude Science / Claude Code，则不需要这项 Windows runtime。

至少准备 DeepSeek、Kimi、GLM API key 或官方 Codex CLI ChatGPT 登录之一。不要求 Docker、Claude.ai 账号用于 WSL Science，也不要求 OpenAI API key 用于 Codex Login。

不要把 key 或 auth JSON 放进命令行、`.cmd`、README、Git、聊天、截图或剪贴板自动化脚本。API key 配置入口使用隐藏输入；Windows→WSL Codex auth 迁移使用 stdin。

## 3. 下载与第一次 Build

```powershell
git clone https://github.com/Tsaer-maker/claude-codex-switchboard.git
Set-Location .\claude-codex-switchboard
.\Build.cmd
```

也可以下载 ZIP 后双击 `Build.cmd`。

Build 分为三个权限边界：

1. Windows 检查或启用 WSL 平台；
2. Ubuntu root 阶段只安装缺失系统依赖；
3. 普通 WSL 用户阶段安装工具、runtime 和按用户状态。

系统依赖包括 `curl`、`git`、`jq`、`zsh`、`lsof`、`python3`、`bubblewrap`、`socat`、`rsync` 与证书工具。Build 不建立 passwordless sudo，不改 `.wslconfig`、系统代理或 Docker，不删除其他发行版，也不读取 Windows Claude profile。

成功结束：

```text
BUILD_OK distro=Ubuntu-24.04 linux_user=<当前用户>
```

若 Windows 要求重启，重启后重新运行 Build；平台状态未完成时脚本不会强行继续。

## 4. 统一菜单

```powershell
.\Switchboard.cmd
```

| 菜单 | 功能 |
| --- | --- |
| 2 | 首次安装或完整修复 |
| 3–6 | 配置 DeepSeek、Kimi、GLM、ChatGPT/Codex |
| 7–10 | 用四种 route 启动 WSL Claude Science |
| 11 | 在隔离 Chrome 中打开当前 Science |
| 12–15 | 状态、doctor、停止 WSL runtime、停止隔离 Chrome |
| 16–18 | runtime、model routes、官方工具更新 |
| 19–22 | 用四种 route 启动原生 WSL Claude Code |
| 23 | 独立 Windows Claude 菜单 |
| 24 | 一次性 Windows Codex auth → WSL |
| 25 | 可选多 Agent 模块 |

## 5. WSL provider 配置

### 5.1 DeepSeek、Kimi、GLM

```powershell
.\windows\Switchboard.ps1 -Action configure-deepseek
.\windows\Switchboard.ps1 -Action configure-kimi
.\windows\Switchboard.ps1 -Action configure-glm
```

每个 key 只写入当前 WSL 用户私有目录，权限 600。配置一家公司不会覆盖另外两家公司或 Codex auth。

### 5.2 ChatGPT / Codex

```powershell
.\windows\Switchboard.ps1 -Action configure-codex
```

该入口调用 WSL 内官方 Codex CLI browser login。若 loopback 不能返回 WSL，可使用：

```powershell
.\windows\Switchboard.ps1 -Action configure-codex-device
```

独立 WSL 登录：

```powershell
.\windows\Switchboard.ps1 -Action login-linux-codex
```

认证 owner 为 `~/.finalkit-client/.codex/auth.json`。Switchboard 只在隔离 HOME 中调用官方 Codex CLI，不把 consumer login 变成通用 OpenAI API key。

## 6. 一次性 Windows Codex auth 导入

先确认 Windows 官方 Codex CLI：

```powershell
codex login status
```

显示 `Logged in using ChatGPT` 后直接执行，不要先手工停止 runtime；控制器需要先读取原状态，才能在导入后恢复同一状态：

```powershell
.\windows\08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd
```

或：

```powershell
.\windows\Switchboard.ps1 -Action migrate-windows-codex-auth-to-wsl
```

Windows 端最多读取 1 MiB 官方 JSON，并经 stdin 一次送入 `fkctl migrate-codex-auth`。WSL manager 在同一把 `FileLock` 内取得不含 secret 的 runtime 快照，区分 stopped、gateway-only、Science + gateway 及原 provider；随后停止受管 runtime、验证 ChatGPT token chain、在临时隔离 auth 上执行官方 `codex login status`、原子替换 auth，并精确恢复原 runtime 形态。任一步失败都会在同一事务中恢复旧 auth 与原 runtime；若恢复自身不完整则明确报错。不会建立同步任务，两边以后仍可分别登录、过期或刷新。

迁移完成后，每一侧的官方 Codex CLI 都是本侧 `auth.json` 的唯一写入和刷新 owner。Windows/WSL gateway 只读该文件：若后端返回 401，仅当官方 CLI 已写入不同 access token 时重试；否则提示先运行本侧官方 Codex CLI 刷新或重新登录。gateway 不再与 CLI 并发写 refresh-token chain。

## 7. Opus、Sonnet、Haiku 与 Reasoning

查看：

```powershell
.\windows\Switchboard.ps1 -Action models
```

交互修改：

```powershell
.\windows\06-Update-Provider-Models.cmd
```

每个 provider 分别询问：

| Claude 档位 | 上游字段 |
| --- | --- |
| Opus | `model_opus` + `reasoning_opus` |
| Sonnet | `model_sonnet` + `reasoning_sonnet` |
| Haiku | `model_haiku` + `reasoning_haiku` |

直接 Enter 接受非空 seed。Sonnet 有独立 prompt 和默认值，不再跟随 Opus。

| Provider | Reasoning 值 |
| --- | --- |
| DeepSeek | `auto` / `none` / `high` / `max` |
| Kimi | K2.6：`auto` / `none`；K2.7-code：仅 `auto`；K3：`auto` / `low` / `high` / `max` |
| GLM | 4.7：`auto` / `none`；5.2：`auto` / `none` / `low` / `medium` / `high` / `xhigh` / `max`；5.3：`auto` / `low` / `high` / `max` |
| Codex | `auto` / `none` / `low` / `medium` / `high` / `xhigh` / `max` / `ultra` |

模型级规则按 [Kimi Thinking Models](https://platform.kimi.ai/docs/guide/use-thinking-models) 和 [GLM Thinking Mode](https://docs.bigmodel.cn/cn/guide/capabilities/thinking) 执行；配置菜单、持久层和 gateway 使用同一能力判断。

3.3.0 使用稳定的跨 provider 语义子集：GLM 5.2 官方 `minimal` 不另设 UI 档，关闭 thinking 统一选择 `none`。

Codex 配置读取官方 `models_cache.json` 最近广告的 `supported_reasoning_levels`；有声明时，固定值和 `auto` 的 incoming effort 都按具体模型前置校验。没有声明时状态标记 capability unknown，由上游最终判定，不能表述为已验证。cache 可能过期，也不等于实时 entitlement；只有显式 `test-codex-tiers` 会发出请求验证。

三家 API 的模型发现只调用官方 model-list endpoint，不发送生成请求：

```powershell
.\windows\Switchboard.ps1 -Action discover-models -RemainingArgs deepseek
.\windows\Switchboard.ps1 -Action discover-models -RemainingArgs kimi
.\windows\Switchboard.ps1 -Action discover-models -RemainingArgs glm
```

命令行原子更新示例：

```powershell
.\windows\Switchboard.ps1 `
  -Action update-models `
  -RemainingArgs codex,--opus,gpt-5.6-sol,--reasoning-opus,max,--sonnet,gpt-5.6-terra,--reasoning-sonnet,max,--haiku,gpt-5.6-luna,--reasoning-haiku,max,--restart
```

`--restart` 只在 active route 受影响时重启 Switchboard 自己的 WSL runtime；失败会恢复旧 route。

## 8. 启动 WSL Claude Science

快捷入口：

```text
windows\10-Start-DeepSeek.cmd
windows\11-Start-ChatGPT-Codex.cmd
windows\12-Start-Kimi.cmd
windows\13-Start-GLM.cmd
```

PowerShell：

```powershell
.\windows\Switchboard.ps1 -Action deepseek
.\windows\Switchboard.ps1 -Action kimi
.\windows\Switchboard.ps1 -Action glm
.\windows\Switchboard.ps1 -Action codex
```

每次切换会验证现有 owner，通过 Claude Science 官方控制面停止自己拥有的实例，启动目标 gateway，安装或复用可识别的本地 Science 身份，用一次性 nonce 建立本地浏览器 session，并核对 `/api/me`、页面 title、endpoint 和 route。

典型输出：

```text
Science identity: Switchboard local-only; no Claude account used
EFFECTIVE_ROUTE=...
```

Science 页面显示 Claude 兼容别名；实际计费模型以 `models`、`status` 与 `EFFECTIVE_ROUTE` 为准。

## 9. 原生 WSL Claude Code

```powershell
.\windows\Switchboard.ps1 -Action claude -RemainingArgs deepseek
.\windows\Switchboard.ps1 -Action claude -RemainingArgs kimi
.\windows\Switchboard.ps1 -Action claude -RemainingArgs glm
.\windows\Switchboard.ps1 -Action claude -RemainingArgs codex
```

入口会选择并验证 gateway，使用隔离 `~/.finalkit-client` HOME，只把 route token 放入该 Claude Code 子进程环境，不把 provider key 写进 Claude settings，也不触碰 Windows Claude。

## 10. 独立 Windows Claude

本功能控制用户已经安装的官方 Windows Claude 应用及其独立 profile。应用本体的提供、可用地区、版本和账号方案仍由 Anthropic 决定；Switchboard 不分发、不修改授权机制，也不会把它替换成 WSL Claude Science。

### 10.1 初始化和配置

```powershell
.\windows\40-Initialize-Windows-Claude.cmd
.\windows\41-Configure-Windows-Claude-DeepSeek.cmd
.\windows\42-Configure-Windows-Claude-Kimi.cmd
.\windows\43-Configure-Windows-Claude-GLM.cmd
.\windows\44-Configure-Windows-Claude-Codex-Login.cmd
```

初始化创建四条 Windows-only profile 和空 provider secret 槽。每条 profile 分别询问 Opus、Sonnet、Haiku 的 Model 和 Reasoning。

Codex profile：

- 先执行官方 Windows `codex login status`；
- 已登录时不重复拉起浏览器；
- 未登录时要求先运行官方 `codex login`；
- 从官方 model cache 列出模型和 reasoning levels；
- 默认建议 Opus→Sol、Sonnet→Terra、Haiku→Luna；本地 cache 只提供最近广告的候选与强度，保存值由用户选择，实时接受情况由实际 test 请求确认；
- 不询问 OpenAI API key。

三家 API key 由当前 Windows 用户 DPAPI 加密。Codex profile 只保存官方 Windows Codex auth 路径，不复制 token。

### 10.2 启动

```powershell
.\windows\45-Start-Windows-Claude-DeepSeek.cmd
.\windows\46-Start-Windows-Claude-Kimi.cmd
.\windows\47-Start-Windows-Claude-GLM.cmd
.\windows\48-Start-Windows-Claude-Codex-Login.cmd
```

### 10.3 状态、停止、恢复官方模式

```powershell
.\windows\49-Windows-Claude-Status.cmd
.\windows\50-Stop-Windows-Claude.cmd
.\windows\51-Restore-Windows-Claude-Official.cmd
```

恢复官方模式只取消第三方 profile 的 active binding，保留 profile、DPAPI secret 和 Codex binding。

Windows 状态目录为 `%LOCALAPPDATA%\ScienceCodexFinalKit\WindowsClaude`。这是升级兼容路径；用户可见名称已经统一为 Switchboard。

## 11. 可选多 Agent 模块

该模块隔离的是 HOME、环境变量与 credential 注入边界，不建立独立 filesystem sandbox。Codex 与 Claude workers 仍继承各自 tool approval，并以当前 WSL 用户访问所选项目和该用户可见的挂载盘。把项目放在 `D:\work\repo` 会映射到 `/mnt/d/work/repo`；同一用户技术上也可能访问其他可见挂载，因此工作区不要存放无关 secret。

### 11.1 能力和边界

模块在 WSL Codex 中安装固定 commit 的 [coredo-eu/codex-claude-orchestrator](https://github.com/coredo-eu/codex-claude-orchestrator)，由 Codex 主控持久 Claude PTY workers。它适用于可分离为检索、实现、调试、审阅或安全检查的任务。

保留的上游能力：

- persistent parent context；
- Haiku/Sonnet/Opus/Fable role routing；
- worker registration、lease 和 canonical-root edit custody；
- 私有 runtime snapshot；
- compaction checkpoint；
- per-HOME busy limit；
- enable/disable kill switch；
- Claude 不可用后的显式 native Codex fallback；
- Codex 最终独立验证。

Switchboard 增加四条 provider route、隔离 credential owner、精确 pin 和 Windows 启动入口。Claude workers 可使用 DeepSeek、Kimi、GLM 或 ChatGPT/Codex gateway，不需要把 key 复制进插件目录。

上游 role-specific effort 与 provider tier 配置的关系是显式的：`auto` 对 API provider 按具体模型校验；Codex 在 cache 能力已知时前置校验、未知时明确标记 unknown。固定值会覆盖同一档位内的上游 role effort。想保留 implementer/debugger/reviewer 等差异时，把对应档位改为 `auto`。

### 11.2 安装与检查

```powershell
.\windows\Switchboard.ps1 -Action agents-install
.\windows\Switchboard.ps1 -Action agents-status
```

安装目标：

```text
repository https://github.com/coredo-eu/codex-claude-orchestrator.git
version    0.3.1
commit     c996b497c6682f4695b5aa342610527731712c51
```

安装会验证 `git`、`zsh`、`jq`、`flock`、`ps`、`sed`、`awk`、`tr`、`sha256sum`、`/proc`、Codex plugin surface、完整 Claude Code flags、origin、commit、clean tree、LICENSE 和上游 self-check，再注册隔离 marketplace/plugin。

`MULTI_AGENT_READY=false` 是正常 read-only 状态，不代表 controller 崩溃。逐项 `CHECK` 会指出依赖、checkout、plugin、enable 状态或 WSL Codex login 中的缺项。

实际启动会再次执行 `status --require-ready`；不是 `MULTI_AGENT_READY=true` 就返回错误，不会打开普通 Codex 冒充多 Agent。

### 11.3 启动项目

```powershell
.\windows\Switchboard.ps1 -Action agents -Project D:\work\repo -RemainingArgs deepseek
.\windows\Switchboard.ps1 -Action agents -Project D:\work\repo -RemainingArgs codex
```

provider 可为 `deepseek`、`kimi`、`glm`、`codex`。Windows 入口只转换项目路径并打开可见 WSL Codex TUI。Codex leader 使用隔离官方 WSL 登录；Claude workers 使用选定 gateway。

安装后需新建一个 Codex task，随后显式调用上游 skill：

```text
Use $codex-claude-orchestrator:claude-pty-agents for this bounded outcome.
Keep Codex as the authority owner and independently verify Claude's handoff.
```

普通提示词不保证选择 Claude executor。Switchboard 只安装 transport，不自动修改项目 `AGENTS.md`；需要长期 Claude-first 选择时，应人工审阅并合并上游 policy snippet，不能覆盖项目已有权限和安全规则。

WSL 当前只有一个受管 gateway。若 Claude Science 正在运行，Agent provider 必须与 Science 的健康 active gateway 相同；不同 provider 会 fail closed。先用 `fkctl start <provider>` 显式切换 Science，或先停止 Science，再启动目标 Agent route。

### 11.4 Kill switch

```powershell
.\windows\Switchboard.ps1 -Action agents-on
.\windows\Switchboard.ps1 -Action agents-off
.\windows\Switchboard.ps1 -Action agents-stop
```

- `agents-off` 禁止新 workers，不强停在途 owner；
- `agents-stop` 禁止新 workers，并经上游 owner 检查停止受管 workers；
- 不按进程名全局杀掉 Claude 或 Codex。

## 12. 隔离浏览器桥

```powershell
.\windows\30-Start-Browser-Bridge.cmd
.\windows\31-Browser-MCP-Info.cmd
```

状态目录为 `%LOCALAPPDATA%\ScienceCodexFinalKit\ChromeProfile`，不是用户默认 Chrome profile。Chrome DevTools MCP 能读取和控制隔离 profile 的全部 tab，因此连接必须由用户显式完成，只应登录愿意暴露给自动化的站点。

## 13. 更新

### 13.1 只更新 Switchboard runtime

```powershell
.\windows\05-Update-Switchboard-Runtime.cmd
```

保留 Ubuntu、API key、Windows/WSL Codex auth、model/reasoning routes、Windows Claude profile 与多 Agent plugin state。更新受管 manager、gateway、identity helper、connector patch、`fkctl` 和契约；失败只恢复 package-managed code、wrapper、connector owner 与 metadata 的更新前字节和权限。Auth、model routes 与最新派生 config 不进入 installer rollback，因而不会覆盖官方 CLI 或另一个终端在更新期间提交的新状态。

### 13.2 更新模型

```powershell
.\windows\06-Update-Provider-Models.cmd
```

只修改已选 provider 的 route，不更新官方客户端。

### 13.3 更新官方工具

```powershell
.\windows\07-Update-Official-Tools.cmd
```

这是明确联网动作，更新 Claude Science、Claude Code、Codex CLI、固定 Node/MCP 依赖。更新前后核对 WSL Codex auth hash，并保留 model routes。

## 14. 状态、Doctor 与日志

```powershell
.\windows\20-Status.cmd
.\windows\21-Doctor.cmd
```

WSL 直接命令：

```powershell
wsl.exe -d Ubuntu-24.04 -- fkctl status
wsl.exe -d Ubuntu-24.04 -- fkctl doctor
wsl.exe -d Ubuntu-24.04 -- fkctl models
wsl.exe -d Ubuntu-24.04 -- fkctl agents status
```

| 对象 | 路径 |
| --- | --- |
| WSL gateway / Science boot | `~/.local/share/science-codex-finalkit/logs` |
| Windows Claude | `%LOCALAPPDATA%\ScienceCodexFinalKit\WindowsClaude\logs` |
| 多 Agent | 隔离 `~/.finalkit-client/.codex` 下的上游 runtime state |

日志可能包含路径、模型和错误响应；分享前脱敏，不要分享 auth 或 secret 文件。

## 15. 常见问题

### 15.1 Windows Codex profile 没有提示登录

配置器先检测官方登录。已登录时会显示 `Existing Windows Codex ChatGPT login detected`，因此不重复拉起浏览器。未登录时会停止并要求：

```powershell
codex login
```

### 15.2 出现“Value 为空字符串”

3.3.0 的 Model prompt 使用现有 profile、Codex catalog、Codex config 或模板形成非空 seed；空 Enter 接受 seed。若仍发生，先运行：

```powershell
.\windows\Switchboard.ps1 -Action windows-claude-init
.\windows\Switchboard.ps1 -Action windows-claude-status
```

再检查 Windows Codex `models_cache.json` 是否可读，不要手工留下空 JSON 字段。

### 15.3 WSL Science 不能启动

```powershell
.\windows\90-Stop.cmd
.\windows\05-Update-Switchboard-Runtime.cmd
.\windows\21-Doctor.cmd
```

Switchboard 只接管身份完全匹配的 Science/gateway。若看到 `SCIENCE_CONTROL_UNAVAILABLE`，按错误中的精确发行版执行 `wsl.exe --terminate <distro>`，再更新 runtime；不要先运行 Clear。

### 15.4 Science 要求 Claude 账号

`status` 应显示：

```text
Science identity: Switchboard local-only; no Claude account used
```

若为 unknown/real credentials preserved，Switchboard 会保护该 profile 并拒绝覆盖。先确认是否启动了错误 data-dir 或错误 WSL 用户，不要删除未知 credential。

### 15.5 Windows Claude 是否影响 WSL

不会。分别检查：

```powershell
.\windows\49-Windows-Claude-Status.cmd
.\windows\20-Status.cmd
```

两边端口、PID、profile 和 auth owner 应不同；Windows controller 的契约还会检查代码路径不调用 `wsl.exe`。

### 15.6 多 Agent 缺少 zsh 或 lsof

重新运行完整 `Build.cmd`，只补装缺失系统包；然后再运行 `agents-status`。不要把上游 scripts 复制到 Windows 执行，该模块是 WSL-only。

### 15.7 能看到 Codex 模型但看不到 Reasoning

Reasoning 列表来自官方 Codex 本地 cache 的 `supported_reasoning_levels`。先完成当前 Codex CLI 登录和 cache 刷新，再重新配置。若 entry 没有 metadata，Switchboard 不会声称所有固定强度可用；cache 也不证明实时 entitlement，最终用显式 `test-codex-tiers` 的实际请求确认。`auto` 仍可用于透传请求的 role effort。

### 15.8 WSL 无法访问隔离 Chrome

浏览器桥依赖 WSL 能访问 Windows loopback。可人工检查 mirrored networking，但 Switchboard 不自动改 `.wslconfig`。该问题只影响可选 Chrome bridge，不影响 provider gateway。

## 16. 多用户与可移植性

每个 Windows 用户、每个 WSL Linux 用户拥有自己的 provider key、Codex auth、Science identity、model routes、Windows DPAPI profile 和多 Agent plugin state。

给另一个 WSL 用户安装：

```powershell
.\windows\Switchboard.ps1 -Action build -LinuxUser alice
```

包目录可以放在任一本地盘。安装后的 runtime 不依赖源包永久留在原位置；更新 runtime 时使用当前可信包。其他用户不需要作者机器的 `D:\Tools`、用户名、auth 或预先存在的 WSL。

## 17. Clear 与恢复

`Clear.cmd` 是可选破坏性入口，不是普通修复手段：

```powershell
.\Clear.cmd
```

默认只选择精确命名发行版，要求输入精确确认词，先导出备份，再调用 `wsl --unregister`。备份目录：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\Backups
```

检查：

```powershell
Get-ChildItem "$env:LOCALAPPDATA\ScienceCodexFinalKit\Backups"
```

恢复示例：

```powershell
wsl --import Ubuntu-24.04-Restored `
  C:\WSL\Ubuntu-24.04-Restored `
  C:\Users\<user>\AppData\Local\ScienceCodexFinalKit\Backups\<backup>.tar `
  --version 2
```

`wsl --unregister` 会删除目标发行版 Linux 文件系统；只有明确要重建且备份可读时才使用。

## 18. 内部兼容命名

以下 owner 暂不改名：

- `fkctl`；
- `FINALKIT_*`；
- `~/.local/share/science-codex-finalkit`；
- `~/.science-finalkit`；
- `~/.finalkit-client`；
- `%LOCALAPPDATA%\ScienceCodexFinalKit`；
- `X-FinalKit-Control` 和现有 DPAPI entropy。

它们参与 credential、进程 owner、connector patch、回滚和升级识别。显示名、脚本入口与文档已统一为 Claude Codex Switchboard；保留内部 namespace 是无感升级兼容，不是第二套产品。

## 19. 完整命令索引

```powershell
# 帮助、安装、更新
.\windows\Switchboard.ps1 -Action help
.\windows\Switchboard.ps1 -Action menu
.\windows\Switchboard.ps1 -Action build
.\windows\Switchboard.ps1 -Action update-runtime
.\windows\Switchboard.ps1 -Action update-tools

# WSL provider auth
.\windows\Switchboard.ps1 -Action configure-deepseek
.\windows\Switchboard.ps1 -Action configure-kimi
.\windows\Switchboard.ps1 -Action configure-glm
.\windows\Switchboard.ps1 -Action configure-codex
.\windows\Switchboard.ps1 -Action configure-codex-device
.\windows\Switchboard.ps1 -Action migrate-windows-codex-auth-to-wsl

# Model / reasoning
.\windows\Switchboard.ps1 -Action models
.\windows\Switchboard.ps1 -Action discover-models -RemainingArgs deepseek
.\windows\Switchboard.ps1 -Action update-models

# WSL Science / Claude Code
.\windows\Switchboard.ps1 -Action deepseek
.\windows\Switchboard.ps1 -Action kimi
.\windows\Switchboard.ps1 -Action glm
.\windows\Switchboard.ps1 -Action codex
.\windows\Switchboard.ps1 -Action science
.\windows\Switchboard.ps1 -Action claude -RemainingArgs deepseek
.\windows\Switchboard.ps1 -Action stop

# Windows Claude
.\windows\Switchboard.ps1 -Action windows-claude-init
.\windows\Switchboard.ps1 -Action windows-claude-configure -RemainingArgs codex
.\windows\Switchboard.ps1 -Action windows-claude -RemainingArgs codex
.\windows\Switchboard.ps1 -Action windows-claude-status
.\windows\Switchboard.ps1 -Action windows-claude-stop
.\windows\Switchboard.ps1 -Action windows-claude-official

# Multi-Agent
.\windows\Switchboard.ps1 -Action agents-install
.\windows\Switchboard.ps1 -Action agents-status
.\windows\Switchboard.ps1 -Action agents -Project D:\work\repo -RemainingArgs deepseek
.\windows\Switchboard.ps1 -Action agents-on
.\windows\Switchboard.ps1 -Action agents-off
.\windows\Switchboard.ps1 -Action agents-stop

# Browser、状态和测试
.\windows\Switchboard.ps1 -Action browser-start
.\windows\Switchboard.ps1 -Action browser-science
.\windows\Switchboard.ps1 -Action browser-status
.\windows\Switchboard.ps1 -Action browser-stop
.\windows\Switchboard.ps1 -Action status
.\windows\Switchboard.ps1 -Action doctor
.\windows\Switchboard.ps1 -Action smoke
.\windows\Switchboard.ps1 -Action test-codex-tiers
```
