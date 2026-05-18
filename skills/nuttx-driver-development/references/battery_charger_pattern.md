# Battery Charger/Monitor/Gauge Driver Pattern

> 本文档是 power/battery 子系统的入口。Agent 首先加载本文档，根据用户输入路由到对应的子模板。
>
> **前置依赖**：`battery_ops_reference.md`（所有类型都需要加载）
> **通用依赖**：`coding_rules.md`（编码规范）、`board_registration.md`（板级注册）
> **I2C 类型额外依赖**：`bus_access.md`（I2C/SPI 总线访问模式）

## 1. 适用范围

NuttX 提供三个电池相关的 upper-half 框架：

| 框架 | 头文件 | 职责 | 设备路径 |
|------|--------|------|---------|
| `battery_charger` | `battery_charger.h` | 充电控制 + 充电器状态检测 | `/dev/charge/xxx` |
| `battery_monitor` | `battery_monitor.h` | 多节电池 BMS 监控 | `/dev/bat_monN` |
| `battery_gauge` | `battery_gauge.h` | 单节电池电量计 | `/dev/bat_gaugeN` |

一个完整的电池方案通常由 **1 个 charger + 1 个 gauge/monitor** 组成。

---

## 2. 输入收集表

Agent 在交互 1 阶段收集以下信息。所有决策点必须由用户明确选择，禁止 agent 自行推断。

### 2.0 输入格式说明

| 输入类型 | 接受的格式 | 示例 |
|----------|-----------|------|
| 函数清单 | 函数原型字符串，或头文件路径（Agent 自行解析） | `void pmu_charger_init(void)` 或 `./include/pmu.h` |
| 寄存器表 | Markdown 表格（地址+名称+位域），或 datasheet PDF 页码 | `0x0B: CHIP_ID [7:0]` 或 "datasheet P42-45" |
| SOC-V 表 | C 数组字面量，或 CSV 文件路径 | `{4200000, 100}, {4100000, 95}, ...` 或 `./soc_table.csv` |
| I2C 地址 | 7-bit 十六进制 | `0x6A` |
| GPIO 引脚 | 板级宏名或引脚编号 | `GPIO_CHARGE_DET` 或 `PA5` |

### 2.1 必填项

| # | 字段 | 选项 | 说明 |
|---|------|------|------|
| 1 | **厂商前缀** | 自由文本（如 `bq25618`、`sample`） | 所有函数/宏/结构体的命名前缀 |
| 2 | **充电器类型** | A. Standalone 型 / B. I2C 可控型 / C. 纯插拔检测型 | 决定 charger 模板 |
| 3 | **电量计类型** | 无 / D. 软件电量计(ADC) / E. 硬件电量计(I2C) | 决定 gauge/monitor 模板 |

### 2.2 条件必填项（根据类型选择后追问）

#### 充电器类型 = A (Standalone)

| # | 字段 | 说明 |
|---|------|------|
| 4a | PMU SDK 函数清单 | `pmu_charger_init`/`get_status`/`set_irq_handler` 的实际函数名和签名 |
| 5a | ADC 采样接口 | ADC 函数名、通道号、坏值常量 |
| 6a | GPIO 接口 | 是否有外部检测引脚；如有，提供 configgpio/gpioread/gpiowrite 函数名 |
| 7a | 满充策略 | 超时 / 过压去抖 / 斜率检测（可多选） |

#### 充电器类型 = B (I2C 可控)

| # | 字段 | 说明 |
|---|------|------|
| 4b | I2C 地址 | 7-bit 地址（如 0x6A） |
| 5b | 芯片 ID 寄存器 | 地址 + 期望值（如 REG=0x0B, ID=0x05） |
| 6b | 寄存器表 | 至少包含：状态寄存器、充电控制寄存器、电压/电流设置寄存器 |
| 7b | 充电参数列表 | vindpm/iindpm/charge_curr/cv_vol/pre_curr/iterm_curr |

#### 充电器类型 = C (纯插拔检测)

