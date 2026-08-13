# Science SwitchModel / FinalKit 3.1.3 代码剖析

本文面向维护者，解释永久 owner、安装路径、provider 请求、事务切换、浏览器桥与多用户边界。README 负责普通用户操作；这里负责“为什么代码这样写”。

## 1. 四个永久 owner

| Owner | 职责 | 不负责 |
|---|---|---|
| `windows/FinalKit.ps1` | 精确 Clear、标准 WSL Build、Linux 用户选择、Windows 快捷入口、隔离 Chrome、Codex 只读交接 | provider payload、Science 内部数据库、自动修改 `.wslconfig` |
| `wsl/install-final-stack.sh` | system/user 两阶段安装、官方客户端、固定 Node/MCP、固定 connector、runtime 部署、初始验收 | 日常 gateway 进程、模型请求、Windows 浏览器进程 |
| `wsl/runtime/switch_manager.py` | provider 事务、私密状态、进程身份、Science endpoint、回滚、doctor/smoke/test | Anthropic HTTP 直通细节、研究分析逻辑 |
| `wsl/runtime/direct_gateway.py` | DeepSeek/Kimi/GLM 固定白名单、认证头、模型角色映射、流式响应 | ChatGPT/Codex 协议翻译、管理 UI、任意 URL 代理 |
| `wsl/runtime/science_identity.py` | 校验 Science 官方账号边界、精确识别并移除旧 FinalKit 虚拟 OAuth | 创建 Claude.ai 账号、伪造订阅、覆盖未知/真实凭据 |
| `wsl/tests/connector_contract.py` | 无凭据、无网络捕获三档 connector 的模型目录、alias 解析和最终 Responses payload | 账号权限、服务端可用性、真实模型质量 |
| `wsl/tests/runtime_control_contract.py` | 无凭据、无真实进程验证 Science stopped/healthy/stale/lock-conflict 控制语义 | 强杀 WSL、真实 daemon 可用性、模型请求 |
| `wsl/tests/science_identity_contract.py` | 无真实凭据验证 login-required、Fernet/v2 精确移除和未知凭据保护 | 真实 Claude 登录、网络认证 |
| `wsl/tests/model_routes_contract.py` | 无凭据、无真实进程验证配置迁移、dry-run、原子持久化、未来 provider 保留 | 厂商账号是否接受某模型 ID |
| `wsl/tests/installer_update_contract.sh` | 临时 fixture 验证 runtime 更新失败精确回滚和成功提交 | 真实 WSL 服务重启、供应商网络 |

`wsl/fkctl` 只是稳定入口：解析真实 Linux home 后 `exec` 系统 Python 运行 manager。`.cmd` 文件也只是给普通用户的薄入口，不复制业务逻辑。

## 2. Clear 的破坏性边界

`Invoke-Clear` 先读当前 Windows 用户的：

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss
```

每个目标必须同时具有非空 `DistributionName`、非空且不是磁盘根的 `BasePath`。`-AllUbuntu` 还会在精确发行版内读取 `/etc/os-release`，确认 `ID=ubuntu`。之后才按顺序执行：

```text
show exact name + BasePath
-> exact typed confirmation (unless -Force)
-> wsl --export (unless -NoBackup)
-> wsl --terminate <exact name>
-> wsl --unregister <exact name>
-> read back distro list and prove absence
```

代码不递归删除 BasePath；VHDX 的移除交给 WSL 自己完成。这样不会因大小写、环境变量、通配符或错误路径计算扩大范围。

## 3. Build 与多用户

默认不提供自定义 `--name` 或 `--location`，而是调用标准 Ubuntu 24.04 安装，让 WSL 使用当前 Windows 用户的正常注册和存储。只有用户同时给出 `-Distro` 与 `-DistroLocation` 时才建立自定义实例。

安装拆为三个权限边界：

```text
Windows ordinary user: Ensure-WslPlatform
  -> only when required, UAC: wsl --install --no-distribution
  -> restart boundary when Windows requires it
