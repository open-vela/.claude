---
name: submit-pr
description: "向 openvela 社区提交 Pull Request。支持单仓库或多仓库批量提交、指定文件/分支、自动检测 GitHub/Gitee 平台、MCP 配置引导。Trigger: 提交 PR、提交代码到社区、push 到 openvela、创建 pull request、批量提交、多仓库提交。"
---

# Submit PR to openvela Community

向 openvela 开源社区（GitHub/Gitee）提交 Pull Request，支持单仓库和多仓库批量操作。

## Prerequisites

需要 GitHub MCP 或 Gitee MCP server 已配置。如未配置，按 Step 0 引导用户完成。

## Workflow

**核心流程**: 检测环境 → 确认提交内容 → 用户确认 → 创建分支 → 提交代码 → 创建 PR → 返回链接

### Step 0: MCP 配置检查与引导

执行前先检查 MCP 是否可用：

**检测方法**: 尝试调用 `mcp_github_search_repositories` 或类似工具。如果报错说工具不存在，说明 MCP 未配置。

**GitHub MCP 配置引导**:

告知用户需要在 MCP 配置文件中添加 GitHub server：

配置文件位置：
- Kiro: `.kiro/settings/mcp.json`（工作区级）或 `~/.kiro/settings/mcp.json`（用户级）
- Claude Code: `~/.claude/settings/mcp.json`

配置内容：
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "<用户的 GitHub PAT>"
      }
    }
  }
}
```

需要用户提供：
1. GitHub Personal Access Token（需要 `repo` 权限）
   - 获取地址：https://github.com/settings/tokens/new
   - 勾选 `repo` (Full control of private repositories)

**Gitee MCP 配置引导**:

Gitee 目前无官方 MCP server，使用 git 命令行 + Gitee API 方式提交：
- 需要用户提供 Gitee Access Token
- 获取地址：https://gitee.com/profile/personal_access_tokens/new

配置完成后提示用户**重启 IDE 或重新连接 MCP** 使配置生效。

### Step 1: 检测平台（GitHub / Gitee）

在用户指定的仓库目录中检测 remote：

```bash
cd <repo_path>
git remote -v
```

判断规则：
- 包含 `github.com` → GitHub 平台
- 包含 `gitee.com` → Gitee 平台
- 两者都有 → 询问用户选择

提取 owner 和 repo 名：
```bash
# 从 remote URL 提取
# git@github.com:open-vela/nuttx.git → owner=open-vela, repo=nuttx
# https://github.com/open-vela/nuttx.git → owner=open-vela, repo=nuttx
```

### Step 2: 确认提交内容

向用户确认以下信息（逐项询问或一次性确认）：

| 信息 | 说明 | 示例 |
|------|------|------|
| 仓库列表 | 要提交的仓库路径（支持多个） | `nuttx`, `apps`, `vendor/xxx` |
| 文件列表 | 每个仓库中要提交的文件 | `drivers/foo.c`, `Kconfig` |
| 目标分支 | PR 的 base 分支 | `dev`, `trunk` |
| PR 标题 | Pull Request 标题 | `fix: resolve mutex issue` |
| PR 描述 | Pull Request 描述（可选） | 修复说明 |
| commit message | 提交信息 | `fix: add mutex support` |

**多仓库场景**: 用户可能一次修改了多个仓库的文件（如 nuttx + vendor），需要分别向各仓库提交 PR。

### Step 3: 用户确认（⚠️ 必须等待确认）

向用户展示完整的提交计划，格式：

```
📋 提交计划确认：

平台: GitHub (open-vela)
目标分支: dev
新建分支: fix/mutex-support-20260515

仓库 1: nuttx
  文件: drivers/sensors/adc_sensor.c, include/nuttx/sensors/adc_sensor.h
  Commit: "fix: add ADC sensor mutex support"

仓库 2: vendor/allwinnertech
  文件: boards/r528/r528s3-gemini-s1/configs/nsh/defconfig
  Commit: "config: enable LIBCXX for r528"