| # | 字段 | 说明 |
|---|------|------|
| 4c | 检测方式 | GPIO 裸操作 / pinutils 框架 |
| 5c | 检测引脚极性 | 高电平=插入 / 低电平=插入 |

#### 电量计类型 = D (软件电量计)

| # | 字段 | 说明 |
|---|------|------|
| 4d | ADC 电压采样接口 | 设备路径（如 `/dev/batt_voltage`）或 SDK 函数 |
| 5d | SOC-V 表 | 按温度分段的电压-电量对照表 |
| 6d | 温度采样 | NTC 设备路径 或 无温度采样 |
| 7d | SOC 持久化 | KVDB / 文件 / 无 |

#### 电量计类型 = E (硬件电量计)

| # | 字段 | 说明 |
|---|------|------|
| 4e | I2C 地址 | 7-bit 地址 |
| 5e | 芯片型号 | 用于搜索参考驱动 |
| 6e | gauge profile | 是否需要写入 profile 参数（如 cw2218） |

### 2.3 可选项（所有类型通用）

| # | 字段 | 默认值 | 说明 |
|---|------|--------|------|
| 8 | 跨模块通信 | 直接回调 | 直接回调(`batt_event_cb`) / deventbus 解耦 |
| 9 | 配置方式 | 硬编码宏 | 硬编码宏(`#define`) / descriptor 结构体(`charger_params_s`) |
| 10 | 事件上报模式 | poll 通知 | poll 通知(`battery_*_changed()`) / ioctl 轮询(仅更新内部状态) |
| 11 | 目标板路径 | 无 | 如提供则生成板级注册代码 |

---

## 3. 接口契约（所有类型共用）

以下规范是所有 battery 驱动的硬约束，不随类型变化。

### 3.1 私有结构体规范

**必须**嵌入完整的 `battery_*_dev_s` 结构体作为首成员（不是裸 ops 指针）：

```c
/* ✅ 正确：嵌入完整 dev 结构体 */
struct xxx_charger_dev_s
{
  struct battery_charger_dev_s dev;   /* 必须是首成员 */
  /* 厂商私有字段 ... */
};

/* ❌ 错误：裸 ops 指针（BES 老代码模式，不要用） */
struct xxx_charger_dev_s
{
  FAR const struct battery_charger_operations_s *ops;
  sem_t batsem;  /* 错误：应该用 mutex_t */
};
```

**原因**：
- `battery_*_dev_s` 内含 `mutex_t batlock` + `list_node flist` + `uint32_t mask`，upper-half 依赖这些字段
- 嵌入模式允许 `(FAR struct xxx_charger_dev_s *)dev` 安全向下转型
- 所有 NuttX in-tree 驱动和 vendor 新驱动都用嵌入模式

### 3.2 锁类型

**必须**使用 `mutex_t` + `nxmutex_init/lock/unlock`，**禁止**使用 `sem_t` + `nxsem_init`：

```c
struct xxx_dev_s
{
  struct battery_charger_dev_s dev;
  mutex_t lock;                       /* ✅ 正确 */
  /* sem_t batsem;                       ❌ 禁止 */
};

/* 初始化 */
nxmutex_init(&priv->lock);           /* ✅ */
/* nxsem_init(&priv->batsem, 0, 1);     ❌ */

/* 使用 */
nxmutex_lock(&priv->lock);
/* ... 临界区 ... */
nxmutex_unlock(&priv->lock);
```

### 3.3 初始化函数签名

所有 battery 驱动的初始化函数**必须**遵循以下签名模式：

```c
/* 返回 dev 指针，失败返回 NULL */
FAR struct battery_charger_dev_s *xxx_charger_initialize(...);
FAR struct battery_monitor_dev_s *xxx_monitor_initialize(...);
FAR struct battery_gauge_dev_s   *xxx_gauge_initialize(...);
```

参数根据配置方式不同：

```c
/* 硬编码宏模式：无参数或仅传入总线句柄 */
FAR struct battery_charger_dev_s *xxx_charger_initialize(void);
FAR struct battery_gauge_dev_s *xxx_gauge_initialize(FAR struct i2c_master_s *i2c);

/* descriptor 模式：传入 config + params */
FAR struct battery_charger_dev_s *
xxx_charger_initialize(FAR struct charger_config_s *config,
                       FAR struct charger_params_s *desc);
```

