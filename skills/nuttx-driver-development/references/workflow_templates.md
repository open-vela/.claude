# Driver Workflow 模板参考

> 本文件包含 driver-workflow agent 使用的所有文档模板和格式定义。
> agent.md 通过引用本文件的章节来生成对应文档。

---

## 交互 1 子系统选项

### 驱动子系统（Mode A 第 2a 轮）

| 选项 | 描述 |
|------|------|
| sensors | 传感器（加速度计、陀螺仪、气压计等） |
| power/battery | 电池充电/监控/电量计 |
| timers | 定时器/看门狗 |
| leds | LED 控制 |
| input | 输入设备（按键、触摸、编码器） |
| analog | 模拟设备（ADC/DAC） |
| serial | 串口设备 |
| vibrator | 振动马达 |
| （custom） | 允许用户输入其他子系统 |

### 总线类型

| 选项 | 描述 |
|------|------|
| I2C | I2C 总线 |
| SPI | SPI 总线 |
| I2C + SPI | 两者都支持 |
| 无总线 | Standalone/GPIO |

### Sensor 子设备类型（当子系统 = sensors 时）

加速度计 / 陀螺仪 / IMU / 气压计 / 温湿度 / 光照 / 磁力计 / 接近 / （custom）

### Power/Battery 类型（当子系统 = power/battery 时）

**充电器类型**：

| 选项 | 描述 |
|------|------|
| A. Standalone 型 | PMU 硬件自主充电，软件监控 |
| B. I2C 可控型 | 通过 I2C 控制充电参数 |
| C. 纯插拔检测型 | 仅检测充电器插拔 |

**电量计类型**：

| 选项 | 描述 |
|------|------|
| 无 | 不需要电量计 |
| D. 软件电量计 | ADC 采样 + SOC-V 查表 |
| E. 硬件电量计 | I2C gauge IC |

**组合验证规则**：充电器类型 = C 且电量计 = 无时，必须向用户确认是否真的不需要电量计。

---

## Mode A Requirements 模板（新驱动）

### Datasheet 章节（从 datasheet 提取，PDF 解析失败时用 TODO 占位）

1. **工作原理**: 芯片核心测量原理、信号链路（传感元件 → ADC → 数据寄存器）
2. **寄存器介绍**: 完整寄存器摘要表（PROD_ID、数据寄存器、控制寄存器、状态/诊断寄存器），含地址、默认值、关键位域说明
3. **工作模式**: 所有工作模式（正常、待机、睡眠、自检等），含模式切换寄存器和时序
4. **低功耗**: 各模式功耗数据、睡眠/唤醒时序、省电策略建议
5. **代码流程**: 驱动初始化序列、数据采集流程、错误处理流程

### 架构与接口

- **架构选择**: uORB / chardev，及选择理由（来自 A.2 架构锁定）
- **接口规格**: 总线类型、SPI 模式/帧位宽/最大频率（或 I2C 地址/频率）、中断引脚
- **数据采集模式**: 中断驱动 push / 定时轮询 push / fetch，及选择理由

### 功能 Checklist

从 datasheet 提取所有可实现功能，默认勾选规则：

- [x] PROD_ID / WHO_AM_I 校验（强制）
- [x] 数据读取（强制）
- [x] activate / deactivate（强制）
- [x] set_interval / ODR 配置（强制）
- [x] selftest — datasheet 支持则默认勾选
- [x] sleep / standby — datasheet 支持则默认勾选
- [x] 中断配置 — datasheet 支持则默认勾选
- [x] FIFO batch — datasheet 支持则默认勾选
- [ ] 校准 — datasheet 支持则列出，默认不勾选
- [ ] 诊断状态检查 — 默认不勾选
- [ ] 高精度数据输出 — 默认不勾选

### 实现约束

**全局约束**：
1. 不忽略功能：datasheet 描述的功能必须在 checklist 中列出
2. 南向接口补齐：尽可能实现 upper-half 提供的所有南向接口

