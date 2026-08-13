# FinalKit 3.2.0 操作手册

本手册是普通用户从零安装、配置、启动、更新和恢复的唯一入口。默认工作流是 WSL 原生 Claude Code，不使用 Claude/Claude.ai 账号，也不启动 Claude Science。

## 1. 最短决策

| 当前状态 | 动作 |
|---|---|
| 新电脑、没有 Ubuntu WSL | 直接运行 `Build.cmd` 或菜单 `2` |
| 有旧 Ubuntu 且明确要删除 | 菜单 `1`，核对备份和精确名称 |
| Build 网络中断 | 重跑 Build，不要 Clear |
| 缺 provider 认证 | 菜单 `3–6`，不要 Build |
| 厂商模型换代 | 菜单 `17`，不要 Build |
| FinalKit 代码更新 | 菜单 `16`，不要 Build |
| 日常带浏览器工作 | 菜单 `7–10` |
| 日常纯终端工作 | 菜单 `19–22` |
| gateway 故障 | 先 `12/13/14`，最后才考虑 Build |

## 2. 可选 Clear

`wsl --unregister` 会永久删除对应 WSL 文件系统。只有确实要删除旧 Ubuntu 时才运行：

```powershell
.\windows\FinalKit.ps1 -Action clear
.\windows\FinalKit.ps1 -Action clear -AllUbuntu
```

FinalKit 先显示精确发行版名称和注册表 BasePath，要求输入 `DELETE <发行版名>`，默认导出 tar 到：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\Backups
```

自动测试可加 `-Force`；只有已确认没有独有数据时才加 `-NoBackup`。FinalKit 不删除目录通配符、不处理非 Ubuntu、不碰其他 Windows 用户或 Docker Desktop 发行版。

## 3. Build：从零建立环境

把 ZIP 完整解压到稳定本地目录，不要直接从 ZIP、微信临时目录或会撤除的 E 盘运行。然后双击：

```text
Build.cmd
```

或：

```powershell
cd D:\Tools\ScienceCodexFinalKit
.\windows\FinalKit.ps1 -Action build
```

Build 会：

1. 检查/准备 WSL 平台；首次需要时只为系统组件请求一次 UAC。
2. 安装或复用标准 `Ubuntu-24.04`。
3. 创建当前 Windows 用户对应的普通 Linux 用户。
4. 安装 Linux Claude Code、Linux Codex CLI、provider gateway 和固定 connector。
5. 在 `%LOCALAPPDATA%\ScienceCodexFinalKit` 安装经过官方 SHA256 校验的便携 Windows Node.js 与固定 Chrome DevTools MCP。
6. 运行离线契约、`doctor` 和不消耗模型用量的 `smoke`。

成功标志是 `BUILD_OK`、`SMOKE_OK` 且 Doctor 无 FAIL。Build 不要求 Claude 账号、不生成 Science 虚拟身份、不修改全局代理、`.wslconfig`、系统 PATH 或 passwordless sudo。

### 多 Linux 用户

```powershell
.\windows\FinalKit.ps1 -Action build -LinuxUser alice
```

每个 Linux 用户独立拥有 key、OAuth、模型路由和 gateway；默认 loopback 端口按 Linux UID 稳定分配。自定义发行版名必须同时给出自定义位置：

```powershell
.\windows\FinalKit.ps1 -Action build `
  -Distro Research-Ubuntu-24.04 `
  -DistroLocation D:\WSL\Research-Ubuntu-24.04 `
  -LinuxUser alice
```

## 4. 配置 provider

```powershell
.\windows\FinalKit.ps1 -Action configure-deepseek
.\windows\FinalKit.ps1 -Action configure-kimi
.\windows\FinalKit.ps1 -Action configure-glm
.\windows\FinalKit.ps1 -Action configure-codex
```

DeepSeek/Kimi/GLM 在 WSL 终端隐藏输入 API key，保存为 `0600`。Codex 使用官方 Linux Codex 浏览器 OAuth，凭据 owner 是：

```text
~/.finalkit-client/.codex/auth.json
```

这四种认证都与 Claude 账号无关。不要把 key 放进 CMD、PowerShell 历史、README、HANDOFF 或 Git。

### 厂商模型换代

菜单 `17` 会：

