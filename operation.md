# Science SwitchModel / FinalKit 3.1.2 操作手册

本手册是 FinalKit 的唯一运行与维护入口，面向首次安装者、日常使用者和本机管理员。架构、安全设计与源码职责见 [README.zh-CN.md](README.zh-CN.md) 和 [代码剖析](docs/CODE_WALKTHROUGH.zh-CN.md)。

## 1. 先判断从哪里开始

不要机械地每次都从 Clear 开始。按实际状态选择入口：

| 当前状态 | 从哪里开始 | 不要做什么 |
|---|---|---|
| 新电脑，还没有 Ubuntu WSL | [3. Build](#3-build从零建立环境) | 不需要 Clear |
| 有旧 Ubuntu，且确认其中没有唯一数据 | [2. Clear](#2-clear可选删除旧-ubuntu-wsl) | 不要加 `-NoBackup` |
| Build 曾因网络中断 | 重新执行 Build | 不要删除 WSL |
| Build 已完成，但没有模型认证 | [4. 配置 provider](#4-配置-provider) | 不要重复 Build |
| 已配置 provider，尚未验证 | [5. 真实后端测试](#5-真实后端测试) | 不要直接承担重要任务 |
| 已验证，准备日常使用 | [6. 启动与切换](#6-启动与切换) | 不要每天重建 |
| Science 或 gateway 出错 | [10. 故障处理](#10-故障处理) | 不要首先 Clear |

### 1.1 新用户最短路径

把 ZIP 完整解压到不会被聊天软件、云盘同步或移动磁盘自动清理的本地目录，例如：

```text
C:\Tools\ScienceCodexFinalKit
D:\Tools\ScienceCodexFinalKit
```

不要直接在 ZIP 内运行，也不要长期从微信/QQ 的消息临时目录或计划撤除的 E 盘运行。路径可含中文和空格，但稳定的短路径更方便普通用户排错。

全新用户只需按顺序执行：

1. 以自己的普通 Windows 账号登录；
2. 双击 `SwitchModel.cmd`，选择 `2 Install/repair`，或直接双击 `Build.cmd`；该入口用于首次安装和完整修复，普通 runtime/模型/工具升级改用菜单 `16/17/18`；
3. 首次启用 WSL 时允许一次 UAC；若 FinalKit 要求重启，重启并登录回同一个 Windows 账号，再执行一次 Build；
4. 看到 `BUILD_OK` 和 `SMOKE_OK` 后，返回菜单配置自己拥有的 DeepSeek、Kimi、GLM API key 或 ChatGPT Codex 登录；
5. 先运行对应的真实后端测试，再启动 Claude Science。

`Clear` 只用于确实存在且明确要删除的旧 Ubuntu。新电脑、空 WSL、Build 中断或普通网络失败都不应先 Clear。

## 2. Clear：可选删除旧 Ubuntu WSL

> 警告：`wsl --unregister` 会删除对应发行版的 Linux 文件系统。FinalKit 默认先导出备份，但仍应确认项目、凭证和唯一结果已有安全副本。

### 2.1 查看现有发行版

在普通 PowerShell 中运行：

```powershell
wsl --status
wsl --list --verbose
```

若没有需要删除的 Ubuntu，跳到 [3. Build](#3-build从零建立环境)。

### 2.2 默认清理标准 Ubuntu

双击根目录：

```text
Clear.cmd
```

或运行：

```powershell
cd C:\Tools\ScienceCodexFinalKit   # 或你自己的稳定解压目录
.\windows\FinalKit.ps1 -Action clear
```

程序会显示精确发行版名称和注册表 `BasePath`，并要求输入：

```text
DELETE Ubuntu-24.04
```

确认文本不完全一致时不会删除。

### 2.3 清理当前 Windows 用户的所有真实 Ubuntu

```powershell
.\windows\FinalKit.ps1 -Action clear -AllUbuntu
```

`-AllUbuntu` 会逐个读取发行版的 `/etc/os-release`，只有 `ID=ubuntu` 才会进入清理范围；不会仅凭名称猜测。

自动化测试可使用：

```powershell
.\windows\FinalKit.ps1 -Action clear -AllUbuntu -Force
```

`-Force` 只跳过键盘确认，仍默认备份。

仅当已经证明没有任何独有数据并且明确放弃恢复时，才可使用：

```powershell
.\windows\FinalKit.ps1 -Action clear -AllUbuntu -Force -NoBackup
```

### 2.4 备份和验证

默认备份目录：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\Backups
```

清理后验证：

```powershell
wsl --list --verbose
Get-ChildItem "$env:LOCALAPPDATA\ScienceCodexFinalKit\Backups"
```

成功判据：

- 终端出现 `CLEAR_OK <发行版名>`；
- 目标不再出现在 WSL 列表；
- 未使用 `-NoBackup` 时，存在非空 tar；
- 非目标发行版仍然存在。

## 3. Build：从零建立环境

### 3.1 前提

需要：

- 支持 WSL2 的 Windows 10/11；WSL 平台尚未启用时可由 Build 准备；
- 可访问 Ubuntu、Anthropic、OpenAI、Node.js、GitHub 和 Python/npm 软件源的网络；
- 至少数 GB 可用磁盘空间；
- 普通 Windows 用户运行 Build；首次启用 Windows WSL 平台时允许一次 UAC 管理员批准。

当前版本不要求用户预先打开管理员 PowerShell。全新机器第一次选择菜单 `2` 时：

1. FinalKit 先只读检查 `wsl --status`；
2. 若 Windows 报告 `需要提升`、`optional component required` 或对应错误码，FinalKit 弹出一次 UAC，只运行 `wsl.exe --install --no-distribution`；
3. 提升进程只准备 Windows 系统组件，不安装 Ubuntu，不写 API key/OAuth/FinalKit 配置；
4. 若系统组件立即可用，Build 回到原普通用户继续安装；
5. 若 Windows 要求重启，Build 明确停止。重启后登录回同一个普通 Windows 用户，再次选择菜单 `2`；
6. 第二次 Build 才把 `Ubuntu-24.04` 注册到当前普通 Windows 用户。

不要从另一个 Administrator 账号手工安装 Ubuntu，否则发行版会属于那个 Windows 账号。若只显示 `No matching Ubuntu WSL distribution is registered.`，这是 Clear 在空 WSL 上的正常结果，可直接选择 Build。

这里使用的参数均为 WSL 官方入口：[`--no-distribution`](https://learn.microsoft.com/zh-cn/windows/wsl/basic-commands) 只准备 WSL 而不安装发行版，`--no-launch` 安装后不自动启动，`--web-download` 在 Store 路径失败时改用联机源。Windows 版本过旧或系统策略阻止虚拟化时，FinalKit 会保留真实诊断而不是继续猜测。

### 3.2 默认 Build

双击：

```text
Build.cmd
```

或执行：

```powershell
cd C:\Tools\ScienceCodexFinalKit   # 或你自己的稳定解压目录
.\windows\FinalKit.ps1 -Action build
```

默认结果：

```text
WSL distro    Ubuntu-24.04
WSL storage   当前 Windows 用户的标准 WSL 位置
Linux user    由当前 Windows 用户名安全转换
```

安装过程会依次准备：

1. Ubuntu 24.04 WSL2；
2. `bubblewrap`、`socat`、Python、Git、curl、jq、rsync 和 xz；
3. Claude Science；
4. 原生 Linux Claude Code；
5. Linux Codex CLI；
6. 固定 Node.js LTS 和 Chrome DevTools MCP；
7. 固定提交的 ChatGPT/Codex connector；
8. FinalKit gateway、切换器、Science 隔离 profile；
9. 不读真实凭据、不访问上游的 connector、Science control、model-route、runtime-update rollback 四组 contract，以及 `doctor` 和不调用外部模型的 `smoke`。

### 3.3 指定 Linux 用户

```powershell
.\windows\FinalKit.ps1 -Action build -LinuxUser alice
```

不同 Linux 用户拥有独立 API key、Science 数据、Claude/Codex 登录、日志和运行锁。

### 3.4 自定义发行版位置

普通用户不需要自定义。确需放到 D 盘时，发行版名称和位置必须一起给出：

```powershell
.\windows\FinalKit.ps1 -Action build `
  -Distro Research-Ubuntu-24.04 `
  -DistroLocation D:\WSL\Research-Ubuntu-24.04 `
  -LinuxUser alice
```

FinalKit 不依赖 E 盘。不要把新环境放到计划撤除的磁盘。

### 3.5 中断恢复

如果下载失败、网络中断或窗口意外关闭，直接重复：

```powershell
.\windows\FinalKit.ps1 -Action build
```

Build 是幂等修复入口，会复用已经建立的发行版、用户和完整组件。此时不要先 Clear。

若普通 Store 下载路径失败，FinalKit 会自动用微软支持的 `--web-download` 在线源重试。若两条路径都失败，终端会保留已去除 ANSI 控制序列的完整 Windows 诊断；不要仅凭 `exit 1` 删除 WSL。

### 3.6 Build 成功判据

末尾应出现：

```text
BUILD_OK distro=Ubuntu-24.04 linux_user=<用户>
CONNECTOR_CONTRACT_OK catalog=3 routes=sol,terra,luna effort=max network=disabled
SMOKE_OK
```

再独立运行：

```powershell
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action smoke
```

`doctor` 中 provider 显示 `not configured` 不是安装失败，只表示尚未保存相应认证。

## 4. 配置 provider

至少配置一种后端。可以同时配置四种，互不覆盖。

> 所有 API key 必须通过隐藏提示输入。不要把 key 写入 PowerShell 命令、`.cmd`、README、项目文件、聊天、截图或 `HANDOFF.md`。

### 三类 Update：以后换模型不再 Build

`Build` 只用于首次建立 Ubuntu/依赖，或者确实损坏后的完整修复。日常升级已经拆开：

| 菜单 | 命令 | 改什么 | 不改什么 |
|---|---|---|---|
| `16 Update FinalKit runtime` | `-Action update-runtime` | 本包的 manager、gateway、connector patch 与契约测试 | 不重建 WSL，不下载官方客户端，不改 API key/OAuth/模型选择 |
| `17 Update provider models` | `-Action update-models` | 先只读列出 DeepSeek/Kimi/GLM 当前账号可调用模型，再编号/手工选择 main/fast；或手工改 Codex 三档 | 不做生成请求、不改 endpoint 与认证；先 dry-run 预览，确认后原子写入 |
| `18 Update official tools` | `-Action update-tools` | 官方 Claude Science、Claude Code、Codex CLI，以及本包固定的 Node/MCP | 不改 FinalKit 模型路由；需要网络并再次确认 |

查看当前配置：

```powershell
.\windows\FinalKit.ps1 -Action models
```

普通用户改模型时直接选择菜单 `17`：选择 DeepSeek/Kimi/GLM 后默认只读调用该厂商固定的官方模型目录，显示当前账号实际可调用的 ID；输入编号或完整 ID，检查预览，再确认。目录请求不会生成文本或消耗推理 tokens，也不会写配置。Codex 没有使用厂商 API key 的同类目录，因此继续手工填写三档 ID。对应 WSL CLI 例如：

```bash
# 只读目录：不回显 key、不生成文本、不写配置
fkctl discover-models deepseek --json

# 只预览，不写文件
fkctl update-models deepseek --main deepseek-v5-pro --fast deepseek-v5-flash --dry-run --json

# 写入持久配置；若 DeepSeek 正在运行，同时安全重启这一条 runtime
fkctl update-models deepseek --main deepseek-v5-pro --fast deepseek-v5-flash --restart

# Codex 三档可分别换代，推理强度独立配置
fkctl update-models codex \
  --opus gpt-6-sol --sonnet gpt-6-terra --haiku gpt-6-luna \
  --effort max --restart
```

持久配置位于 `~/.local/share/science-codex-finalkit/config/model-routes.json`，权限为 `0600`。新包只为后来新增的内置 provider 补默认项，不覆盖用户已经选定的模型；未知但格式正确的厂商条目也会保留。修改当前未运行的厂商不会打断正在运行的其他厂商。`discover-models` 证明 ID 对该账号可见，但不证明每种协议/套餐都接受真实推理；需要验收时再显式运行会产生最小真实请求的 `test-<provider>`。

厂商可能在不改变 API ID 的情况下升级后台权重。例如 2026-08-13 的 DeepSeek 文档说明 `deepseek-v4-pro` 已指向 DeepSeek-V4-Pro-0813，调用方法和 ID 不变；这种情况无需修改 FinalKit 路由。`discover-models` 解决的是“有哪些可调用 ID”，不是自动抓取新闻或猜测某个 alias 背后的权重日期。

无人值守工具更新可显式使用 `-Action update-tools -Force` 跳过第二次确认；`-Force` 不会跳过下载失败、官方二进制存在性、auth 字节未变或 `doctor` 检查。

### 4.1 DeepSeek

双击：

```text
windows\01-Configure-DeepSeek.cmd
```

或运行：

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek
```

看到下面提示后粘贴 key 并回车：

```text
DeepSeek API key (hidden):
```

输入期间不显示字符或星号，这是正常行为。

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

### 4.4 ChatGPT/Codex account backend

```powershell
.\windows\FinalKit.ps1 -Action configure-codex
```

快捷入口：

```text
windows\02-Configure-ChatGPT-Codex.cmd
```

命令在 Claude Science 的临时 staging HOME 中调用官方 Linux Codex CLI 的默认 `codex login`，显式固定 `cli_auth_credentials_store="file"`，并保留 WSL interop/display 环境以打开 Windows 浏览器 OAuth。只有 staging 缓存结构、`0600` 权限和 `codex login status` 全部通过后，才原子替换隔离 `~/.science-finalkit/.codex/auth.json`；登录取消、403 或验证失败不会破坏原缓存。connector 直接读写同一份官方缓存，不再导出第二份 `codex-auth.json`。

FinalKit 只为这次 Codex 子进程选择网络，不写 `.wslconfig`，也不增删 Windows/WSL 全局代理变量。默认浏览器 OAuth 需要浏览器能把回调送回 WSL CLI 的 `localhost:1455`。若该回调在特定机器上不可用，并且已在 ChatGPT 个人安全设置或工作区权限中启用 device code，可显式运行：

```powershell
.\windows\FinalKit.ps1 -Action configure-codex-device
```

device code 是官方标记的 beta 备用流，不再作为菜单 6 的默认路径。若它返回 403，优先使用默认 `configure-codex`；403 也可能表示账号/工作区未启用 device login，而不是 FinalKit 或 WSL 安装损坏。

Codex 模式采用三档实际路由：

| Science 请求 ID 家族（不是菜单标题） | 实际 ChatGPT Codex 模型 | 默认推理强度 | 适用倾向 |
|---|---|---|---|
| Opus（包括带版本/日期后缀的 ID） | `gpt-5.6-sol` | `max` | 最复杂、质量优先的研究和编码 |
| Sonnet（包括带版本/日期后缀的 ID） | `gpt-5.6-terra` | `max` | 智能与成本平衡 |
| Haiku（包括带版本/日期后缀的 ID） | `gpt-5.6-luna` | `max` | 高频、轻量任务 |

Claude Science 仍使用 Opus / Sonnet / Haiku 家族的兼容 ID 发请求，但 FinalKit 的 `/v1/models` 会把菜单标题透明显示为真实的 `ChatGPT Codex | gpt-5.6-* | max` 路由；它没有修改 Claude Science 前端。标题下的 `Best for scientific rigor`、`Most efficient for simple tasks`、`Fastest, lightweight` 是 Science 按兼容 ID 家族附加的固定说明，不是实际 upstream 名。启动后仍应以 `EFFECTIVE_ROUTE`、`status` / `doctor` 和 gateway 日志中的实际 `model` 与 `effort` 作最终核验。

### 4.5 查看配置状态

```powershell
.\windows\FinalKit.ps1 -Action status
```

它只显示 `configured` / `not configured`，不会打印凭证。

## 5. 真实后端测试

配置后先发送一次最小真实请求，再承担正式任务。真实测试可能产生极少量费用。

### 5.1 DeepSeek

```powershell
.\windows\FinalKit.ps1 -Action test-deepseek
```

预期：

```text
BACKEND_OK mode=deepseek
```

### 5.2 Kimi

```powershell
.\windows\FinalKit.ps1 -Action test-kimi
```

预期：

```text
BACKEND_OK mode=kimi
```

### 5.3 GLM

```powershell
.\windows\FinalKit.ps1 -Action test-glm
```

预期：

```text
BACKEND_OK mode=glm
```

### 5.4 ChatGPT/Codex

```powershell
.\windows\FinalKit.ps1 -Action test-codex
```

预期：

```text
BACKEND_OK mode=codex
```

`test-codex` 发送一次 Sol 最小真实请求来验证 OAuth、协议转换与当前 Codex account backend。Sol/Terra/Luna 家族映射和 `max` payload 由发布测试在本地 connector 单元层逐项验证；若需要证明当前账号也实时开放 Terra/Luna，可在取得相应用量授权后再分别进行真实请求，不把额外两次消费混入普通用户默认测试。

需要一次性验收当前 ChatGPT 账号的全部三档时，显式运行：

```powershell
.\windows\FinalKit.ps1 -Action test-codex-tiers
```

该命令依次发出三个极小真实请求，全部通过才输出：

```text
CODEX_TIERS_OK actual configured routes verified
```

这条命令会产生三次账号用量，不放进普通 `doctor`、Build 或默认 `test-codex`。若某档返回 model/permission 错误，FinalKit原样失败，不会偷偷改成 Sol；这通常表示当前 ChatGPT 账号型 Codex backend 尚未开放该档，而不是 WSL 安装损坏。公开 OpenAI API 的模型目录能证明模型 ID 和 `reasoning.effort=max` 的协议有效性，但不能替代对当前 ChatGPT 账号权限的真实验收。

测试失败时先执行 [10. 故障处理](#10-故障处理)，不要重新 Build 或 Clear。

## 6. 启动与切换

### 6.1 菜单入口

双击：

```text
SwitchModel.cmd
```

菜单项职责并不相同：

| 菜单 | 作用 |
|---|---|
| 7–10 | 切换到指定后端、启动 gateway 与 Claude Science，并在默认浏览器打开 Science |
| 11 | 将已经运行的当前 Claude Science URL 打开到可被 MCP 控制的隔离 Chrome；不会切换模型 |
| 12 | 只读显示运行状态 |
| 13 | 只读检查安装、权限、connector 和进程身份 |
| 14 | 停止 Claude Science 和 FinalKit gateway，不关闭自动化 Chrome |
| 15 | 只停止 FinalKit 隔离自动化 Chrome，不停止 Science |

因此 `10` 与 `11–13` 不能互相替代：要用 ChatGPT/Codex 后端启动 Science，选择 `10`；只有在还要让 Science/Claude 通过 Chrome DevTools MCP 操作网页时才再选择 `11`。

### 6.2 直接启动 Claude Science

```powershell
# DeepSeek
.\windows\FinalKit.ps1 -Action deepseek

# Kimi
.\windows\FinalKit.ps1 -Action kimi

# GLM
.\windows\FinalKit.ps1 -Action glm

# ChatGPT/Codex account backend
.\windows\FinalKit.ps1 -Action codex
```

成功后会打印 Claude Science 的一次性 URL，并在默认 Windows 浏览器中打开。

Codex 启动的关键输出应类似：

```text
ACTIVE_MODE=codex
EFFECTIVE_ROUTE=Science Opus -> ChatGPT Codex gpt-5.6-sol (effort=max); Sonnet -> gpt-5.6-terra (effort=max); Haiku -> gpt-5.6-luna (effort=max)
```

模型 ID 仍是 Claude 兼容家族是预期行为；新版 gateway 会把下拉框标题显示为对应的真实 Codex 模型和 effort。若仍看到 `Claude Opus 4.8`，依次检查：菜单 `12 Status` 是否仍报告旧 distribution metadata；运行菜单 `16 Update FinalKit runtime` 后是否重新执行了 `10 Start Codex`；浏览器是否已重新打开或硬刷新；菜单 `13 Doctor` 中 connector security patch 是否为 `OK`。如果 runtime 尚未安装，才先用菜单 `2`。如果这些均正常，查看 Science bootstrap/gateway 日志，排除 `/v1/models` 拉取失败后使用离线默认列表的情况。发行包版本只用于识别 ZIP 和诊断，不参与 Start/Stop/Status 等命令放行；Windows 入口始终调用当前 WSL 用户已经部署的 `fkctl`。只有在调用该 runtime 尚未提供的新命令时，才会提示补齐能力；普通启动不会因为版本文本不同而被封锁。

### 6.3 切换 provider

直接运行另一个启动命令即可。例如：

```powershell
.\windows\FinalKit.ps1 -Action deepseek
.\windows\FinalKit.ps1 -Action kimi
```

FinalKit 会停止当前 Science、验证并停止旧 gateway、启动新 gateway、重启 Science、核对 endpoint，最后才提交新模式。不要手工编辑 gateway 配置。

### 6.4 状态与停止

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action stop
```

健康运行应显示：

```text
Gateway:            healthy
Claude Science:     running
Runtime identity:   matched
```

`stop` 只停止身份匹配的 FinalKit 进程，不停止整个 WSL。

## 7. 在 WSL 使用原生 Claude Code

进入 WSL：

```powershell
wsl -d Ubuntu-24.04
```

核对原生安装：

```bash
command -v claude
claude --version
```

路径应为：

```text
/home/<user>/.local/bin/claude
```

不应指向 `/mnt/c/Users/...` 下的 Windows npm launcher。

运行：

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

从 Windows 直接调用：

```powershell
.\windows\FinalKit.ps1 -Action claude -RemainingArgs deepseek
```

Claude Code 只取得本地 gateway endpoint，不取得真实 provider key。

## 8. Windows 隔离浏览器桥

只有需要真实页面、登录态、点击、表单、截图或下载时才启用。普通公开资料检索优先使用 Claude Science 自带的 WebSearch/WebFetch。

### 8.1 启动和验证

```powershell
.\windows\FinalKit.ps1 -Action browser-start
.\windows\FinalKit.ps1 -Action browser-science
.\windows\FinalKit.ps1 -Action browser-status
```

菜单 `11` 会进一步把当前已经运行的 Claude Science URL 直接打开到该隔离 Chrome；单独的 `browser-start` 仍只启动可自动化浏览器，便于没有 Science 的网页任务。

成功判据：

```text
BROWSER_OK
Status: reachable
WSL reachability: OK
```

FinalKit 使用独立 profile：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\ChromeProfile
```

只在这个 Chrome 中登录明确允许自动化访问的网站。MCP 能检查或控制这个 profile 中的所有页面。

### 8.2 在 Claude Science 添加 MCP

先打印精确配置：

```powershell
.\windows\FinalKit.ps1 -Action browser-mcp-info
```

在 Claude Science 中进入：

```text
Customize -> Connectors -> Custom MCP -> stdio
```

不要猜 Linux 用户名或手工改路径。运行 `browser-mcp-info` 后，把它打印的 `Command` 与 `Arguments` 原样复制到 Claude Science。输出形态如下，其中 `<Linux 用户>` 会被换成当前 Build 实际使用的用户：

```text
Command:
/home/<Linux 用户>/.local/bin/chrome-devtools-mcp-finalkit

Arguments:
--browser-url=http://127.0.0.1:9223 --slim
```

先做无登录的最小测试，例如“打开公开网页并返回标题，不提交表单”。

### 8.3 在 Claude Code 添加 MCP

在 WSL 中执行：

```bash
claude mcp add chrome-devtools -- \
  ~/.local/bin/chrome-devtools-mcp-finalkit \
  --browser-url=http://127.0.0.1:9223 \
  --slim

claude mcp list
```

FinalKit 不自动修改 Claude Code MCP 配置，启用必须由用户显式决定。

### 8.4 停止浏览器桥

```powershell
.\windows\FinalKit.ps1 -Action browser-stop
```

它只终止使用精确隔离 profile 的 Chrome 进程，并保留该 profile 数据。

## 9. Claude Science 与 Windows Codex 协作

### 9.1 创建唯一 handoff

```powershell
.\windows\FinalKit.ps1 `
  -Action init-project `
  -Project D:\path\to\project
```

生成：

```text
<project>\.science-codex\HANDOFF.md
```

已有 handoff 时不会覆盖。

`init-project` 还会打印 Claude Science 审阅 skill 源文件和便携 ZIP 的精确路径，但不会替你修改 Claude Science 的个人 skill 状态。

### 9.2 在本地 Claude Science 发布独立审阅 skill

只需在首次使用或 FinalKit 更新该 skill 后操作一次。你继续控制 Windows 浏览器，并在 Claude Science 当前对话中发送：

```text
请使用 customize skill 和 host.skills API 发布我提供的 reviewer skill。
读取附件 SKILL.md；如果当前沙箱可见该路径，也可直接读取：
/mnt/d/Tools/ScienceCodexFinalKit/claude-science-skills/reviewing-codex-science/SKILL.md

先确认 frontmatter name 是 reviewing-codex-science，并检查 host.skills.list()。
首次安装时用 host.skills.edit 写入 SKILL.md，再用 host.skills.publish 发布；
若同名 skill 已存在，先读回现有内容并向我报告差异，不要未经确认覆盖。
完成后用 host.skills.list() 和 host.skills.read("reviewing-codex-science") 回读，
报告 origin、description、发布状态和未完成步骤。不要修改其他 skill 或 agent profile。
```

把 FinalKit 根目录下这个文件作为附件即可：

```text
claude-science-skills\reviewing-codex-science\SKILL.md
```

只有 `host.skills.list()` / `host.skills.read()` 显示 live/personal skill 后，才视为已启用。受限 profile 还需要 Claude Science 明确把它 attach 到对应 profile；默认 unrestricted 主 agent 会看到新发布的 live skill。

同目录的 `reviewing-codex-science.zip` 是便携备份；仅当某个 Claude surface 确实提供标准自定义 Skills 上传 UI 时才使用。Claude.ai 的标准上传说明见[Use skills in Claude](https://support.claude.com/en/articles/12512180-use-skills-in-claude)，但不能把该界面假定为本地 Claude Science 的安装入口。FinalKit 不直接编辑 Claude Science 的数据库、会话、cookie 或账号设置。

### 9.3 由 Windows Codex 准备待审对象

至少填写：

- Windows/WSL 同一注册项目叶子的精确路径；
- 研究问题、estimand、独立统计单位和 claim boundary；
- 权威输入、样本/cohort/reference 身份与主要 contrast；
- 审阅冻结点、当前唯一 writer 和活动计算状态；
- Claude Science 当轮 reviewer provider/model 与独立性边界；
- Codex 实际修改的永久 owner；
- canonical result、exact Source Data、图表、报告和真实 workflow log；
- 实际执行的测试、图形读回、已知限制和希望 reviewer 独立回答的问题。

禁止写 API key、token、cookie、密码、私钥或敏感数据。

### 9.4 让 Claude Science 独立审阅

在 Claude Science 对话中发送：

```text
请使用 reviewing-codex-science，读取
/mnt/d/path/to/project/.science-codex/HANDOFF.md，
从权威输入和真实项目证据独立审阅 Windows Codex 的本轮工作。
所有科学项目文件只读；如需写回，只替换 HANDOFF.md 中
CLAUDE_SCIENCE_REVIEW_START 与 CLAUDE_SCIENCE_REVIEW_END 之间的内容。
```

Claude Science 应返回 `critical / major / minor / uncertain` 发现、精确位置、直接证据、影响和最小可证伪修复/验证路线。没有实质问题时，也必须列出实际覆盖和未覆盖边界。它不能通过审阅自动授权删除、发布、登录、上传或外部发送。

Claude Science 是工作台而不是固定模型。若它使用 Codex backend，本轮是 `separate_context_only`，不能称为跨模型独立审阅；切到不同 provider/model family 后才标记 `different_model_provider`。看不到实际身份时标记 `unknown`，不要猜。

### 9.5 Windows Codex 核验和修复

```text
Windows Codex 实施并直接验证
-> Claude Science 独立只读审阅
-> Windows Codex 将每个 finding 判为 accepted / rejected / unresolved
-> 修复最早受影响 owner 并直接复验
-> 仅在方法、结果或 claim 边界实质变化时再审
```

Claude Science 的 finding 是审阅证据，不是自动裁决。不要因为两个 Agent 一致就把结论当真，也不要因为 finding 与实现叙述冲突就直接驳回。

### 9.6 反向：Windows Codex 只读复核 Claude Science 工作

```powershell
.\windows\FinalKit.ps1 `
  -Action windows-review `
  -Project D:\path\to\project
```

该入口使用 ephemeral、read-only sandbox。只有 Claude Science/Claude Code 确实拥有该轮执行时才使用反向节奏：

```text
Claude Science/Claude Code 执行
-> 更新唯一 HANDOFF.md
-> Windows Codex 只读复核
-> 用户指定下一轮唯一 writer
-> 再进行修改
```

不要让两个 Agent 同时无边界写同一项目。

## 10. 故障处理

按从低风险到高风险的顺序处理。

### 10.1 先查看状态

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
```

### 10.2 查看日志

先进入当前发行版：

```powershell
wsl -d Ubuntu-24.04
```

然后执行：

```bash
fkctl status
fkctl doctor
fkctl logs gateway
fkctl logs science
```

### 10.3 常见情况

| 症状 | 优先处理 |
|---|---|
| `auth: not configured` | 重新运行对应 `configure-*` |
| API 返回 401/403 | 检查 key 是否有效、权限/余额/地区策略，必要时轮换 key |
| API 返回 model not found | 查看 provider 当前模型可用性；不要修改上游 URL |
| gateway stopped | 运行对应启动命令 |
| gateway identity mismatch | 先 `-Action stop`，再启动；不要手工 kill 未知进程 |
| `FINALKIT_SCIENCE_CONTROL_UNAVAILABLE`、Status 显示 `control unavailable`，或 owner state 为 `D` | 3.1.2 已把 Science cwd/PWD/HOME 固定到 WSL ext4、用纯 Linux PATH 启动，并对一次瞬时 socket 失败做短暂重试；旧 daemon 或持续 Linux `D` 状态仍须先用 `wsl.exe --list --verbose` 确认本包选中的发行版并关闭其中其他任务，再从 Windows执行 `wsl.exe --terminate Ubuntu-24.04`（名称按实际替换），运行菜单 `16 Update FinalKit runtime` 和对应 provider 启动。terminate 只重启该发行版的 WSL 内核实例，不注销发行版、不删除文件、不清 API/Codex 认证；若 runtime 文件本身缺失才用菜单 `2`。manager 不强杀失联/不可中断 daemon；不要选 Clear |
| Start 后很快 sign out，日志出现 `invalid_grant` 或 `treating as logged-out` | 旧 FinalKit/HGSX 0.1.25 风格虚拟 OAuth 与 Science 0.1.27 不兼容。运行菜单 `16 Update FinalKit runtime`；3.1.2 会在停机边界识别并原子移除旧 FinalKit Fernet/v2 虚拟身份，改用 API-key-only BYOK。未知或真实 Claude Science 凭据不会被覆盖。然后重新 Start；不需要重新配置 DeepSeek/Kimi/GLM 或 Codex auth |
| 日志只有 `growthbook: not signed in — flags stay at defaults` | 这是 API-key-only BYOK 没有 Claude.ai 个性化/实验开关账号的正常提示，不等于 Science 工作台退出，也不影响本地 gateway 推理。始终从菜单 Start 打开的最新 nonce URL 进入；旧标签页可关闭 |
| Science 页面没有打开 | 运行 `status`，再运行 `fkctl url` 获取新一次性 URL |
| 端口被占用 | 查明占用者；FinalKit 不会强占第三方进程 |
| 浏览器 `Status: stopped` | 重新运行 `browser-start` |
| WSL 无法访问浏览器 127.0.0.1 | 检查 WSL mirrored networking；FinalKit 不自动修改 `.wslconfig` |
| MCP 找不到 `node` | 使用 `~/.local/bin/chrome-devtools-mcp-finalkit`，不要直接运行 npm shim |
| Build 下载中断 | 重复 Build，不要 Clear |
| apt 的 Ubuntu security 源经 `127.0.0.1` 代理返回 502 | 3.0.1 会仅对失败的 apt 命令自动直连重试；不会修改全局代理 |
| Build 在 `wsl --list/--install` 后出现乱码 | 使用当前发行包；自 3.0.3 起按原始字节识别 UTF-16LE、UTF-8 和混合输出并去除 ANSI 控制序列 |
| 首次 Build 显示 `403`、`请求的操作需要提升` | 允许当前发行包的 UAC 系统准备；若提示重启，重启后以同一个普通 Windows 用户再运行 Build，不要 Clear |
| Store 路径无法下载 Ubuntu | 当前发行包自动重试 `--web-download`；两条路径均失败时按保留的真实网络/错误码诊断处理 |
| 旧版 `configure-codex` 显示 device code 403 | 使用当前发行包的默认浏览器 OAuth；只有已启用 device login 时才运行 `configure-codex-device` |
| Windows Codex 已登录，但 FinalKit 显示 `not configured` | 两者是不同 OS/用户的缓存；在目标 Linux 用户下运行 `configure-codex -LinuxUser <name>`，不要手工复制或打印 auth JSON |
| Codex 请求返回意外 `401` | 当前 connector 会在尚未输出回答时用共享缓存强制刷新并且只重试一次；若 refresh chain 没有变化或再次 `401`，运行 `configure-codex` 完成新的浏览器登录 |
| Codex 登录成功但 backend 超时 | 保留可访问 `chatgpt.com` 的现有代理/VPN，运行 `doctor` 和 `test-codex`；不要把登录直连策略全局化 |

### 10.4 修复安装

只有 `doctor` 表明组件缺失或 installer 未完成时才重复：

```powershell
.\windows\FinalKit.ps1 -Action build
```

修复 Build 不应覆盖已有 secret 和用户登录。

### 10.5 最后才考虑 Clear

只有以下条件同时满足时才重新 Clear：

- WSL 发行版确实不可修复或明确希望完全重置；
- 已停止活跃任务；
- 唯一项目、结果和凭证已有外部副本；
- 已确认目标名称和 BasePath；
- 接受从 tar 恢复或永久丢弃该发行版。

## 11. 多用户操作

### 11.1 同一 Windows 用户、同一 WSL、多个 Linux 用户

为新 Linux 用户运行：

```powershell
.\windows\FinalKit.ps1 -Action build -LinuxUser bob
```

随后所有命令都显式带同一用户：

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek -LinuxUser bob
.\windows\FinalKit.ps1 -Action deepseek -LinuxUser bob
.\windows\FinalKit.ps1 -Action status -LinuxUser bob
```

每个 Linux 用户分别配置 API key 和隔离的 ChatGPT/Codex 浏览器登录。

### 11.2 多个 Windows 用户

每个 Windows 用户拥有自己的：

- WSL 注册；
- `%LOCALAPPDATA%` 浏览器 profile；
- Windows Codex 登录；
- FinalKit 备份目录。

不要把一个 Windows 用户的 WSL 注册目录或 Chrome profile 复制给另一个用户。

## 12. 凭证轮换

当 key 被粘贴到聊天、命令行、截图或非私密文档时，应在供应商控制台撤销并生成新 key，然后重新运行：

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek
.\windows\FinalKit.ps1 -Action configure-kimi
.\windows\FinalKit.ps1 -Action configure-glm
```

新值会原子替换当前 Linux 用户的旧 key。替换后执行对应真实测试。

ChatGPT/Codex 重新授权：

```powershell
.\windows\FinalKit.ps1 -Action configure-codex
```

该命令每次都会发起新的官方浏览器登录，因此既用于首次配置，也用于恢复服务端已撤销或失效、但本地文件仍存在的登录。新登录先在 staging HOME 验证，成功才替换，失败保留旧缓存。不要手工复制、打印或编辑 auth JSON。官方 CLI 与 connector 共用一个文件缓存，并会把轮换后的 refresh chain 原子写回同一文件，所以普通 Build/repair 不应让授权失效。

## 13. 恢复与回滚

### 13.1 从 WSL tar 恢复

先选择一个不存在的新名称和空目录：

```powershell
$restoreDir = "D:\WSL\Recovered-Ubuntu-24.04"
New-Item -ItemType Directory -Path $restoreDir

wsl --import `
  Recovered-Ubuntu-24.04 `
  $restoreDir `
  "C:\Users\<user>\AppData\Local\ScienceCodexFinalKit\Backups\<backup>.tar" `
  --version 2
```

验证：

```powershell
wsl --list --verbose
wsl -d Recovered-Ubuntu-24.04 -u root -- cat /etc/os-release
```

不要覆盖当前运行中的发行版目录。

### 13.2 软件包回滚

保留旧版 ZIP 时，可解压到新的并列目录进行只读比较，不要直接在 ZIP 内运行。日常只保留一个明确的解压运行目录，避免从微信临时副本或多个旧目录启动；运行资格由已部署命令与健康检查决定，不由发行号文本决定。

## 14. 日常最短命令

完成 Build、配置和真实测试后，日常只需：

```powershell
cd C:\Tools\ScienceCodexFinalKit   # 或你自己的稳定解压目录

# 启动一种后端
.\windows\FinalKit.ps1 -Action deepseek

# 查看状态
.\windows\FinalKit.ps1 -Action status

# 把当前运行中的 Science 打开到可自动化的隔离 Chrome
.\windows\FinalKit.ps1 -Action browser-science

# 网页任务结束
.\windows\FinalKit.ps1 -Action browser-stop

# 当天任务结束
.\windows\FinalKit.ps1 -Action stop
```

## 15. 安装后的最短验收

每位用户在自己的普通 Windows 账号和 Linux home 内完成一次：

1. 运行 `status` 和 `doctor`，确认发行版、客户端、connector、权限与运行身份；
2. 只配置自己拥有的一种 provider：DeepSeek、Kimi、GLM 或 ChatGPT Codex；
3. 运行对应的 `test-deepseek`、`test-kimi`、`test-glm` 或 `test-codex`；
4. 看到 `BACKEND_OK mode=<provider>` 后，再运行同名启动动作；
5. 最后再次运行 `status`，应看到 gateway healthy、Claude Science running、runtime identity matched；
6. 只有确实需要页面自动化时才运行 `browser-science` 和 `browser-mcp-info`。

API key 只能在配置命令的隐藏提示中输入，不要发到聊天、截图、`.cmd`、README 或项目文件。已经公开过的 key 应在供应商控制台轮换。
