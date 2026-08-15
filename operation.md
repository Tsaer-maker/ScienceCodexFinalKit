# Science SwitchModel / FinalKit 3.2.3 操作手册

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
5. 先运行对应的真实后端测试，再用菜单 `7–10` 启动 Claude Science；需要 CLI 时另用菜单 `19–22` 进入原生 Claude Code。

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
9. 不读真实凭据、不访问模型上游的 connector、direct-gateway、Science control、Science identity、model-route、Windows-entry、runtime-update rollback 七组 WSL contract，以及 `doctor` 和不调用外部模型的 `smoke`。

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
CONNECTOR_CONTRACT_OK catalog=3 routes=sol,terra,luna reasoning=max,high,low retry502=once network=disabled
DIRECT_GATEWAY_CONTRACT_OK
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
| `17 Update provider models` | `-Action update-models` | DeepSeek/Kimi/GLM 先只读列出账号模型；Codex 读取本地 CLI 缓存；四个 provider 都逐角色编辑三组 Model/Reasoning | 不做生成请求、不改 endpoint 与认证；先 dry-run 预览，确认后原子写入 |
| `18 Update official tools` | `-Action update-tools` | 官方 Claude Science、Claude Code、Codex CLI，以及本包固定的 Node/MCP | 不改 FinalKit 模型路由；需要网络并再次确认 |

查看当前配置：

```powershell
.\windows\FinalKit.ps1 -Action models
```

普通用户改模型时直接选择菜单 `17`：选择 DeepSeek/Kimi/GLM 后默认只读调用该厂商固定的官方模型目录，显示当前账号可见的 ID；Codex 从当前 Linux Codex home 的 `models_cache.json` 显示模型及逐模型 reasoning 能力。随后四个 provider 都分别输入 Opus、Sonnet、Haiku 的 Model 与 Reasoning，检查预览，再确认。目录请求不会生成文本或消耗推理 tokens，也不会写配置。对应 WSL CLI 例如：

```bash
# 只读目录：不回显 key、不生成文本、不写配置
fkctl discover-models deepseek --json

# 只预览，不写文件；三个 Claude 角色独立
fkctl update-models deepseek \
  --opus deepseek-v5-pro --reasoning-opus max \
  --sonnet deepseek-v5-chat --reasoning-sonnet high \
  --haiku deepseek-v5-flash --reasoning-haiku none \
  --dry-run --json

# 写入持久配置；若 DeepSeek 正在运行，同时安全重启这一条 runtime
fkctl update-models deepseek \
  --opus deepseek-v5-pro --reasoning-opus max \
  --sonnet deepseek-v5-chat --reasoning-sonnet high \
  --haiku deepseek-v5-flash --reasoning-haiku none \
  --restart

# Codex 三档可分别换代，推理强度独立配置
fkctl update-models codex \
  --opus gpt-6-sol --reasoning-opus max \
  --sonnet gpt-6-terra --reasoning-sonnet high \
  --haiku gpt-6-luna --reasoning-haiku low \
  --restart
```

持久配置位于 `~/.local/share/science-codex-finalkit/config/model-routes.json`，权限为 `0600`，每个 profile 只保留 `model_opus/reasoning_opus`、`model_sonnet/reasoning_sonnet`、`model_haiku/reasoning_haiku`。新包只为后来新增的内置 provider 补默认项，不覆盖用户已经选定的模型；旧 `main/fast/shared effort` 和早期长字段会迁移一次。修改当前未运行的厂商不会打断正在运行的其他厂商。`discover-models` 证明 ID 对该账号可见，但不证明每种模型/套餐都接受所有 Reasoning；需要验收时再显式运行会产生最小真实请求的 `test-<provider>`。

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

保存 key 后，配置器只显示三组短提示：`Opus Model/Reasoning`、`Sonnet Model/Reasoning`、`Haiku Model/Reasoning`。依据 DeepSeek 官方 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode) 与 [Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api) 参数，当前 provider 级 Reasoning 选项为 `auto/none/high/max`；`auto` 保留上游默认，`none` 显式关闭 thinking，`high/max` 发送 `thinking.type=enabled` 与 `output_config.effort`。账号模型目录不声明逐模型强度，因此配置器能校验模型 ID 是否可见，但真实模型是否接受所选强度仍由 `test-deepseek` 验收。

