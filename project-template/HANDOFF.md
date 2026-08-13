# Windows Codex → Claude Science 独立审阅交接

> 本文件是当前项目唯一的跨运行时交接面，只记录可核验事实、证据、发现与下一步。禁止写入 API key、OAuth token、cookie、密码、私钥、患者隐私或隐藏思维过程。默认流程为 `Windows Codex 实施 → Claude Science 独立审阅 → Windows Codex 核验/修复`。

## 本轮控制面

- 协作模式：`Windows Codex 实施 → Claude Science 独立审阅 → Windows Codex 核验/修复`
- 审阅轮次：
- 当前唯一写入者：
- Claude Science reviewer provider/model：
- 独立性边界：`different_model_provider` / `separate_context_only` / `unknown`
- Windows 项目根：
- WSL 项目根：
- 审阅对象冻结点（commit、run ID、workflow log 或精确文件+时间）：
- 当前活动计算/写入进程（无则写 `none`）：
- Claude Science 写入边界：仅可替换下方两个 `CLAUDE_SCIENCE_REVIEW` 标记之间的内容；其他科学项目文件只读。

## 研究问题与声明边界

- 用户原始目标：
- 主要科学/技术问题：
- estimand / claim boundary：
- 独立统计单位：
- 对照、重复、配对/池化、批次与主要 contrast：
- 本轮明确不覆盖：

## 权威输入与身份

| 类型 | owner / 精确路径 | 身份、版本或样本映射 | 对本轮结论的作用 |
|---|---|---|---|
| 项目配置 |  |  |  |
| 原始/前驱输入 |  |  |  |
| 样本/cohort/reference |  |  |  |

## Windows Codex 待审交付

- 本轮修改的永久 owner：
- 关键方法与参数决定：
- 拒绝的替代方法及理由：
- canonical result 与 exact Source Data：
- figures / reports / reader claims：
- 实际执行的命令、测试与 workflow log：
- 实际完成的图形 final-size / grayscale 读回：
- Codex 当前主张：
- Codex 已知限制或不确定性：

## 希望 Claude Science 独立审阅什么

> 请先从上述问题、权威输入和冻结对象独立重建证据，再读取并挑战 Codex 的实现叙述。不要以 Codex 自评为结论前提。

1. 

建议按需覆盖：研究设计与统计单位、样本/参考身份、方法与模型、代码 owner/失败边界、结果与 exact Source Data、图表/报告映射、claim 边界、资源与可重复入口、凭证与数据外流风险。

## Windows 浏览器任务（可选）

- 需要浏览器完成的明确任务：
- 允许访问的 URL/站点：
- 是否需要在 FinalKit 隔离 Chrome profile 内登录：
- 禁止读取、上传或发送的内容：
- 期望证据（URL、页面标题、截图路径、下载文件路径）：
- 完成后是否应退出账号：

> 用户日常 Windows 浏览器默认由用户控制。浏览器登录、自动化、上传和外部发送属于独立授权面，不因本 handoff 自动获得许可。

## Claude Science 独立审阅

<!-- CLAUDE_SCIENCE_REVIEW_START -->
- 审阅时间：
- reviewer provider/model：
- 独立性边界：`different_model_provider` / `separate_context_only` / `unknown`
- 审阅结论：`changes_required` / `acceptable_with_minor` / `no_material_issue_found` / `evidence_insufficient`
- 实际读取的证据：
- 未覆盖边界：

| ID | 分类 | 审阅轴 | 精确位置 | 直接证据 | 影响 | 最小可证伪修复或验证 |
|---|---|---|---|---|---|---|

### 需要 Codex 优先核验的问题

1. 

### 未发现问题时的剩余风险

- 
<!-- CLAUDE_SCIENCE_REVIEW_END -->

## Windows Codex 核验与处置

> Claude Science 的发现是审阅证据，不是自动裁决。Codex 必须针对权威输入、owner code、结果或聚焦测试逐项核验。

| Finding ID | 处置（accepted / rejected / unresolved） | 决定性证据 | 修复的最早 owner | 直接验证 |
|---|---|---|---|---|

- 修复后主张与 claim boundary：
- 是否需要下一轮 Claude Science 审阅及理由：
- 最终唯一写入者/活动计算状态：
