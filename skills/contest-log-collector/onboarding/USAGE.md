# 选手使用手册 — AI Coding 日志归集

<!-- install.sh 会把本文件复制到选手仓根目录,文件名 USAGE.md -->

## 这份文档是什么

你拿到组委会建好的 demo 仓后,日志归集工具已经**预装**好了。本手册告诉你:

1. 拿到仓库后第一件事做什么
2. 怎么开始用 AI 工具开发
3. **对话自动入仓与手动补导**(自动完成,手动可选)
4. 出了问题怎么办

> **核心架构**: 工具采用**工作区感应**设计 — 只在 openvela 工作区内(能向上找到 .repo/)才收集；在工作区内对话结束后，日志会自动写入比赛仓 logs/ 目录(只写文件,不自动 commit/push)。工作区外的对话(含个人项目)完全不收集，从根源保护隐私。

> 🌳 **关于 `.claude/`**: 这个仓是大赛工具仓,**已经登记在 manifest 里**,你执行 `repo sync` 时会自动拉到 `<manifest 根>/.claude/`,跟你的 demo 仓是 **sibling**(平级目录)。你不需要 git clone 它,也不需要修改它。

---

## 1. 拿到仓库后必做的 3 件事

### 1.1 跑 install.sh(只装一次)

按官方提交指南做完 `repo init` + `repo sync` 后,在你的 demo 仓里跑一次:

```bash
cd <你的 demo 仓>     # 例如 contest2026-042-app
bash ../.claude/skills/contest-log-collector/onboarding/install.sh \
  --team-id contest2026-042-app \
  --github-login <你的 GitHub username>
```

`install.sh` 会**自动创建** `~/.claude/contest-collector.env`(身份信息文件,内容 TEAM_ID + GITHUB_LOGIN),你不需要自己建。可以跑完后 `cat ~/.claude/contest-collector.env` 验证一下:

```
TEAM_ID=contest2026-XXX
GITHUB_LOGIN=<你的 GitHub username>
```

> ⚠️ **如果 GITHUB_LOGIN 不是你**: 改成你自己的 username,否则你的 log 会归到队友名下。

### 1.2 跑健康检查脚本

`verify-setup.sh` 在 manifest 拉下来的工具仓里,从你 demo 仓内用相对路径跑:

```bash
bash ../.claude/skills/contest-log-collector/onboarding/verify-setup.sh
```

任何 `[FAIL]` 项都按提示修;不会的发组委会群求助。

### 1.3 (可选) 看一眼 staging

```bash
ls ~/.claude/contest-collector-staging/<your-github-login>/
```

第一次进来应该是空的。第一次结束 AI session 后,会出现 `<date>/<tool>__<sid>.jsonl`。

---

## 2. 启用 AI 工具

支持 **4 种工具**,挑顺手的一个用就行,组委会已经把全局 hook 装好了。

### 2.1 大赛官方主推: Claude Code (CLI 或 AIoT-IDE 内嵌)

#### 用 AIoT-IDE (推荐)

1. 装 AIoT-IDE: 看大赛官方 IDE 使用文档
2. 在 AIoT-IDE 里**任意位置**(包括桌面、子目录、仓外)打开 Claude Code 插件,开始对话
3. 关闭对话 → 自动落 staging

#### 用 Claude Code CLI

```bash
claude   # 任意目录都可以,不必非得在仓里
```

退出时(`/exit` 或 Ctrl+D)自动落 staging。

### 2.2 OpenCode (CLI / TUI / VS Code 扩展)

```bash
opencode
```

OpenCode V1 plugin 已预装,session 结束自动落 staging。

### 2.3 Codex CLI

```bash
codex
```

Stop hook 自动落 staging。

### 2.4 多人组队怎么协作

**每位组员各自做以下事情**:

1. 各自 clone 自己的本地副本
2. **改 `~/.claude/contest-collector.env` 里的 GITHUB_LOGIN 为自己的 username**(很重要!)
3. 跟 AI 工具协作

各自的 staging 互不干扰,各自导出自己的 session 即可。

---

## 3. 对话自动入仓与手动补导

工具现在支持**自动入仓**,你不再需要手动执行打包命令。

### 3.1 自动入仓流程 (默认)

1. **工作区识别**: 只要你在 openvela 工作区内(即当前目录向上能找到 `.repo/` 目录)启动 AI 工具。
2. **自动写入**: 对话结束(退出工具)时,日志会自动写入比赛仓的 `logs/<your-github-login>/` 目录下。
3. **手动提交**: 工具只写文件,**不会**自动执行 git 操作。你仍需手动提交:

```bash
git add logs/
git commit -s -m "logs: capture session"
git push
```

### 3.2 手动补导与管理 (可选)

如果你需要重新导出、查看清单或有选择性地补导,手动工具仍然可用。3 种方式选一个:

