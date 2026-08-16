# 相近项目检索、比较与采纳报告

检索快照：**2026-08-16**
对应版本：**Claude Codex Switchboard 3.3.0**

## 1. 目的与方法

本次不是按名称找一个项目直接照搬，而是围绕四个决策检索：

1. Claude Science / Claude Code 如何安全接入不同 API 与官方 Codex 登录；
2. Windows 与 WSL 如何保持 credential owner 独立；
3. Opus、Sonnet、Haiku 及 reasoning 如何显式配置、发现和验证；
4. Codex 与 Claude 如何形成可选、可停止、可核验的多 Agent 工作区。

证据优先级：

- 项目 Git 仓库、README、manifest、LICENSE、核心脚本和测试；
- GitHub API 的 default branch、HEAD commit、归档状态和许可证 metadata；
- 本地临时 clone 的 exact commit 文件级 readback；
- 在临时隔离 HOME 中运行真实 Codex plugin marketplace JSON 命令。

每个项目区分：

- **直接运行时依赖**：固定 URL、commit、版本、许可证和 owner；
- **机制采纳**：独立实现同一类机制，不复制代码；
- **仅作为设计证据**：记录优点和边界，不进入安装包；
- **拒绝**：许可证、认证、产品边界或重复 owner 不适合。

检索结果是该日期的快照；上游以后可能改 commit、许可证、命令或兼容性。

## 2. 定稿结论

### 2.1 项目名

采用 **Claude Codex Switchboard**，仓库建议名 `claude-codex-switchboard`。

不采用 `Codex-Claude-Orchestrator`，原因不是名称不好，而是它会让用户误以为产品只负责 Agent 编排；当前产品的主 owner 还包括 Claude Science 本地身份、四 provider gateway、Windows/WSL auth 隔离、模型/强度 route、Windows Claude 与恢复机制。`Switchboard` 更准确地表达“一个入口、多个独立 owner、显式路由”。

### 2.2 直接集成