Windows original ordinary user: Ensure-Distro / Ensure-LinuxUser
  -> root: install-final-stack.sh --system
       apt installs sandbox/runtime dependencies
  -> selected Linux user: install-final-stack.sh --user
       install tools + per-user runtime + verification
```

UAC 进程只准备机器级 WSL/Virtual Machine Platform，不创建 Ubuntu；发行版安装返回原始 Windows 用户后执行，避免注册到另一个管理员账号。`--system` 只写 Ubuntu 系统包；`--user` 的全部持久状态写到当前用户 home。因此同一 WSL 中的多个 Linux 用户可以重复执行 user phase，各自拥有：

```text
~/.local/share/science-codex-finalkit
~/.science-finalkit
~/.local/bin/{claude-science,claude,codex,fkctl}
```

没有共享 API key、OAuth state、PID 文件或 gateway lock。installer 不建立 passwordless sudo。

Windows PowerShell 5 会把 `wsl.exe` 的本地化 stderr 包装成 `NativeCommandError`。不同 WSL 版本的重定向管理输出可能是 UTF-16LE、UTF-8，首次安装失败时甚至会出现 UTF-8/ANSI console 前缀与 UTF-16LE Win32 文本拼接。`Invoke-WslCapture` 因此直接读取原始 stdout/stderr 字节、并发排空两条 pipe、自适应解码、移除终端控制序列并检查 exit code。`需要提升/optional component` 进入受限 UAC 系统准备；需要重启时停止；普通 Store 下载失败才追加 `--web-download` 重试。“当前用户没有注册任何发行版”由 Lxss 注册表空状态判为首次安装，而不是脚本异常。WSL 内 apt 仍先尊重用户网络环境；若代理失败，只对该 apt 命令移除代理变量再重试，不写全局网络配置。

## 4. 官方客户端与固定依赖

installer 通过临时文件下载并运行三个官方 installer：

- Claude Science：`https://claude.ai/install-claude-science.sh`；
- Claude Code stable：`https://claude.ai/install.sh`；
- Linux Codex CLI：`https://chatgpt.com/codex/install.sh`。

下载时记录 installer SHA256，但由于供应商未在包内固定脚本摘要，这个摘要是运行证据而不是预置供应链 pin。真正固定的组件是：

- Node.js `v24.19.0`：下载官方 `SHASUMS256.txt`，找到精确 archive，再比较实际 SHA256；
- `chrome-devtools-mcp@1.2.0`：安装到当前用户 FinalKit root；
- `claude-science-codex-connector`：固定 Git commit `30b26d7c6f097b186bbd228e93a427a731399960`；
- connector Python 依赖：`requirements.lock` 精确版本。

更新任一 pin 时必须先验证上游发布、许可证、参数兼容和 local smoke，再修改版本记录。

### 三条独立升级通道

`Build` 仍是首次安装与完整修复 owner，但不再承担所有日常变更：

- `install-final-stack.sh --runtime` 只部署包内 runtime owner 和受控 connector patch。它先跑离线 connector、runtime-control、science-identity、model-route contract，完整 Build 另跑 runtime-update rollback contract；失败时按精确文件备份恢复，provider/Codex 认证与模型配置不属于替换集合。Science OAuth 清理属于 3.1.3 的显式兼容边界：只移除已完整认证为 FinalKit 虚拟身份的旧文件。
- `fkctl discover-models` 只读请求 DeepSeek/Kimi/GLM 包内固定的官方模型目录 URL，以当前 Linux 用户已经保存的 provider key 鉴权；只解析合法 `data[].id`，不做生成请求、不打印 key、不写配置。网络层先直连，再只对 transport failure 尝试继承代理；HTTP 认证/权限失败不会换通道重放。`fkctl update-models` 才更新 `config/model-routes.json`。该 JSON 是模型选择的唯一持久 owner，`bridge/config.json` 是可再生的 Codex 兼容派生配置。更新支持 `--dry-run --json`，用临时文件、`fsync` 和 `os.replace` 原子提交；若修改的是当前 backend，必须显式 `--restart`，失败恢复旧配置与已观察到的 runtime。
- `install-final-stack.sh --tools` 是明确联网的官方客户端/依赖更新。它在更新前停止 FinalKit，更新官方客户端、Node/MCP 与 `requirements.lock` 后核对 Codex auth 文件字节哈希未变，并保留模型路由。