**Sensor 子系统专属约束**（详见 `sensor_uorb_pattern.md`）：
3. 有中断必须用中断：datasheet 提供 DRDY/INT → 必须实现中断驱动
4. 有 FIFO 必须用 FIFO：datasheet 提供 FIFO → 必须实现 FIFO batch

> 规则 3-4 不适用于非 Sensor 子系统。

### 其他必填项

- 性能指标：采样率范围、功耗模式、唤醒时间
- 参考驱动：骨架模板及其与目标设备的关键差异

---

## Mode B1 Requirements 模板（新 Feature）

1. **现状分析**: 现有驱动的文件清单、架构模式、已实现功能列表
2. **新功能需求**: 功能目标、接口要求、约束条件
3. **影响范围**: 需要修改的文件列表、新增的文件、可能影响的现有功能
4. **实现方案**: 新功能的实现思路、与现有代码的集成方式
5. **功能 Checklist**: 新增功能的 checkbox 列表

---

## Mode B2 Requirements 模板（全面扫描）

1. **现状分析**: 现有驱动的文件清单、架构模式、已实现功能列表

2. **系统集成分析**（从 A.3 调用链分析中提取）:
   - **完整调用链**: 从系统启动入口到硬件操作的逐跳链路图，每一跳标注文件名、函数名、static/extern、数据流方向、是否启动链节点
   - **替换边界**: 标注哪些节点保留、哪些替换、边界上的切换方式
   - **数据流方向确认**: 每个 BSP 接口的数据是主动读取还是被动推送
   - **启动链节点识别**: 标注哪些旧文件不可 filter-out

3. **上下游依赖分析**:

   | 原始接口 | 调用方文件 | 集成方式 | 重构后对接方案 |
   |---------|-----------|---------|-------------|

4. **BSP 对接表**:

   | BSP 操作 | 原始函数 | 新驱动接口 | 实现位置 |
   |---------|---------|-----------|---------|

5. **审查结果**: 6 维审查的 FAIL/WARN 项
6. **改进计划**: 每个改进项的修改方案 + 优先级
7. **功能 Checklist**: 改进项的 checkbox 列表

---

## 可追溯性矩阵格式（design.md 必含）

| req 编号 | 需求描述 | 目标文件 | 目标函数/结构体 | board 层对接 | 上游适配 | 切换点 |
|---------|---------|---------|---------------|------------|---------|-------|

**填写规则**：
- **目标文件** 为空 = 需求未落地，D.2 中必须补全
- **board 层对接** 为空 = 缺少集成代码
- **上游适配** 为空且上下游依赖表标注了"需要适配" = 缺少桥接代码
- **切换点** 为空（Mode B2）= 未确定切换方式，回查系统集成分析

**切换点类型**：
- 内部 #ifdef：旧文件是启动链节点，不可 filter-out
- Makefile 互斥：旧文件可整个 filter-out
- 符号重命名：新旧函数同名无法互斥时

---

## A.2 芯片参考搜索 Sub-agent Prompt 模板

### 委托给 Explore sub-agent（model=sonnet）的 prompt

