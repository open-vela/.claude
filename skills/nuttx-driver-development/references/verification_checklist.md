# Driver Workflow 验证规则参考

> 本文件包含 driver-workflow 各步骤的验证规则和 checklist。
> agent 在对应节点执行验证脚本或手动检查时参考本文件。
> 脚本位于同级 `scripts/` 目录，遵循统一契约：成功静默(exit 0)，失败输出错误(exit 2)。

---

## D.2 完成自检 Checklist

在标记 D.2 完成前，逐项验证。任何一项未通过则 D.2 不算完成。

对应脚本：`scripts/validate-deliverables.sh`

### 1. 产出物完整性

对照 design.md 中描述的所有文件（.c / .h / board init / Kconfig / Make.defs），确认每个文件都已实际创建或修改。

- 列出 design.md 提到但未生成的文件 → 必须补全
- 列出实际生成但 design.md 未提到的文件 → 更新 design.md

### 2. 函数可达性（调用链回放）

拿 requirements.md 的"系统集成分析"中画的完整调用链，从系统启动入口开始逐跳验证：

- [ ] 这个函数在新 config 下是否存在且会被编译？
- [ ] 这个函数在旧 config 下是否存在且会被编译？
- [ ] 如果是 #ifdef 切换点，新旧分支是否都有实现？
- [ ] 如果是 filter-out 文件，其中是否有启动链节点被误删？
- [ ] 如果 requirements.md 标注了"启动链节点，不可 filter-out"，确认该文件确实没有被 filter-out

验证格式：逐跳标注"存在/缺失/被排除"，任何一跳标注为"缺失"或"被排除"则 D.2 不通过。

### 3. BSP 对接完整性（仅 Mode B）

requirements.md 的 BSP 对接表中列出的每个 BSP 函数：

- [ ] 是否都有对应的传入路径？
- [ ] 如果 initialize 函数需要回调参数，是否有 board 层代码传入实际的 BSP 函数？
- [ ] BSP 函数名是否与实际声明一致（不是凭空假设的）？
- [ ] 数据流方向是否与旧系统一致（主动读 vs 被动推）？

### 4. 上下游适配完整性（仅 Mode B）

requirements.md 的上下游依赖表中标注"需要适配"的接口：

- [ ] 是否都有对应的桥接代码？
- [ ] 桥接函数是否有调用方？
- [ ] 旧接口的消费方是否能通过新接口获取等价数据？

---

## D.3.5 寄存器与骨架验证

第 6-7 项通过 `vela-config`（setconfig）+ `vela-build`（lunch + m）执行，不再使用独立脚本。

### 条件执行项（仅 A.3 datasheet 解析成功时）

**1. 寄存器地址验证**

提取代码中所有寄存器宏定义（`#define.*REG.*0x`），与 requirements.md 中的寄存器映射表逐项比对。任何地址不匹配必须修正。

**2. PROD_ID 验证**

确认代码中的 PROD_ID 地址和期望值与 requirements.md 一致。

**3. SPI 位宽验证**

确认 `SPI_SETBITS()` 的参数与 requirements.md 中的帧位宽一致。

### 始终执行项

**4. 骨架残留检查**

grep 搜索参考驱动的原始芯片名（如参考 `l3gd20` 则搜索 `l3gd20`/`L3GD20`），如果在新驱动代码中出现则报错并清理。

**5. SPDX 头检查**

每个新建的 `.c` / `.h` 文件第一行必须是 SPDX-License-Identifier。

**6. 数据依赖验证**

检查新代码引用的所有外部符号是否在新的 include path 下可解析：

- 提取新代码中所有 `#include` 的头文件，确认每个在构建路径下可找到
- 提取引用的外部宏/enum，确认有明确的定义来源
- 如果引用了旧驱动私有头文件中的符号，必须将实际数值内联或创建独立数据头文件
- **特别注意**：编译通过 ≠ 数据依赖正确 — 新 CONFIG 未启用时新代码不会被编译

**7. 强制编译验证**

如果新驱动有独立的 Kconfig 开关，通过 `vela-config` + `vela-build` skill 验证：
1. 备份 .config：`cp $OUTDIR/.config $OUTDIR/.config.d35-backup`
2. 用 `vela-config` skill 的 `setconfig` 临时启用新驱动 CONFIG（kconfiglib 自动解析依赖）
3. 用 `vela-build` skill 的 `source build/envsetup.sh && lunch <target> && m` 编译
4. 恢复 .config：`cp $OUTDIR/.config.d35-backup $OUTDIR/.config`
5. 编译失败按 E.1 编译错误分类规则处理