### 3.4 内存分配与错误处理

```c
FAR struct xxx_dev_s *priv;

priv = kmm_zalloc(sizeof(struct xxx_dev_s));
if (priv == NULL)
  {
    baterr("ERROR: Failed to allocate instance\n");
    return NULL;
  }

priv->dev.ops = &g_xxx_ops;
nxmutex_init(&priv->lock);

/* ... 初始化失败时必须清理 ... */
ret = xxx_hw_init(priv);
if (ret < 0)
  {
    baterr("ERROR: HW init failed: %d\n", ret);
    nxmutex_destroy(&priv->lock);
    kmm_free(priv);
    return NULL;
  }

return (FAR struct battery_charger_dev_s *)priv;
```

### 3.5 不支持的 ops 回调

不支持的回调**必须**实现为返回 `-ENOSYS` 的函数，**禁止**在 ops 表中填 NULL：

```c
/* ✅ 正确 */
static int xxx_cellvoltage(FAR struct battery_monitor_dev_s *dev,
                           FAR struct battery_monitor_voltage_s *cellv)
{
  return -ENOSYS;
}
```

### 3.6 锁与上报的配合规则（防死锁）

`battery_*_changed()` 内部会获取 upper-half 的 `batlock`。如果在持有 `priv->lock` 的情况下调用，
可能导致 AB-BA 死锁（upper-half ioctl 路径：`batlock → ops → priv->lock`）。

**硬规则**：在 `priv->lock` 内完成数据更新和判断，unlock 后再调用 `changed()`：

```c
/* ✅ 正确：锁内判断，锁外上报 */
bool soc_changed = false;

nxmutex_lock(&priv->lock);
/* ... 更新数据 ... */
if (priv->soc != priv->last_soc)
  {
    priv->last_soc = priv->soc;
    soc_changed = true;
  }
nxmutex_unlock(&priv->lock);

if (soc_changed)
  {
    battery_gauge_changed(&priv->dev, BATTERY_CAPACITY_CHANGED);
  }

/* ❌ 错误：锁内调用 changed() 可能死锁 */
nxmutex_lock(&priv->lock);
battery_gauge_changed(&priv->dev, BATTERY_CAPACITY_CHANGED);
nxmutex_unlock(&priv->lock);
```

### 3.7 可选功能的降级路径（硬规则）

模板中标记为"可选"的功能（温度采样、KVDB 持久化、deventbus 等），
**必须**同时提供"有"和"没有"两条代码路径。禁止只写有的情况而留 TODO。

| 可选功能 | 有时的行为 | 没有时的降级行为 |
|----------|-----------|----------------|
| 温度采样 | 按温度段选择 SOC-V 表 | 直接使用第一段（常温表），跳过温度匹配 |
| KVDB 持久化 | 重启后从 KVDB 恢复 SOC | 从电压查表计算初始 SOC，充电中减去补偿电压 |
| deventbus | 通过消息总线通知 | 通过直接回调通知（或不通知，仅被动查询） |
| NTC 温度传感器 | 读取 NTC 设备计算温度 | temp ops 返回固定默认值（如 25°C） |

**代码模式**：

```c
/* ✅ 正确：两条路径都有完整实现 */
#ifdef CONFIG_XXX_NTC_TEMP
  /* 有温度：按温度段匹配 */
  for (i = 0; i < priv->soc_list->count; i++) { ... }
#else
  /* 无温度：直接用第一段 */
  *count = priv->soc_list->tables[0].count;
  return priv->soc_list->tables[0].soc_v_table;
#endif

/* ❌ 错误：只写了有的情况 */
for (i = 0; i < priv->soc_list->count; i++)
  {
    if (priv->batt_temp >= t->temp_min && ...)  /* batt_temp 未初始化！ */
  }
```

### 3.8 裸 GPIO API 规范