```
在开源项目中搜索 {chip_name} 芯片的驱动实现，提取寄存器定义和初始化序列作为交叉验证参考。

目标芯片：{chip_name}
驱动子系统：{subsystem}（如 sensors/accel、sensors/gyro、power/battery 等）
总线类型：{bus_type}（I2C / SPI）

按以下优先级搜索，找到第一个即停止：

1. Linux Kernel（优先）：
   - sensors 子系统搜索 drivers/iio/ 下对应子目录
   - power 子系统搜索 drivers/power/supply/
   - 在 https://github.com/torvalds/linux 中搜索文件名或内容包含 "{chip_name}" 的 .c 文件
   - 找到后提取：寄存器地址宏定义（#define.*REG.*0x）、初始化函数、SPI/I2C 配置

2. Zephyr RTOS（次优）：
   - 在 https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/sensor 中搜索 "{chip_name}"
   - 找到后提取同上内容

3. NuttX in-tree（兜底）：
   - 在本地 nuttx/drivers/{subsystem_dir}/ 目录中 grep 搜索 "{chip_name}" 或 "{vendor_name}"

输出格式（严格遵守，不要添加额外内容）：

```json
{{
  "found": true,
  "source": "linux|zephyr|nuttx",
  "file_path": "drivers/iio/accel/bmi270.c",
  "url": "https://github.com/torvalds/linux/blob/master/drivers/iio/accel/bmi270.c",
  "registers": [
    {{"name": "CHIP_ID", "addr": "0x00", "default": "0x24"}},
    {{"name": "DATA_START", "addr": "0x0C", "length": 12}}
  ],
  "spi_mode": "MODE0|MODE3|unknown",
  "spi_bits": 8,
  "i2c_addr": "0x68",
  "init_sequence_summary": "reset → wait 10ms → check chip_id → configure ODR → enable sensor",
  "key_findings": "支持 FIFO burst read，自检通过写 0x01 到 REG_SELF_TEST"
}}
```

如果所有来源都没找到，返回：
```json
{{
  "found": false,
  "searched": ["linux", "zephyr", "nuttx"],
  "suggestion": "建议直接从 datasheet 提取寄存器信息"
}}
```
```

---

## A.2 骨架参考搜索 Sub-agent Prompt 模板

### 委托给 Explore sub-agent（model=sonnet）的 prompt

```
在 NuttX 源码树中搜索最适合作为代码骨架模板的 in-tree 驱动。

目标子系统：{subsystem}
目标总线：{bus_type}（I2C / SPI / 无总线）
目标架构：{architecture}（uORB / chardev / battery_charger / battery_gauge / battery_monitor）
项目根目录：{project_root}

按以下优先级搜索，返回最多 3 个候选：

1. 推荐驱动（最高优先级）：
   - sensors 子系统：检查 nuttx/drivers/sensors/ 下的 goldfish_sensor_uorb.c、bmp280_uorb.c、bmi160_uorb.c
   - power/battery 子系统：检查 nuttx/drivers/power/battery/ 下的 mcp73871.c、bq27426.c
   - 其他子系统：检查 nuttx/drivers/{subsystem_dir}/ 下所有 .c 文件

2. 同设备类型 + 同总线 + 同架构：
   - uORB 架构：grep 搜索包含 "sensor_register" 和 "{bus_api}" 的 *_uorb.c 文件
   - chardev 架构：grep 搜索包含 "register_driver" 和 "{bus_api}" 的 .c 文件
   - battery 架构：grep 搜索包含 "battery_charger_register\|battery_gauge_register" 的 .c 文件
   （{bus_api} = I2C 时为 "I2C_TRANSFER"，SPI 时为 "SPI_SELECT"）

3. 同架构模式（放宽总线限制）：
   - 搜索所有使用相同注册 API 的驱动

对每个候选驱动，读取文件前 50 行和注册函数，提取关键信息。

输出格式（严格遵守）：

```json
{{
  "candidates": [
    {{
      "rank": 1,
      "file": "nuttx/drivers/sensors/bmp280_uorb.c",
      "match_reason": "同子系统(sensors) + 同总线(I2C) + 同架构(uORB)",
      "register_api": "sensor_register",
      "bus_api": "I2C_TRANSFER",
      "line_count": 450,
      "has_interrupt": false,
      "has_fifo": false,
      "data_mode": "fetch"
    }},
    {{
      "rank": 2,
      "file": "nuttx/drivers/sensors/bmi160_uorb.c",
      "match_reason": "同子系统(sensors) + 同架构(uORB)，SPI+I2C 双总线",
      "register_api": "sensor_register",
      "bus_api": "I2C_TRANSFER + SPI_SELECT",
      "line_count": 800,
      "has_interrupt": true,
      "has_fifo": true,
      "data_mode": "push"
    }}
  ],
  "recommended": "nuttx/drivers/sensors/bmp280_uorb.c",
  "recommendation_reason": "行数最少、结构清晰、与目标总线匹配"
}}
```
```

---