1. 读取当前 WSL 用户已保存的 provider key；
2. 从包内固定的官方 models URL 只读拉取可调用目录；
3. 显示模型编号；
4. 让用户选择 main/fast；
5. 显示 dry-run；
6. 确认后原子写入 `model-routes.json`；
7. 仅在修改当前活动后端且用户允许时安全重启 gateway。

命令行：

```powershell
.\windows\FinalKit.ps1 -Action discover-models -RemainingArgs deepseek
.\windows\FinalKit.ps1 -Action update-models
.\windows\FinalKit.ps1 -Action update-models `
  -RemainingArgs codex,--opus,gpt-6-sol,--sonnet,gpt-6-terra,--haiku,gpt-6-luna,--effort,max,--restart
```

厂商若升级同一 alias 背后的权重，例如 `deepseek-v4-pro` 指向新的 Pro-0813，调用 ID 不变，无需改配置。FinalKit 不根据网页新闻自动改生产配置；必须由官方账号目录或用户明确输入提供候选，再预览确认。

## 5. 真实后端测试

配置后先运行一次：

```powershell
.\windows\FinalKit.ps1 -Action test-deepseek
.\windows\FinalKit.ps1 -Action test-kimi
.\windows\FinalKit.ps1 -Action test-glm
.\windows\FinalKit.ps1 -Action test-codex
```

Codex 三档接受测试会产生三次真实请求，仅在需要验证账号是否接受 Sol/Terra/Luna 时运行：

```powershell
.\windows\FinalKit.ps1 -Action test-codex-tiers
```

## 6. 启动与切换

双击 `SwitchModel.cmd`：

| 菜单 | 结果 |
|---|---|
| 7 | WSL Claude Code + Windows Chrome + DeepSeek |
| 8 | WSL Claude Code + Windows Chrome + Kimi |
| 9 | WSL Claude Code + Windows Chrome + GLM |
| 10 | WSL Claude Code + Windows Chrome + ChatGPT/Codex |
| 11 | 只启动/检查隔离 Chrome |
| 19–22 | 只启动 WSL Claude Code，不带浏览器 |

对应命令：

```powershell
# 带浏览器
.\windows\FinalKit.ps1 -Action deepseek
.\windows\FinalKit.ps1 -Action codex

# 纯 Claude Code
.\windows\FinalKit.ps1 -Action deepseek -NoBrowser
.\windows\FinalKit.ps1 -Action claude -RemainingArgs deepseek
```

每次启动会打印：

```text
ACTIVE_MODE=<provider>
EFFECTIVE_ROUTE=<真实模型路由>
Runtime: WSL distro=<name>; Linux user=<user>; client=Claude Code; provider=<provider>
```

Claude Code 中 `/model` 显示实际模型，例如 `Current model: gpt-5.6-sol`。Opus/Sonnet/Haiku 只是兼容角色映射，不是对上游模型身份的声明。

## 7. Windows 隔离浏览器桥

菜单 7–10 自动完成：

1. 以 `%LOCALAPPDATA%\ScienceCodexFinalKit\ChromeProfile` 启动隔离 Chrome；
2. 只监听 Windows `127.0.0.1:9223`；
3. 生成 WSL `0600` session-only MCP 配置；
4. WSL Claude Code 通过 `cmd.exe` 启动 FinalKit 便携 Windows Node.js；
5. Windows MCP 连接同一 loopback 上的 Chrome。

因此无需让 WSL 直接访问 Windows loopback，也无需启用 mirrored networking。FinalKit 不写 Claude Code 全局 MCP 设置。

检查与停止：

```powershell
.\windows\FinalKit.ps1 -Action browser-start
.\windows\FinalKit.ps1 -Action browser-status
.\windows\FinalKit.ps1 -Action browser-mcp-info
.\windows\FinalKit.ps1 -Action browser-stop
```

首次使用某个 browser tool 时 Claude Code 会要求权限，这是正常安全边界。隔离 Chrome 中只登录允许自动化访问的网站。

## 8. 状态、Doctor 与停止

```powershell
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action stop
.\windows\FinalKit.ps1 -Action browser-stop
```

正常 Status 包含：

```text
Native client:       /home/<user>/.local/bin/claude
Gateway:             healthy (...) 或 stopped
Effective route:     ...
Claude account:      not used
Optional Science:    not installed 或 installed; not used by default
```

`stop` 只停止 FinalKit gateway；`browser-stop` 只停止带精确隔离 profile 的 Chrome，保留 profile 数据。

## 9. 三类 Update

```powershell
# FinalKit 代码/网关/connector patch；保留认证和模型
.\windows\FinalKit.ps1 -Action update-runtime