NuttX 没有统一的 `board_gpio_*` API。每个 BSP 有自己的 GPIO 函数，命名规范为 `{arch}_gpio*()`。

**模板中的 GPIO 占位符**（`xxx_configgpio`/`xxx_gpioread`/`xxx_gpiowrite`/`xxx_gpioirq_attach`）
**必须**在生成代码时替换为以下之一：

1. 目标 BSP 的实际函数（如 `stm32_configgpio`、`bes_gpioread`）
2. 用户在输入收集表中提供的函数名
3. 如果用户未提供，生成为 `static` stub 函数并标注 `/* TODO: Replace with BSP GPIO call */`

**禁止**生成虚构的 `board_gpio_*` 系列函数 — 这些在 NuttX 中不存在，会导致链接错误。

### 3.9 跨模块通信接口

Charger 和 Gauge/Monitor 之间需要通信（如充电器插拔通知电量计调整采样策略）。两种模式：

#### 模式 A：直接回调（简单场景，BES 模式）

```c
/* 在 monitor/gauge 的测量数据结构中定义回调 */
typedef void (*batt_event_cb_t)(int status, uint16_t volt);

/* charger 初始化时注册回调 */
priv->measure->batt_event_cb = xxx_charger_event_handler;

/* monitor 采样完成后调用 */
if (priv->measure->batt_event_cb)
  {
    priv->measure->batt_event_cb(status, volt);
  }
```

优点：简单直接，零额外依赖。
缺点：charger 和 monitor 编译时耦合。

#### 模式 B：deventbus 解耦（复杂场景，vendor 模式）

```c
/* 发送方（charger/plug_in）*/
#include <nuttx/deventbus/deventbus.h>

bool plug_state = true;
deventbus_send_msg(DEVENTBUS_AP_PLUGIN_STATE_MSG,
                          &plug_state, sizeof(bool));

/* 接收方（gauge）*/
struct deventbus_notifier dnotifier;
dnotifier.msg_type = DEVENTBUS_AP_PLUGIN_STATE_MSG;
dnotifier.notifier_call = plugin_state_callback;
deventbus_register(&dnotifier);
```

优点：完全解耦，支持多对多通信。
缺点：依赖 deventbus 框架（`CONFIG_DEVENTBUS`）。

#### 选择建议

| 场景 | 推荐 |
|------|------|
| 单芯片方案（charger + monitor 在同一 vendor 目录） | 直接回调 |
| 分立方案（独立 charger IC + 独立 gauge IC） | deventbus |
| 需要通知多个模块（如 gauge + 温控 + UI） | deventbus |

### 3.10 配置传入接口

#### 模式 A：硬编码宏（简单场景）

```c
/* 在 .h 或 Kconfig 中定义 */
#define XXX_BATTERY_MAX_MV              4200
#define XXX_BATTERY_MIN_MV              3400
#define XXX_BATTERY_PD_MV               3200
#define XXX_BATTERY_MONITOR_PERIODIC_MS 10000
#define XXX_BATTERY_STABLE_COUNT        5
```

优点：简单，编译时确定，零运行时开销。
缺点：同一驱动不能支持多个产品配置。

#### 模式 B：descriptor 结构体（灵活场景，vendor 模式）

```c
/* 公共头文件中定义 */
struct charger_params_s
{
  int vindpm;          /* 输入电压下限 (mV) */
  int iindpm;          /* 输入电流限制 (mA) */
  int charge_curr;     /* 充电电流 (mA) */
  int cv_vol;          /* 恒压充电电压 (mV) */
  int pre_curr;        /* 预充电电流 (mA) */
  int iterm_curr;      /* 终止电流 (mA) */
};

struct charger_config_s
{
  struct i2c_client_s client;   /* I2C 地址/频率 */
  struct pin_ctl_s pin_ctl;     /* GPIO/中断引脚配置 */
};

/* 初始化时传入 */
FAR struct battery_charger_dev_s *
xxx_initialize(FAR struct charger_config_s *config,
               FAR struct charger_params_s *desc);

/* 驱动内部从 desc 读取参数 */
xxx_setvolt(priv, priv->desc->cv_vol);
xxx_setcurr(priv, priv->desc->charge_curr);
```

