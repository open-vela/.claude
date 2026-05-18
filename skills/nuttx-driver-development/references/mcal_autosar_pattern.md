# MCAL 到 NuttX 驱动适配模式

将 AUTOSAR MCAL 模块桥接到 NuttX upper-half 驱动框架。本文档覆盖从 MCAL 静态代码分析到编译验证的完整适配生命周期。

> **适用范围**：本模式适用于将厂商提供的 AUTOSAR MCAL 模块（如 Infineon TriCore）适配到 NuttX upper-half 驱动。对于标准 NuttX 驱动开发（非 MCAL），请使用 Driver Type Dispatch Table 中对应子系统的 pattern。

## 目录

1. [全局约束](#全局约束)
2. [术语](#术语)
3. [架构概览](#架构概览)
4. [MCAL 适配工作流](#mcal-适配工作流)
5. [代码生成规则](#代码生成规则)
6. [MCAL 编译注意事项](#mcal-编译注意事项)
7. [关键参考文档](#关键参考文档)

---

## 全局约束

> **优先级最高**：以下约束在整个 MCAL 适配工作流中始终生效，不可被任何步骤的局部逻辑覆盖。

### 文件已存在时的处理（强制询问）

当执行 MCAL 适配工作流时，如果在 Step A 阶段发现目标模块的关键文件已存在于工程中（包括但不限于）：

- 适配层代码（`<module>.c`、`<module>.h`）
- 需求文档（`requirements_<module>.md`）
- EB 动态代码（`<Module>_PBcfg.c`、`<Module>_Cfg.h`）
- 板级配置和初始化代码
- Wrapper 头文件（`<Module>.h`）
- Kconfig / CMakeLists.txt 中的模块条目

**必须停止并询问用户**，提供以下三个选项：

```
⚠️ 检测到目标模块 {module_name} 的适配文件已存在：
- {已存在文件列表，含路径}

请选择后续处理方式：
```

| 选项 | 说明 |
|------|------|
| **A. 基于已有文件更新** | 保留现有代码，基于新提供的 SWS 文档进行验证和增量更新（推荐） |
| **B. 全部重新生成** | 忽略已有实现，从零重新走完整流程生成所有文件（已有文件将被覆盖） |
| **C. 跳过代码生成，仅生成 EB 配置指南** | 保留现有代码不动，只生成/更新 EB 配置指南文档 |

**各选项的执行路径**：

- **选项 A（更新）**：继续完整流程，但 Step D（代码生成）时以已有代码为基础做增量修改，而非从骨架模板重新生成。Step D.2.5（EB 配置指南）仍然必须执行。
- **选项 B（重新生成）**：继续完整流程，Step D 从骨架模板重新生成所有文件。Step D.2.5 必须执行。
- **选项 C（仅 EB 指南）**：跳过 Step B/C/D 的代码生成部分，直接执行 Step D.2.5 生成 EB 配置指南文档，然后结束工作流。

**禁止的行为**：
- ❌ 自行判断"已完成"并中断工作流
- ❌ 不询问用户就直接选择某个选项
- ❌ 以"文件已存在"为由跳过 EB 配置指南生成步骤（选项 A 和 B 中 D.2.5 始终执行）

**展示选项后必须立即停止输出，等待用户的下一条消息。**

---

## 术语

- **MCAL（静态代码）** — 厂商提供的 AUTOSAR 兼容抽象层源码（只读，位于 `mcal_code/<Module>/ssc/`）。
- **动态代码** — EB Tresos 生成的配置文件（位于 `mcal_dynamic_code/`），与具体板级和项目相关。
- **iLLD** — Infineon Low-Level Driver，芯片厂商原始的非 AUTOSAR 驱动。仅作为参考模式使用，不可直接复制代码。
- **适配层** — MCAL 与 NuttX 之间的桥接代码（本模式生成的产物，位于 `frameworks/system/autocore/mcal/`）。
- **EB Tresos** — AUTOSAR 配置工具，生成 `_PBcfg.c`、`_Cfg.h` 等动态代码文件。
- **Det/Dem** — AUTOSAR 错误上报机制（Development Error Tracer / Diagnostic Event Manager）。

---

## 架构概览

```
NuttX 应用层
    │  open/read/write/ioctl
    ▼
NuttX Upper-Half 框架 (serial, SPI, I2C, etc.)
    │  ops 回调
    ▼
MCAL 适配层 (frameworks/system/autocore/mcal/<module>.c)
    │  MCAL API 调用 (<Module>_Init, <Module>_Read, etc.)
    ▼
MCAL 静态代码（厂商提供，只读）
    │  寄存器访问
    ▼
硬件
```

**与标准 NuttX 驱动的关键区别**：适配层不直接访问硬件寄存器，而是调用 MCAL API 来完成所有寄存器级操作。适配层的职责是在 NuttX 框架回调和 MCAL API 调用之间做翻译。

### 文件布局

**适配层源码和文档统一平铺在 `frameworks/system/autocore/mcal/` 目录下**，不为每个模块创建子目录。文件名通过模块名前缀区分。

```
frameworks/system/autocore/mcal/
├── <module>.c                        # 适配层实现
├── <module>.h                        # 适配层头文件（config 结构体 + init 原型）
├── requirements_<module>.md          # 需求文档（本地参考，不提交）
├── design_<module>.md                # 设计文档（本地参考，不提交）
├── tasks_<module>.md                 # 任务分解（本地参考，不提交）
├── eb_config_guide_<module>.md       # EB 配置指南（本地参考，不提交）
├── README_<module>.md                # 驱动说明（随代码提交）
├── CMakeLists.txt                    # 构建集成（所有模块共用一个）
└── Kconfig                           # 配置选项（所有模块共用一个）

vendor/infineon/chips/aurix/mcal/
├── mcal_code/<Module>/ssc/           # MCAL 静态代码（只读）
├── <Module>.h                        # AUTOSAR 标准名 → 厂商名映射
├── aurix_mcal_<module>.c             # 已有适配示例（仅供学习）
└── aurix_mcal_<module>.h

vendor/infineon/boards/aurix/common_code/mcal/mcal_dynamic_code/
├── src/SchM_<Module>.c               # 每核 config 指针数组
├── src/<Module>_PBcfg.c              # EB 生成的配置
├── inc/<Module>_PBcfg.h
├── inc/<Module>_Cfg.h
└── inc/mcal.h                        # config 数组的 extern 声明

vendor/infineon/boards/aurix/tc4d9_evb_bmp/src/
├── tc4d9.c                           # 主板级文件（只调用各模块 init）
├── tc4d9_mcal.c                      # 所有 MCAL 模块的板级配置 + EB 回调 + init
└── ...
```

**命名规则**：
- 源码：`<module>.c`、`<module>.h`（如 `wdg.c`、`uart.c`）
- 文档：`<doctype>_<module>.md`（如 `requirements_wdg.md`、`design_uart.md`）
- Wrapper：`<Module>.h`（如 `Wdg.h`、`Uart.h`）

---

## MCAL 适配工作流

> **与 driver-workflow agent 的关系**：本工作流描述 MCAL 适配的**领域特有步骤**。通用流程（用户输入收集、需求确认、编译验证、PR 提交）由 agent 驱动，此处不重复。当 agent 识别到 mcal 子系统时，按以下步骤执行 MCAL 特有的分析和代码生成。

### 步骤 1：解析 AUTOSAR SWS 规范文档

**优先级**：用户提供的 AUTOSAR SWS（Software Specification）文档是 AUTOSAR 官方规范，定义了模块的标准 API、行为、状态机和配置参数，**跨厂商通用**。因此在分析厂商特定的 MCAL 静态代码之前，必须先解析 SWS 文档。

**执行逻辑**：

1. **如果用户提供了 AUTOSAR SWS PDF**（如 `AUTOSAR_SWS_WatchdogDriver.pdf`）：
   - 调用 PDF 解析工具读取文档
   - 提取以下关键信息：
     - **标准 API 列表**：函数签名、Service ID、同步/异步属性、可重入性
     - **状态机**：模块状态（UNINIT → IDLE → BUSY 等）及转换条件
     - **配置参数**：`<Module>ConfigType` 的标准字段定义
     - **错误码**：DET 错误码定义（`E_PARAM_*`、`E_DRIVER_STATE` 等）
     - **功能需求**：SWS 中定义的功能性需求（`[SWS_Wdg_*]` 标签）
     - **模式定义**：如 WDG 的 OFF/SLOW/FAST 模式
   - 以 SWS 文档为**权威来源**，后续步骤 2 中读取的厂商 MCAL 静态代码用于验证厂商实现是否符合 SWS 规范，以及了解厂商特有的扩展

2. **如果用户未提供 SWS 文档**：
   - 直接进入步骤 2，从厂商 MCAL 静态代码中提取 API 信息
   - 标记 `⚠️ 无 AUTOSAR SWS 文档，API 分析仅基于厂商 MCAL 静态代码，可能包含厂商特有扩展`

**SWS 文档的价值**：
- 定义了哪些 API 是**强制实现**的（Mandatory），哪些是**可选的**（Optional）
- 明确了 API 的前置条件和后置条件
- 规定了错误处理行为（哪些情况报 DET 错误、哪些静默忽略）
- 提供了跨厂商一致的行为规范，适配层可以基于此做通用设计

### 步骤 2：定位和分析 MCAL 静态代码

搜索路径：
```
vendor/infineon/chips/aurix/mcal/mcal_code/<ModuleName>/ssc/src/
vendor/infineon/chips/aurix/mcal/mcal_code/<ModuleName>/ssc/inc/
```

各模块的规范目录名参见 `references/mcal_module_mapping.md`（如 `Can_17_McmCan`、`Pwm_17_TimerIp`）。

读取静态代码，提取：公开 API、`<Module>_ConfigType` 及相关类型、ISR 处理函数签名（`<Module>_Isr*`）、通知回调 typedef、状态/结果枚举。

**禁止**将 `vendor/infineon/chips/aurix/mcal/aurix_mcal_*.c` 当作 MCAL 源码使用——它们是适配层包装。可单独作为参考模式学习。

### 步骤 2.5：模块 Pattern 检查（阻塞步骤）

> **执行时机**：步骤 2 完成后、步骤 3 之前。此步骤的结果决定了后续步骤 4（iLLD 参考研究）和步骤 6（代码生成）中 NuttX 框架侧的接口定义。

**目的**：确认目标 NuttX 子系统的框架 pattern 文档是否存在。Pattern 文档定义了 lower-half ops 结构体、设备注册 API、IOCTL 命令等关键接口信息，是适配层设计的基础。

**检查路径**：`.claude/skills/nuttx-driver-development/references/`

**检查规则**：根据目标 MCAL 模块对应的 NuttX 子系统，搜索匹配的 pattern 文件（映射表见下方"模块 Pattern 检查（生成 requirements.md 时执行）"章节）。

**执行流程**：

1. 确定目标 MCAL 模块对应的 NuttX 子系统（如 `Wdg_17_Wtu` → watchdog → `wdg_pattern.md`）
2. 检查 `.claude/skills/nuttx-driver-development/references/` 中是否存在对应的 pattern 文件
3. **如果存在** → 读取 pattern 文件全文，标记 `✅ 步骤 2.5 完成`，继续步骤 3
4. **如果不存在** → **阻塞**，展示以下提示并等待用户决策：

```
⚠️ 未找到目标模块的 NuttX 框架 pattern 文档。

当前模块：{module_name}（对应 NuttX 子系统：{subsystem}）
缺失文件：.claude/skills/nuttx-driver-development/references/{expected_pattern_file}

模块 pattern 文档定义了 NuttX upper-half 框架的 ops 回调接口、设备注册方式、
数据流模型等关键信息。有 pattern 文档可以让后续适配更完整准确。

是否要基于 NuttX 框架源码先创建该模块的 pattern 文档？
```

选项：
- **是，先创建 pattern** → 读取 NuttX 对应子系统的 upper-half 源码（头文件 + 驱动框架实现），生成 pattern 文件，保存到 references 目录，然后标记 `✅ 步骤 2.5 完成`，继续步骤 3
- **否，跳过** → 再次提示风险后给第二轮选项（详见下方"模块 Pattern 检查（生成 requirements.md 时执行）"章节的完整交互流程）

**阻塞性质**：此步骤展示选项后**必须停止并等待用户回复**，禁止自行跳过或自动选择。Pattern 文件的存在与否直接影响 requirements.md 的"NuttX 框架适配参考"章节质量和后续代码生成的正确性。

### 步骤 3：依赖分析

AUTOSAR MCAL 模块之间存在模块间依赖。对每个依赖，确定其类型：

| 类型 | 含义 | 处理方式 |
|------|------|----------|
| **硬依赖（AUTOSAR 架构级）** 🔴 | AUTOSAR 规范要求由另一个独立模块负责（如 Port 负责引脚复用） | 必须启用依赖模块或提供 iLLD 回退方案 |
| **硬依赖（功能级）** 🟠 | 特定模式下需要另一个模块（如 SPI DMA 模式需要 DMA） | 必须启用依赖模块或降级模式 |
| **软依赖** 🟢 | 增强功能，非必需 | 可选 |

检查每个依赖的当前状态：
1. `vendor/.../mcal_dynamic_code/` 中是否包含 `<DepModule>_PBcfg.c`？
2. defconfig 中是否设置了 `CONFIG_AUTOCORE_MCAL_<DEPMODULE>`？
3. `board_lateinitialize_phaseB()` 中是否调用了 `<DepModule>_Init()`？

完整依赖表参见 `references/mcal_module_mapping.md` — Cross-Module Dependencies。

### 步骤 4：研究 iLLD 参考实现

搜索路径：
```
vendor/infineon/chips/aurix/aurix_<module_lowercase>.c
vendor/infineon/chips/aurix/aurix_<module_lowercase>.h
```

理解：每个函数实现了哪个 NuttX ops 回调、IRQ 如何挂载、DMA 如何配置、初始化顺序。这展示了要遵循的*模式*——MCAL 适配用 MCAL API 调用替换 iLLD 调用。

### 步骤 5：生成 EB 配置指南文档

在生成适配代码之前（或编译验证之前），**必须输出一份完整的 EB Tresos 配置步骤文档**，保存为 `eb_config_guide_<module>.md`，放在适配层同级目录（如 `frameworks/system/autocore/mcal/<module>/eb_config_guide_<module>.md`）。

**目的**：用户根据此文档在 EB Tresos 中完成配置并生成动态代码（`<Module>_PBcfg.c`、`<Module>_Cfg.h` 等），这些文件是编译的前置依赖。

**文档必须包含以下章节**：

1. **前置条件** — 需要先配置好的依赖模块（如 MCU、Port）
2. **EB 项目路径** — 在哪个 EB 项目中操作（如 `TC4D9_evb`）
3. **模块添加** — 如何在 EB 中添加该 MCAL 模块（如果尚未添加）
4. **General 配置** — 模块级全局参数设置（逐项列出参数名、推荐值、说明）
5. **实例配置** — 每个实例/通道的详细配置步骤：
   - EcucPartition 分配（哪个核）
   - 模式/超时/窗口等功能参数
   - 通知回调函数名（如果有）
   - 中断优先级和 SRC 分配（如果有）
6. **代码生成** — 点击生成后预期产出的文件列表
7. **产出文件放置路径** — 生成的文件应复制到哪些目录
8. **验证** — 如何确认 EB 配置正确（如检查生成的 config 结构体字段值）

**与 driver-workflow agent 的集成**：

在 agent 的 **Step D（生成代码）** 中，D.2 生成适配代码之后、D.3 跨文件审查之前，插入一个子步骤：

> **D.2.5 生成 EB 配置指南**：基于 requirements.md 中的实例规划和模块级参数，生成 `eb_config_guide_<module>.md`。此文件不提交到远程（仅本地参考）。生成后**必须停止并提示用户**：
>
> ```
> 📋 EB 配置指南已生成：<path>/eb_config_guide_<module>.md
>
> 请按照指南在 EB Tresos 中完成配置并生成动态代码，然后将生成的文件放入工程对应目录。
> 完成后回复"已完成"继续编译验证。
> ```
>
> **这是一个强制等待点**：必须等待用户确认 EB 生成的代码已放入工程后，才能进入 Step E（编译验证）。即使检测到 EB 动态代码（`<Module>_PBcfg.c`）已存在于工程中，仍然**必须生成 EB 配置指南文档**并**必须停止等待用户确认**——EB 动态代码可能是旧版本或参数与当前适配层需求不匹配。agent 不得以"EB 代码已存在"为由自行跳过此检查点。

### 步骤 6：生成适配代码

**前置加载**：在此步骤开始时，根据目标 NuttX 子系统查找 SKILL.md Dispatch Table，加载该子系统的**所有** reference 文件（主 pattern + 辅助文件）。这些文件提供框架接口定义、实现案例、避坑指南等完整上下文，用于指导代码生成。

详细模板和规则参见下方[代码生成规则](#代码生成规则)。

三个组件：
- **6.1 适配层** — `frameworks/system/autocore/mcal/<module>.c` 和 `.h`
- **6.2 MCAL Config 指针数组** — `SchM_<Module>.c` 和 `mcal.h`
- **6.3 板级配置和初始化** — `tc4d9_mcal.c`（配置数组、EB 回调包装、模块 init 函数）

---

## MCAL requirements.md 模板

> **说明**：此模板供 `driver-workflow` agent 的 Step B（生成 requirements.md）使用。当 agent 识别到 MCAL 模式时，使用此模板替代通用的 sensor/chardev requirements.md 模板。

### MCAL API 来源决策（强制规则）

- **如果用户提供了 AUTOSAR SWS 官方文档**：requirements.md 中的 MCAL API 分析章节**完全基于官方文档中定义的 API**。后续适配代码生成时，也**完全使用官方文档中的 API 签名和行为定义**，不依赖厂商静态代码中的 API（厂商代码仅用于确认实现存在和了解扩展）。
- **如果用户未提供 AUTOSAR SWS 官方文档**：在生成 requirements.md 之前，必须提示用户：

```
⚠️ 未检测到 AUTOSAR SWS 官方文档输入。

AUTOSAR SWS 文档定义了跨厂商通用的标准 API，是适配层设计的最佳依据。
没有官方文档时，将直接从厂商 MCAL 静态代码中读取 API 进行适配，
可能包含厂商特有扩展，跨平台移植性较差。

是否直接读取厂商 MCAL 代码进行适配？
```

选项：
- **是，直接读取代码适配** → 从厂商 MCAL 静态代码提取 API，继续生成 requirements.md
- **否，我去找文档** → 暂停，等待用户提供 SWS PDF 路径后重新开始

### requirements.md 章节结构

MCAL 适配的 requirements.md 应包含以下章节：

1. **现有资源** — 列出所有找到的相关文件（MCAL 静态代码、动态代码、iLLD 参考、SWS 文档）
2. **适配范围** — 需要创建和修改的文件清单
3. **实例规划** — 基于板级目录已有配置确定的实例数量和分配（见下方"实例规划规则"）
4. **MCAL API 分析** — 关键 API（来自 SWS 文档或静态代码）、ISR、ConfigType、通知回调
5. **NuttX 框架适配参考** — 记录 pattern 文件路径引用和关键摘要信息：
   - pattern 文件路径（如 `references/wdg_pattern.md`）
   - MCAL API → NuttX ops 的映射表（从 pattern 中提取的关键对应关系）
   - iLLD 参考实现路径（如有）
   - 注：pattern 文件全文将在步骤 6（代码生成）时加载，此处不拷贝全文
6. **NuttX 集成** — 需要实现的 ops 回调、设备注册方式（基于 pattern 或框架源码）
7. **中断路由** — 从硬件 → MCAL → NuttX 的 ISR 路径
8. **跨模块依赖** — 类型、原因、当前状态、额外工作
9. **EB 配置要点** — 主模块的关键参数
10. **依赖模块 EB 配置要点**
11. **验证方案**

### 实例规划规则（强制）

实例规划**必须基于目标板级目录中已有的 EB 配置和功能定义**，保证适配后的功能与原板级配置一致。

**参考源（仅以下 3 个，无其他来源）**：

1. `out/generated/<module>/src/` — 构建系统生成的模块配置代码
2. `vendor/infineon/boards/aurix/<board>/src/<board>.c` — 板级初始化文件
3. `vendor/infineon/chips/aurix/tc4xx.c` — 芯片级通用配置

**执行步骤**（按优先级逐级查找，找到即停）：

1. **读取板级生成配置**（首选）：检查 `out/generated/<module>/src/` 目录下该模块的配置文件（如 `aurix_<module>_cfg.c`），提取：
   - 配置了多少个实例/通道
   - 每个实例分配到哪个核
   - 每个实例的工作模式和参数
   - 设备路径、中断号、引脚配置等板级信息

   > **说明**：`out/generated/` 目录是构建系统根据板级配置自动生成的模块配置代码，包含了该板子实际使用的实例数量、通道分配、参数设置等信息，是实例规划的**首要参考来源**。

2. **回退：读取板级源码中的配置**（当 `out/generated/` 中无该模块配置时）：

   检查 `vendor/infineon/boards/aurix/<board>/src/<board>.c`（如 `tc4d9.c`），搜索该模块的配置数组定义或初始化调用，提取：
   - 实例数量和配置参数
   - 设备路径命名规则
   - 核亲和性
   - 初始化顺序

3. **回退：读取芯片级通用配置**（当板级源码中也无该模块配置时）：

   检查 `vendor/infineon/chips/aurix/tc4xx.c`，搜索该模块对应的 iLLD 驱动配置数组，提取：
   - 所有可能的实例定义（通过 `#ifdef CONFIG_*` 条件编译控制）
   - 设备路径命名规则（如 `/dev/wdt_cpu0`、`/dev/ttyS0`）
   - 核亲和性（`initcoreid` 字段）
   - 硬件资源指针（寄存器基地址、模块索引等）

   > **说明**：`tc4xx.c` 定义了该芯片上所有可用的硬件实例。MCAL 适配层应覆盖相同的实例集合（或其子集），保证功能对等。

**禁止使用的参考源**：
- ❌ EB Tresos 配置文件（`.xdm`）— 这是 EB 工具的输入配置，不是实例规划的依据
- ❌ EB 动态代码（`<Module>_PBcfg.c`）— 这是 EB 生成的 MCAL 配置，不决定 NuttX 侧的实例规划
- ❌ 自行推测/假设 — 不允许基于芯片架构文档推测实例数量

**EB 配置的正确用途**：EB `.xdm` 和动态代码仅用于提取**模块级参数**（超时值、模式、窗口百分比、DisableAllowed 等功能开关），不用于确定实例数量和分配。实例规划必须来自上述 3 个参考源。

5. **生成实例规划表**：

```markdown
| 实例 | 类型 | 核分配 | 设备路径 | 说明 |
|------|------|--------|----------|------|
| WDT0 | WDTCPU | Core0 | /dev/watchdog0 | CPU0 看门狗 |
| WDT1 | WDTCPU | Core1 | /dev/watchdog1 | CPU1 看门狗 |
| WDT_SYS | WDTSYS | Core0 | /dev/watchdog_sys | 系统级看门狗 |
```

**关键原则**：
- **不自行发明实例**：实例数量和分配完全来自板级已有配置，不凭空添加
- **不遗漏实例**：EB 配置中定义的所有实例都必须在适配层中支持
- **核亲和性一致**：适配层的 `core_id` 字段必须与 EB 配置的 EcucPartition 对应
- **设备路径严格沿用已有命名**：必须使用参考源（`tc4xx.c` 或板级文件）中已定义的 `devpath` 字符串，禁止自行改用 NuttX 通用命名（如已有 `/dev/wdt_cpu0` 则不得改为 `/dev/watchdog0`）
- **初始化时机与已有实现一致**：MCAL 适配层的初始化调用必须放在与参考源（`tc4xx.c` 或板级文件）中同模块 iLLD 初始化相同的函数和相同的位置（如 iLLD WDG 在 `up_lateinitialize()` 中初始化，则 MCAL WDG 也必须在 `up_lateinitialize()` 中初始化），禁止自行更改初始化时机

### 模块 Pattern 检查（生成 requirements.md 时执行）

在生成 requirements.md 时，**必须先检查**是否已有该模块对应的 NuttX 框架 pattern 文档，并将其内容纳入 requirements.md 的"NuttX 框架适配参考"章节。

**检查路径**：`.claude/skills/nuttx-driver-development/references/`

**检查规则**：根据目标模块对应的 NuttX 子系统，搜索是否存在匹配的 pattern 文件：

| NuttX 子系统 | 对应 pattern 文件 |
|-------------|------------------|
| CAN | `can_netdev_pattern.md` |
| LIN | `lin_netdev_pattern.md` |
| Ethernet | `eth_netdev_pattern.md` |
| Timer/GPT | `timer_pattern.md` |
| Framebuffer | `fb_pattern.md` |
| LCD | `lcd_pattern.md` |
| Input | `input_pattern.md` |
| Sensor | `sensor_uorb_pattern.md` |
| USBdev | `usbdev_dcd_pattern.md` |
| USBhost | `usbhost_hcd_pattern.md` |

**执行流程**：

1. 确定目标 MCAL 模块对应的 NuttX 子系统（如 `Wdg_17_Wtu` → watchdog → `wdg_pattern.md`）
2. 检查 `.claude/skills/nuttx-driver-development/references/` 中是否存在对应的 pattern 文件
3. **如果存在** → 读取 pattern 文件**全文**以了解 NuttX 框架接口（ops 结构体、注册 API、数据流模型等），用于指导 requirements.md 中 API 映射表和 NuttX 集成章节的编写。但 requirements.md 的"NuttX 框架适配参考"章节**只记录 pattern 文件路径引用**，不拷贝全文内容。完整 pattern 内容将在步骤 6（代码生成）时重新加载。
4. **如果不存在** → 提示用户：

```
⚠️ 未找到目标模块的 NuttX 框架 pattern 文档。

当前模块：{module_name}（对应 NuttX 子系统：{subsystem}）
缺失文件：.claude/skills/nuttx-driver-development/references/{expected_pattern_file}

模块 pattern 文档定义了 NuttX upper-half 框架的 ops 回调接口、设备注册方式、
数据流模型等关键信息。有 pattern 文档可以让 requirements.md 更完整准确。

是否要基于 NuttX 框架源码先创建该模块的 pattern 文档？
```

选项：
- **是，先创建 pattern** → 读取 NuttX 对应子系统的 upper-half 源码（头文件 + 驱动框架实现），生成 `{subsystem}_pattern.md`，保存到 references 目录，然后将其内容写入 requirements.md
- **否，跳过** → 再次提示：

```
⚠️ 强烈建议先创建 pattern 文档。

没有 pattern 文档的风险：
1. requirements.md 中的 NuttX 集成章节可能不完整
2. 可能遗漏框架要求的必选 ops 回调
3. 后续代码生成缺乏框架层面的指导

确定要跳过吗？
```

选项：
- **还是先创建** → 同上，创建 pattern 后继续
- **确定跳过** → 直接读取 NuttX 对应子系统的框架头文件和 upper-half 实现源码，从源码中提取 ops 结构体、注册 API 等信息，写入 requirements.md 的"NuttX 框架适配参考"章节（内容等同于 pattern，但不持久化为文件）

**Pattern 文档创建规范**（当用户选择创建时）：

生成的 pattern 文档应包含：
1. **框架概述** — 该子系统在 NuttX 中的架构（upper-half/lower-half 分层）
2. **关键头文件** — 框架头文件路径和 lower-half ops 结构体定义
3. **必选 ops 回调** — 框架要求必须实现的回调函数列表
4. **可选 ops 回调** — 可选实现的增强功能回调
5. **设备注册 API** — 注册函数签名和调用方式
6. **数据流模型** — 该子系统典型的数据交互模式
7. **MCAL API 映射表** — MCAL 标准 API 到 NuttX ops 的推荐映射
8. **参考实现** — NuttX in-tree 中该子系统的参考驱动路径

**创建 pattern 后必须同步更新 SKILL.md**：

创建完 `references/<subsystem>_pattern.md` 后，必须同步更新 `.claude/skills/nuttx-driver-development/SKILL.md` 中的 Driver Type Dispatch Table：
1. 将对应子系统的 Status 从 `🔲 Planned` 改为 `✅ Available`
2. 将 Reference Document 列从 `待创建: <file>` 改为实际文件路径 `references/<subsystem>_pattern.md`
3. 在 `references/nuttx_nav_search.md` 中添加该子系统的参考驱动路径（如有）

**完成后提示用户提交（强制等待点）**：

所有文件更新完成后，提示用户检查正确性并 push 合入：

```
📋 新建了 pattern 文档并更新了 Dispatch Table：
- 新建：.claude/skills/nuttx-driver-development/references/<subsystem>_pattern.md
- 修改：.claude/skills/nuttx-driver-development/SKILL.md（Dispatch Table 状态更新）

⚠️ 这些文件属于 .claude/ 仓库（skill 定义），与驱动代码不在同一个 repo。
请完成以下操作后回复"确认"：
1. 检查 pattern 文件内容的正确性
2. 将上述文件 push 到 .claude/ 仓库并合入（或确认暂不合入，后续统一提交）
```

**这是一个强制等待点**：展示提示后**必须立即停止输出，等待用户的下一条消息**。用户可能需要阅读 pattern 文件、修改内容、push 到远端仓库并合入。只有收到用户确认消息后，才能标记 `✅ 步骤 2.5 完成` 并继续步骤 3。

---

## MCAL design.md 模板

> **说明**：此模板供 `driver-workflow` agent 的 Step C（生成 design.md）使用。

MCAL 适配的 design.md 应包含：

1. **适配架构图** — 从应用层到硬件的完整调用链（ASCII 图），标注每层的文件路径
2. **API 映射表** — 每个 NuttX ops 对应的适配层内部函数名、调用的 MCAL API、行为描述
3. **数据结构设计** — 适配层私有结构体（lower-half 嵌入）和板级配置结构体的完整字段定义
4. **初始化流程** — 从 `mcal_<module>_initialize()` 入口到设备注册完成的完整步骤（伪代码或流程图），标注关键设计决策（如 Init 后自动启动的处理）
5. **Ops 实现详细设计** — 每个 ops 回调的伪代码逻辑，包含边界条件处理（如 stop 被拒绝、timeout 超限截断）
6. **中断路由图** — 如果模块有中断：从硬件 SRC → NuttX IRQ → MCAL ISR 的路由路径；如果无中断则标注"无中断"
7. **文件组织** — 所有新建/修改文件的目录树，标注每个文件的职责
8. **验证方法** — 编译验证和功能测试的具体命令和预期结果表

> **注意**：EB Tresos 配置步骤已独立为 `eb_config_guide_<module>.md`（见步骤 5），不再放在 design.md 中。

---

## 代码生成规则

### AUTOSAR 标准 API 名称映射（Wrapper 头文件）

AUTOSAR SWS 官方文档定义的 API 名称是通用的（如 `Wdg_Init`），而厂商 MCAL 实现通常带有厂商/IP 后缀（如 Infineon 的 `Wdg_17_Wtu_Init`）。适配层代码**必须使用 AUTOSAR 标准 API 名称**，以保证跨厂商可移植性。

**执行逻辑**：

1. 检查 EB 工具生成的动态代码中是否已提供标准名到厂商名的映射（如 `Wdg.h` 中 `#define Wdg_Init Wdg_17_Wtu_Init`）
2. **如果 EB 已生成映射** → 直接 include 对应头文件，无需额外处理
3. **如果 EB 未生成映射** → 自动生成一个 wrapper 头文件

**Wrapper 头文件规范**：

- **路径**：`vendor/infineon/chips/aurix/mcal/<Module>.h`（如 `Wdg.h`）
- **命名规则**：`<StandardModuleName>.h`
- **内容模板**：

```c
/****************************************************************************
 * vendor/infineon/chips/aurix/mcal/<Module>.h
 *
 * AUTOSAR standard API name mapping to vendor-specific implementation.
 * Auto-generated when EB Tresos does not provide this mapping.
 *
 * Standard API (SWS)          -> Vendor API (Infineon)
 ****************************************************************************/

#ifndef __VENDOR_INFINEON_CHIPS_AURIX_MCAL_<MODULE>_H
#define __VENDOR_INFINEON_CHIPS_AURIX_MCAL_<MODULE>_H

#include "<VendorModule>.h"  /* e.g. Wdg_17_Wtu.h */

/* API name mapping: AUTOSAR standard -> Infineon vendor-specific */

#define <Module>_Init(cfg)                  <VendorModule>_Init(cfg)
#define <Module>_SetMode(mode)              <VendorModule>_SetMode(mode)
#define <Module>_SetTriggerCondition(t)     <VendorModule>_SetTriggerCondition(t)
#define <Module>_GetVersionInfo(info)       <VendorModule>_GetVersionInfo(info)
/* ... map all APIs used by the adaptation layer ... */

/* Type mapping (if names differ) */

#define <Module>_ConfigType                 <VendorModule>_ConfigType

#endif /* __VENDOR_INFINEON_CHIPS_AURIX_MCAL_<MODULE>_H */
```

**示例**（Wdg 模块）：

```c
/* vendor/infineon/chips/aurix/mcal/Wdg.h */

#ifndef __VENDOR_INFINEON_CHIPS_AURIX_MCAL_WDG_H
#define __VENDOR_INFINEON_CHIPS_AURIX_MCAL_WDG_H

#include "Wdg_17_Wtu.h"

#define Wdg_Init(cfg)                  Wdg_17_Wtu_Init(cfg)
#define Wdg_SetMode(mode)              Wdg_17_Wtu_SetMode(mode)
#define Wdg_SetTriggerCondition(t)     Wdg_17_Wtu_SetTriggerCondition(t)
#define Wdg_GetVersionInfo(info)       Wdg_17_Wtu_GetVersionInfo(info)
#define Wdg_InitCheck(cfg)             Wdg_17_Wtu_InitCheck(cfg)

#define Wdg_ConfigType                 Wdg_17_Wtu_ConfigType

#endif /* __VENDOR_INFINEON_CHIPS_AURIX_MCAL_WDG_H */
```

**适配层使用方式**：

```c
/* In frameworks/system/autocore/mcal/wdg.c */

#include "Wdg.h"  /* Use standard API names */

/* Now use standard names throughout: */
Wdg_Init(ConfigPtr);
Wdg_SetMode(WDGIF_SLOW_MODE);
Wdg_SetTriggerCondition(timeout_ms);
```

**关键原则**：
- 适配层（`frameworks/system/autocore/mcal/`）中**只出现 AUTOSAR 标准 API 名称**
- 厂商特定名称**只出现在** wrapper 头文件和板级文件中
- 如果更换厂商（如从 Infineon 换到 Renesas），只需替换 wrapper 头文件，适配层代码无需修改

### 适配层（`frameworks/system/autocore/mcal/`）

关键规则：
- **禁止厂商特定符号**出现在此层。不允许 `SRC_ADDR`、`IfxSrc_init`、直接寄存器宏。硬件特定值（IRQ 号、HW unit 索引）通过板级配置结构体 `mcal_<module>_config_s` 传入。
- **禁止 EB 生成的回调名**（如 `Uart_Ch0_TxNotification`）。适配层只暴露通用处理函数如 `mcal_<module>_tx_notify(channel_id, error)`。板级文件提供 EB 命名的包装函数。
- include 顺序：NuttX config 头文件在前，NuttX 框架头文件次之，MCAL 头文件最后。
- 使用 `CONFIG_AUTOCORE_MCAL_<MODULE>` 进行条件编译。
- **错误上报**：使用 `Det_ReportError()` / `Det_ReportRuntimeError()`，适配层 Service ID 从 `0x80` 开始。详见 `references/mcal_code_pattern.md` — Error Reporting Pattern。
- **代码风格**：`clang-format --style=WebKit -i <file>`（CI 对此目录使用 WebKit 风格）。

完整 C 代码模板（源文件、头文件、ISR 路由、配置传递、通知回调、错误上报）参见 `references/mcal_code_pattern.md`。

### MCAL Config 指针数组（`SchM_<Module>.c`）

添加每核 `MCAL_<MODULE>_CONFIG[6]` 指针数组。参考 `SchM_I2c.c`、`SchM_Dma.c` 的模式：
```c
#include "<Module>_Data.h"
#include "<Module>_PBcfg.h"

const <Module>_ConfigType *MCAL_<MODULE>_CONFIG[6] =
{
  &<Module>_Config_EcucPartition_Core0,
  &<Module>_Config_EcucPartition_Core1,
  NULL, NULL, NULL, NULL,
};
```

在 `mcal.h` 中添加 extern 声明：
```c
#ifdef CONFIG_AUTOCORE_MCAL_<MODULE>
#include "<Module>_Data.h"
extern <Module>_ConfigType *MCAL_<MODULE>_CONFIG[6];
#endif
```

### 板级配置和初始化（`tc4d9_mcal.c`）

四个部分：
- **A — Include** 在 `#ifdef CONFIG_AURIX_MCAL` 块中
- **B — 板级配置数组**（static，在 init 函数之前定义）
- **C — Init 调用** 在板级 init 函数中，位于依赖模块之后
- **D — EB 通知回调包装**（EB 命名的回调分发到通用处理函数）

板级代码可以使用 Infineon 特定的 IRQ 宏（如 `SRC_ID_ASCLIN0TX`），并将其转换为整数传入适配层配置结构体。

### Kconfig 和 CMake

```kconfig
config AUTOCORE_MCAL_<MODULE>
	bool "Enable the MCAL <Module> module"
	default n
```

```cmake
if(CONFIG_AUTOCORE_MCAL_<MODULE>)
  target_sources(arch PRIVATE <module>.c)
endif()
```

---

## MCAL 编译注意事项

> **Note**: 编译验证由 `driver-workflow` agent 的 Step E 驱动（默认调用 `vela-build` skill）。如果编译无法启动，agent 会自动搜索 `.claude/skills/` 下能编译该板级的其他 skill 并回退。本节仅记录 MCAL 特有的注意事项。

### MCAL / iLLD 配置互斥

MCAL 和 iLLD 适配层实现同一硬件功能，不能同时启用：

| 配置项 | 操作 | 说明 |
|--------|------|------|
| `CONFIG_AUTOCORE_MCAL_<MODULE>=y` | ✅ 启用 | MCAL 适配层 |
| `# CONFIG_AURIX_<MODULE> is not set` | ❌ 关闭 | iLLD 同功能实现 |

搜索 `vendor/infineon/chips/aurix/Kconfig` 中 `CONFIG_AURIX_<MODULE>` 及其依赖选项（如 `CONFIG_AURIX_WDG_SMU_INIT`），全部禁用以避免符号冲突。

**编译验证前必须执行**：在 Step E.1 编译验证之前，agent 必须自动修改 defconfig 完成配置切换（关闭 iLLD 对应模块 + 启用 MCAL 模块），否则会产生重复符号或链接冲突。此步骤不需要用户确认。

**defconfig 查找规则**：defconfig 文件位于目标板路径下的 `configs/` 子目录中（如 `vendor/infineon/boards/aurix/tc4d9_evb_bmp/configs/<target>/defconfig`）。禁止在其他路径（如 `vendor/micar/`）下搜索 defconfig。选择 defconfig 时优先使用主配置（如 `bmp/defconfig`、`nsh/defconfig`），禁止使用 `*_test/` 目录下的 defconfig 进行编译验证。

### MCAL 常见编译错误

| 错误 | 原因 | 修复 |
|------|------|------|
| Missing headers | include 路径或 CMakeLists.txt 未更新 | 检查 include 路径 |
| Undefined types | MCAL 头文件 include 顺序错误 | 调整 include 顺序 |
| Duplicate symbols | iLLD 对应模块未禁用 | 确认 `CONFIG_AURIX_<MODULE>` 已关闭 |
| Signature mismatch | 适配层函数签名与 MCAL 静态代码不一致 | 重新读取 MCAL 静态代码确认签名 |
| Undefined references | `SchM_<Module>.c` 或 `mcal.h` 声明缺失 | 补充 extern 声明和源文件 |

### MCAL 模块功能测试命令

| 模块 | 测试命令 | 预期结果 |
|------|---------|---------|
| UART | `echo "test" > /dev/ttyS0` | 示波器或回环可见输出 |
| I2C | `i2c_tool get -b 1 -a 0x50 -r 0x00` | 返回设备 ID |
| SPI | `spi_test /dev/spi0` | MISO 接 MOSI 回环成功 |
| CAN | `cansend can0 123#DEADBEEF && candump can0` | 可见发送帧 |

### MCAL 功能测试失败排查

1. 检查 `dmesg` 中的 MCAL 错误日志（Det_ReportError 输出）
2. 确认 EB 配置的 EcucPartition 与运行测试的 core 一致
3. 确认 `Port_Init()` 在 `<Module>_Init()` 之前调用（依赖顺序）

---

## 关键参考文档

- `references/mcal_code_pattern.md` — 适配文件的 C 代码模板（结构体布局、ops 回调、init 函数、ISR 路由、通知回调、错误上报）
- `references/mcal_module_mapping.md` — MCAL→NuttX 框架映射表，以及每个依赖的 EB 配置步骤的完整跨模块依赖列表
