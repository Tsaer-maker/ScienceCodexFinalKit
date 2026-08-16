# Science SwitchModel / FinalKit 3.2.3 操作手册

本手册面向第一次接触本项目的 Windows 用户，说明如何安装、配置、验证、日常使用、更新和恢复。先读项目概览请看 [README.zh-CN.md](README.zh-CN.md)；需要检查实现和安全 owner 时再看 [代码剖析](docs/CODE_WALKTHROUGH.zh-CN.md)。

FinalKit 的两套运行面彼此独立：

- **WSL 主运行面**：Claude Science 和原生 Linux Claude Code 可接 DeepSeek、Kimi、GLM 或 ChatGPT/Codex。
- **Windows 可选运行面**：官方 Windows Claude 应用使用单独的 DeepSeek、Kimi、GLM 或 Windows Codex CLI 登录；它不读取、不调用、不修改 WSL。

四种 provider 不是四份通用 API key：DeepSeek、Kimi、GLM 使用各自 API key；Codex 使用官方 Codex CLI 的 ChatGPT 登录，不要求 OpenAI API key。

## 目录

1. [使用边界与系统前提](#1-使用边界与系统前提)
2. [获取软件包与首次检查](#2-获取软件包与首次检查)
3. [Build：首次安装或完整修复](#3-build首次安装或完整修复)
4. [配置 WSL provider 与认证](#4-配置-wsl-provider-与认证)
5. [配置 Model 与 Reasoning](#5-配置-model-与-reasoning)
6. [测试、启动和使用 WSL](#6-测试启动和使用-wsl)
7. [配置独立 Windows Claude](#7-配置独立-windows-claude)
8. [更新与可选浏览器桥](#8-更新与可选浏览器桥)
9. [多用户、自定义路径与协作](#9-多用户自定义路径与协作)
10. [故障处理](#10-故障处理)
11. [备份、Clear、恢复与凭据轮换](#11-备份clear恢复与凭据轮换)
12. [日常命令与最终验收](#12-日常命令与最终验收)

## 1. 使用边界与系统前提

### 1.1 当前发布状态

3.2.3 当前位于 GitHub 分支 `fix/science-entry-v3.2.1`，默认 `main` 仍是 3.2.0。在 merge/tag 前，需要本手册所述 Windows/WSL 独立 profile、逐角色 Model/Reasoning 和一次性 auth 迁移的用户，必须明确获取该分支。

仓库当前没有项目级 `LICENSE`，只有 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。因此当前代码可以供维护者和获授权用户安装验证，但在维护者选择许可证前，不应把它描述为已经完成许可闭环的正式公共发行版。

### 1.2 必需条件

- Windows 10 版本 2004 / Build 19041 以上，或 Windows 11；微软的当前 WSL 安装说明见 [Install WSL](https://learn.microsoft.com/windows/wsl/install)。
- 硬件虚拟化可用，Windows 策略允许启用 WSL2。
- x64 或 ARM64 Windows/WSL。安装器会根据 Linux `uname -m` 选择 Node.js `x64` 或 `arm64`。
- 稳定网络，可访问 Ubuntu、GitHub、Node.js、PyPI、npm、Claude/OpenAI，以及准备使用的 provider。
- 建议至少 `20 GB` 可用磁盘空间。当前完整参考环境约占 `14 GB`，缓存和后续工具会继续增长。
- 至少一种自己有权使用的推理凭据。
- 用自己的普通 Windows 账号运行；只有首次启用 WSL 系统组件时允许一次 UAC。

### 1.3 只在独立 Windows Claude 中需要

- 官方 Claude Windows 应用；Anthropic 当前要求 Windows 10 以上，见 [Install Claude Desktop](https://support.claude.com/en/articles/10065433-install-claude-desktop)。
- Windows Python `3.10+`：

  ```powershell
  py -3 --version
  ```

  或：

  ```powershell
  python --version
  ```

- 配置 Codex profile 时，Windows 还需安装官方 Codex CLI，并由用户自己完成 `codex login`。OpenAI 对 ChatGPT 计划登录 Codex 的说明见 [Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)。

只使用 WSL Claude Science 时，不要求安装 Windows Claude、Windows Python 或 Windows Codex CLI。

### 1.4 不做什么

FinalKit 不会：

- 绕过账号、订阅、额度、地区、组织、验证码或付费限制；
- 把 API key 写进脚本、命令行参数、Claude JSON 或项目文件；
- 把 Windows 和 WSL 的 auth 做成自动同步；
- 将 Windows Claude 失败静默转交给 WSL；
- 将 WSL Science 失败静默转交给 Windows；
- 强占未知端口或结束身份不匹配的进程；
- 因普通网络失败、模型错误或未配置认证自动删除 WSL。

## 2. 获取软件包与首次检查

### 2.1 获取当前 3.2.3 分支

使用 Git：

```powershell
git clone --branch fix/science-entry-v3.2.1 --single-branch `
  https://github.com/Tsaer-maker/ScienceCodexFinalKit.git
Set-Location .\ScienceCodexFinalKit
```

使用 GitHub ZIP 时，应在分支选择器中先切到 `fix/science-entry-v3.2.1`，再下载并完整解压。

不要直接在以下位置运行：

- ZIP 压缩包内部；
- 浏览器、聊天软件或邮件的临时目录；
- 会自动清理或按需下载的目录；
- 即将撤除的磁盘；
- 多个内容不同但名称相同的旧副本。

建议放在稳定目录，例如：

```text
C:\Tools\ScienceCodexFinalKit
D:\Tools\ScienceCodexFinalKit
```

根 `.cmd` 通过自身目录定位包，安装盘和用户名不需要与示例一致。

### 2.2 以正确的 Windows 用户运行

WSL 发行版注册、Windows Codex 登录、Windows Claude DPAPI、Chrome profile 和备份都属于当前 Windows 用户。不要从另一个 Administrator 账号代装 Ubuntu，再回普通账号使用。

先查看当前 WSL 状态：

```powershell
wsl --status
wsl --list --verbose
```

若没有 Ubuntu，这是新安装的正常起点；直接 Build，不需要 Clear。

### 2.3 查看入口帮助

在包根目录执行：

```powershell
.\windows\FinalKit.ps1 -Action help
```

或双击：

```text
SwitchModel.cmd
```

菜单适合日常交互；PowerShell 命令适合复现和排错。两者调用同一 owner。

## 3. Build：首次安装或完整修复

### 3.1 默认安装

双击：

```text
Build.cmd
```

也可以运行：

```powershell
.\windows\FinalKit.ps1 -Action build
```

默认建立或复用：

```text
WSL distro   Ubuntu-24.04
Linux user   由当前 Windows 用户名安全转换
WSL storage  当前 Windows 用户的标准 WSL 位置
```

Build 会安装或核验：

1. Ubuntu 24.04 WSL2；
2. 必需 Linux 系统工具；
3. Claude Science；
4. 原生 Linux Claude Code；
5. Linux Codex CLI；
6. 固定 Node.js 与 Chrome DevTools MCP；
7. 固定提交的 Codex connector；
8. FinalKit manager、gateway、Science 隔离身份和模型路由；
9. 不访问真实模型上游的离线契约与 smoke。

### 3.2 首次 UAC 与重启

若 WSL 平台尚未启用，Build 会申请一次 UAC，只准备 Windows WSL 系统组件。它不会在提升进程中安装 Ubuntu、保存 API key 或配置 OAuth。

若 Windows 要求重启：

1. 让 Build 正常停止；
2. 重启 Windows；
3. 登录回原来的普通 Windows 用户；
4. 再运行一次 `Build.cmd`。

不要切换到另一个管理员账号继续安装。

### 3.3 指定 Linux 用户

```powershell
.\windows\FinalKit.ps1 -Action build -LinuxUser alice
```

后续配置、启动和状态命令也应始终带同一个 `-LinuxUser alice`。不同 Linux 用户拥有独立的 API key、Codex 登录、Science 数据、模型路由和日志。

### 3.4 自定义 WSL 名称与位置

普通用户无需自定义。确需把发行版放到其他盘时，发行版名称和位置必须同时明确：

```powershell
.\windows\FinalKit.ps1 -Action build `
  -Distro Research-Ubuntu-24.04 `
  -DistroLocation D:\WSL\Research-Ubuntu-24.04 `
  -LinuxUser alice
```

目标应是稳定位置。不要把 WSL 放进同步盘、临时目录或将要撤除的磁盘。

### 3.5 中断后恢复

下载失败、网络中断或窗口意外关闭时，直接重复同一个 Build 命令。Build 是幂等的安装/修复入口，会复用已成功的部分。

此时不要先 Clear。只有 `doctor` 明确证明完整组件缺失时，才需要 Build；仅换模型或更新 runtime 不需要重新 Build。

### 3.6 成功判据

末尾至少应出现：

```text
BUILD_OK distro=Ubuntu-24.04 linux_user=<user>
SMOKE_OK
```

然后运行：

```powershell
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action smoke
```

`doctor` 中某个 provider 显示 `not configured` 不是安装失败，只表示当前 Linux 用户还没有保存该 provider 的认证。

## 4. 配置 WSL provider 与认证

至少配置一种 provider。四种可同时存在，互不覆盖。

> API key 只能在隐藏提示中输入。不要把 key 或 auth JSON 放进命令行、`.cmd`、README、项目、聊天、截图、剪贴板脚本或 `HANDOFF.md`。

### 4.1 DeepSeek

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek
```

快捷入口：

```text
windows\01-Configure-DeepSeek.cmd
```

终端出现 `DeepSeek API key (hidden):` 后粘贴 key 并回车。输入时不会显示字符或星号，这是正常的。

### 4.2 Kimi

```powershell
.\windows\FinalKit.ps1 -Action configure-kimi
```

快捷入口：

```text
windows\03-Configure-Kimi.cmd
```

### 4.3 GLM

```powershell
.\windows\FinalKit.ps1 -Action configure-glm
```

快捷入口：

```text
windows\04-Configure-GLM.cmd
```

三家 key 保存在目标 Linux 用户的 WSL ext4 home 中，文件权限 `0600`。它们不会复制到 Claude Science data HOME。

### 4.4 ChatGPT/Codex：WSL 自主登录

默认使用官方 Linux Codex CLI 的浏览器登录：

```powershell
.\windows\FinalKit.ps1 -Action configure-codex
```

快捷入口：

```text
windows\02-Configure-ChatGPT-Codex.cmd
```

登录先在临时 HOME 中完成并由官方 `codex login status` 验证；成功后才原子替换 WSL 的正式 auth。取消登录或验证失败不会覆盖原文件。

默认浏览器回调需要 Windows 浏览器能访问 WSL CLI 的 `localhost:1455`。只有账号或工作区明确允许 device login、且普通浏览器回调确实不可用时，才使用 beta 备用流：

```powershell
.\windows\FinalKit.ps1 -Action configure-codex-device
```

device flow 返回 `403` 往往表示账号/工作区没有启用该流，不表示 WSL 安装损坏。优先回到默认 `configure-codex`。

### 4.5 可选：一次性把 Windows Codex auth 初始化到 WSL

如果 Windows 官方 Codex CLI 已经显示：

```text
Logged in using ChatGPT
```

可以选择一次性迁移：

```powershell
.\windows\FinalKit.ps1 -Action migrate-windows-codex-auth-to-wsl
```

快捷入口：

```text
windows\08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd
```

该动作会：

1. 显示 Windows 源 owner、目标 distro 和 Linux 用户；
2. 要求输入精确确认词 `MIGRATE`；
3. 安全停止 FinalKit 自己的 WSL Science/gateway；
4. 只经子进程 stdin 传递 auth 字节；
5. 在临时 Linux HOME 中验证官方 Codex 登录；
6. 验证成功后以 `0600` 原子替换；
7. 失败时恢复此前 WSL auth 的精确字节和权限。

它不会创建定时同步、启动同步或双向同步。迁移完成后，Windows 和 WSL 仍是两个独立 auth owner；上游 refresh-token 轮换可能让其中一份以后失效。只在失效的一侧重新登录即可。

不要用 `Get-Content`、剪贴板或命令行参数手工复制 `auth.json`。

### 4.6 只读查看认证状态

```powershell
.\windows\FinalKit.ps1 -Action status
```

状态只显示 `configured` / `not configured`，不会打印 key 或 token。

## 5. 配置 Model 与 Reasoning

### 5.1 三个角色独立配置

每个 provider 都保存六个短字段：

```text
Opus   Model / Reasoning
Sonnet Model / Reasoning
Haiku  Model / Reasoning
```

Sonnet、Opus 和 Haiku 是三个独立路由；Sonnet 不会再被默认绑到 Opus。持久配置只保留 `model_<role>` 和 `reasoning_<role>`，不再使用早期冗长的配置名。

包内当前建议映射是：

```text
Opus   gpt-5.6-sol   max
Sonnet gpt-5.6-terra max
Haiku  gpt-5.6-luna  max
```

这只是新 profile 的建议初值，不是所有账号的能力承诺。其他用户必须以自己操作系统、自己 Codex home 中的 `models_cache.json` 和配置界面为准。

### 5.2 查看当前路由

```powershell
.\windows\FinalKit.ps1 -Action models
```

### 5.3 只读发现三家 API 的模型

```powershell
.\windows\FinalKit.ps1 -Action discover-models -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action discover-models -RemainingArgs kimi
.\windows\FinalKit.ps1 -Action discover-models -RemainingArgs glm
```

该操作读取源码固定的官方 HTTPS endpoint，不生成文本、不消耗推理 tokens、不写配置，也不回显 key。账号能在目录中看到模型，只证明模型 ID 可见，不证明套餐或具体 Reasoning 一定可调用。

### 5.4 交互更新

最简单的入口是菜单 `17 Update provider models`：

```powershell
.\windows\FinalKit.ps1 -Action update-models
```

流程为：

1. 选择 provider；
2. 显示该账号或本机缓存可见的模型；
3. 分别输入 Opus、Sonnet、Haiku 的 Model；
4. 分别选择三个 Reasoning；
5. 显示 dry-run 预览；
6. 用户确认后才原子写入；
7. 只有明确选择 restart 时才重启当前 route。

Codex 会逐模型显示本机缓存中的 `supported_reasoning_levels`、默认值和说明；已知模型会拒绝缓存未声明的强度。`ultra` 只有具体模型明确支持时才会出现。

### 5.5 非交互更新

以下只是语法示例。模型 ID 必须替换为当前账号目录或本机 Codex 缓存实际列出的值：

```powershell
.\windows\FinalKit.ps1 -Action update-models -RemainingArgs `
  'codex,--opus,gpt-5.6-sol,--reasoning-opus,max,--sonnet,gpt-5.6-terra,--reasoning-sonnet,max,--haiku,gpt-5.6-luna,--reasoning-haiku,max,--restart'
```

在 WSL 内也可直接使用：

```bash
fkctl update-models codex \
  --opus gpt-5.6-sol --reasoning-opus max \
  --sonnet gpt-5.6-terra --reasoning-sonnet max \
  --haiku gpt-5.6-luna --reasoning-haiku max \
  --dry-run --json
```

确认预览后去掉 `--dry-run`，需要重启当前 route 时加 `--restart`。

### 5.6 Reasoning 选项

| Provider | 配置器可选值 | 能力来源与边界 |
|---|---|---|
| DeepSeek | `auto/none/high/max` | provider 级映射；最终以真实请求为准 |
| Kimi | `auto/none/low/high/max` | always-thinking 模型可能拒绝 `none` |
| GLM | `auto/none/high/max` | 模型和套餐可能进一步收窄 |
| Codex | `none/low/medium/high/xhigh/max/ultra` 的模型支持子集 | 每个模型读取当前 OS 的 Codex 缓存 |

Windows Claude 的 Model/Reasoning 不由 WSL `update-models` 修改；重新运行对应 `windows-claude-configure` 即可独立更新。

## 6. 测试、启动和使用 WSL

### 6.1 先做最小真实测试

只运行已经配置的 provider：

```powershell
.\windows\FinalKit.ps1 -Action test-deepseek
.\windows\FinalKit.ps1 -Action test-kimi
.\windows\FinalKit.ps1 -Action test-glm
.\windows\FinalKit.ps1 -Action test-codex
```

成功输出：

```text
BACKEND_OK mode=<provider>
```

这些命令发送真实请求，可能产生少量费用或账号用量。`build`、`doctor` 和 `smoke` 的离线契约不会访问模型上游。

`test-codex` 默认只验证当前 Opus route。需要明确验收 Codex 三档时运行：

```powershell
.\windows\FinalKit.ps1 -Action test-codex-tiers
```

它会产生三次最小真实请求，全部成功才输出：

```text
CODEX_TIERS_OK actual configured routes verified
```

某一档失败时 FinalKit 不会偷偷回退到 Opus。

### 6.2 启动 Claude Science

```powershell
.\windows\FinalKit.ps1 -Action deepseek
.\windows\FinalKit.ps1 -Action kimi
.\windows\FinalKit.ps1 -Action glm
.\windows\FinalKit.ps1 -Action codex
```

也可以双击 `SwitchModel.cmd`，使用菜单 `7–10`。

启动命令会：

1. 核验或原子建立 FinalKit 本地 Science 身份；
2. 启动对应 loopback gateway；
3. 启动 Claude Science；
4. 核验本地 `/api/me`、工作台和 runtime identity；
5. 生成一次性本机 URL；
6. 在默认 Windows 浏览器打开。

页面可能显示一次 `Sign in`。只有地址为 `127.0.0.1` 或 `localhost` 时才接受；它是本机 nonce/cookie 会话门，不是 Claude.ai 登录。

WSL Claude Science 不需要 Claude.ai 账号。实际推理依然需要前面配置的 API key 或 Codex ChatGPT 登录。

### 6.3 状态、诊断和停止

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action stop
```

健康状态应包含：

```text
Gateway:          healthy
Claude Science:   running
Runtime identity: matched
Science identity: FinalKit local-only; no Claude account used
```

`stop` 只停止身份匹配的 FinalKit Science/gateway，不执行全局 `wsl --shutdown`。

### 6.4 切换 provider

直接运行另一个启动命令即可。FinalKit 会先停止自己拥有的当前 Science/gateway，再切换 route、重启并核对 endpoint。

不要手工编辑 gateway runtime JSON，也不要手工结束未知端口占用者。

### 6.5 使用原生 Linux Claude Code

菜单 `19–22` 会在单独 WSL 终端打开对应 provider。命令行方式：

```powershell
.\windows\FinalKit.ps1 -Action claude -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action claude -RemainingArgs kimi
.\windows\FinalKit.ps1 -Action claude -RemainingArgs glm
.\windows\FinalKit.ps1 -Action claude -RemainingArgs codex
```

在 WSL 内：

```bash
fkctl claude deepseek
fkctl claude kimi
fkctl claude glm
fkctl claude codex
```

传递 Claude Code 参数：

```bash
fkctl claude deepseek --help
```

原生 Claude Code 复用同一 provider 配置，但不是 Claude Science 的启动替代品。它只取得本地 gateway endpoint，不取得三家 provider 的真实 key。

## 7. 配置独立 Windows Claude

本节完全是可选功能。控制器固定使用 Windows loopback `127.0.0.1:18987`，不会读取、启动、终止或修改 WSL。

### 7.1 先确认前提

```powershell
py -3 --version
codex --version
codex login status
```

- 只配置三家 API 时，Codex CLI 可缺省。
- 配置 Codex profile 时，必须先由用户运行官方 `codex login`。
- 若 `codex login status` 已显示 `Logged in using ChatGPT`，配置器不会再弹浏览器，这是预期行为。
- 若未登录，FinalKit 不代替官方 CLI 发起登录；先运行 `codex login`，完成后再配置。

### 7.2 初始化四个空 profile

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-init
.\windows\FinalKit.ps1 -Action windows-claude-status
```

快捷入口：

```text
windows\40-Initialize-Windows-Claude.cmd
windows\49-Windows-Claude-Status.cmd
```

初始化只建立四个隔离槽，保持官方 `1p`，不索取 key、不登录、不启动 gateway。看到 `Configured profiles: 0/4` 是正常结果。

### 7.3 配置 DeepSeek、Kimi 或 GLM

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs kimi
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs glm
```

对应快捷入口是 `windows\41–43`。

API key 通过隐藏提示输入，用 Windows DPAPI `CurrentUser` 加密保存。随后为 Opus、Sonnet、Haiku 分别选择 Model 与 Reasoning。

### 7.4 配置 Windows Codex profile

先完成官方登录：

```powershell
codex login
codex login status
```

再运行：

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs codex
```

快捷入口：

```text
windows\44-Configure-Windows-Claude-Codex-Login.cmd
```

配置器使用 Windows 官方 Codex CLI 的 ChatGPT 登录，不要求 OpenAI API key。它从当前 Windows Codex home 的 `models_cache.json` 和 `config.toml` 显示本机可见模型、建议值、逐模型 Reasoning 能力和说明。

Opus、Sonnet、Haiku 会逐个询问，不共享一个输入。若直接回车，则各自采用显示的默认值；不会再把空字符串传入 PowerShell，也不会把 Sonnet 强制设为 Opus。

### 7.5 启动、查看状态和停止

```powershell
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs kimi
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs glm
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs codex

.\windows\FinalKit.ps1 -Action windows-claude-status
.\windows\FinalKit.ps1 -Action windows-claude-stop
```

对应启动快捷入口是 `windows\45–48`，状态/停止是 `windows\49–50`。

若 Claude 已经打开，并且用户明确接受结束当前窗口后重启以读取新 profile，可加：

```powershell
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs codex -Force
```

未配置 profile 会在启动 Python 或修改 Claude deployment mode 前失败，Claude 保持官方状态。端口 `18987` 被未知进程占用时也会 fail-closed。

### 7.6 恢复官方 Claude 模式

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-official
```

快捷入口：

```text
windows\51-Restore-Windows-Claude-Official.cmd
```

它只处理 Windows Claude：

- 停止身份匹配的 Windows gateway；
- 把 Windows Claude 恢复为官方 `1p`；
- 移除 FinalKit 登记的四条 3P profile；
- 保留三家 DPAPI 设置和 Windows Codex 登录供以后复用；
- 不检查、不调用、不修改 WSL。

修改真实 Claude 配置前会建立可恢复备份。

### 7.7 Windows 状态与日志位置

每个 Windows 用户的 owner：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\WindowsClaude
```

主要内容：

```text
profiles.json
secrets\
runtime\
logs\
```

Windows Codex 登录仍由官方 `%CODEX_HOME%\auth.json` 持有；默认 `CODEX_HOME` 是 `%USERPROFILE%\.codex`。FinalKit runtime JSON 只保存 auth 路径，不复制 token。

## 8. 更新与可选浏览器桥

### 8.1 三类更新

| 动作 | 命令 | 会修改 | 不会修改 |
|---|---|---|---|
| FinalKit runtime | `-Action update-runtime` | 当前包中的 WSL manager、gateway、connector patch 与契约 | 不重建 WSL，不改 key/auth/模型 |
| Model/Reasoning | `-Action update-models` | 当前 provider 的三角色路由 | 不更新官方工具，不改 endpoint/auth |
| 官方工具 | `-Action update-tools` | Claude Science、Claude Code、Codex CLI、固定 Node/MCP | 不改 FinalKit 模型路由 |

从 Git 更新包后再部署 runtime：

```powershell
git status --short
git pull --ff-only
.\windows\FinalKit.ps1 -Action update-runtime
```

`git pull --ff-only` 只适用于没有未提交本地修改的 checkout。ZIP 用户应把新版本完整解压到明确的新目录，核对后从新目录执行 `update-runtime`，不要混拷几个版本的单个脚本。

官方工具更新需要联网并再次确认：

```powershell
.\windows\FinalKit.ps1 -Action update-tools
```

自动化环境可显式使用：

```powershell
.\windows\FinalKit.ps1 -Action update-tools -Force
```

`-Force` 只跳过第二次键盘确认，不跳过下载、二进制、auth 不变性或 doctor 检查。

### 8.2 Windows 隔离浏览器桥

只有需要页面点击、表单、截图、下载或登录态时才启用：

```powershell
.\windows\FinalKit.ps1 -Action browser-start
.\windows\FinalKit.ps1 -Action browser-science
.\windows\FinalKit.ps1 -Action browser-status
.\windows\FinalKit.ps1 -Action browser-mcp-info
.\windows\FinalKit.ps1 -Action browser-stop
```

`browser-science` 把当前已经运行的 Science 打开到隔离 Chrome，不切换 provider。浏览器 profile 位于：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\ChromeProfile
```

只有在这个隔离 profile 中登录明确允许自动化的网站。MCP 能看到和控制该 profile 内的页面。

配置 Claude Science 或 Claude Code 的 Chrome DevTools MCP 时，运行 `browser-mcp-info`，逐字使用它输出的 Command 与 Arguments；不要猜 Linux 用户名或手写安装路径。

## 9. 多用户、自定义路径与协作

### 9.1 便携性与隔离单位

执行 owner 中没有固定的 `TSA`、`C:\Users\TSA`、`/home/tsa` 或 `D:\Tools`：

- 根 `.cmd` 使用自身目录定位包；
- Windows 状态使用当前用户的 `%LOCALAPPDATA%` / `%USERPROFILE%`；
- Linux 状态使用目标用户的 `/home/<user>`；
- 自定义 distro/location/user 通过参数传递；
- Windows Claude DPAPI 使用 `CurrentUser`。

隔离单位为“Windows 用户 + WSL 发行版 + Linux 用户”。

### 9.2 多个 Windows 用户

每个 Windows 用户应在自己的普通账号下分别 Build，并分别配置认证。每人拥有自己的：

- WSL 注册；
- Windows Codex 登录；
- Windows Claude profile 与 DPAPI；
- Chrome profile；
- FinalKit 备份目录。

不要复制另一个 Windows 用户的 WSL 注册目录、DPAPI blob、Chrome profile 或 Codex auth。

### 9.3 同一发行版中的多个 Linux 用户

```powershell
.\windows\FinalKit.ps1 -Action build -LinuxUser bob
.\windows\FinalKit.ps1 -Action configure-deepseek -LinuxUser bob
.\windows\FinalKit.ps1 -Action deepseek -LinuxUser bob
.\windows\FinalKit.ps1 -Action status -LinuxUser bob
```

用户 home 和凭据独立，但同一 WSL 发行版仍共享网络 namespace 和默认端口。同一时刻只运行一套默认端口的 Science/gateway。若多个 Linux 用户需要真正并发，使用不同 `-Distro` 和 `-DistroLocation` 建立独立发行版。

### 9.4 可选的 Claude Science / Windows Codex 协作

初始化项目 handoff：

```powershell
.\windows\FinalKit.ps1 `
  -Action init-project `
  -Project D:\path\to\project
```

生成：

```text
<project>\.science-codex\HANDOFF.md
```

已有文件不会覆盖。只写研究问题、权威输入、统计单位、唯一 writer、改动 owner、验证、限制和待审问题；禁止写 key、token、cookie、密码、私钥或敏感数据。

反向只读复核：

```powershell
.\windows\FinalKit.ps1 `
  -Action windows-review `
  -Project D:\path\to\project
```

Claude Science reviewer skill 位于：

```text
claude-science-skills\reviewing-codex-science\SKILL.md
```

是否发布到 Claude Science 由用户在其可见的 Skills 界面或 `host.skills` API 中决定。FinalKit 不直接修改 Claude Science 的个人 skill、数据库、cookie 或账号设置。

协作时始终保持一个当前 writer；另一个 Agent 只读审阅。审阅意见是证据，不是自动裁决，也不授权删除、发布、登录、上传或外部发送。

## 10. 故障处理

按 `status -> doctor -> logs -> 最小修复 -> Build -> 最后才 Clear` 的顺序处理。

### 10.1 先获取状态

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
```

进入目标 WSL 后查看日志：

```powershell
wsl -d Ubuntu-24.04
```

```bash
fkctl status
fkctl doctor
fkctl logs gateway
fkctl logs science
```

自定义 distro 或 Linux 用户时，命令必须使用安装时同一组参数。

Windows Claude 日志目录：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\WindowsClaude\logs
```

`windows-claude-status` 还会打印当前 gateway 的精确日志路径。

### 10.2 常见故障

| 症状 | 优先处理 |
|---|---|
| `auth: not configured` | 运行对应 `configure-*`；不需要 Build |
| API `401/403` | 检查凭据、账号权限、余额、地区和组织策略；必要时轮换 |
| `model not found` 或 Reasoning 被拒绝 | 重新发现/选择当前账号模型；用对应真实测试验证 |
| gateway stopped | 运行目标 provider 的启动命令 |
| gateway identity mismatch | 先运行 `stop` 再启动；不要 kill 未知进程 |
| 本地页面显示 `Sign in` | 确认地址为 loopback，接受一次本机会话；不是 Claude.ai 登录 |
| 页面只有 Opus/Sonnet/Haiku fallback 或 `No credentials` | `update-runtime`，再运行 `smoke` 和对应 `test-*` |
| Science 页面没有打开 | 先 `status`；在 WSL 运行 `fkctl url` 获取新的本机一次性 URL |
| Build 下载中断 | 原命令重跑 Build；不要 Clear |
| 首次 Build 提示需要提升/重启 | 允许 UAC；重启后登录同一普通用户再 Build |
| Ubuntu Store 路径失败 | 当前安装器会尝试 `--web-download`；保留真实错误码继续诊断 |
| Codex device login 返回 `403` | 使用默认浏览器 `configure-codex`；确认账号是否允许 device flow |
| Windows Codex 已登录，WSL 显示未配置 | 两侧默认独立；WSL 自主登录或运行显式一次性迁移 |
| Codex 请求意外 `401` | connector 只安全刷新并重试一次；失败后在对应 OS 重新登录 |
| Windows Claude `Configured profiles: 0/4` | 初始化后的正常状态；逐个配置需要的 profile |
| Windows Claude Codex `login-missing` | 在 Windows 运行 `codex login` 和 `codex login status` |
| Windows Claude 找不到 Python | 安装 Windows Python 3.10+，确保 `py -3` 或 `python` 可用 |
| Windows Claude 仍显示旧的 `Default/Opus/Sonnet` 提示 | 当前包文件不是 3.2.3；切到正确分支/ZIP，再重新配置 |
| Windows Claude Sonnet 与 Opus 被绑在一起 | 用当前 3.2.3 controller 重新配置；三个角色应分别询问 Model/Reasoning |
| Windows Claude `unconfigured` | 补齐所选 profile 的认证及三组 Model/Reasoning；不会回退 WSL |
| Windows Claude 端口 `18987` 被占用 | 查明 PID；不要结束未知进程 |
| Windows Claude 切 profile 后界面未变 | 完全退出 Claude 重开，或在允许时用 `-Force` |
| 浏览器桥 stopped | 重新运行 `browser-start` |
| WSL 无法访问浏览器 loopback | 检查 WSL mirrored networking；FinalKit 不自动改 `.wslconfig` |
| MCP 找不到 Node | 使用 `browser-mcp-info` 打印的固定 wrapper，不要猜 npm shim |

### 10.3 Claude Science 启动期 `D` 状态

Claude Science 0.1.27 在首次启动时可能短暂处于 Linux 不可中断 I/O 的 `D` 状态。FinalKit 只在以下身份全部匹配时，允许最多 45 秒的受限 readiness 窗口：

- 同一 PID；
- 相同 argv；
- 相同 HOME/data-dir；
- 相同 lock owner。

如果刚启动后立刻查询状态遇到该窗口，先等启动窗口完成，再重试 `status`。

若 45 秒后仍持续 `D`，或已运行 daemon 后来持续 `D`：

1. 用 `wsl --list --verbose` 确认精确 distro；
2. 结束该 distro 中自己的其他任务；
3. 从 Windows 精确终止该发行版：

   ```powershell
   wsl --terminate Ubuntu-24.04
   ```

4. 运行：

   ```powershell
   .\windows\FinalKit.ps1 -Action update-runtime
   ```

`--terminate` 不注销发行版、不删除文件、不清除 API/Codex 认证。不要因此 Clear。

### 10.4 何时重新 Build

只有以下情况才重新 Build：

- `doctor` 证明组件或 owner 文件缺失；
- installer 确认没有完成；
- 官方客户端/依赖已损坏，独立 update 无法修复。

```powershell
.\windows\FinalKit.ps1 -Action build
```

修复 Build 不应覆盖已有 secret、Codex 登录和 Model/Reasoning 选择。

### 10.5 何时才考虑 Clear

只有以下条件全部成立时才 Clear：

- distro 确实不可修复，或用户明确要求完全重置；
- 没有活跃任务；
- 唯一项目、结果和凭据已有外部副本；
- 已核对精确 distro 名称和 BasePath；
- 接受从 tar 恢复或永久丢弃。

普通网络失败、单个 provider 错误、模型配置错误、登录过期、gateway 故障或 Build 中断，都不是 Clear 理由。

## 11. 备份、Clear、恢复与凭据轮换

### 11.1 Clear 是破坏性操作

`wsl --unregister` 会删除目标发行版的 Linux 文件系统。FinalKit 默认先导出备份，并要求输入精确确认词。

查看目标：

```powershell
wsl --list --verbose
```

清理默认 Ubuntu：

```powershell
.\windows\FinalKit.ps1 -Action clear
```

或双击：

```text
Clear.cmd
```

程序显示精确 distro 和注册表 BasePath，然后要求：

```text
DELETE Ubuntu-24.04
```

确认文本不完全相同就不会删除。

### 11.2 多个 Ubuntu

只在确实要处理当前 Windows 用户注册的所有真实 Ubuntu 时使用：

```powershell
.\windows\FinalKit.ps1 -Action clear -AllUbuntu
```

程序会读取每个候选的 `/etc/os-release`，只有 `ID=ubuntu` 才进入范围。

自动化场景可显式跳过键盘确认：

```powershell
.\windows\FinalKit.ps1 -Action clear -AllUbuntu -Force
```

`-Force` 仍默认备份。只有已经证明没有任何独有数据、并明确接受无法恢复时，才可再加 `-NoBackup`。

### 11.3 默认备份

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\Backups
```

Clear 后验证：

```powershell
wsl --list --verbose
Get-ChildItem "$env:LOCALAPPDATA\ScienceCodexFinalKit\Backups"
```

应满足：

- 出现 `CLEAR_OK <distro>`；
- 目标不再位于 WSL 列表；
- 未用 `-NoBackup` 时存在非空 tar；
- 非目标 distro 仍存在。

### 11.4 从 tar 恢复

选择一个不存在的新 distro 名称和空目录：

```powershell
$restoreDir = 'D:\WSL\Recovered-Ubuntu-24.04'
New-Item -ItemType Directory -Path $restoreDir

wsl --import `
  Recovered-Ubuntu-24.04 `
  $restoreDir `
  'C:\Users\<user>\AppData\Local\ScienceCodexFinalKit\Backups\<backup>.tar' `
  --version 2
```

验证：

```powershell
wsl --list --verbose
wsl -d Recovered-Ubuntu-24.04 -u root -- cat /etc/os-release
```

不要把恢复目录指向当前运行中的 distro 目录。

### 11.5 凭据轮换

若 key 曾进入聊天、命令行、截图、项目或公开文件，应先在 provider 控制台撤销，再运行对应配置命令写入新 key：

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek
.\windows\FinalKit.ps1 -Action configure-kimi
.\windows\FinalKit.ps1 -Action configure-glm
```

Windows Claude 的三家 key 用对应 `windows-claude-configure` 重新输入。

Codex 登录失效时，在失效的 OS 单独重新登录：

```powershell
# WSL owner
.\windows\FinalKit.ps1 -Action configure-codex

# Windows owner
codex login
codex login status
```

不要打印、编辑或跨聊天搬运 auth JSON。

## 12. 日常命令与最终验收

### 12.1 WSL 日常最短路径

```powershell
# 启动已配置 provider
.\windows\FinalKit.ps1 -Action codex

# 状态
.\windows\FinalKit.ps1 -Action status

# 可选：在隔离 Chrome 打开当前 Science
.\windows\FinalKit.ps1 -Action browser-science

# 结束浏览器桥
.\windows\FinalKit.ps1 -Action browser-stop

# 结束 Science/gateway
.\windows\FinalKit.ps1 -Action stop
```

### 12.2 Windows Claude 日常最短路径

```powershell
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs codex
.\windows\FinalKit.ps1 -Action windows-claude-status
.\windows\FinalKit.ps1 -Action windows-claude-stop
```

需要返回官方 Claude：

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-official
```

### 12.3 每位新用户的 WSL 验收

1. 在自己的普通 Windows 账号运行 Build。
2. 看到 `BUILD_OK` 和 `SMOKE_OK`。
3. 运行 `doctor`，确认组件和 owner。
4. 只配置自己拥有的一种 provider。
5. 查看/选择该账号实际支持的 Model/Reasoning。
6. 运行对应 `test-*`。
7. 看到 `BACKEND_OK mode=<provider>`。
8. 启动同名 Science route。
9. 只在 loopback 页面接受本机会话，不做 Claude.ai 登录。
10. 再运行 `status`，确认 gateway、Science、runtime 和本地身份全部健康。

### 12.4 可选 Windows Claude 验收

1. 确认官方 Windows Claude 与 Python 3.10+。
2. 运行 `windows-claude-init`；`0/4` 和官方 `1p` 是正确初态。
3. 若用 Codex，先确认 Windows `codex login status`。
4. 配置一个 profile 的认证及三组独立 Model/Reasoning。
5. 启动该 profile。
6. `windows-claude-status` 应显示 gateway owner、profile、PID 和日志。
7. 在 Windows Claude 内发送一个最小请求，验证当前账号真实可用性。
8. 停止 gateway 或恢复官方 `1p`。

### 12.5 “其他用户可直接用”的准确结论

当前执行代码已经不依赖维护者的用户名、固定盘符或固定 home；普通新用户可按本手册在自己的 Windows 用户、WSL distro 和 Linux home 中安装验证。

仍需由维护者完成的正式发布动作是：

- 将 3.2.3 合并到默认分支；
- 选择并添加项目级许可证；
- 创建明确 tag/release；
- 在一台从未安装过 FinalKit 的普通用户环境完成一次冷启动验收。

在这些发布动作完成前，当前分支属于可安装、可验证的候选版本，而不是已经完成许可和发行闭环的稳定公开版。