#### 方式 A: 自然语言

跟 AI 说一句:

- "archive this session into the contest repo"
- "把刚才的会话存到比赛仓库"
- "package this conversation"
- "归档对话"

AI 会跑 `../.claude/skills/contest-log-collector/tools/export-session.py --latest --confirm` 来执行手动导出。

#### 方式 B: Slash Command (Claude Code)

```
/contest-snapshot
```

效果同上。

#### 方式 C: 直接跑脚本

`install.sh` 装好了短命令 `contest-snapshot`,用于查看状态或手动导出:

```bash
# 1. 列出所有 session,确认状态
contest-snapshot --list

# 2. 预览特定 session (不写文件)
contest-snapshot --session <session-id>

# 3. 加 --confirm 手动导出(例如误删了 logs/ 需要重导)
contest-snapshot --session <session-id> --confirm
contest-snapshot --all --confirm
```

> ⚠️ **提示**: 正常流程下,你只需要关注 `git push`。手动工具仅用于补救或管理。

### 3.3 补回历史对话 (--backfill)

如果你在**安装 hook 之前**就开始用 AI 工具开发了,历史对话仍保存在本机的工具数据里。用以下命令一键补回:

```bash
# 在选手仓内执行,补回所有支持工具的历史
contest-snapshot --backfill

# 只补回单一工具(可选)
contest-snapshot --backfill --source claude     # Claude Code (~/.claude/projects/)
contest-snapshot --backfill --source opencode   # OpenCode SQLite
contest-snapshot --backfill --source mimocode   # MiMo Code SQLite
contest-snapshot --backfill --source cursor     # Cursor state.vscdb
```

命令会自动扫描对应工具的历史数据,把还没采集过的会话补导进 `logs/`。跑完后:

```bash
git add logs/ && git commit -s -m "logs: backfill history" && git push
```

> 幂等:多次跑不会产生重复。已采集过的会话会自动跳过。
>
> **Cursor 特别说明**: Cursor 没有实时 hook,只能靠 backfill 补导。建议 Cursor 用户开发时**每天跑一次** `contest-snapshot --backfill --source cursor` 增量补进 logs。

---

## 4. 隐私保护 — 这个工具到底采集什么

### 4.1 哪些**会**被采集

工具只在 **openvela 工作区内**(能向上找到 `.repo/` 目录)激活。

- 只要在工作区内跟 AI 工具聊天,对话会被采集,并在结束时自动写入比赛仓 `logs/`。
- 采集只在你本机进行,**工具自身永远不会上传** — 是否 push 完全由你控制。

### 4.2 哪些**会**进比赛仓 (= 评委能看到)

**只有在 openvela 工作区内的对话**。

- 工作区内的对话结束时会自动写入 `logs/`(只写文件,不自动 commit/push)。
- **工作区外的对话(如你的个人项目)完全不采集**,不会进比赛仓。


### 4.3 字段清单 (会被记录的内容)

| 字段 | 内容 |
|------|------|
| `text` | 你跟 AI 的对话正文 |
| `thinking` | AI 的思考过程(如工具暴露) |
| `tool_name` / `input` / `output` | AI 调用的工具(read/edit/bash 等) |
| `model` / `tokens_in/out` | 用了哪个模型、token 用量 |
| `seq` | session 内单调递增序号(防作弊) |

### 4.4 自动脱敏

不论是否导出,所有事件都会自动脱敏:

- `sk-*` (OpenAI / Anthropic API key)
- `ghp_*` (GitHub Personal Access Token)
- `Bearer xxx` (HTTP Authorization header)

### 4.5 哪些**不会**被采集

- ❌ 你的 shell 历史 / 环境变量 / 文件系统其他内容
- ❌ AI 工具配置以外的本地文件
- ❌ 浏览器/桌面/其他应用的活动
- ❌ 你的 API key(自动脱敏)

只采集 **AI 工具自己 session transcript 里的内容**。

### 4.6 查看自己导出后的内容

```bash
# 终端预览(彩色)
python3 ../.claude/skills/contest-log-collector/tools/render-log.py logs/<your-github-login>/

# 生成 HTML 报告(浏览器打开)
python3 ../.claude/skills/contest-log-collector/tools/render-log.py logs/<your-github-login>/ \
  --format html --out my-report.html
```

---

## 5. 验证工具在工作

### 5.1 看 staging 是否在累积

```bash
# 跟 AI 协作几轮后,新开终端跑:
ls -lt ~/.claude/contest-collector-staging/<your-github-login>/<today>/
```

应该看到 `.jsonl` 文件,大小随对话进展增长。

### 5.2 看 stderr 提示

每次 AI session 结束,collector 会在 stderr 输出:

```
[session-log] captured 3 event(s) -> .../claude-code__abc.jsonl
              (remember to 'git add logs/' when committing)
```