---

## E.1 编译错误分类规则

### 代码问题（自动修复，计入 3 轮限制）

错误信息包含以下关键词之一：

| 关键词 | 错误类型 |
|--------|---------|
| `error: expected` | 语法错误 |
| `incompatible type` | 类型不匹配 |
| `implicit declaration` | 隐式声明 |
| `undefined reference` + 符号属于本驱动 | 未定义符号（忘加源文件到 Make.defs） |
| `redefinition` | 重复定义 |
| `No such file` + 为本驱动头文件 | 缺少头文件 |

### 环境问题（立即停止，不计入限制）

| 关键词 | 错误类型 |
|--------|---------|
| `command not found` | 工具链缺失 |
| `No such file` + 为编译器/链接器路径 | 工具链路径错误 |
| `cannot find -l` | 系统库缺失 |
| `CONFIG_` 相关 `undefined` | 配置缺失 |
| `undefined reference` + 符号属于外部库 | 外部依赖缺失 |

### 判断规则

- `undefined reference` 的符号名包含本驱动名称前缀 → 代码问题
- 否则 → 环境问题
- 无法自动分类 → 询问用户

### 处理流程

1. 代码问题 → 自动修复 → 重新编译（最多 3 轮）
2. 环境问题 → 立即停止 → 提示用户
3. 3 轮后仍失败 → 停止，展示错误日志，等待用户介入

---

## Hook-Ready 架构说明

当前所有验证脚本和状态管理脚本遵循统一契约，可无缝切换为 hook 自动触发。

### 脚本清单

| 脚本 | 当前触发方式 | hook 触发事件 | 触发条件 |
|------|------------|-------------|---------|
| `validate-requirements.sh` | agent 在 Step B 完成时调用 | `PostToolUse(Write)` | 文件名匹配 `requirements.md` |
| `validate-design.sh` | agent 在 C.1 完成时调用 | `PostToolUse(Write)` | 文件名匹配 `design.md` |
| `validate-deliverables.sh` | agent 在 D.2 门禁时调用 | `Stop` | `.driver-workflow/progress.json` 中 D2 状态为 completed |
| `workflow-state.sh` | agent 在每个 Step 完成时调用 | 不需要 hook | 已通过 agent 调用覆盖 |

> 注：D.3.5 强制编译验证已改为直接调用 `vela-config` + `vela-build` skill，不再使用独立脚本。

### 切换为 Claude Code Hook

在 `.claude/settings.json` 中添加（不提交到远程，个人配置）：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [{
          "type": "command",
          "command": "f=$TOOL_INPUT_FILE; case \"$f\" in *requirements.md) bash .claude/skills/nuttx-driver-development/scripts/validate-requirements.sh \"$f\" ;; *design.md) bash .claude/skills/nuttx-driver-development/scripts/validate-design.sh \"$f\" ;; esac"
        }]
      }
    ],
    "Stop": [
      {
        "hooks": [{
          "type": "command",
          "command": "bash .claude/skills/nuttx-driver-development/scripts/validate-deliverables.sh design.md ."
        }]
      }
    ]
  }
}
```

### 切换为 OpenCode Plugin

在 `.opencode/plugins/driver-workflow-checks.ts` 中：

```typescript
export const DriverWorkflowChecks = async ({ $, directory }) => ({
  "file.edited": async ({ event }) => {
    const f = event.path;
    if (f.endsWith("requirements.md"))
      await $`bash ${directory}/.claude/skills/nuttx-driver-development/scripts/validate-requirements.sh ${f}`;
    if (f.endsWith("design.md"))
      await $`bash ${directory}/.claude/skills/nuttx-driver-development/scripts/validate-design.sh ${f}`;
  },
  "session.idle": async () => {
    await $`bash ${directory}/.claude/skills/nuttx-driver-development/scripts/validate-deliverables.sh design.md ${directory}`;
  }
})
```

### 关键原则

1. 验证逻辑只写一份 shell 脚本，agent/hook/plugin 三种触发方式复用
2. 脚本不依赖任何 IDE 特定环境变量（除 hook 示例中的 `$TOOL_INPUT_FILE`）
3. 成功静默（exit 0），失败输出错误（exit 2）— 兼容所有触发方式