## A.3 Datasheet PDF 解析 Sub-agent Prompt 模板

### 委托给 Explore sub-agent（model=opus）的 prompt

```
解析以下 datasheet PDF，提取驱动开发所需的关键硬件信息。

PDF 文件路径：{pdf_path}
芯片名称：{chip_name}（用于验证 PDF 内容是否匹配）
总线类型：{bus_type}（I2C / SPI，决定提取哪些接口参数）

请完整读取 PDF 文件，然后提取以下 4 类信息。每类信息必须从 PDF 原文中提取，禁止猜测或使用外部知识补充。如果 PDF 中找不到某项信息，标注 "PDF 未提供"。

1. 寄存器摘要表（必须提取）：
   提取所有关键寄存器，按以下分类整理：
   - PROD_ID / WHO_AM_I / CHIP_ID：地址 + 默认值
   - 数据输出寄存器：起始地址 + 长度（字节数）+ 数据格式（大端/小端、有符号/无符号、位宽）
   - 控制/配置寄存器：ODR 设置、量程设置、模式切换、中断配置
   - 状态/诊断寄存器：数据就绪标志、错误标志、自检结果

2. 接口规格（必须提取）：
   - I2C：7-bit 地址（含 SDO/SA0 引脚对地址的影响）、支持的频率（Standard/Fast/Fast+）
   - SPI：模式（Mode 0/3）、帧位宽（8/16-bit）、最大频率、读写位定义（如 bit7=R/W）
   - 中断引脚：INT1/INT2 功能、默认极性、推挽/开漏

3. 功能清单（必须提取）：
   逐项列出 datasheet 中描述的所有可实现功能，每项标注 "datasheet 支持"：
   - 自检（self-test）：触发方式、判定标准
   - 低功耗模式：sleep/standby/suspend 模式、切换寄存器、唤醒时间
   - FIFO：深度（样本数）、模式（stream/FIFO/bypass）、水印中断
   - 校准：出厂校准 / 用户校准、校准寄存器
   - 温度传感器：内置温度输出寄存器、精度
   - 其他特殊功能

4. 时序要求（必须提取）：
   - 上电到可通信延迟（POR delay）
   - 软复位恢复时间
   - 模式切换等待时间（如 sleep → normal）
   - SPI 片选间隔（CS deassert time）
   - I2C 重复起始条件要求

输出格式（严格遵守，使用 JSON）：

```json
{{
  "chip_name": "{chip_name}",
  "pdf_verified": true,
  "registers": {{
    "chip_id": {{"addr": "0x00", "default": "0x24", "name": "CHIP_ID"}},
    "data_start": {{"addr": "0x0C", "length": 12, "format": "little-endian, signed 16-bit"}},
    "control": [
      {{"name": "PWR_CTRL", "addr": "0x7D", "description": "电源模式控制"}},
      {{"name": "ACC_CONF", "addr": "0x40", "description": "加速度计 ODR/带宽/量程"}}
    ],
    "status": [
      {{"name": "STATUS", "addr": "0x03", "description": "数据就绪 + 错误标志"}}
    ]
  }},
  "interface": {{
    "i2c_addr": "0x68 (SDO=GND) / 0x69 (SDO=VDDIO)",
    "i2c_freq": "Standard (100kHz) / Fast (400kHz)",
    "spi_mode": "Mode 0 / Mode 3",
    "spi_bits": 8,
    "spi_max_freq": "10MHz",
    "spi_rw_bit": "bit7: 0=write, 1=read",
    "interrupt_pins": "INT1 (push-pull, active-low), INT2 (push-pull, active-low)"
  }},
  "features": [
    {{"name": "self_test", "supported": true, "detail": "写 0x01 到 SELF_TEST，比较输出差值"}},
    {{"name": "sleep_mode", "supported": true, "detail": "PWR_CTRL bit0=0 进入 suspend"}},
    {{"name": "fifo", "supported": true, "detail": "1024 字节 FIFO，支持 stream/FIFO 模式"}},
    {{"name": "temperature", "supported": true, "detail": "内置温度传感器，TEMP_MSB/LSB 寄存器"}}
  ],
  "timing": {{
    "por_delay_ms": 10,
    "soft_reset_delay_ms": 2,
    "sleep_to_normal_ms": 5,
    "spi_cs_deassert_us": 2
  }}
}}
```

如果 PDF 无法读取或内容与芯片名称不匹配，返回：
```json
{{
  "chip_name": "{chip_name}",
  "pdf_verified": false,
  "error": "PDF 读取失败 / 内容与芯片名称不匹配"
}}
```
```

