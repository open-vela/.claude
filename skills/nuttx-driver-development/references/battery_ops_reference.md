# Battery Framework Ops Reference

> **来源**：直接从 NuttX 头文件提取，禁止手写简化版。版本差异时以实际编译环境头文件为准。

## 1. battery_charger_operations_s（11 个回调）

来源：`include/nuttx/power/battery_charger.h`

```c
struct battery_charger_operations_s
{
  /* [必须] 返回电池状态 (enum battery_status_e) */
  CODE int (*state)(FAR struct battery_charger_dev_s *dev, FAR int *status);

  /* [必须] 返回电池健康状态 (enum battery_health_e) */
  CODE int (*health)(FAR struct battery_charger_dev_s *dev, FAR int *health);

  /* [必须] 返回电池是否在线 */
  CODE int (*online)(FAR struct battery_charger_dev_s *dev, FAR bool *status);

  /* [类型相关] 设置充电电压 (mV)。Standalone 型返回 OK */
  CODE int (*voltage)(FAR struct battery_charger_dev_s *dev, int value);

  /* [类型相关] 设置充电电流 (mA)。Standalone 型返回 OK */
  CODE int (*current)(FAR struct battery_charger_dev_s *dev, int value);

  /* [类型相关] 设置输入电流限制 (mA)。Standalone 型返回 OK */
  CODE int (*input_current)(FAR struct battery_charger_dev_s *dev, int value);

  /* [必须] 自定义操作。参数为 struct batio_operate_msg_s 指针 */
  CODE int (*operate)(FAR struct battery_charger_dev_s *dev, uintptr_t param);

  /* [必须] 返回芯片 ID */
  CODE int (*chipid)(FAR struct battery_charger_dev_s *dev,
                     FAR unsigned int *value);

  /* [可选] 获取实际输出充电电压 (mV) */
  CODE int (*get_voltage)(FAR struct battery_charger_dev_s *dev,
                          FAR int *value);

  /* [可选] 获取充电电压信息 */
  CODE int (*voltage_info)(FAR struct battery_charger_dev_s *dev,
                           FAR int *value);

  /* [可选] 获取充电协议 */
  CODE int (*get_protocol)(FAR struct battery_charger_dev_s *dev,
                           FAR int *value);
};
```

### charger dev 结构体

```c
struct battery_charger_dev_s
{
  FAR const struct battery_charger_operations_s *ops;
  mutex_t batlock;
  struct list_node flist;
  uint32_t mask;
  /* 厂商私有数据跟在后面 */
};
```

### charger 上报与注册

```c
int battery_charger_changed(FAR struct battery_charger_dev_s *dev, uint32_t mask);
int battery_charger_register(FAR const char *devpath, FAR struct battery_charger_dev_s *dev);
```

### operate 常见类型

`operate` 回调的 `param` 实际指向 `struct batio_operate_msg_s`（定义在 `battery_ioctl.h`）：

| operate_type | 用途 | 典型处理 |
|-------------|------|---------|
| `BATIO_OPRTN_VBUS_STATE` | VBUS 适配器状态变化 | 更新 adapter_state，触发 work_queue 上报 |
| `BATIO_OPRTN_CAPACITY` | 电量查询 | 返回当前 SOC |
| `BATIO_OPRTN_CYCLE_COUNT` | 循环次数 | 返回充放电循环计数 |
| `BATIO_OPRTN_CYCLE_LEVEL` | 循环等级 | 返回电池老化等级 |

不支持的 operate_type 返回 `-ENOSYS`。

---

## 2. battery_monitor_operations_s（16 个回调）

来源：`include/nuttx/power/battery_monitor.h`

```c
struct battery_monitor_operations_s
{
  /* [必须] 返回电池状态 (enum battery_status_e) */
  CODE int (*state)(FAR struct battery_monitor_dev_s *dev, FAR int *status);

  /* [必须] 返回电池健康状态 (enum battery_health_e) */
  CODE int (*health)(FAR struct battery_monitor_dev_s *dev, FAR int *health);

  /* [必须] 返回电池是否在线 */
  CODE int (*online)(FAR struct battery_monitor_dev_s *dev, FAR bool *status);

  /* [必须] 返回电池电压。注意：类型为 int *（单位 uV），不是 b16_t * */
  CODE int (*voltage)(FAR struct battery_monitor_dev_s *dev, FAR int *value);

  /* [可选] 返回多节电池各节电压 */
  CODE int (*cell_voltage)(FAR struct battery_monitor_dev_s *dev,
                           FAR struct battery_monitor_voltage_s *cellv);

  /* [可选] 返回电流 */
  CODE int (*current)(FAR struct battery_monitor_dev_s *dev,
                      FAR struct battery_monitor_current_s *current);

  /* [必须] 返回电量百分比。注意：类型为 b16_t *（定点数） */
  CODE int (*soc)(FAR struct battery_monitor_dev_s *dev, FAR b16_t *value);

  /* [可选] 返回库仑计读数 */
  CODE int (*coulombs)(FAR struct battery_monitor_dev_s *dev, FAR int *value);

  /* [可选] 返回温度传感器数据 */
  CODE int (*temperature)(FAR struct battery_monitor_dev_s *dev,
                          FAR struct battery_monitor_temperature_s *temps);

  /* [可选] 均衡开关控制 */
  CODE int (*balance)(FAR struct battery_monitor_dev_s *dev,
                      FAR struct battery_monitor_balance_s *bal);

  /* [必须] 低功耗控制。param=1 恢复正常，param=0 降低采样频率 */
  CODE int (*shutdown)(FAR struct battery_monitor_dev_s *dev, uintptr_t param);

  /* [必须] 设置安全阈值 */
  CODE int (*setlimits)(FAR struct battery_monitor_dev_s *dev,
                        FAR struct battery_monitor_limits_s *limits);

  /* [可选] 充放电开关控制 */
  CODE int (*chgdsg)(FAR struct battery_monitor_dev_s *dev,
                     FAR struct battery_monitor_switches_s *sw);

  /* [可选] 清除故障标志 */
  CODE int (*clearfaults)(FAR struct battery_monitor_dev_s *dev,
                          uintptr_t param);

  /* [可选] 自定义操作 */
  CODE int (*operate)(FAR struct battery_monitor_dev_s *dev, uintptr_t param);

  /* [必须] 返回芯片 ID */
  CODE int (*chipid)(FAR struct battery_monitor_dev_s *dev,
                     FAR unsigned int *value);
};
```