`fkctl capabilities` 是 Windows 判断新命令是否存在的稳定接口；包版本仍只用于发行识别，不决定旧命令能否启动。模型 schema 当前为 `1`，校验器会合并新增内置 provider 默认项、保留已有与未来厂商条目，并拒绝含空格或 shell 元字符的模型 ID。

## 5. API provider 表

`switch_manager.py` 的 `API_PROVIDERS` 是运行意图；`direct_gateway.py` 的 `PROVIDERS` 是安全 allowlist。两处必须同步，后者再独立拒绝任何不完全一致的 upstream。

```python
API_PROVIDERS = {
    "deepseek": {"upstream": "https://api.deepseek.com/anthropic", ...},
    "kimi": {"upstream": "https://api.moonshot.ai/anthropic", ...},
    "glm": {"upstream": "https://open.bigmodel.cn/api/anthropic", ...},
}
```

不允许把 URL 变成用户输入，因为 gateway 会携带 provider key。任意 URL 会重新引入 SSRF、redirect credential leak 和配置漂移。

provider key 分别保存在：

```text
secrets/deepseek.key
secrets/kimi.key
secrets/glm.key
```

`configure_provider()` 使用 `getpass` 隐藏输入，原子替换为 `0600` 文件。manager 启动 gateway 时建立两个匿名 pipe：一个传 runtime JSON，一个传 key。子进程 argv 只出现 FD 编号：

```text
python3 direct_gateway.py --config-fd 5 --key-fd 6
```

所以 `/proc/<pid>/cmdline` 不含 key、private path 或 instance id。

## 6. direct gateway

gateway 只 bind `127.0.0.1`，并在所有接口前要求一个至少 32 字符的随机 URL path：

```text
http://127.0.0.1:9876/<private>/v1/messages
```

它还拒绝非 loopback `Host`。根路径没有 dashboard、config 或 health。manager 读取私密 health 并同时核对：

```json
{
  "status": "ok",
  "finalkit_instance": "...",
  "finalkit_backend": "kimi",
  "profile_id": "kimi-official-anthropic",
  "upstream_host": "api.moonshot.ai"
}
```

消息路径不做自创 schema 翻译，只执行：

1. 限制请求体为 64 MiB；
2. 确认 JSON object；
3. `haiku` 角色映射为 provider fast model，其余映射为 default model；
4. 转发 `/v1/messages` 或 `/v1/messages/count_tokens`；
5. DeepSeek/GLM 设置 `x-api-key`，Kimi 设置 `Authorization: Bearer`；
6. 保留 Anthropic version/beta 与必要响应 header；
7. 流式复制响应；
8. 不跟随 HTTP redirect。

`offline_smoke` 会在任何外连前返回 503，用于验证本地控制面但不产生费用。

## 7. ChatGPT/Codex connector

ChatGPT/Codex 不是简单 Anthropic 直通，路径是：

```text
Anthropic Messages/tools
-> connector normalizes blocks and tools
-> OpenAI Responses request
-> ChatGPT Codex account backend
-> Anthropic response/SSE
```

因此该模式保留社区 connector。installer 先核对 origin 和固定 commit，再应用 `connector-security.patch`；patch 必须只修改 `proxy.py`。安全补丁主要做：

- 管理 API 从 wildcard CORS 收紧到 loopback origin；
- dashboard/config/device-login 等管理接口要求 private control token；
- control token 和 instance id 从匿名 FD 读取；
- health 返回 instance/backend/control protection；
- connector 仍只 bind loopback。

Science 的模型名在这条协议中是兼容标签，不是上游身份。`switch_manager.py` 写入三个稳定家族前缀，connector 用“精确命中，否则最长家族前缀命中”解析版本化 ID：

```text
claude-opus-*   -> gpt-5.6-sol
claude-sonnet-* -> gpt-5.6-terra
claude-haiku-*  -> gpt-5.6-luna
```