---

## D.3.5 寄存器验证 Sub-agent Prompt 模板

### 委托给 Explore sub-agent（model=opus）的 prompt

```
你是一个独立的验证者（Evaluator），任务是从 datasheet PDF 中重新提取寄存器信息，与驱动代码中的宏定义交叉验证。你没有看过之前的提取结果，必须独立从 PDF 提取。

Datasheet PDF 路径：{pdf_path}
驱动源文件路径：{driver_c_path}
芯片名称：{chip_name}

执行以下 3 项验证：

1. 寄存器地址验证：
   - 从 PDF 中提取所有关键寄存器的地址（CHIP_ID、数据寄存器、控制寄存器、状态寄存器）
   - 从驱动 .c 文件中提取所有 #define.*REG.*0x 的宏定义
   - 逐项对比，标注匹配/不匹配

2. PROD_ID / CHIP_ID 验证：
   - 从 PDF 中提取芯片标识寄存器的地址和默认值
   - 从驱动代码中找到 chip_id 检查逻辑（通常在 register/initialize 函数中）
   - 验证地址和期望值是否一致

3. SPI/I2C 接口验证：
   - 从 PDF 中提取 SPI 模式（Mode 0/3）、帧位宽（8/16-bit）、I2C 地址
   - 从驱动代码中找到 SPI_SETMODE/SPI_SETBITS/I2C addr 的设置
   - 验证是否一致

输出格式（严格遵守）：

```json
{{
  "verification_results": [
    {{
      "item": "register_addresses",
      "status": "PASS|FAIL",
      "details": [
        {{"name": "CHIP_ID", "pdf_addr": "0x00", "code_addr": "0x00", "match": true}},
        {{"name": "DATA_START", "pdf_addr": "0x0C", "code_addr": "0x0E", "match": false, "note": "代码地址错误，应为 0x0C"}}
      ]
    }},
    {{
      "item": "prod_id",
      "status": "PASS|FAIL",
      "pdf_addr": "0x00",
      "pdf_value": "0x24",
      "code_addr": "0x00",
      "code_value": "0x24",
      "match": true
    }},
    {{
      "item": "interface",
      "status": "PASS|FAIL",
      "details": {{
        "spi_mode": {{"pdf": "Mode 0/3", "code": "SPIDEV_MODE0", "match": true}},
        "spi_bits": {{"pdf": 8, "code": 8, "match": true}},
        "i2c_addr": {{"pdf": "0x68", "code": "0x68", "match": true}}
      }}
    }}
  ],
  "overall": "PASS|FAIL",
  "fail_count": 0,
  "fix_suggestions": []
}}
```

如果 PDF 无法读取，返回：
```json
{{
  "verification_results": [],
  "overall": "SKIP",
  "reason": "PDF 读取失败"
}}
```
```

---

## D.2 函数可达性验证 Sub-agent Prompt 模板

### 委托给 Explore sub-agent（model=sonnet）的 prompt

