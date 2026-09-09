# 评委指南 — 如何阅读 `logs/`

<!-- install.sh 会把本文件复制到选手仓根目录,文件名 JUDGE_GUIDE.md -->

## 快速上手

```bash
# 1. clone 选手仓
git clone https://github.com/<contest-org>/<demo-repo>
cd <demo-repo>

# 2. 终端预览某位组员的全部 session (彩色)
python3 tools/render-log.py logs/<github_login>/

# 3. 生成单个 session 的 HTML 评分报告 (浏览器打开)
python3 tools/render-log.py \
  logs/<github_login>/<date>/<tool>__<session_id>.jsonl \
  --format html --out report.html
```

## 仓库结构

```
<demo-repo>/
├─ src/                  # 选手作品代码
├─ logs/
│  ├─ <组员A_github_login>/    # 组员 A 的全部 session
│  │  ├─ manifest.json         # 组员 A 的索引文件
│  │  └─ <date>/<tool>__<session_id>.jsonl
│  └─ <组员B_github_login>/    # 组员 B,目录结构相同
└─ tools/
   ├─ render-log.py      # JSONL → 终端 / Markdown / HTML 渲染
   └─ validate-log.py    # 防作弊校验 (seq 单调性 + 跨字段一致性)
```

## 数据合规校验 (怀疑选手改过 log 时使用)

```bash
python3 tools/validate-log.py logs/

# ALL OK     => 数据合规
# ERRORS     => 该队可能改过 logs
#               (seq 缺号 / team_id 不一致 / manifest 与文件对不上 等)
```

> **关于长会话的已知情况**: 2026-07-10 之前的采集插件版本会把一个跨天会话按日期
> 拆成多个 JSONL 文件，manifest 只保留一条累计记录。validate-log.py 已按
> "同 session 多文件聚合" 校验，这类队伍不会误报。另有 `duplicate identical
> line (plugin re-export artifact)` 形式的 WARNING，是插件早期重导出追加的
> 字节级重复行，内容一致、非人为篡改，可忽略；只有 `duplicate seq with
> different content` 才是反作弊 ERROR。

## 批量生成评分报告 (打分工作流)

```bash
for member in logs/*/; do
  login=$(basename "$member")
  python3 tools/render-log.py "$member" \
    --format html --out reports/$login.html
done
# 逐个打开 reports/ 下的 HTML,按组员打分
```

## 数据契约 (评委关心的字段)

每条 JSONL 事件包含:

- `seq`           — session 内单调递增。**缺号或重复即数据可疑**
- `role`          — user / assistant / tool / system
- `tool`          — opencode / claude-code / codex / kiro
- `text`          — 消息正文 (完整保留,无截断)
- `thinking`      — AI 思考过程 (工具暴露时)
- `tool_name` / `tool_call_id` / `input` / `output` — 完整工具调用链 (read / edit / bash 等)
- `model`         — 使用的模型
- `tokens_in` / `tokens_out` — token 用量
- `redacted_count` — 自动脱敏次数 (API key / token)

`manifest.json` 顶层字段: `team_id` / `github_login` / `sessions[]`。这些字段在所有文件间必须一致。
