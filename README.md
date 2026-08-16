# ScienceCodexFinalKit

面向普通 Windows 用户的 Claude Science / Claude Code / Codex / WSL2 多模型接入工具。

- [中文项目说明与五步安装](README.zh-CN.md)
- [完整操作手册](operation.md)
- [代码与安全边界](docs/CODE_WALKTHROUGH.zh-CN.md)
- [第三方组件与许可证](THIRD_PARTY_NOTICES.md)

最短入口：完整解压或 clone 到稳定的本地目录，以普通 Windows 用户双击 `Build.cmd`；安装完成后配置至少一种 provider，先执行对应 `test-*`，再从 `SwitchModel.cmd` 启动 Claude Science。

> 发布状态：3.2.3 当前位于 `fix/science-entry-v3.2.1` 分支；默认 `main` 尚未更新。仓库也尚未声明项目级 `LICENSE`。代码已经去除固定用户名和固定安装盘依赖，但在合并/tag 与许可证选择完成前，不应把当前分支描述为正式稳定发行版。
