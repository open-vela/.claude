---
name: contest-log-collector
description: "为 openvela AI 大赛归集 AI Coding 日志,采用工作区感应 + 自动入仓架构。只在 openvela 工作区内(当前目录向上能找到 .repo/)才采集;工作区内的对话结束时自动写入选手仓 logs/<github_login>/(只写文件,不自动 commit/push,选手自己 git push);工作区外的对话(含个人项目)完全不采集。手动工具 contest-snapshot / export-session.py 仍可选,用于查看清单或补导。Trigger: 大赛 AI 日志收集、contest log、AI Coding 日志、打包对话、archive session、归档会话、export session、把会话存到仓库、保存这次对话、把这个 session 提交、contest-snapshot."
---

# Contest Log Collector — openvela AI 大赛 AI Coding 日志归集

为 openvela AI 大赛提供 AI Coding 日志归集能力,**工作区感应、对话结束自动入仓**。

## 核心架构

```
       ┌─────────────────────────────────────────────────────┐
       │ 门控: 当前目录向上能找到 .repo/ ?                    │
       │   否 → 不在 openvela 工作区 → 完全不采集             │
       │   是 → 在 openvela 工作区 → 进入采集                 │
       └─────────────────────────────────────────────────────┘
                               │ (在工作区内)
                               ↓
       ┌─────────────────────────────────────────────────────┐
       │ 对话结束自动写入: demo 仓 logs/<login>/              │
       │ 只写文件,不自动 commit/push                         │
       │ 选手 git push 时跟代码一起上 GitHub                  │
       └─────────────────────────────────────────────────────┘
```

**关键安全特性**: 用"是否在 openvela 工作区(`.repo/`)内"作为隐私门控 —— 选手在个人项目(工作区外)跟 AI 聊的对话**完全不采集**;只有在比赛工作区内的对话才会自动写入选手仓 `logs/`,且工具自身**永远不会 git push**,上传由选手控制。

## 自动入仓(默认,无需操作)

在 openvela 工作区内跟 AI 工具(Claude Code / OpenCode / Codex)对话,会话结束时日志会**自动写入**选手仓的 `logs/<github_login>/`。选手只需在提交代码时一起 push:

```bash
git add logs/
git commit -s -m "logs: capture session"
git push
```

## (可选)手动补导

正常流程不需要,但你可以用手动工具查看清单或补导某次会话。

### 1. 短命令 `contest-snapshot`(`install.sh` 装在 `~/.local/bin/`)

```bash
# 列出本机已采集的所有 session
contest-snapshot --list

# 补导某个 session (加 --confirm 真写入)
contest-snapshot --session <session-id> --confirm

# 按日期补导
contest-snapshot --today --confirm
contest-snapshot --since 2026-06-15 --confirm
```

### 2. 完整路径 fallback(`~/.local/bin` 不在 PATH 时)

```bash
python3 ../.claude/skills/contest-log-collector/tools/export-session.py --list
```

### 3. 自然语言 / Slash command

对 AI 说"补导这次会话",或在 Claude Code 里跑 `/contest-snapshot`,AI 会调用 `export-session.py` 预览,确认后加 `--confirm` 写入。

> ⚠️ 手动工具的 `--confirm`:不加时只预览不写文件。手动补导仅用于补救/管理,正常对话已自动入仓。

## 目录结构

```
contest-log-collector/
├── SKILL.md                       # 本文件
├── commands/
│   └── contest-snapshot.md        # slash command 定义
├── onboarding/
│   ├── install.sh                 # 主入口
│   ├── verify-setup.sh            # 健康检查
│   ├── JUDGE_GUIDE.md             # 评委指南 (装到选手仓根)
│   └── USAGE.md                   # 选手手册 (装到选手仓根)
├── adapters/
│   ├── opencode/collector.js      # OpenCode V1 plugin
│   ├── shared/                    # Claude Code + Codex 共用
│   │   ├── snapshot_core.py
│   │   ├── get_github_login.py
│   │   └── get_github_login.js
├── schema/
│   ├── event.schema.json
│   └── manifest.schema.json
└── tools/
    ├── export-session.py          # 选手主动导出工具 (核心)
    ├── render-log.py              # 评委渲染工具
    └── validate-log.py            # 防作弊校验
```

## 组委会预装(建仓时)

```bash
git clone --depth 1 -b dev-ai-contest-2026 \
  https://github.com/open-vela/.claude.git /tmp/clt
cd <选手仓路径>
bash /tmp/clt/skills/contest-log-collector/onboarding/install.sh \
  --team-id contest2026-042 \
  --github-login <login>
git add -A && git commit -m "init: contest-log-collector preinstalled"
git push
```

`install.sh` 安装项:

- **本机**:
  - `~/.claude/contest-collector.env` — 选手身份(TEAM_ID + GITHUB_LOGIN)
  - `~/.claude/contest-shared/` — snapshot core + 全局 hook 脚本
  - `~/.claude/settings.json` — 注册 Stop / SessionEnd hook(deep merge,保留原有配置)
  - `~/.claude/contest-collector-staging/` — staging 区
- **选手仓**:
  - `.claude/shared/` — snapshot core(给 export 工具用)
  - `.claude/commands/contest-snapshot.md` — slash command
  - `.opencode/plugins/contest-collector.js` — OpenCode plugin
  - `../.claude/skills/contest-log-collector/tools/export-session.py` + `render-log.py` + `validate-log.py`
  - `schema/event.schema.json` + `manifest.schema.json`
  - `JUDGE_GUIDE.md` — 评委指南
  - `USAGE.md` — 选手手册
  - `.gitignore` — 已加日志相关排除

## 数据契约

JSONL 每条事件必含字段:

- `schema_version` — 当前 1.0
- `session_id` — AI 工具内部 session ID
- `team_id` — 对应仓库的官方匿名编号
- `github_login` — 当前组员 GitHub username
- `tool` — opencode / claude-code / codex / kiro
- `seq` — session 内单调递增(防作弊核心)
- `ts` — ISO 8601 UTC 时间戳
- `role` — user / assistant / tool / system

## 安全特性

- **工作区门控**: 只在 openvela 工作区(向上能找到 `.repo/`)内采集,工作区外的对话(含个人项目)完全不采集
- **不自动上传**: 自动入仓只写本机仓内 `logs/` 文件,工具自身从不 `git push`,上传由选手控制
- **自动脱敏**: 默认正则覆盖 `sk-*` (API key) / `ghp_*` (GitHub token) / `Bearer *` (auth header)
- **TEAM_ID 缺失主动拒绝**: 防止数据归属错乱
- **多人组天然安全**: 按 `<github_login>/` 一人一目录,组员之间互不干扰

## 工具兼容性

| 工具 | 形态 | 状态 |
|------|------|------|
| Claude Code (大赛主推) | CLI / AIoT-IDE 内嵌 | ✅ 完全支持(包括 slash command) |
| AIoT-IDE | VS Code fork + Claude Code 插件 | ✅ 完全支持 |
| OpenCode | CLI / TUI / VS Code 扩展 | ✅ 完全支持 |
| MiMo Code | CLI / TUI (基于 OpenCode) | ✅ 完全支持 |
| Codex | CLI | ✅ 自然语言触发 |
| DeepSeek Harness (dsh) | CLI / Web / TUI | ✅ 本地会话日志 backfill |
| Kiro | IDE | ⏸️ 暂未实现 |

## License

Apache 2.0,跟仓库其他 skills 一致。