### monitor dev 结构体

```c
struct battery_monitor_dev_s
{
  FAR const struct battery_monitor_operations_s *ops;
  mutex_t batlock;
  struct list_node flist;
  uint32_t mask;
};
```

### monitor 上报与注册

```c
int battery_monitor_changed(FAR struct battery_monitor_dev_s *dev, uint32_t mask);
int battery_monitor_register(FAR const char *devpath, FAR struct battery_monitor_dev_s *dev);
```

### monitor 专用结构体

```c
struct battery_monitor_voltage_s
{
  int cell_count;                  /* 输入：期望读取的电池节数；输出：实际读取数 */
  FAR uint32_t *cell_voltages;     /* 各节电压数组，单位 uV */
};

struct battery_monitor_current_s
{
  int32_t current;                 /* 电流值，单位 uA */
  uint32_t time;                   /* 测量时间窗口，单位 uS。0 = 瞬时 */
};

struct battery_monitor_temperature_s
{
  int sensor_count;                /* 输入：期望读取的传感器数；输出：实际读取数 */
  FAR uint32_t *temperatures;      /* 温度值数组，单位 uV（需应用层转换） */
};

struct battery_monitor_balance_s
{
  int balance_count;
  FAR bool *balance;               /* 均衡开关数组，true=开启 */
};

struct battery_monitor_limits_s
{
  uint32_t overvoltage_limit;      /* 过压阈值，单位 uV */
  uint32_t undervoltage_limit;     /* 欠压阈值，单位 uV */
  uint32_t overcurrent_limit;      /* 过流阈值，单位 mA */
  uint32_t shortcircuit_limit;     /* 短路电流阈值，单位 mA */
  uint32_t overvoltage_delay;      /* 过压保护延迟，单位 uS */
  uint32_t undervoltage_delay;     /* 欠压保护延迟，单位 uS */
  uint32_t overcurrent_delay;      /* 过流保护延迟，单位 uS */
  uint32_t shortcircuit_delay;     /* 短路保护延迟，单位 uS */
};

struct battery_monitor_switches_s
{
  bool charge;                     /* 充电开关 */
  bool discharge;                  /* 放电开关 */
};
```

---

## 3. battery_gauge_operations_s（8 个回调）

来源：`include/nuttx/power/battery_gauge.h`

```c
struct battery_gauge_operations_s
{
  /* [必须] 返回电池状态 (enum battery_status_e) */
  CODE int (*state)(FAR struct battery_gauge_dev_s *dev, FAR int *status);

  /* [必须] 返回电池是否在线 */
  CODE int (*online)(FAR struct battery_gauge_dev_s *dev, FAR bool *status);

  /* [必须] 返回电池电压。注意：类型为 b16_t *（定点数，单位 V） */
  CODE int (*voltage)(FAR struct battery_gauge_dev_s *dev, FAR b16_t *value);

  /* [必须] 返回电量百分比。类型为 b16_t * */
  CODE int (*capacity)(FAR struct battery_gauge_dev_s *dev, FAR b16_t *value);

  /* [可选] 返回电流。类型为 b16_t *（单位 mA） */
  CODE int (*current)(FAR struct battery_gauge_dev_s *dev, FAR b16_t *value);

  /* [可选] 返回温度。类型为 b8_t *（定点数） */
  CODE int (*temp)(FAR struct battery_gauge_dev_s *dev, FAR b8_t *value);

  /* [必须] 返回芯片 ID */
  CODE int (*chipid)(FAR struct battery_gauge_dev_s *dev,
                     FAR unsigned int *value);

  /* [必须] 自定义操作 */
  CODE int (*operate)(FAR struct battery_gauge_dev_s *dev, FAR int *param);
};
```

### gauge dev 结构体