旧实现同时写了 `force_model=gpt-5.6-sol`，而 connector 的优先级是 `force_model > model_map > default`，使三档无论选择什么都被强制成 Sol；并且精确 `model_map.get()` 无法命中 `claude-haiku-4-5-20251001`。3.0.6 把 `force_model` 留空、引入家族前缀解析，并把 `codex_reasoning_effort=max` 显式注入 Responses request 的 `reasoning.effort`。配置侧和 connector 侧都校验允许值，日志同时写实际 model、effort 和 original model。3.0.7 让 connector 的 `/v1/models` 继续返回 Science 0.1.27 必需的 Claude 家族 ID，但将 `display_name` 从同一份 route config 动态生成真实 Codex 模型和 effort；Science 以它作为标题，再按兼容 ID 附加固定 family description。菜单可读性和请求证据因此保持一致，而无需修改供应商前端或使用容易随版本漂移的 `--assets-root` 覆盖。

`configure-codex` 使用临时 staging HOME 发起官方默认浏览器 OAuth，显式固定 file credential store，并保留 WSL interop/display 环境；新缓存通过官方 status 后才原子替换隔离缓存，因此既能首次配置也能安全重授权。它不复制 Windows Codex 或普通 Linux Codex 的 refresh token；`configure-codex-device` 仅是已启用 device login 时的 beta 备用。

登录职责收口到官方 Linux Codex CLI：以 `~/.science-finalkit/.codex/auth.json` 作为唯一凭据 owner。固定 connector 识别官方 `tokens` schema，刷新时把新 access/refresh token 原子写回同一 schema；不再产生 `bridge/codex-auth.json`，从结构上消除 CLI 与 connector 两条 refresh chain 的竞争。登录完成后 manager 还会执行官方 `codex login status`。若 Codex backend 在尚未输出回答时意外返回 `401`，connector 只在共享缓存实际换出新 token 后重试一次；对回答开始前的瞬时 `502/503/504` 也只按原模型、原 effort 重试一次。第二次失败原样返回，不进行无限重试、不降级模型，也不重复已开始的流。connector 访问受保护的 `chatgpt.com` backend 时保留用户现有代理，并由真实 connector health/test 判断后端是否可用。

## 8. 事务切换

`_switch_locked()` 有两个显式客户端路径：Science 路径使用完整事务，原生 Claude Code 路径只选择 gateway；两者共用同一 backend owner，但不共享登录前提。

```python
lock()
observe(previous_mode, gateway_identity, science_identity)
stop_science()
stop_verified_gateway()
try:
    endpoint = spawn_target_gateway()
    verify_gateway_instance_backend_profile()
    if start_science:
        start_science(endpoint)
        verify_science_process_environment(endpoint)
    atomic_commit_mode()
except:
    stop_new_owned_processes()
    restore_only_the_previously_observed_running_state()
    report_primary_and_rollback_errors()
```

manager 停 gateway 前必须核对 PID、`/proc/<pid>/stat` start ticks 和 `/proc/<pid>/cmdline` 中的当前 owner 路径。身份不一致时拒绝 kill，也不抢占已有端口。

`current-mode` 只在所选 gateway 已验证、且请求 Science 时 Science 与 gateway 最终匹配后原子写入。失败回滚遵循 observed state：切换前停止则保持停止；切换前运行才恢复旧 runtime。`VERSION` 与 WSL `versions.txt` 仅是分发和诊断元数据，绝不作为 Start/Stop/Status/配置的总门禁。Windows owner 把稳定命令交给当前用户已部署的 `fkctl`；新增动作通过 `fkctl capabilities` 检查具体能力。这样源码目录更新不会锁死健康旧 runtime，而旧 runtime 也不会被要求执行它根本没有的新子命令。

## 9. Claude Science 与 Claude Code

Science 使用隔离短 HOME：

```text
HOME=~/.science-finalkit
--data-dir=~/.science-finalkit/.claude-science
ANTHROPIC_BASE_URL=<verified local gateway>
```