### 5.3 export 后自检合规性

```bash
python3 ../.claude/skills/contest-log-collector/tools/validate-log.py logs/
```

应该输出 `ALL OK`。报错大概率是工具 bug,发组委会群报。

---

## 6. FAQ

### Q1: 对话会自动上传吗?

**绝对不会**。工具只会在检测到你处于 openvela 工作区时,自动将日志写入你本地比赛仓的 `logs/` 目录。**物理上它不会自己执行 git push**,上传由你完全控制。

### Q2: 我在个人项目里跟 AI 聊了私事,会泄漏吗?

**完全不会**。工具感应到你在非 openvela 工作区(没有 `.repo/` 目录)时,会保持静默,不采集任何内容。

### Q3: 我能改 staging / logs 里的内容吗?

`../.claude/skills/contest-log-collector/tools/validate-log.py` 会检测 seq 缺号、跨字段不一致、manifest 与文件对不上等手脚,**改 log 等于作弊**。

但**删除整个 session**(在 `git commit` 前删掉 `logs/` 下对应文件,或删掉 staging 里的文件)是允许的 — 只要还没 push,评委就看不到。

### Q4: 我能临时关掉日志收集吗?

最简单的办法:在 **openvela 工作区外**(即找不到 `.repo/` 目录的地方)跟 AI 工具对话,工具不会采集任何内容。

### Q5: 截止时刻怎么办?

截止时间一到,组委会会:

1. 把仓库权限从 write 降为 read(你不能再 push)
2. 触发最终 archive

**截止前几小时**:

```bash
# 看看还有多少没导出的 session
python3 ../.claude/skills/contest-log-collector/tools/export-session.py --list

# 一次性全导出
python3 ../.claude/skills/contest-log-collector/tools/export-session.py --all
git add logs/ && git commit -s -m "logs: final batch" && git push
```

### Q6: 工具有 bug / 没采到怎么办?

按以下顺序处理:

1. `bash ../.claude/skills/contest-log-collector/onboarding/verify-setup.sh` 先看健康检查
2. `cat ~/.claude/contest-collector-staging/<your-login>/errors/*.err` 看是不是有错误日志
3. 实在搞不定,在大赛技术支持群报问题

### Q7: 我从仓的子目录(比如 `cd src && claude`)启动 AI 行不行?

**完全行**。新架构下 hook 是**全局**的,不论你 cwd 在哪,只要跟 Claude Code/OpenCode/Codex 聊天,都会进 staging。

### Q8: 我有多个 demo 仓(主仓 + 子模块),log 怎么归?

按大赛规则,**所有 log 统一汇集到主 demo 仓**。子模块仓不需要装日志工具,在主仓里跑 `export-session.py` 即可。

### Q9: 我用 ChatGPT / Cursor / Cody / 其他工具行不行?

本届大赛官方支持:

- Claude Code(主推,含 AIoT-IDE 内嵌) — 实时 hook
- AIoT-IDE — 实时 hook
- OpenCode — 实时 hook + SQLite backfill
- Codex — 实时 hook
- MiMo Code — 实时 hook + SQLite backfill
- **Cursor — 仅 SQLite backfill**(见 3.3 章节)

ChatGPT / Cody / 其他没列出的工具**不支持**,产生的对话无法进入 staging。

**Cursor 特殊说明**:
- 只支持 `--backfill --source cursor` 从本机 Cursor SQLite 补导历史,**没有实时 hook**
- 只解析 Cursor 当前 Composer 格式(2025 年后新版),旧格式(2024 年前 aichat.chatdata / aiService.prompts)不支持
- 每次 backfill 时,manifest 里会记录 Cursor state.vscdb 的 SHA256 + mtime,便于事后审计
- 想让日志更完整?配合 Claude Code / OpenCode / MiMoCode 用,它们有实时 hook,不会漏

### Q10: 我用了 Anthropic API / OpenAI API 直接调,行吗?

不行。直接调 API 的对话**不在 session transcript 里**,工具采不到。必须用上面 4 个工具之一。

---

## 7. 工具自带文件清单

`repo sync` 拉下来的工程长这样。**install.sh 不会在你的 demo 仓里复制任何文件** — 工具源都在 `.claude/` 工具仓里直接调用,你的 demo 仓**只会出现 `logs/` 目录**(且只在你 `--confirm` 导出后):