| 组件 | Pin | 许可证 | 用途 |
| --- | --- | --- | --- |
| [claude-science-codex-connector](https://github.com/haoyuan-sjtu/claude-science-codex-connector) | `30b26d7c6f097b186bbd228e93a427a731399960` | MIT | WSL ChatGPT/Codex Responses ↔ Claude-compatible 协议 |
| [coredo-eu/codex-claude-orchestrator](https://github.com/coredo-eu/codex-claude-orchestrator) | `0.3.1` / `c996b497c6682f4695b5aa342610527731712c51` | MIT | 可选 Codex 主控、Claude PTY workers |

多 Agent 上游不被 vendor 到仓库，只在用户显式执行 `agents-install` 时 clone 到隔离 integration root。安装器核对 origin、commit、clean tree、LICENSE、自检与真实 plugin JSON surface。

上游把 plugin 定义为 transport，而不是自动 executor-selection policy：安装后需新建 Codex task，并显式调用 `$codex-claude-orchestrator:claude-pty-agents`；若要形成长期项目偏好，应由用户人工审阅并合并 policy snippet。Switchboard 采纳这一权限边界，没有自动写入任何项目 `AGENTS.md`。

### 2.3 已采纳机制及其代码 owner

| 机制 | 来源启发 | Switchboard owner |
| --- | --- | --- |
| Science 本地隔离身份、空 refresh、私有 endpoint | 本机 HGSX 通用思想；connector loopback 结构 | `science_identity.py`、`switch_manager.py` |
| Provider profile 与显式 model catalog | 4xian、SeemSeam、model-switchboard | `switch_manager.py`、`WindowsClaude.ps1` |
| Opus/Sonnet/Haiku 三档独立 Model + Reasoning | connector、Codex catalog、多个 bridge 的 active/pending model 思路 | WSL model-routes schema 3、Windows profile schema 4 |
| 事务切换、owner check、失败恢复 | HGSX 通用机制、SeemSeam recovery、CLI doctor 项目 | `switch_manager.py`、两个 gateway、runtime updater |
| 一次性 auth import，不做持续 credential relay | ahmojo transfer 的 local-first/backup 边界 | Windows stdin export + WSL official Codex validation + 原 runtime 形态/provider 恢复 |
| persistent workers、leases、snapshots、compaction、kill switch | coredo | 上游 plugin；`agents_manager.py` 负责 pin/隔离/provider route |
| 主控最终核验，不按 Agent 投票 | coredo、alexzh3、review-flow | 上游 policy + Switchboard 文档和启动边界 |
| status/doctor 先展示缺项，不把未安装当崩溃 | fuergaosi233、Codex-in-Claude、4xian | `agents status`、`status`、`doctor` |
| Windows/WSL 两套 profile 不互相调用 | 对比多个单栈 bridge 后确定 | `WindowsClaude.ps1` 与 `Switchboard.ps1` 函数边界契约 |

## 3. Provider、配置与桥接类项目

| 项目与快照 | License | 主要优点 | 采纳决定 |
| --- | --- | --- | --- |
| 本机 HGSX Windows Offline AILAB r7，2026-08-07 | proprietary | 本地隔离身份、空 refresh token、私有路由、owner/rollback 思想 | 只独立实现通用机制；未复制代码、binary、Docker layer、credential 或许可内容 |
| [haoyuan-sjtu/claude-science-codex-connector](https://github.com/haoyuan-sjtu/claude-science-codex-connector) `30b26d7…` | MIT | Claude Science 到 Codex Responses 的协议翻译 | 固定 commit 直接集成；应用受控 security patch；仅用于 Codex route |
| [4xian/claude-codex-api](https://github.com/4xian/claude-codex-api) `1d8ba973…` | 未声明 | 多 API 配置切换、模型数组、latency/validity 检查、环境管理 | 采纳“模型发现 + 显式 profile + 配置预览”；不复制代码；不自动按延迟换 route，避免计费与稳定性误判 |
| [fcakyon/claude-codex-settings](https://github.com/fcakyon/claude-codex-settings) `bad8cb6e…` | Apache-2.0 | 跨 Claude/Codex 的大量配置、hooks、agents、Kimi/GLM 支持 | 证明 provider 配置应可移植；未批量导入 skill/config 集合，保持核心 runtime 精简 |
| [SeemSeam/claude_codex_bridge](https://github.com/SeemSeam/claude_codex_bridge) `5dff2368…` | AGPL-3.0（仓库 LICENSE；GitHub metadata 为 NOASSERTION） | 可见多 Agent CLI、provider TUI、active/pending model/thinking、guarded hot reload、Windows/WSL 处理 | 采纳 effective route、pending→commit、恢复提示等概念；不复制代码、不嵌入其 runtime，避免 AGPL 派生和过大的 mux surface |
| [fuergaosi233/claude-codex](https://github.com/fuergaosi233/claude-codex) `dece6e1f…` | MIT | Codex Desktop ↔ Claude app-server adapter、doctor、明确 provider/loop 选择、sanitized config projection | 采纳 doctor 与显式 protocol path 思路；不加入 app-server adapter，当前目标是 Science/Claude provider route |
| [abhishekgahlot2/codex-claude-bridge](https://github.com/abhishekgahlot2/codex-claude-bridge) `320b7599…` | 未声明 | 双向 channel 与 web UI | 仅作实时协作参考；未采纳代码或 web service，许可证和权限面不清晰 |
| [Jayden-X-L/claude-codex-bridge](https://github.com/Jayden-X-L/claude-codex-bridge) `34cf3498…` | MIT | 本地 CLI-first 双向 review loop | 采纳“本地、显式、可核验”原则；未建立第二套 review protocol |
| [AetherX-Technologies/Codex-ClaudeCode-CLI-to-OpenAI-API](https://github.com/AetherX-Technologies/Codex-ClaudeCode-CLI-to-OpenAI-API) `f05d3609…` | MIT | 把 Codex/Claude CLI 包成 OpenAI-compatible API | 拒绝。会把 consumer CLI 登录转换成通用 API service，扩大凭据、条款和网络攻击面 |
| [p90-lover/dsh-plugin-codex-claude-auth](https://github.com/p90-lover/dsh-plugin-codex-claude-auth) `902abf8b…` | PolyForm Noncommercial 1.0.0 | OAuth model discovery、fallback、多账号 usage-aware 切换、refresh serialization | 只参考 refresh serialization / catalog 思路；不采纳 OAuth relay、多账号切换或代码，许可证与 provider 条款边界不适合 |
| [manaflow-ai/subrouter](https://github.com/manaflow-ai/subrouter) `29c7ebb3…` | MIT | 多 ChatGPT/Claude 订阅与 API key 路由、sticky account、usage、GUI | 不采纳多账号 subscription relay。Switchboard 保持“一侧一个官方 owner”，避免账号池和自动 credential fan-out |
| [hannasdev/model-switchboard](https://github.com/hannasdev/model-switchboard) `0925dd2b…` | MIT | session-aware route authority、continuity 与可检查 evidence | 采纳显式 route/effective-route 概念；当前不声称在 stock Codex TUI 内热切模型，provider 变化发生在受控 command boundary |
| [FutureisinPast/mcp-agent-switchboard](https://github.com/FutureisinPast/mcp-agent-switchboard) `9d35157b…` | PolyForm Noncommercial 1.0.0 | MCP 多 CLI 路由、跨模型 debate、共享 context、Windows install | 作为相近命名和功能基准；不复制或集成，避免非商业许可证、MCP 全局控制面和重复 switchboard owner |

## 4. 多 Agent / Orchestrator 类项目

| 项目与快照 | License | 主要优点 | 采纳决定 |
| --- | --- | --- | --- |
| [coredo-eu/codex-claude-orchestrator](https://github.com/coredo-eu/codex-claude-orchestrator) `c996b497…` | MIT | Codex 主控、persistent Claude PTY parent、角色/模型路由、lease、snapshot、compaction、busy limit、kill switch、native fallback、最终核验 | **直接可选集成**。不 fork/删除上游实现；外加 WSL-only、四 provider route、isolated HOME、exact pin、依赖/flag/self-check、可选 role-effort 透传 |
| [wwhsaber/codex-orchestrator](https://github.com/wwhsaber/codex-orchestrator) `f8d1e3a5…` | MIT | Codex-native workers/explorers、外部 CLI lanes、supervisor、长 wait、可见 dashboard、结果与 live log 分离 | 采纳“主会话 architect、非模型 supervisor、结果与 live log 分离、长 wait”设计证据；暂不同时安装第二个全局 orchestrator，避免路由/skill 冲突 |
| [alexzh3/codex-orchestrator](https://github.com/alexzh3/codex-orchestrator) `37e954e7…` | MIT | Claude 主控 Codex，exact prompts/event streams、独立验证、证据裁决而非模型投票 | 采纳最终结果必须由主控对真实 diff/test 核验；不集成反向 Claude→Codex runtime，因为 3.3.0 主模块方向为 Codex→Claude |
| [kingbootoshi/codex-orchestrator](https://github.com/kingbootoshi/codex-orchestrator) `035d5813…` | MIT | tmux Codex workers、后台 job、capture/output/attach、mid-task send、JSONL metadata | 参考 background/resume/steering；不加入 tmux 第二 runtime，coredo PTY owner 已覆盖当前方向 |
| [zm2231/codex-orchestrator](https://github.com/zm2231/codex-orchestrator) `15e09056…` | 未声明 | persistent teams、supervision、HITL、git worktree isolation、workflow/skills | worktree/HITL 作为后续候选；不采纳代码，许可证缺失且会引入独立 orchestration service |
| [Untrivial-ai/agent-orchestrator](https://github.com/Untrivial-ai/agent-orchestrator) `300d5b7d…` | Apache-2.0 | Agent IDE、fleet、worktree/PR、CI fix、merge conflict、dashboard、plugin registry | 学习 fleet 可观察性和 worktree isolation；不引入服务端、数据库、Web UI 和 PR automation，超出本地 Switchboard 权限边界 |
| [keepitmello/claude-codex-meight](https://github.com/keepitmello/claude-codex-meight) `dbac471c…` | MIT | Claude 主控 Codex SDK workers、mid-turn steering、token-free observation、QUESTION protocol、状态/结果持久化 | 采纳 steering/QUESTION/terminal-result 概念；不接入反向 SDK owner，避免与 Codex 主控模块重叠 |
| [tjdwls101010/Codex-in-Claude](https://github.com/tjdwls101010/Codex-in-Claude) `67422aaf…` | Apache-2.0 | background/resume、filtered event log、stop-and-redirect、固定 sandbox/model/effort、doctor | 采纳“resume 必须重申 model/effort/sandbox”和 filtered status 思想；不装第二个反向 manager |
| [jnopareboateng/codex-claude-subagents](https://github.com/jnopareboateng/codex-claude-subagents) `a2ba1956…` | MIT | Codex 中 scoped/resumable Claude workers、gitignored logs | 与 coredo 方向最接近；其 scoped/resume 优点由选中的 coredo lease/snapshot owner 覆盖，因此不并装 |
| [KimYx0207/Claudecode-Codex-Gemini](https://github.com/KimYx0207/Claudecode-Codex-Gemini) `73276baa…` | MIT | Claude/Codex/Gemini 多 CLI、commands、skills、MCP、subagents、hooks、health | 采纳 capability preflight 和 health checks；不批量导入 commands/skills/MCP，不在 3.3.0 增加 Gemini credential owner |
| [aproto9787/codex-bridge](https://github.com/aproto9787/codex-bridge) `678b7c28…` | MIT | Claude team 中 Codex native teammate、按 agent/model 双路由 | 证明 heterogeneous team 路由可行；不集成 Claude-team 反向 bridge，当前模块由 Codex 主控 |
| [Z-M-Huang/claude-codex](https://github.com/Z-M-Huang/claude-codex) `99aa73a5…` | 仓库 metadata 未识别；项目已归档 | sequential multi-AI review、hooks、Codex final gate | 仅作 failure/maintenance 证据：强制每次多模型流水线成本高，归档项目也不适合作新 runtime |

### 4.1 为什么选 coredo 作为第一条运行时

它与当前产品的控制方向最一致：

- Codex 是主会话，不需要倒转现有用户入口；
- Claude workers 通过官方 Claude Code CLI 启动，Switchboard 可以用已有 Anthropic-compatible gateway 路由；
- 进程、lease、root、edit custody 和 kill switch 都有可检查 owner；
- 不要求 hosted coordinator、数据库、Web UI 或 MCP；
- MIT 许可证允许作为显式下载的可选依赖；
- exact commit 的自检、manifest、toggle scripts 和 test surface 可读。

上游 README 把 native Windows 列为非目标；Switchboard 没有把它伪装成 native Windows。实际 integration 运行在 Ubuntu WSL2，Windows 只提供可见启动入口和路径转换。

### 4.2 没有“预先做窄”的具体含义

3.3.0 没有把多 Agent 固定成“只做 review”或“永远三段式流水线”。安装后上游完整 skill 仍可由 Codex按任务选择：

- 主会话直接完成小任务；
- 单一 persistent Claude parent；
- Haiku discovery/triage；
- Sonnet implementation/debugging；
- Opus review/security；
- Fable exceptional long-horizon route；
- Claude unavailable 后经 custody transfer 使用 native Codex roles。

Switchboard 只固定安全外壳：可选安装、provider route、credential owner、exact upstream、dependency check、status 与 kill switch。上游 role map 在同一模型族内可能使用不同 effort；API provider 的 `auto` 按具体模型校验，Codex 在本地 cache 能力已知时前置校验、未知时明确标记。显式固定值覆盖该 tier。旧 Kimi/GLM provider-wide 配置会保留模型并安全迁移不兼容 effort 到 `auto`。

## 5. Handoff、Review 与 Session 类项目

| 项目与快照 | License | 主要优点 | 采纳决定 |
| --- | --- | --- | --- |
| [OpenMOSS/claude-codex-handoff](https://github.com/OpenMOSS/claude-codex-handoff) `baab7913…` | MIT | JSONL 双向流、cursor、lock、doctor、跨 session handoff | **明确不进入产品**。用户要求去除未使用 handoff/skill；它会在每个项目增加第二状态协议 |
| [gongchen0916/codex-claude-review-flow](https://github.com/gongchen0916/codex-claude-review-flow) `741095d4…` | MIT | 本地 JSONL risk/done/pass、环境清理、可审计 review | 采纳环境去敏和“pass 需证据”原则；不加入 JSONL review ledger |
| [ahmojo/codex-claude-transfer](https://github.com/ahmojo/codex-claude-transfer) `862fa52f…` | MIT | local-first session bundle、backup、secret scanning、不直接改 SQLite | 一次性 auth import 借鉴 local-first/backup/fail-closed 边界；不传输完整会话，不建立同步或 bundle format |
| [abhishekgahlot2/codex-claude-bridge](https://github.com/abhishekgahlot2/codex-claude-bridge) `320b7599…` | 未声明 | 双向实时 channel、Web UI | 仅用于理解实时协作 UX；不加入常驻 Web service |
| [Jayden-X-L/claude-codex-bridge](https://github.com/Jayden-X-L/claude-codex-bridge) `34cf3498…` | MIT | CLI-first review loop、local execution | 与最终“本地、显式、核验”原则一致；没有单独安装 |

删除的本地文件：

- `claude-science-skills/reviewing-codex-science/SKILL.md`；
- `claude-science-skills/reviewing-codex-science.zip`；
- `project-template/HANDOFF.md`；
- `project-template/windows-codex-review-prompt.zh-CN.md`。

删除依据是这些文件没有 runtime writer/reader，且选中的多 Agent plugin 已提供真正可执行、可停止、可验证的工作面。历史可由 Git 恢复。

## 6. 实际验证

### 6.1 上游文件级检查

对 coredo exact commit 检查了：

- root `LICENSE`；
- marketplace/plugin manifest；
- `scripts/self-check.zsh`；
- `toggle-agents.zsh`；
- worker launch、assign、rotate/retire、runtime-lib；
- model/effort environment handling；
- `--agents`、`--model`、`--effort`、`--session-id`、`--resume`、`--settings`、`--setting-sources`、`--strict-mcp-config`、`--disallowedTools`；
- registration、lease、health、snapshot、compaction 和 native fallback references。

### 6.2 真实 Codex plugin smoke

在临时 `/tmp/switchboard-plugin-smoke-*` HOME 中，使用本机 WSL Codex CLI 运行：

```text
codex plugin marketplace add <exact-local-checkout> --json
codex plugin marketplace list --json
codex plugin list --marketplace codex-claude-orchestrator --available --json
codex plugin add codex-claude-orchestrator@codex-claude-orchestrator --json
codex plugin list --json
```

返回的实际对象确认：

- marketplace name = `codex-claude-orchestrator`；
- plugin ID = `codex-claude-orchestrator@codex-claude-orchestrator`；
- version = `0.3.1+codex.20260809190528`；
- installed/enabled = `true`；
- source 指向 exact local checkout。

该 smoke HOME 不含现有 auth，完成后删除；没有读取、修改或调用 Windows auth、WSL production auth、Claude Science 或 provider key。

### 6.3 Switchboard 契约

新增或扩展的直接验证：

- `agents_manager_contract.py`：exact pin、selector、无 `shell=True`、环境去敏、无 `/mnt/c` PATH 泄漏、隔离 HOME、read-only status；
- `windows_entry_contract.py`：可选 Agent actions、project path、四 provider launcher、Windows auth 不注入与 Science identity 不修改边界；
- `direct_gateway_contract.py`：Fable→Opus 和三档 reasoning；
- `runtime_control_contract.py`：provider-routed Codex environment、隔离 Linux login 与 signal-safe auth migration；
- `model_routes_contract.py`：route update 共锁、跨 host redirect 拒绝与 Ctrl-C/TERM rollback；
- `installer_update_contract.sh`：`agents_manager.py` 进入 package rollback 集合，同时 auth/model route 的并发新状态不被旧快照覆盖；
- PowerShell parser、`bash -n` 与 Python compile。

## 7. 许可证与认证决定

### 7.1 可以直接依赖

- MIT / Apache-2.0 上游仍需 exact pin、notices 和兼容 smoke；
- 3.3.0 只直接运行 MIT connector 与 MIT coredo plugin；
- Switchboard 原创代码与文档现已由仓库根目录 [MIT License](../LICENSE) 明确授权；第三方组件仍分别遵循本报告和 notices 记录的许可证。

### 7.2 只学机制、不复制代码

- AGPL-3.0 项目 SeemSeam；
- PolyForm Noncommercial 项目 p90 与 FutureisinPast；
- 未声明 license 的 4xian、zm2231、abhishekgahlot2；
- proprietary HGSX。

报告记录它们改变了哪个设计决定，但没有复制实现、资源、prompt、配置集合或 binary。

### 7.3 不做 credential relay

以下能力有技术价值但未采纳：

- 多账号 subscription pool；
- consumer CLI → 通用 OpenAI API server；
- 持续 Windows↔WSL auth 同步；
- OAuth cookie/token relay；
- 自动按额度或延迟切账号；
- 把未知 Claude credential 改成本地 identity；
- 在没有 owner proof 时按进程名 kill。

原因是这些机制会扩大 provider 条款、billing、private token、并发刷新和故障恢复边界。Switchboard 的替代方案是：每个平台保留一个官方 Codex owner，允许一次性显式导入，之后继续独立。

## 8. 后续可吸收但不应混入 3.3.0 核心的能力

这些是已检索、有明确来源、但需要单独设计和权限的候选：

| 候选 | 来源 | 进入前必须证明 |
| --- | --- | --- |
| 多 orchestrator catalog | wwhsaber、alexzh3、kingbootoshi | plugin/skill 冲突可隔离，用户能显式选择 owner，卸载可恢复 |
| Native Codex workers 与 Claude workers 混编 | coredo、wwhsaber、aproto9787 | edit custody、并发上限、model/effort 与 provider route 不漂移 |
| Worktree isolation | zm2231、Untrivial agent-orchestrator | 不自动建 branch/PR，不覆盖 dirty worktree，清理有 exact owner |
| 非模型 supervisor 与可见 dashboard | wwhsaber、kingbootoshi | 日志不把 routine output 塞回主模型，状态与结果文件分离 |
| Mid-turn QUESTION / redirect | Meight、Codex-in-Claude | terminal state、resume ID 和权限重申可验证 |
| Gemini/OpenCode 等新 CLI owner | KimYx0207、wwhsaber | 官方安装/auth、独立 credential owner、模型目录和 Windows/WSL 隔离 |
| Session export/import | ahmojo | secret scanning、版本 schema、备份、无数据库破坏、用户明确授权 |

这些候选没有被写成“已支持”。3.3.0 先提供一个完整、可选、可停、可验证的多 Agent owner，同时在代码中保留明确的 `fkctl agents` 边界，后续可增加 catalog，而不需要改 Science、Windows Claude 或 provider credential owners。

## 9. 最终采纳清单

3.3.0 实际落地：

1. 产品与入口改名为 Claude Codex Switchboard；
2. 保留旧 credential/runtime namespace 作为升级兼容层；
3. 删除无 runtime owner 的 Claude Science skill/handoff 文件；
4. Windows Claude profile 名缩短；
5. Windows/WSL 四 provider 都使用三档独立 Model + Reasoning；
6. Codex 本地 cache 显示最近广告的 supported reasoning levels，并提供 `auto` role-effort 透传；
7. Sonnet 默认/输入与 Opus 分离；
8. WSL Fable alias 映射到 Opus；
9. 一次性 Windows Codex auth → WSL，之后仍独立；
10. 新增 WSL-only 多 Agent 模块、四 provider route、pin、status、enable/disable/owned-worker stop；
11. 完整 README、操作手册、代码剖析、第三方通知和本报告；
12. 离线契约与临时隔离 plugin smoke。

未采纳：

- 未经许可的代码复制；
- 多账号/OAuth relay；
- consumer subscription 转 API server；
- 全局 Claude/Codex 进程 kill；
- 自动安装所有调研项目；
- 第二套 handoff/JSONL ledger；
- 常驻 Web dashboard/service；
- 自动 branch/PR/merge；
- Windows/WSL auth 持续同步。