### 4.2 Kimi

```powershell
.\windows\FinalKit.ps1 -Action configure-kimi
```

快捷入口：

```text
windows\03-Configure-Kimi.cmd
```

保存 key 后同样分别配置三组 Model/Reasoning。根据官方 [Kimi K3](https://github.com/MoonshotAI/Kimi-K3) 与 [Kimi Code 模型元数据](https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/configuration/config-files.md)，当前 provider 级选项为 `auto/none/low/high/max`；非自动档使用 `thinking.type` 与顶层 `reasoning_effort`。Kimi K3 属于 always-thinking 路线时可能拒绝 `none`，应改用其支持的强度并以 `test-kimi` 为准。

### 4.3 GLM

```powershell
.\windows\FinalKit.ps1 -Action configure-glm
```

快捷入口：

```text
windows\04-Configure-GLM.cmd
```

GLM 也逐角色配置 Model/Reasoning。依据智谱官方 [思考模式](https://docs.bigmodel.cn/cn/guide/capabilities/thinking) 与 [Claude 接入](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)，当前 provider 级选项为 `auto/none/high/max`；非自动档使用 `thinking.type` 与顶层 `reasoning_effort`。模型版本或账号套餐可能进一步收窄能力，以 `test-glm` 的真实响应为准。

### 4.4 ChatGPT/Codex account backend

```powershell
.\windows\FinalKit.ps1 -Action configure-codex
```

快捷入口：

```text
windows\02-Configure-ChatGPT-Codex.cmd
```

命令在独立的 FinalKit client staging HOME 中调用官方 Linux Codex CLI 的默认 `codex login`，显式固定 `cli_auth_credentials_store="file"`，并保留 WSL interop/display 环境以打开 Windows 浏览器 OAuth。只有 staging 缓存结构、`0600` 权限和 `codex login status` 全部通过后，才原子替换 `~/.finalkit-client/.codex/auth.json`；登录取消、403 或验证失败不会破坏原缓存。登录验证后，配置器从同一个 Linux Codex home 读取 `models_cache.json`，显示每个模型的 `supported_reasoning_levels` 和说明，再分别询问 Opus、Sonnet、Haiku 的 Model/Reasoning；已知模型会拒绝缓存未声明的强度，`ultra` 也只在具体模型明确声明时可选。connector 直接读写同一份官方缓存，不再导出第二份 `codex-auth.json`，也不会把 Codex OAuth 写进 Science data HOME。

FinalKit 只为这次 Codex 子进程选择网络，不写 `.wslconfig`，也不增删 Windows/WSL 全局代理变量。默认浏览器 OAuth 需要浏览器能把回调送回 WSL CLI 的 `localhost:1455`。若该回调在特定机器上不可用，并且已在 ChatGPT 个人安全设置或工作区权限中启用 device code，可显式运行：

```powershell
.\windows\FinalKit.ps1 -Action configure-codex-device
```

device code 是官方标记的 beta 备用流，不再作为菜单 6 的默认路径。若它返回 403，优先使用默认 `configure-codex`；403 也可能表示账号/工作区未启用 device login，而不是 FinalKit 或 WSL 安装损坏。

#### 4.4.1 可选：把当前 Windows Codex 登录一次性迁入 WSL

如果 Windows 官方 Codex CLI 已显示 `Logged in using ChatGPT`，并且你希望 WSL 从同一账号起步，可以双击：

```text
windows\08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd
```

等价命令是：

```powershell
.\windows\FinalKit.ps1 -Action migrate-windows-codex-auth-to-wsl
```

这是菜单 `24` 的可选一次性动作，不是启动项。脚本显示 Windows 源文件与目标 distro/Linux 用户，要求输入 `MIGRATE`，先安全停止本包自己的 WSL Science/gateway，再把 Windows auth 字节只经子进程 stdin 交给 `fkctl import-codex-auth`。Linux 端只接受 `auth_mode=chatgpt` 且 access/refresh chain 完整、大小不超过 1 MiB 的 JSON；候选先在临时 HOME 中通过官方 `codex login status`，然后才以 `0600` 原子替换 WSL owner。最终验证失败会恢复此前 WSL 文件的精确字节与权限；迁移失败还会尝试用旧登录恢复 Codex Science。

成功后不会建立任何定时、启动或双向同步：Windows 继续拥有 `%CODEX_HOME%\auth.json`，WSL 继续拥有 `~/.finalkit-client/.codex/auth.json`。这能省掉本次 WSL 浏览器登录，但不会向服务端换取两套独立 OAuth 会话；两份文件起初仍是同一 token chain。若上游 refresh-token 轮换使另一份副本后来失效，只需在失效的一侧重新登录。WSL 始终可独立运行 `configure-codex` 或 `configure-codex-device`，Windows 也保留自己的 `codex login`。不要用 `Get-Content`、剪贴板或命令行参数手工搬运 token。

Codex 模式采用三档实际路由：

| Science 请求 ID 家族（不是菜单标题） | 实际 ChatGPT Codex 模型 | 默认推理强度 | 适用倾向 |
|---|---|---|---|
| Opus（包括带版本/日期后缀的 ID） | `gpt-5.6-sol` | `max` | 最复杂、质量优先的研究和编码 |
| Sonnet（包括带版本/日期后缀的 ID） | `gpt-5.6-terra` | `max` | 智能与成本平衡 |
| Haiku（包括带版本/日期后缀的 ID） | `gpt-5.6-luna` | `max` | 高频、轻量任务 |

Claude Science 仍使用 Opus / Sonnet / Haiku 家族的兼容 ID 发请求，但 FinalKit 的 `/v1/models` 会把菜单标题透明显示为真实的 `ChatGPT Codex | <model> | reasoning=<value>` 路由；它没有修改 Claude Science 前端。标题下的固定家族说明不是实际 upstream 名。启动后仍应以 `EFFECTIVE_ROUTE`、`status` / `doctor` 和 gateway 日志中的实际 `model` 与 `reasoning` 作最终核验。

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

`test-codex` 发送一次当前 Opus Model/Reasoning 的最小真实请求来验证 OAuth、协议转换与当前 Codex account backend。三档独立映射和各自 Reasoning payload 由离线 connector contract 逐项验证；若需要证明当前账号也实时开放 Sonnet/Haiku 路由，可在取得相应用量授权后再分别进行真实请求，不把额外两次消费混入普通用户默认测试。

需要一次性验收当前 ChatGPT 账号的全部三档时，显式运行：

```powershell
.\windows\FinalKit.ps1 -Action test-codex-tiers
```

该命令依次发出三个极小真实请求，全部通过才输出：

```text
CODEX_TIERS_OK actual configured routes verified
```

这条命令会产生三次账号用量，不放进普通 `doctor`、Build 或默认 `test-codex`。若某档返回 model/permission 错误，FinalKit 原样失败，不会偷偷改成 Opus 模型；这通常表示当前 ChatGPT 账号型 Codex backend 尚未开放该 Model/Reasoning 组合，而不是 WSL 安装损坏。本地 Codex 能力缓存用于配置校验，仍不能替代当前账号的真实请求验收。

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
| 7–10 | 切换到指定后端，启动 gateway 与 Claude Science，并在默认浏览器打开 Science |
| 11 | 将已经运行的当前 Claude Science URL 打开到可被 MCP 控制的隔离 Chrome；不会切换模型 |
| 12 | 只读显示运行状态 |
| 13 | 只读检查安装、权限、connector 和进程身份 |
| 14 | 停止 Claude Science 和 FinalKit gateway，不关闭自动化 Chrome |
| 15 | 只停止 FinalKit 隔离自动化 Chrome，不停止 Science |
| 19–22 | 使用同一套 DeepSeek/Kimi/GLM/Codex 配置进入原生 Claude Code；不启动 Science |

因此入口职责是稳定的：`7–10` 是 Claude Science 主路径，`11` 是当前 Science 的自动化浏览器桥，`19–22` 才是额外的原生 Claude Code 路径。四种后端共用同一份 provider/Codex 配置和模型路由；3.2.1 由隔离 WSL profile 内的 FinalKit 本地身份满足 Science 身份门禁，不需要 Claude.ai 账号，也不会把任一后端凭据复制进 Science。浏览器仍保留官方本机 nonce/cookie 会话门；它与 Claude.ai 账号登录不是一回事。

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

成功后会启动对应 gateway 与 Claude Science，先由 runtime 用自己的未共享 nonce 验证 `/api/me` 与真实工作台，再打印另一条一次性 Science URL并在默认 Windows 浏览器打开。浏览器若显示 `Sign in`，点击一次只是接受本机 daemon 会话；不会把你带到 Claude.ai OAuth，也不会读取 provider/Codex 凭据。

Codex 启动的关键输出应类似：

```text
ACTIVE_MODE=codex
EFFECTIVE_ROUTE=Opus: Model=gpt-5.6-sol, Reasoning=max; Sonnet: Model=gpt-5.6-terra, Reasoning=max; Haiku: Model=gpt-5.6-luna, Reasoning=max (ChatGPT Codex)
```

菜单 `7–10` 会先校验或原子创建 FinalKit 本地身份，再启动同一条 loopback gateway；`/v1/models` 把 Science 兼容 ID 的标题显示为真实 provider/model。若页面仍只有 Opus/Sonnet/Haiku fallback 并提示 `No credentials`，先运行菜单 `16` 更新 runtime，再用对应 `test-*`、`status` 和 gateway 日志核验身份与 route；不要通过登录 Claude 账号或复制 provider 凭据来绕开本地身份失败。发行包版本只用于识别 ZIP 和诊断，不参与 Start/Stop/Status 等命令放行；Windows 入口始终调用当前 WSL 用户已经部署的 `fkctl`，而 `7–10`/`science` 会要求 `science-isolated-local-identity` capability，防止新入口误调旧 runtime。

### 6.3 切换 provider

直接运行另一个启动命令即可。例如：

```powershell
.\windows\FinalKit.ps1 -Action deepseek
.\windows\FinalKit.ps1 -Action kimi
```

FinalKit 会复用健康的同后端 gateway；切换后端时先通过控制面安全停止 Science，再替换 gateway、重启 Science并核对 endpoint。不要手工编辑 gateway 配置。

### 6.4 状态与停止

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action stop
```

使用菜单 `7–10` 启动后，健康状态应显示：

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

菜单 `11` 会把当前已经运行的 Claude Science URL 打开到该隔离 Chrome；单独的 `browser-start` 仍只启动可自动化浏览器，便于没有 Science 的网页任务。

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

## 9. 独立 Windows Claude 应用与四种 provider

这一套只服务官方 Windows Claude 应用，不是 WSL Claude Science 的启动入口。它的唯一状态根是：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\WindowsClaude
```

固定监听为 `127.0.0.1:18987`；四个 Claude 3P profile 也只指向这个 Windows 端口。控制器不调用 `wsl.exe`、不读取 Linux home、不使用 WSL 的 `9876` gateway，也不改变 WSL Science 的 `8765` daemon。

### 9.1 先建立四个隔离配置槽

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-init
.\windows\FinalKit.ps1 -Action windows-claude-status
```

也可双击 `windows\40-Initialize-Windows-Claude.cmd`。初始化会：

1. 建立 DeepSeek、Kimi、GLM、Codex login 四个 profile；四个 profile 都有三组独立 Model/Reasoning，前三家 key 为空，Codex 预置 Opus → Sol、Sonnet → Terra、Haiku → Luna；
2. 为每个 profile 生成独立 loopback path 与 Claude client token；
3. 将 profile 登记到 `%LOCALAPPDATA%\Claude-3p\configLibrary`；
4. 继续保持 `deploymentMode=1p`；
5. 不创建 API key、不发起 Codex 登录、不启动 gateway、不打开 Claude。

因此刚初始化时，未登录 Windows Codex 的机器是 `Configured profiles: 0/4`；已有有效 ChatGPT Codex 登录的机器可显示 Codex 为 `configured=True` 和 `Configured profiles: 1/4`。两种情况下都必须保持 `Claude profile: official-or-unconfigured`、`Gateway running: false`，因为初始化不会激活 3P profile。

### 9.2 以后由你配置 Windows provider

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs kimi
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs glm
.\windows\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs codex
```

DeepSeek/Kimi/GLM 依次配置 Opus、Sonnet、Haiku 的 Model/Reasoning，再输入隐藏 API key。上游 URL 固定在包内官方 HTTPS allowlist，不作为日常冗长提示。三家的 key 使用 Windows DPAPI `CurrentUser` 加密到 `secrets\<mode>.dpapi`；schema 3 的 `profiles.json` 只保存 endpoint、六个短路由字段和随机本机能力值。不要把 API key 写进 `-RemainingArgs`、`.cmd`、环境变量或 Claude JSON。

Codex 不提示 API key。先在普通 Windows PowerShell 中完成一次官方登录并核验：

```powershell
codex login
codex login status
```

随后运行 `windows-claude-configure ... codex`，控制器会要求状态为 `Logged in using ChatGPT`，从 `%CODEX_HOME%\auth.json`（默认 `%USERPROFILE%\.codex\auth.json`）读取官方 `tokens` schema，并从同一 Windows Codex home 的 `models_cache.json` 与 `config.toml` 显示可用模型和建议值。当前已有有效登录时不会再次弹浏览器；配置会逐档显示该模型缓存中的 Reasoning 强度、默认值和说明，再分别保存。默认映射是 Opus → `gpt-5.6-sol`、Sonnet → `gpt-5.6-terra`、Haiku → `gpt-5.6-luna`；本机已知模型不接受目录未声明的强度，`ultra` 只有在所选模型明确支持时才出现。四个 provider 的 Claude alias 都按角色成对取得 Model/Reasoning；三家直连使用各自 Anthropic-compatible Messages endpoint，Codex 走 `https://chatgpt.com/backend-api/codex/responses` 并由 Windows gateway 完成 Messages → Responses、工具调用和 SSE 转换。Windows auth 与 WSL `~/.finalkit-client/.codex/auth.json` 平时是两个独立 owner；只有上节的显式一次性入口会用当时的 Windows bytes 初始化 WSL，Windows Claude 控制器自身仍不调用 WSL。

### 9.3 启动、状态和停止

```powershell
# 选择一个已配置的 Windows profile
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs deepseek

# Claude 已经打开时，显式重启以读取新 profile
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs deepseek -Force

.\windows\FinalKit.ps1 -Action windows-claude-status
.\windows\FinalKit.ps1 -Action windows-claude-stop
```

未配置 profile 会在启动 Python 或改 `deploymentMode` 前失败，Claude 保持 `1p`。启动成功后才切到 `3p` 并写入选中 profile；停止命令只结束通过 instance/PID/命令行/health 四重核验的 Windows gateway。若 18987 被未知进程占用，启动失败并要求先识别占用者，绝不强占或 kill。

相同操作也可从主菜单 `23` 的子菜单或 `windows\40–51` 快捷脚本完成。

### 9.4 恢复 Windows Claude 官方模式

```powershell
.\windows\FinalKit.ps1 -Action windows-claude-official
```

它只停止 Windows gateway、把 Windows Claude 恢复为 `1p`、删除 FinalKit 自己登记的四条 3P profile；不会检查或操作 WSL。三家 DPAPI API 设置与 Windows Codex 登录绑定保留，日后可直接再次启动。执行前会为真实存在的 Claude 配置建立可恢复备份：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\Backups\ClaudeDesktop
```

## 10. Claude Science 与 Windows Codex 协作

### 10.1 创建唯一 handoff

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

### 10.2 在本地 Claude Science 发布独立审阅 skill

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

### 10.3 由 Windows Codex 准备待审对象

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

### 10.4 让 Claude Science 独立审阅

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

### 10.5 Windows Codex 核验和修复

```text
Windows Codex 实施并直接验证
-> Claude Science 独立只读审阅
-> Windows Codex 将每个 finding 判为 accepted / rejected / unresolved
-> 修复最早受影响 owner 并直接复验
-> 仅在方法、结果或 claim 边界实质变化时再审
```

Claude Science 的 finding 是审阅证据，不是自动裁决。不要因为两个 Agent 一致就把结论当真，也不要因为 finding 与实现叙述冲突就直接驳回。

### 10.6 反向：Windows Codex 只读复核 Claude Science 工作

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

## 11. 故障处理

按从低风险到高风险的顺序处理。

### 11.1 先查看状态

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
```

### 11.2 查看日志

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

### 11.3 常见情况

| 症状 | 优先处理 |
|---|---|
| `auth: not configured` | 重新运行对应 `configure-*` |
| API 返回 401/403 | 检查 key 是否有效、权限/余额/地区策略，必要时轮换 key |
| API 返回 model not found | 查看 provider 当前模型可用性；不要修改上游 URL |
| gateway stopped | 运行对应启动命令 |
| gateway identity mismatch | 先 `-Action stop`，再启动；不要手工 kill 未知进程 |
| `FINALKIT_SCIENCE_CONTROL_UNAVAILABLE`、Status 显示 `control unavailable`，或 owner state 为 `D` | 新启动时，同一 PID 且 argv/HOME/data-dir/lock 全部核验通过的 Science 0.1.27 owner 可在最多 45 秒的 readiness 窗口内完成初始 ext4 数据库 I/O；这不会让稳定态检查放宽。若 45 秒后仍为 `D`，或既有 daemon 的 `status/doctor/stop` 观察到持续 `D`，FinalKit 仍 fail-closed 且不发信号。先用 `wsl.exe --list --verbose` 确认目标发行版并关闭其中其他任务，再从 Windows 精确执行 `wsl.exe --terminate Ubuntu-24.04`（名称按实际替换），然后运行菜单 `16 Update FinalKit runtime`。terminate 不注销发行版、不删除文件、不清 API/Codex 认证；若 runtime 本身缺失才用菜单 `2`，不要 Clear |
| Science 很快 sign out，日志出现 `invalid_grant` 或 `treating as logged-out` | 旧 FinalKit/HGSX 风格 refresh token 被 Science 送往官方 refresh endpoint。运行菜单 `16`；3.2.1 只迁移能完整识别的旧 FinalKit Fernet/v2 身份，未知或真实凭据原样保留并阻断自动覆盖，然后创建空 refresh token、长期本地 access token 与固定 user/org 的 `science-local-v2` 身份。重新用菜单 `7–10` 打开，不需要 Claude 登录 |
| Science `/api/models` 或页面提示 `No credentials`，只显示 Opus/Sonnet/Haiku fallback | 本地身份、gateway 身份端点或已部署 runtime 未闭合；先运行菜单 `16`，再运行 `smoke` 和对应 `test-*`。`smoke` 会同时校验本地 token、user/org、provider gateway、Science endpoint 与 data-dir 身份；未知或真实 Science 凭据不会被自动替换 |
| 本地页面显示 `Sign in` | 这是 Claude Science daemon 的一次性 nonce/cookie 会话门，不是 Claude.ai 账号登录。仅接受地址栏为 `http://127.0.0.1:<本机端口>` 或 `localhost` 的页面；点击一次进入工作台。若确认后仍回到同一页，运行菜单 `16`，再看 `status`、`doctor` 和 Science 日志 |
| Science 页面没有打开 | 运行 `status`，再运行 `fkctl url` 获取新的一次性本机 URL；不要复用或转发旧 nonce |
| 端口被占用 | 查明占用者；FinalKit 不会强占第三方进程 |
| Windows Claude 显示 `Configured profiles: 0/4` | 三家 API 槽仍为空，且 Windows Codex 登录或模型路由未闭合。运行 `codex login status`；若已显示 `Logged in using ChatGPT`，再运行 Codex 配置并直接回车接受本机模型默认值。不要填写另一份 OpenAI API key |
| 配置仍提示 `Default/Opus/Sonnet model ID`，或 Sonnet 与 Opus 被迫相同 | 仍在使用旧 schema/controller。更新 FinalKit 后重新配置；正常界面只依次出现 `Opus Model/Reasoning`、`Sonnet Model/Reasoning`、`Haiku Model/Reasoning`。Codex 还会先列出本机缓存模型及逐模型 Reasoning 能力 |
| Windows Claude 启动报 `unconfigured` | DeepSeek/Kimi/GLM 表示缺少 DPAPI key 或任一角色的 Model/Reasoning；Codex 表示 Windows ChatGPT login 或任一角色路由缺失/与当前能力缓存冲突。它不会退回 WSL，也不会在失败时切换 Claude `1p` 状态 |
| Windows Claude Codex 显示 `login-missing` | 在 Windows 运行 `codex login` 与 `codex login status`；不要复制 WSL auth.json，也不要给 Windows Claude 另填 OpenAI API key |
| Windows Claude 18987 被占用 | 用 `Get-NetTCPConnection -LocalPort 18987 -State Listen` 查明 PID；不要结束未知进程。Windows owner 不会改用 WSL 9876 |
| Windows Claude 已切 profile 但界面未变 | 完全退出 Claude 后重开，或在明确允许结束当前 Claude 窗口时用 `windows-claude ... -Force` |
| 浏览器 `Status: stopped` | 重新运行 `browser-start` |
| WSL 无法访问浏览器 127.0.0.1 | 检查 WSL mirrored networking；FinalKit 不自动修改 `.wslconfig` |
| MCP 找不到 `node` | 使用 `~/.local/bin/chrome-devtools-mcp-finalkit`，不要直接运行 npm shim |
| Build 下载中断 | 重复 Build，不要 Clear |
| apt 的 Ubuntu security 源经 `127.0.0.1` 代理返回 502 | 3.0.1 会仅对失败的 apt 命令自动直连重试；不会修改全局代理 |
| Build 在 `wsl --list/--install` 后出现乱码 | 使用当前发行包；自 3.0.3 起按原始字节识别 UTF-16LE、UTF-8 和混合输出并去除 ANSI 控制序列 |
| 首次 Build 显示 `403`、`请求的操作需要提升` | 允许当前发行包的 UAC 系统准备；若提示重启，重启后以同一个普通 Windows 用户再运行 Build，不要 Clear |
| Store 路径无法下载 Ubuntu | 当前发行包自动重试 `--web-download`；两条路径均失败时按保留的真实网络/错误码诊断处理 |
| 旧版 `configure-codex` 显示 device code 403 | 使用当前发行包的默认浏览器 OAuth；只有已启用 device login 时才运行 `configure-codex-device` |
| Windows Codex 已登录，但 WSL FinalKit 显示 `not configured` | 两者默认是不同 OS/用户的缓存。可在目标 Linux 用户下运行 `configure-codex -LinuxUser <name>` 自主登录；若要从 Windows 同账号起步，运行菜单 `24` 或 `08-One-Time-Migrate-Windows-Codex-Auth-to-WSL.cmd`，不要手工复制或打印 auth JSON |
| Codex 请求返回意外 `401` | 当前 connector 会在尚未输出回答时用共享缓存强制刷新并且只重试一次；若 refresh chain 没有变化或再次 `401`，运行 `configure-codex` 完成新的浏览器登录 |
| Codex 登录成功但 backend 超时 | 保留可访问 `chatgpt.com` 的现有代理/VPN，运行 `doctor` 和 `test-codex`；不要把登录直连策略全局化 |

### 11.4 修复安装

只有 `doctor` 表明组件缺失或 installer 未完成时才重复：

```powershell
.\windows\FinalKit.ps1 -Action build
```

修复 Build 不应覆盖已有 secret 和用户登录。

### 11.5 最后才考虑 Clear

只有以下条件同时满足时才重新 Clear：

- WSL 发行版确实不可修复或明确希望完全重置；
- 已停止活跃任务；
- 唯一项目、结果和凭证已有外部副本；
- 已确认目标名称和 BasePath；
- 接受从 tar 恢复或永久丢弃该发行版。

## 12. 多用户操作

### 12.1 同一 Windows 用户、同一 WSL、多个 Linux 用户

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

### 12.2 多个 Windows 用户

每个 Windows 用户拥有自己的：

- WSL 注册；
- `%LOCALAPPDATA%` 浏览器 profile；
- Windows Codex 登录；
- FinalKit 备份目录。

不要把一个 Windows 用户的 WSL 注册目录或 Chrome profile 复制给另一个用户。

## 13. 凭证轮换

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

## 14. 恢复与回滚

### 14.1 从 WSL tar 恢复

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

### 14.2 软件包回滚

保留旧版 ZIP 时，可解压到新的并列目录进行只读比较，不要直接在 ZIP 内运行。日常只保留一个明确的解压运行目录，避免从微信临时副本或多个旧目录启动；运行资格由已部署命令与健康检查决定，不由发行号文本决定。

## 15. 日常最短命令

完成 Build、配置和真实测试后，日常只需：

```powershell
cd C:\Tools\ScienceCodexFinalKit   # 或你自己的稳定解压目录

# 启动一种后端
.\windows\FinalKit.ps1 -Action deepseek

# 查看状态
.\windows\FinalKit.ps1 -Action status

# 可选：Science 已由菜单 7–10 启动后，打开到自动化 Chrome
.\windows\FinalKit.ps1 -Action browser-science

# 网页任务结束
.\windows\FinalKit.ps1 -Action browser-stop

# 当天任务结束
.\windows\FinalKit.ps1 -Action stop

# 可选：完全独立的 Windows Claude（先配置 Windows provider）
.\windows\FinalKit.ps1 -Action windows-claude -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action windows-claude-status
```

## 16. 安装后的最短验收

每位用户在自己的普通 Windows 账号和 Linux home 内完成一次：

1. 运行 `status` 和 `doctor`，确认发行版、客户端、connector、权限与运行身份；
2. 只配置自己拥有的一种 provider：DeepSeek、Kimi、GLM 或 ChatGPT Codex；
3. 运行对应的 `test-deepseek`、`test-kimi`、`test-glm` 或 `test-codex`；
4. 看到 `BACKEND_OK mode=<provider>` 后，再运行菜单 `7–10` 的同名 Start Science 动作；
5. 不做 Claude.ai 账号登录；在 `127.0.0.1` 页面接受一次本机会话，再次运行 `status`，确认 `Science identity: FinalKit local-only`、`Gateway: healthy`、`Claude Science: running`、`Runtime identity: matched`；
6. 只有确实需要页面自动化时才运行菜单 `11` / `browser-science` 和 `browser-mcp-info`；
7. 需要独立 CLI 时可用菜单 `19–22`，它复用同一后端配置但不启动 Science。

Windows Claude 是另一套可选验收：先运行 `windows-claude-init`，确认 `0/4`、`1p`、gateway stopped；以后自行配置一个 Windows provider，再启动并确认 `127.0.0.1:18987` 的 owner identity。Codex 槽应额外显示 `auth=windows-codex-login`，且 runtime JSON 只有 auth 文件路径、没有 token。不要用 Windows Claude 验收代替 WSL Science，也不要把 WSL API/OAuth 手工复制到 Windows；可选迁移方向只允许由明确入口执行 Windows → WSL 一次性导入。

DeepSeek/Kimi/GLM API key 只能在配置命令的隐藏提示中输入，不要发到聊天、截图、`.cmd`、README 或项目文件。Windows Codex token 只由官方 CLI auth 文件持有，不要打印、复制或手工编辑。已经公开过的 key/token 应在对应供应商或账号侧轮换/重新登录。