```
<你的工作树>/                            # repo init 拉到的工作树根
├── .repo/                              # repo 工具元数据
├── .claude/                            # 大赛工具仓 (open-vela/.claude, 由 manifest 拉下来)
│   └── skills/contest-log-collector/
│       ├── adapters/                   # snapshot core / opencode plugin 源 (install 会复制到 ~/.claude/)
│       ├── commands/                   # slash command (Claude Code 自动找 ~/.claude/)
│       ├── tools/                      # export / render / validate (选手 + 评委直接调用)
│       ├── schema/                     # JSONL 契约 (validate-log.py 自动找)
│       └── onboarding/
│           ├── install.sh              # 一次性安装脚本 (装到 ~/.claude/)
│           ├── verify-setup.sh         # 健康检查
│           ├── USAGE.md                # 本文件
│           └── JUDGE_GUIDE.md          # 评委指南
├── nuttx/  apps/  vendor/  ...         # openvela 全量源码
└── <你的 demo 仓>/                      # 例如 contest2026-042-app
    ├── (你的代码、README、配置等 — install.sh 不动)
    └── logs/                           # 在工作区内使用 AI 工具后会自动出现
        └── <your-github-login>/
            ├── manifest.json
            └── <date>/<tool>__<sid>.jsonl
```

组委会的 install.sh 把**全部工具状态都装在你的 home 目录**,不进 demo 仓:

```
~/.claude/
├── settings.json                       # 注入了 Stop/SessionEnd hook
├── contest-collector.env               # 你的身份 (TEAM_ID + GITHUB_LOGIN)
├── contest-shared/                     # 全局 hook
│   ├── snapshot_core.py
│   ├── get_github_login.py
│   └── contest-snapshot.sh
└── contest-collector-staging/          # staging 区 (你的所有 AI 对话)
    └── <your-github-login>/
        ├── manifest.json
        └── <date>/<tool>__<sid>.jsonl

~/.config/opencode/plugin/
└── contest-collector.js                # OpenCode 全局 plugin
```

**全局 hook 不会自己 push**,只在你电脑本地写文件。push 由你自己控制。
**整个 demo 仓里只会出现 `logs/<your-github-login>/...`,其他啥都没有。**

---

## 8. Windows 用户操作指南

Windows 用户无需 WSL,只需 **Python 3 + Git** 即可使用全部功能。

### 8.1 环境准备(一次性)

1. **安装 Python 3**: 从 [python.org](https://www.python.org/downloads/) 下载,安装时勾选 **Add Python to PATH**。
2. **安装 Git for Windows**: 从 [git-scm.com](https://git-scm.com/download/win) 下载,安装时会自带 **Git Bash**。
3. **安装 Claude Code**(如使用): `npm install -g @anthropic-ai/claude-code`

> 如果你已经装了 WSL,直接在 WSL 里操作即可,跳过本节,按 Linux 流程走。

### 8.2 拉取 openvela 工程

打开 **Git Bash**(不是 PowerShell / CMD),执行:

```bash
# 如果没有 repo 工具,先装
mkdir -p ~/.bin
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/.bin/repo
chmod +x ~/.bin/repo
export PATH=~/.bin:$PATH

# 拉取工程
repo init -u https://github.com/open-vela/contest2026_XXX_yourteam \
  -b dev-ai-contest-2026 -m contest2026_XXX_yourteam.xml
repo sync -c -j8
```

### 8.3 安装日志工具

在 **Git Bash** 里,进入选手仓执行:

```bash
cd contest2026_XXX_yourteam

bash ../.claude/skills/contest-log-collector/onboarding/install.sh \
  --team-id contest2026_XXX_yourteam \
  --github-login <你的GitHub用户名>
```

安装脚本会自动检测 Python 路径(`python3` / `python` / `py`),并在 `settings.json` 里用 Python 直接调用 `snapshot_core.py` — **不依赖 bash 执行 hook**,所以 Claude Code 的 hook 在 Windows 上也能正常触发。

### 8.4 开始开发

```bash
# 在 Git Bash 里启动 Claude Code
claude
```

**进入 openvela 工作区(有 `.repo/` 目录)= 视同同意采集**,后续每次对话结束会自动写入本地仓的 `logs/` 目录。工具**不会**自动 push,提交完全由你控制。

不想让某次对话被采集?→ 到 openvela 工作区**外面**跟 AI 聊即可,工具会静默。

OpenCode / MiMo Code 用户同理,直接在 Git Bash 里运行即可。

### 8.5 提交

```bash
git add logs/
git commit -s -m "logs: capture session"
git push
```

### 8.6 与 Mac/Linux 的区别

| 步骤 | Mac/Linux | Windows |
| --- | --- | --- |
| 终端 | Terminal | **Git Bash** |
| Python | 系统自带 python3 | 装 Python 3(勾选 Add to PATH) |
| hook 执行方式 | bash 调用 | **Python 直接调用**(不依赖 bash) |
| 其他 | 无 | **无区别** |

---

## 9. 反馈与支持

- 技术问题: 大赛技术支持群(组委会拉)
- 工具 bug: `https://github.com/open-vela/.claude/issues`
- 隐私 / 数据相关问题: 组委会邮箱

---

> **祝你比赛顺利!好好享受跟 AI 一起码代码的过程,日志的事我们包了。**