Science 0.1.27 的 Web UI 需要其自身支持的 Claude 账号登录；DeepSeek/Kimi/GLM API key 和 ChatGPT/Codex OAuth 只认证 FinalKit gateway，不能代替 Science 会话。旧版 FinalKit 曾写入虚拟 refresh token，Science 会把它发往官方 OAuth refresh endpoint，得到 `invalid_grant` 后将页面判为 logged-out。3.1.3 只在能完整解密并验证为旧 FinalKit Fernet/v2 虚拟身份时原子移除它；未知或真实凭据一律原样保留。原生 `fkctl claude <provider>` 不启动 Science，因此只准备 provider/Codex 认证即可使用。Codex 的官方 device/browser auth 仍独立保存在 `~/.science-finalkit/.codex/auth.json`。

短路径避免 Science sandbox 的多层 AF_UNIX socket 超过 Linux `sun_path` 限制。所有 Science status/stop/serve/url 子进程还固定在 WSL ext4 的 `~/.science-finalkit` 工作目录运行，并使用纯 Linux PATH；即使安装包位于 D/E 盘、微信临时目录或网盘，detached daemon 也不会继承 `/mnt/<drive>` 的 9p/DrvFS cwd/PATH 并卡在 `p9_client_rpc`。manager 用 Science 自己的 status 读取真实 daemon PID，再同时核对 `/proc/<pid>/cmdline`、`HOME`、`--data-dir`、lock PID、Linux process state 与 endpoint，而不是只相信启动命令退出码。对同一完整 owner 的一次瞬时 control socket/JSON 失败只做短暂有限重试；PID/lock/HOME/data-dir 身份冲突立即 fail-closed。即使官方 status 返回 running，只要 owner 已进入 Linux `D`（uninterruptible I/O），页面和 control socket 就不能再作为健康证据，Status/Doctor/Start/Smoke 会返回 `FINALKIT_SCIENCE_CONTROL_UNAVAILABLE`。manager 不对这类进程发送信号，而是要求用户从 Windows 精确 terminate 选定 WSL distribution 后做菜单 16 runtime 更新，只有 runtime 缺失或完整栈损坏时才用菜单 2 修复。

`fkctl claude <mode> [args...]` 对原生 Linux Claude Code 临时设置同一个本地 endpoint，并清除外部 `ANTHROPIC_*` 和 Bedrock/Vertex 选择变量。真实 provider key 仍只在 gateway 进程内。

## 10. Windows 浏览器桥

Chrome 136+ 要求远程调试使用非默认 `--user-data-dir`。`Start-BrowserBridge` 固定使用当前 Windows 用户：

```text
%LOCALAPPDATA%\ScienceCodexFinalKit\ChromeProfile
127.0.0.1:9223
```

启动后它必须同时验证 `/json/version` 和 Windows 进程命令行中的精确 profile + port，才写 browser state。停止时再次按精确 profile 匹配进程；不结束日常 Chrome。

WSL 侧 `chrome-devtools-mcp-finalkit` 以固定 Node 绝对路径启动 MCP，再通过 `--browser-url=http://127.0.0.1:9223` 连接 Windows Chrome。包装入口不依赖交互 shell 的 PATH。FinalKit 不自动改 Science 或 Claude Code MCP 配置，因为该 MCP 能读取/控制隔离 profile 中的所有页面，启用必须是用户的显式决定。

`browser-start` 只建立一个可自动化的空浏览器，适合普通网页任务；`browser-science` 先从运行时取得当前带会话参数的 Science URL，再把该 URL 交给同一个隔离 Chrome。菜单 `19` 调用后者；菜单 `11` 负责打开 Science，菜单 `7–10` 则直接进入不依赖 Science 会话的原生 Claude Code。

## 11. Windows Codex 与 Claude Science 审阅闭环

`init-project` 创建唯一 `.science-codex/HANDOFF.md`，并打印 `claude-science-skills/reviewing-codex-science/SKILL.md` 及便携 ZIP 的位置。本地 Claude Science 的真实安装面是其内置 `customize` skill 调用 `host.skills.edit` / `host.skills.publish`，并以 `host.skills.list` / `host.skills.read` 回读为准；该发布动作由用户在 Windows 浏览器对话中明确触发。ZIP 只保留给确实支持标准上传 UI 的 Claude surface，FinalKit 不写其个人 skill 状态。