```
验证驱动的函数可达性：从系统启动入口到驱动注册函数，逐跳检查调用链是否完整。

需求文档路径：{requirements_path}
项目根目录：{project_root}
新驱动的 CONFIG 开关：{config_flag}（如 CONFIG_SENSORS_NTC）

执行以下步骤：

1. 从 requirements.md 中提取「系统集成分析」或「完整调用链」章节，获取函数调用链列表。
   如果 requirements.md 中没有调用链章节（Mode A 新驱动），则从驱动的 _register/_initialize 函数出发，向上搜索调用方直到 board_late_initialize 或 board_app_initialize。

2. 对调用链中的每个函数，执行以下检查：
   - grep 搜索函数定义是否存在（在 .c 文件中搜索函数名 + 左括号）
   - 检查函数所在文件是否在 Makefile/CMakeLists.txt 中被编译（新旧 CONFIG 下都检查）
   - 如果是 #ifdef 切换点，检查新旧分支是否都有实现
   - 如果文件被 filter-out，检查是否是启动链必经节点

3. 输出格式（严格遵守）：

```json
{{
  "chain": [
    {{
      "hop": 1,
      "function": "board_late_initialize",
      "file": "boards/.../board_bringup.c",
      "status": "exists",
      "compiled_new_config": true,
      "compiled_old_config": true
    }},
    {{
      "hop": 2,
      "function": "board_ntc_initialize",
      "file": "boards/.../board_ntc.c",
      "status": "exists",
      "compiled_new_config": true,
      "compiled_old_config": false,
      "note": "#ifdef CONFIG_SENSORS_NTC 保护"
    }},
    {{
      "hop": 3,
      "function": "ntc_register",
      "file": "nuttx/drivers/sensors/ntc_uorb.c",
      "status": "exists",
      "compiled_new_config": true,
      "compiled_old_config": false
    }}
  ],
  "overall": "PASS|FAIL",
  "broken_hops": [],
  "warnings": ["board_ntc.c 仅在新 CONFIG 下编译，旧 CONFIG 无此文件"]
}}
```

如果某一跳的函数不存在或文件未被编译，标记为 broken_hop 并在 broken_hops 数组中列出。
```

---

## E.3 端到端功能回查 Sub-agent Prompt 模板

### 委托给 Explore sub-agent（model=sonnet）的 prompt

```
验证 requirements.md 中的每个功能项是否都在驱动代码中实现了。

需求文档路径：{requirements_path}
驱动源文件路径：{driver_c_path}
驱动头文件路径：{driver_h_path}（如有）

执行以下步骤：

1. 从 requirements.md 中提取「功能 Checklist」章节，获取所有勾选（[x]）的功能项。

2. 对每个勾选的功能项，在驱动代码中搜索对应的实现：
   - PROD_ID / WHO_AM_I 校验 → 搜索 chip_id/prod_id 相关的读取和比较逻辑
   - 数据读取 → 搜索 push_event 或 fetch 回调实现
   - activate / deactivate → 搜索 activate 回调中的 enable/disable 分支
   - set_interval / ODR → 搜索 set_interval 回调
   - selftest → 搜索 selftest 回调或 self_test 相关函数
   - sleep / standby → 搜索 activate(false) 中的低功耗模式设置
   - 中断配置 → 搜索 irq_attach 或中断相关寄存器写入
   - FIFO batch → 搜索 batch 回调或 FIFO 相关寄存器操作

3. 输出格式（严格遵守）：

```json
{{
  "features": [
    {{"name": "PROD_ID 校验", "required": true, "implemented": true, "evidence": "line 142: if (chip_id != EXPECTED_ID)"}},
    {{"name": "数据读取", "required": true, "implemented": true, "evidence": "line 210: push_event()"}},
    {{"name": "selftest", "required": true, "implemented": false, "evidence": "未找到 selftest 回调或相关函数"}},
    {{"name": "FIFO batch", "required": true, "implemented": true, "evidence": "line 305: batch 回调实现"}}
  ],
  "total_required": 8,
  "total_implemented": 7,
  "missing": ["selftest"],
  "overall": "FAIL"
}}
```

overall = PASS 当且仅当所有 required=true 的功能都 implemented=true。
```

---

## D.3 跨文件审查 Sub-agent Prompt 模板

### 委托给 Explore sub-agent 的 prompt

