# openvela AI Skills

[English](README.md) | **中文**

面向 [openvela](https://github.com/open-vela)（基于 NuttX 的 RTOS）的 AI 开发技能集。每个 Skill 为 AI 助手提供嵌入式系统开发、调试和优化的专业知识。

## 技能列表

| 技能 | 说明 |
|------|------|
| [codesize](skills/codesize/) | 分析固件二进制大小，支持多核/多架构（ARM/Xtensa/RISC-V） |
| [executor](skills/executor/) | 管理持久化交互式 CLI 进程（REPL、调试器、QEMU、NuttX 模拟器） |
| [kconfig-tweak](skills/kconfig-tweak/) | 命令行修改 NuttX/Linux .config 文件，无需交互式 menuconfig |
| [memdump](skills/memdump/) | 分析 NuttX 运行时 memdump 日志，检测内存泄漏和高消耗模块 |
| [pcm-audio](skills/pcm-audio/) | 分析 PCM 音频质量问题 — 削波、静音、爆音、底噪、周期性失真 |
| [skill-creator](skills/skill-creator/) | 创建新技能的指南，扩展 AI 助手能力 |
| [tmux](skills/tmux/) | 远程控制 tmux 会话，用于交互式 CLI（python、gdb 等） |
| [openvela-quickstart](skills/openvela-quickstart/) | openvela 开发环境一键搭建 — 环境检测、依赖安装、智能选源、编译运行模拟器 |

## 快速开始

将本仓库克隆到 openvela 项目根目录的 `.claude/` 下：

```bash
git clone https://github.com/open-vela/ai-skills.git .claude
```

AI 助手会自动发现并在相关任务中使用这些技能。

## 技能结构

每个技能遵循标准目录布局：

```
skills/<技能名>/
├── SKILL.md              # 技能定义（必需）
├── scripts/              # 辅助脚本（可选）
├── references/           # 参考文档（可选）
└── LICENSE               # 许可证文件（可选，默认使用仓库许可证）
```

`SKILL.md` 使用 YAML front matter 定义元数据：

```yaml
---
name: 技能名称
description: 何时以及如何使用此技能
---
```

## 创建新技能

使用 `skill-creator` 技能交互式生成新技能，或按照上述结构手动创建。

## 贡献

欢迎贡献。请提交 Issue 或 Pull Request。

添加新技能时：
1. 在 `skills/` 下创建一个描述性名称的目录
2. 编写 `SKILL.md`，包含清晰的触发条件和分步说明
3. 如需要，在 `scripts/` 中添加辅助脚本
4. 在 `references/` 中添加领域知识参考文档

## 许可证

Apache-2.0