```c
struct battery_gauge_dev_s
{
  FAR const struct battery_gauge_operations_s *ops;
  mutex_t batlock;
  struct list_node flist;
  uint32_t mask;
};
```

### gauge 上报与注册

```c
int battery_gauge_changed(FAR struct battery_gauge_dev_s *dev, uint32_t mask);
int battery_gauge_register(FAR const char *devpath, FAR struct battery_gauge_dev_s *dev);
```

---

## 4. 三个框架的对比与选择

| 维度 | battery_charger | battery_monitor | battery_gauge |
|------|----------------|-----------------|---------------|
| 职责 | 充电控制 | 多节电池 BMS 监控 | 单节电池电量计 |
| ops 数量 | 11 | 16 | 8 |
| 电压类型 | 设置值 `int` (mV) | 读取值 `int *` (uV) | 读取值 `b16_t *` (V) |
| SOC | 无 | `b16_t *` | `b16_t *` (capacity) |
| 电流 | 设置值 `int` (mA) | `battery_monitor_current_s *` | `b16_t *` (mA) |
| 温度 | 无 | `battery_monitor_temperature_s *` | `b8_t *` |
| 安全阈值 | 无 | `setlimits` | 无 |
| 均衡 | 无 | `balance` | 无 |
| 充放电开关 | 无 | `chgdsg` | 无 |
| health | 有 | 有 | 无 |
| 典型场景 | 充电 IC 控制 | 多节锂电 BMS | 单节电池 SOC |
| 代表驱动 | bq25618, plug_in | bq769x0 | cw2218, soft_gauge |

### 选择规则

- **充电控制**（设置电压/电流/输入限流，或检测充电器插拔）→ `battery_charger`
- **多节电池监控**（需要 cell voltage、均衡、充放电开关）→ `battery_monitor`
- **单节电池电量计**（只需 SOC、电压、温度）→ `battery_gauge`
- **Standalone 型芯片**（PMU 内置充电 + ADC 采样）→ `battery_charger` + `battery_monitor`（BES 模式）
- **分立方案**（独立充电 IC + 独立 gauge IC）→ `battery_charger` + `battery_gauge`（vendor 模式）

> **注意**：`battery_monitor` 和 `battery_gauge` 不要混用。monitor 面向多节电池 BMS（如 bq769x0），
> gauge 面向单节电池电量计（如 cw2218、max1704x）。如果只需要 SOC + 电压 + 温度，用 gauge。

---

## 5. 通用枚举值

来源：`include/nuttx/power/battery_ioctl.h`

### battery_status_e

```c
enum battery_status_e
{
  BATTERY_UNKNOWN = 0,
  BATTERY_FAULT,
  BATTERY_IDLE,
  BATTERY_FULL,
  BATTERY_CHARGING,
  BATTERY_DISCHARGING
};
```

### battery_health_e

```c
enum battery_health_e
{
  BATTERY_HEALTH_UNKNOWN = 0,
  BATTERY_HEALTH_GOOD,
  BATTERY_HEALTH_DEAD,
  BATTERY_HEALTH_OVERHEAT,
  BATTERY_HEALTH_OVERVOLTAGE,
  BATTERY_HEALTH_UNSPEC_FAIL,
  BATTERY_HEALTH_COLD,
  BATTERY_HEALTH_WD_TMR_EXP,
  BATTERY_HEALTH_SAFE_TMR_EXP,
  BATTERY_HEALTH_DISCONNECTED
};
```

### 事件 mask（用于 changed 函数）

```c
#define BATTERY_STATE_CHANGED       (1 << 0)
#define BATTERY_HEALTH_CHANGED      (1 << 1)
#define BATTERY_ONLINE_CHANGED      (1 << 2)
#define BATTERY_VOLTAGE_CHANGED     (1 << 3)
#define BATTERY_CAPACITY_CHANGED    (1 << 4)
#define BATTERY_TEMPERATURE_CHANGED (1 << 5)
```

---

## 6. 定点数类型说明

gauge 框架大量使用 NuttX 定点数类型（`include/fixedmath.h`）：

| 类型 | 格式 | 整数部分 | 小数部分 | 转换宏 |
|------|------|---------|---------|--------|
| `b16_t` | Q16.16 | 16 bit | 16 bit | `itob16(i)` 整数→b16，`b16toi(b)` b16→整数 |
| `b8_t` | Q24.8 | 24 bit | 8 bit | `itob8(i)` 整数→b8，`b8toi(b)` b8→整数 |

```c
/* 示例：返回电压 3800mV */
*value = itob16(3800);

/* 示例：返回 SOC 85% */
*value = itob16(85);

/* 示例：返回温度 25.5°C */
*value = (b8_t)(25 << 8 | 128);  /* 0.5 = 128/256 */
```

> **注意**：vendor 的 soft_gauge 和 cw2218 实际代码中直接用 `int` 赋值给 `b16_t *`，
> 没有调用 `itob16()`。这在 gauge 的 upper-half 中能工作是因为 upper-half 直接透传值。
> 但严格来说应该使用转换宏。生成代码时**必须使用 `itob16()`/`itob8()`**。