```
对以下 NuttX 驱动代码执行跨文件系统性审查。你是独立的 Evaluator，不是代码的作者。请严格审查，不要因为代码"看起来合理"就跳过检查。

驱动文件：{file_list}
关联文件：{kconfig_path}, {makedefs_path}, {cmakelists_path}, {board_init_path}

按以下 5 项逐一检查，每项标记 PASS / WARN / FAIL：

1. 资源对称性：alloc 必有 free，lock 必有 unlock，irq attach 必有 detach，open 必有 close。检查所有路径包括错误路径。
2. 初始化时序：board_late_initialize vs board_app_finalinitialize 是否正确？依赖的子系统是否已初始化？
3. PM 完整性：activate(true) 和 activate(false) 是否对称？close 时是否断电？deactivate 后是否还有 work_queue 在跑？
4. 头文件与源文件声明一致性：.h 中声明的函数是否都在 .c 中实现？参数类型是否一致？
5. Kconfig 依赖完整性：CONFIG_XXX 是否在 Kconfig 中定义？depends on 是否正确？Make.defs 和 CMakeLists.txt 的条件是否与 Kconfig 一致？

对每个 FAIL 项，提供：行号 + 问题描述 + 修复代码片段。
对每个 WARN 项，提供：行号 + 问题描述。
PASS 项只输出一行确认。

输出格式：
  1. 资源对称性: PASS
  2. 初始化时序: FAIL — {file}:{line} {description} → 修复: {code}
  3. PM 完整性: WARN — {file}:{line} {description}
  ...
```

---

## A.3 系统集成分析 Sub-agent Prompt 模板

### 委托给 Explore sub-agent 的 prompt

```
分析以下驱动文件在系统中的完整集成方式，为重构提供事实依据。

目标文件：{file_list}
项目根目录：{project_root}

请执行以下分析并输出结构化结果：

1. **完整调用链**：从系统启动入口开始，逐跳追踪到硬件操作。每一跳标注：
   - 文件名 + 函数名
   - 是否 static（决定能否从外部调用）
   - 数据流方向（主动调用 / 回调注册 / 被动推送）
   - 是否是启动链必经节点（被 filter-out 会导致断链）

2. **替换边界**：在调用链上标注"保留不动的节点"和"要替换的节点"。边界线上的接口 = 必须兼容的切换点。

3. **数据流方向确认**：对每个 BSP 接口，确认数据是主动读取还是被动推送。

4. **上下游依赖表**：
   - 对每个目标文件，grep 搜索其导出函数/回调在项目中的所有调用点（上游消费方）
   - 对每个目标文件，grep 搜索其 #include 和函数调用的外部依赖（下游提供方）
   - 输出表格：| 原始接口 | 调用方文件 | 集成方式 | 需要适配/可直接替换 |

输出格式：

调用链图：
  系统入口 → func()  [文件, static/extern, 启动链节点, 不可filter-out]
                ↓ 回调注册
           func2()    [文件, static, 可替换]
  ---替换边界---
           func3()    [文件, 要替换]

上下游依赖表：
  | 原始接口 | 调用方文件 | 集成方式 | 重构对接方案 |

请尽量完整，不要遗漏调用点。
```

---

## A.2 参考驱动匹配结果输出格式

搜索完成后按以下格式输出结果：

```
🔍 参考驱动匹配结果：

芯片参考（寄存器/时序）：
- {source_path} — {match_reason} ✅

骨架参考（NuttX API 模式）：
1. {driver}.c — {match_reason} [推荐骨架]
2. {driver}.c — {match_reason}
3. {driver}.c — {match_reason}

→ 使用 {骨架排名第一} 作为骨架模板
→ 使用 {芯片参考} 作为寄存器/时序参考
```

自动选择骨架参考排名第一的作为骨架，无需用户确认。

---

## 飞书导出格式（交互 3）

导出为飞书云文档，包含以下章节：

1. **驱动概览**: 驱动名称、子系统、总线类型、架构模式
2. **代码结构**: 生成/修改的文件列表 + 职责说明
3. **实现功能**: 最终功能 checklist
4. **设计要点**: 关键设计决策
5. **审查结果**: PASS/WARN/FAIL 统计
6. **编译与测试**: 编译结果、测试用例数量