PR 标题: fix: add ADC sensor with mutex support
PR 描述: ...

⚠️ 请确认以上信息是否正确？(y/n)
```

**必须等待用户回复 y 后才能继续执行。**

### Step 4: 创建分支并提交

对每个仓库执行：

```bash
cd <repo_path>

# 1. 确保在最新的目标分支上
git fetch origin <target_branch>
git checkout -b <new_branch> origin/<target_branch>

# 2. 添加指定文件
git add <file1> <file2> ...

# 3. 提交
git commit -m "<commit_message>"

# 4. 推送到远程
git push -u origin <new_branch>
```

分支命名规则: `<type>/<description>-<date>`，如 `fix/adc-sensor-20260515`

### Step 5: 创建 Pull Request

**GitHub 平台** — 使用 MCP 工具：

```
mcp_github_create_pull_request(
  owner="open-vela",
  repo="<repo_name>",
  title="<pr_title>",
  head="<new_branch>",
  base="<target_branch>",
  body="<pr_description>"
)
```

**Gitee 平台** — 使用 Gitee API（curl）：

```bash
curl -X POST "https://gitee.com/api/v5/repos/<owner>/<repo>/pulls" \
  -H "Content-Type: application/json" \
  -d '{
    "access_token": "<token>",
    "title": "<pr_title>",
    "head": "<new_branch>",
    "base": "<target_branch>",
    "body": "<pr_description>"
  }'
```

### Step 6: 返回结果

提交完成后，向用户展示：

```
✅ PR 创建成功！

仓库 1: nuttx
  PR: https://github.com/open-vela/nuttx/pull/123
  分支: fix/adc-sensor-20260515 → dev

仓库 2: vendor_allwinnertech
  PR: https://github.com/open-vela/vendor_allwinnertech/pull/45
  分支: fix/adc-sensor-20260515 → dev
```

## 多仓库批量提交

当用户修改涉及多个仓库时：

1. 识别所有修改的仓库（通过 `.repo/project.list` 或用户指定）
2. 每个仓库独立创建分支、commit、push、PR
3. PR 描述中互相引用关联的 PR（如 "Related: open-vela/nuttx#123"）
4. 所有 PR 使用相同的分支名前缀，方便识别

## 检测用户修改了哪些仓库

```bash
# 在 openvela 根目录执行
repo forall -c 'if [ -n "$(git status --porcelain)" ]; then echo "$REPO_PATH: $(git status --short)"; fi'
```

或者逐个检查：
```bash
cd <repo_path> && git status --porcelain
```

## Error Handling

| 错误 | 原因 | 解决 |
|------|------|------|
| `Permission denied` | Token 权限不足 | 重新生成 Token，勾选 repo 权限 |
| `branch already exists` | 分支名冲突 | 加时间戳后缀或递增编号 |
| `rejected (non-fast-forward)` | 本地分支落后 | `git pull --rebase origin <branch>` |
| `MCP tool not found` | MCP 未配置 | 回到 Step 0 引导配置 |
| `fork not found` | 用户没有 fork | 先 fork 仓库再提交 |
| `base branch not found` | 目标分支不存在 | 确认分支名（dev/trunk/main） |

## Fork 模式 vs 直接推送

- **有仓库写权限**（openvela 成员）: 直接在 origin 创建分支并提交 PR
- **无写权限**（外部贡献者）: 需要先 fork，推送到 fork，再从 fork 向上游提 PR

检测方式：
```bash
git push --dry-run origin HEAD 2>&1 | grep -q "denied\|403\|protected"
```

如果无权限，引导用户 fork：
```
mcp_github_fork_repository(owner="open-vela", repo="<repo>")
```

然后推送到 fork 并从 fork 创建 PR。

## Notes

- 所有破坏性操作（push、创建 PR）前必须等待用户确认
- commit message 遵循 Conventional Commits 格式（feat/fix/docs/chore）
- PR 描述建议包含：修改原因、影响范围、测试方法
- 多仓库提交时，建议在 PR 描述中说明关联关系
