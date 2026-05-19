---
name: api-doc-check
description: Checks API documentation coverage and sync status against source header files. Use when adding new APIs, modifying header files, or auditing documentation completeness for openvela modules (kernel, bluetooth, media, telephony, etc).
---

# API 文档检查 Skill

## ⚡ TL;DR 快速入门

```bash
# 检查头文件变更时文档是否同步
bash .claude/skills/api-doc-check/scripts/check-api-doc-sync.sh

# 检查本次变更的 API 文档翻译质量（中文检查中英混杂，英文检查混入中文）
bash .claude/skills/api-doc-check/scripts/check-zh-translation.sh

# 作为 pre-commit hook 安装（两项检查都会执行）
cp .claude/skills/api-doc-check/scripts/pre-commit .git/hooks/pre-commit
```

---

## 目标与适用场景

### 目标
确保 openvela 公开头文件（`.h`）与 API 参考文档（`docs/zh-cn/api/`）保持同步，防止头文件变更后文档未更新。

### 核心能力
| 能力              | 说明                                 |
| ----------------- | ------------------------------------ |
| ✅ 头文件-文档映射 | 维护 50+ 个头文件到文档的对应关系    |
| ✅ 变更检测        | 检测 git staged 中的头文件变更       |
| ✅ 同步校验        | 验证头文件变更时对应文档是否也有更新 |
| ✅ 翻译质量检查    | 检测中英文文档的翻译质量问题         |
| ✅ pre-commit 集成 | 可作为 git hook 阻止不同步的提交     |

### 适用场景
- 新增 API 头文件后，检查是否需要创建文档
- 修改头文件后，提醒同步更新文档
- 定期审计文档覆盖率
- CI/CD 中自动检查文档同步状态

---

## 映射关系

当前维护的头文件 → 文档映射覆盖以下模块：

| 模块      | 映射数 | 头文件路径前缀                                         |
| --------- | ------ | ------------------------------------------------------ |
| 内核      | 5      | `nuttx/include/`                                       |
| 蓝牙      | 13     | `frameworks/connectivity/bluetooth/framework/include/` |
| 多媒体    | 8      | `frameworks/multimedia/media/include/`                 |
| Telephony | 12     | `frameworks/connectivity/telephony/include/`           |
| 系统框架  | 2      | `apps/system/` / `frameworks/system/`                  |

详细映射见 `scripts/check-api-doc-sync.sh` 中的 `HEADER_DOC_MAP`。

---

## 使用方式

### 作为 pre-commit hook

```bash
cp .claude/skills/api-doc-check/scripts/pre-commit .git/hooks/pre-commit
```

这会同时执行两项检查：
1. 头文件变更 → 文档同步检查
2. API 文档 → 翻译质量检查（中文检查中英混杂，英文检查混入中文）

### 跳过检查

```bash
git commit --no-verify
```

### 新增模块时

1. 在 `scripts/check-api-doc-sync.sh` 的 `HEADER_DOC_MAP` 中添加映射
2. 创建对应的 API 文档文件

---

## 文档规范要点

生成 API 文档时遵循以下格式：

```markdown
### 函数名

\`\`\`c
返回类型 函数名(参数列表);
\`\`\`

功能描述。

**参数**：

- \`param1\` 描述。

**返回值**：

成功时返回 X，失败时返回 Y。
```

### 关键规则
- 全中文描述（函数名、类型名保留英文）
- 每个 API 独立 `###` 章节
- 参数列表与函数签名严格一致
- 返回值说明成功和失败两种情况
- 头文件使用尖括号：`#include <xxx.h>`