# 模型目录和持久路由
.\windows\FinalKit.ps1 -Action update-models

# 官方 Claude Code、Codex CLI、便携 Node/MCP
.\windows\FinalKit.ps1 -Action update-tools
```

这三类更新都不重建 Ubuntu。版本号不是门禁；Windows 按 `fkctl capabilities` 判断新命令是否可用。只有缺少所需能力时才提示菜单 16，普通已有命令继续运行。

## 10. Windows Codex 协作

在项目中建立唯一交接文件：

```powershell
.\windows\FinalKit.ps1 -Action init-project -Project D:\path\to\project
```

文件位置：

```text
.science-codex/HANDOFF.md
```

Windows Codex 与 WSL Claude Code 共享项目事实，但不要同时无边界写同一文件。Windows 只读复核：

```powershell
.\windows\FinalKit.ps1 -Action windows-review -Project D:\path\to\project
```

HANDOFF 不得包含 key、token、cookie 或私有浏览器数据。

## 11. 故障处理

| 症状 | 处理 |
|---|---|
| 出现 Claude Science “Sign in with Claude.ai” | 运行的是旧包/旧 Science 入口；用当前根目录菜单 16 更新，再走 7–10 或 19–22；默认工作流不打开 Science |
| `Claude account: not used` 但 provider 不响应 | 运行对应 `test-*`，检查该 provider key/Codex OAuth，不要登录 Claude |
| `RUNTIME_UPDATE_REQUIRED` | 当前 WSL runtime 缺该新 capability；运行菜单 16，不是版本号门禁 |
| gateway stale | 菜单 14 后重启对应 provider；再看 `fkctl logs gateway` |
| Chrome 未启动 | 菜单 11；确认本机安装 Google Chrome |
| MCP 工具不可见 | 先用 7–10 启动，确认 `browser-status` reachable；不要用 19–22 期待浏览器工具 |
| MCP tool blocked | 在 Claude Code 权限提示中允许需要的具体工具 |
| Node/MCP 缺失 | 重跑菜单 2 或 18；FinalKit 会在 `%LOCALAPPDATA%` 修复便携依赖 |
| WSL 安装乱码/失败 | 保留完整错误；重跑 Build 会按原始字节解码并在 Store 失败时尝试 `--web-download` |
| Codex OAuth 失效 | 只重跑菜单 6；不需要 Build、Clear 或 Claude 登录 |

日志：

```bash
fkctl logs gateway --lines 120
```

普通错误不要 Clear。Build 用于缺文件/完整修复；Clear 仅用于明确删除 WSL。

## 12. 凭证轮换与恢复

API key 轮换：重新运行对应 configure；成功写入后旧 key 被原子替换。Codex 轮换：菜单 6。WSL tar 恢复示例：

```powershell
wsl --import Ubuntu-24.04 D:\WSL\Ubuntu-24.04 `
  "$env:LOCALAPPDATA\ScienceCodexFinalKit\Backups\Ubuntu-24.04-YYYYMMDD-HHMMSS.tar" `
  --version 2
```

恢复后运行菜单 13；只有缺少 runtime 文件时才运行菜单 2。

## 日常最短命令

```powershell
cd D:\Tools\ScienceCodexFinalKit
.\SwitchModel.cmd
```

选择 `7–10` 工作；结束时：

```powershell
.\windows\FinalKit.ps1 -Action stop
.\windows\FinalKit.ps1 -Action browser-stop
```

## 安装后的最短验收

```powershell
.\windows\FinalKit.ps1 -Action doctor
.\windows\FinalKit.ps1 -Action status
.\windows\FinalKit.ps1 -Action test-deepseek   # 或已配置的其他后端
.\windows\FinalKit.ps1 -Action deepseek
```

验收标准：

1. Doctor 无 FAIL；
2. Status 明确 `Claude account: not used`；
3. 真实后端测试输出 `BACKEND_OK`；
4. Claude Code 能正常回答；
5. 菜单 7–10 启动后 `browser-status` 为 reachable；
6. Claude Code 能看到 `mcp__chrome-devtools__evaluate/navigate/screenshot`，授权后可从 Chrome 读回当前页面；
7. `fkctl status` 的 `Effective route` 与预期真实模型一致。