优点：同一驱动二进制支持多个产品配置，board 层传入不同参数即可。
缺点：需要定义额外结构体，运行时多一层间接。

#### 选择建议

| 场景 | 推荐 |
|------|------|
| 单产品、参数固定 | 硬编码宏 |
| 多产品共用同一驱动 | descriptor |
| I2C 可控型（参数需要运行时设置） | descriptor |
| Standalone 型（参数编译时确定） | 硬编码宏 |

---

## 4. 决策树 → 加载子模板

根据用户在输入收集表中的选择，加载对应的子模板文件：

```
用户选择充电器类型
    │
    ├── A. Standalone 型
    │   → 加载 battery_standalone.md
    │   → 生成: xxx_charger.c + xxx_monitor.c + xxx_monitor.h
    │
    ├── B. I2C 可控型
    │   → 加载 battery_i2c_charger.md + bus_access.md
    │   → 生成: xxx_charger.c + xxx_charger_reg.h
    │
    └── C. 纯插拔检测型
        → 加载 battery_plugin_detect.md
        → 生成: xxx_plugin.c

用户选择电量计类型
    │
    ├── 无 → 跳过
    │
    ├── D. 软件电量计
    │   → 加载 battery_soft_gauge.md
    │   → 生成: xxx_soft_gauge.c + xxx_soft_gauge.h
    │
    └── E. 硬件电量计
        → 加载 battery_hw_gauge.md + bus_access.md
        → 生成: xxx_gauge.c + xxx_gauge.h + xxx_gauge_reg.h

所有类型都加载:
    → battery_ops_reference.md（ops 签名参考）
    → coding_rules.md（编码规范）
    → board_registration.md（板级注册）
```

### 文件生成矩阵

| 组合 | 生成文件 |
|------|---------|
| A + 无 | charger.c, monitor.c, monitor.h, board.c, Kconfig, Make.defs |
| A + D | charger.c, monitor.c, monitor.h, soft_gauge.c, soft_gauge.h, board.c, Kconfig, Make.defs |
| B + 无 | charger.c, charger_reg.h, board.c, Kconfig, Make.defs |
| B + E | charger.c, charger_reg.h, gauge.c, gauge.h, gauge_reg.h, board.c, Kconfig, Make.defs |
| C + 无 | plugin.c, board.c, Kconfig, Make.defs |
| C + D | plugin.c, soft_gauge.c, soft_gauge.h, board.c, Kconfig, Make.defs |
| C + E | plugin.c, gauge.c, gauge.h, gauge_reg.h, board.c, Kconfig, Make.defs |

### A+D 组合职责分工说明

当 Standalone 充电器（A）搭配软件电量计（D）时，两个模块的职责需要明确划分，避免 ADC 采样和 SOC 计算重叠：

| 职责 | A+无电量计 | A+D（有 soft_gauge） |
|------|-----------|---------------------|
| ADC 电压采样 | monitor 负责 | **soft_gauge 负责** |
| SOC 计算 | monitor 的 `soc` ops | **soft_gauge 独占** |
| 电压/温度阈值判定 | monitor 负责 | **soft_gauge 通过 measure 结构体提供数据，charger 做阈值判定** |
| 满充检查 | charger 的 event_cb | charger 的 event_cb（由 soft_gauge 周期回调触发） |
| 状态上报 | charger → `battery_charger_changed()` | charger → `battery_charger_changed()`，soft_gauge → `battery_gauge_changed()` |
| 应用层 SOC 读取 | `/dev/bat_mon0` 的 soc ops | **`/dev/bat_gauge0`** 的 capacity ops |

> **关键原则**：A+D 组合中，soft_gauge 替代了 monitor 的 ADC 采样和 SOC 职责。charger 通过 `xxx_gauge_get_measure()` 获取 soft_gauge 的共享测量数据（电压、SOC、状态），不再需要独立的 monitor 模块。
>
> 如果产品确实需要同时暴露 `/dev/bat_mon0`（阈值保护接口）和 `/dev/bat_gauge0`（SOC 接口），可以保留 monitor，但 monitor 的 `soc` ops 应返回 `-ENOSYS` 或转发到 soft_gauge，避免两套 SOC 计算逻辑。