默认 owner 流是 `Windows Codex 实施 -> Claude Science 只读审阅 -> Windows Codex 核验/修复`。handoff 用标记块限制 Claude Science 的唯一写回范围；科学代码、结果、Source Data、图表、报告和日志在审阅期间保持只读。Claude Science 是工作台，不是固定模型，因此 handoff 还记录当轮 provider/model 与 `different_model_provider / separate_context_only / unknown` 独立性边界，防止把 Codex backend 的第二会话冒充成跨模型审阅。反向复核时，`windows-review` 调用已安装的 Windows Codex：

```text
--ignore-user-config
--ephemeral
--sandbox read-only
```

这条反向 lane 只复核文件，不登录浏览器、不写项目、不共享 token。两个方向都只使用同一 handoff；Claude Science finding 由 Codex 针对权威证据标记 `accepted / rejected / unresolved`，不能凭 Agent 一致性完成科学晋级。浏览器任务仍记录在同一 handoff 的专门小节，由用户在 Codex Desktop 或隔离 Chrome 中显式执行，避免形成第二份交接协议。

## 12. doctor、smoke 与 real test

| 命令 | 本地 gateway | Science | 外部模型 | 目的 |
|---|---:|---:|---:|---|
| `fkctl doctor` | 只读现态 | 否 | 否 | 文件、权限、版本、pin、身份 |
| `fkctl smoke` | DeepSeek/Kimi/GLM 依次启动 | 是 | 否 | 全控制面验收 |
| `fkctl test <provider>` | 是 | 是 | 是 | 最小真实 API/auth 验收 |
| `fkctl test codex` | 是 | 是 | 是（1 个极小请求） | OAuth、Sol 路由、转换、当前账号后端验收；三档映射与 max payload 另做离线单元测试 |
| `fkctl test-codex-tiers` | 是 | 是 | 是（3 个极小请求） | 显式验收当前账号对 Sol/Terra/Luna 与 max 的实际接受；任一失败即失败，不降级 |

`smoke` 的占位 key 只通过匿名 pipe 存活于测试进程，不写 secret 文件。real test 只要求模型返回 `BACKEND_OK`，仍可能产生少量费用。

## 13. 增加 provider 的门槛

只有官方原生支持 Anthropic Messages、认证和模型语义已经核实，才可加入 direct gateway：

1. 在 manager 增加模型、key path 和 profile；
2. 在 gateway 增加精确 HTTPS URL、认证类型和 label；
3. 增加离线 allowlist/identity 测试；
4. 增加真实最小请求入口；
5. 更新 README、代码剖析和第三方说明；
6. 重新执行全新 WSL Build。

需要协议翻译的 provider 应有独立、可审计、固定版本的 connector；不能把翻译逻辑堆入 direct gateway。

## 14. 发布验收

新版本至少验证：

1. PowerShell parser；
2. WSL UTF-16LE、UTF-8、混合编码与 ANSI 清理函数测试；
3. WSL ready/no-distro/UAC/restart/unknown-error/`--web-download` 模拟控制流；
4. `bash -n` installer/fkctl；
5. Python `py_compile`；
6. 三 provider 的离线 gateway smoke 与 allowlist；
7. connector patch 对固定 commit 的 apply/reverse-apply；
8. requirements lock 安装和 `pip check`；
9. 精确 Clear 的备份、unregister 与不存在回读；
10. 全新标准 Ubuntu Build；
11. `doctor` 全部 mandatory check；
12. `smoke` 无外连通过；
13. 浏览器 bridge 启动、WSL reachability、MCP command 和精确停止；
14. 至少一个用户提供的真实 API key 隐藏输入测试；
15. Windows Codex 登录状态和只读 handoff smoke；
16. ZIP 内容审计，不含 key、token、cookie、日志或 WSL VHDX。