---

## 5. Board 层注册模板

所有类型通用的 board 层注册模式（详细模板见 `board_registration.md`）：

```c
int board_xxx_power_initialize(void)
{
  FAR struct battery_charger_dev_s *charger;
  FAR struct battery_gauge_dev_s *gauge;  /* 或 battery_monitor_dev_s */
  int ret;

  /* Step 1: 初始化电量计（如有） */

  gauge = xxx_gauge_initialize(...);
  if (gauge == NULL)
    {
      baterr("ERROR: gauge init failed\n");
      return -ENODEV;
    }

  /* Step 2: 初始化充电器 */

  charger = xxx_charger_initialize(...);
  if (charger == NULL)
    {
      baterr("ERROR: charger init failed\n");
      return -ENODEV;
    }

  /* Step 3: 注册设备 */

  ret = battery_gauge_register("/dev/bat_gauge0", gauge);
  if (ret < 0)
    {
      baterr("ERROR: gauge register failed: %d\n", ret);
      return ret;
    }

  ret = battery_charger_register("/dev/charge/charger0", charger);
  if (ret < 0)
    {
      baterr("ERROR: charger register failed: %d\n", ret);
      return ret;
    }

  return OK;
}
```

> **初始化顺序**：电量计先于充电器。原因：充电器初始化时可能需要读取当前电池电压来决定初始状态。
> 如果使用直接回调模式，充电器初始化时会连接回调到电量计的测量数据结构。

---

## 6. Kconfig 模板

```kconfig
config BATTERY_CHARGER_XXX
	bool "XXX Battery Charger Driver"
	default n
	depends on BATTERY_CHARGER
	select I2C              # 仅 I2C 可控型需要
	---help---
		Enable driver for the XXX battery charger.

if BATTERY_CHARGER_XXX

# === 硬编码宏模式的可配置项 ===

config XXX_BATTERY_MAX_MV
	int "Full charge voltage (mV)"
	default 4200

config XXX_BATTERY_MIN_MV
	int "Low voltage warning threshold (mV)"
	default 3400

# ... 更多参数 ...

endif # BATTERY_CHARGER_XXX
```

---

## 7. Make.defs 模板

```makefile
ifeq ($(CONFIG_BATTERY_CHARGER_XXX),y)
  CSRCS += xxx_charger.c
endif

ifeq ($(CONFIG_BATTERY_GAUGE_XXX),y)
  CSRCS += xxx_gauge.c
endif
```

---

## 8. 参考驱动索引

| 类型 | 代表驱动 | 代码位置 | 特点 |
|------|---------|---------|------|
| Standalone | BES batt_charger | `vendor/bes/chips/bes/bes_batt_charger.c` | PMU 内置充电，ADC 采样，满充判断 |
| Standalone | MCP73871 | `nuttx/drivers/power/battery/mcp73871.c` | NuttX in-tree，最简 standalone |
| I2C 可控 | BQ25618 | `vendor/xiaomi/vela/drivers/power/charge/charger/bq25618.c` | I2C 充电 IC，完整寄存器操作 |
| I2C 可控 | SC89620 | 同上目录 `sc89620.c` | 类似 BQ25618，更多寄存器 |
| 纯插拔检测 | plug_in | 同上目录 `plug_in.c` | GPIO 检测 + deventbus + 完整 11 ops |
| 软件电量计 | soft_gauge | `vendor/xiaomi/vela/drivers/power/charge/gauge/soft_gauge.c` | ADC + SOC-V 表 + NTC + KVDB |
| 硬件电量计 | CW2218 | 同上目录 `cw2218.c` | I2C gauge IC + profile 写入 |
| 硬件电量计 | BQ27426 | `nuttx/drivers/power/battery/bq27426.c` | NuttX in-tree gauge |
