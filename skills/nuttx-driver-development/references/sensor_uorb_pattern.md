# Sensor uORB Driver Pattern — openvela 传感器框架

本文档是 sensor 驱动子系统的完整参考，由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

## Table of Contents

1. [一、框架概述](#一框架概述) — 上下半区模型、驱动核心职责、自动电源管理
2. [二、开发准备：代码与配置](#二开发准备代码与配置) — 关键文件位置、Kconfig、驱动文件布局
3. [三、关键数据结构](#三关键数据结构) — 传感器类型/主题、`sensor_lowerhalf_s`、驱动实现模式（单/多传感器、IMU pattern）
4. [四、核心 API 与驱动操作集](#四核心-api-与驱动操作集) — `sensor_register`、`sensor_ops_s` 回调详解
5. [五、数据采集模式](#五数据采集模式) — 中断模式（推荐）、轮询模式、fetch 模式、nbuffer 建议
6. [六、框架特性](#六框架特性) — 数据降采样、多核通信（sensor_rpmsg）
7. [七、设备私有结构与注册](#七设备私有结构与注册) — 私有结构模板、注册函数、公共头文件
8. [八、完整 Bring-Up 流程](#八完整-bring-up-流程) — 从 Kconfig 到应用层的端到端流程
9. [九、测试工具 sensortest](#九测试工具-sensortest) — 命令行测试工具用法
10. [十、Cross References](#十cross-references) — 关联文档索引

---

## 一、框架概述

openvela 传感器框架借鉴了 Linux IIO (Industrial I/O) 子系统的设计思想，提供统一、高效的传感器管理平台。核心是**分层架构**，通过将通用功能抽象到上半区，让驱动开发者专注于与物理硬件的交互逻辑。

### 驱动核心职责

- **驱动层关注物理传感器 (Physical Sensor)**：下半区驱动负责与物理传感器硬件直接交互——通信、数据采集和控制。
- **应用层处理虚拟传感器 (Virtual Sensor)**：通过数据融合生成的虚拟传感器（如设备姿态）在应用层通过 uORB 发布/订阅实现，不属于内核驱动职责。
- **多功能器件（如 IMU）**：需为每种物理功能（如加速度、陀螺）分别实例化 `lowerhalf` 结构，通过 `sensor_register` 注册为独立设备节点。

### 上下半区驱动模型 (Upper/Lower Half)

```
Application (read/write/ioctl/poll)
    │
    ▼
VFS: /dev/uorb/sensor_<type><devno>
    │
    ▼
Upper-half: sensor.c ← 通用层，处理所有与硬件无关的公共逻辑
    │  activate / set_interval / batch / fetch / ...
    ▼
Lower-half: YOUR driver ← 特定传感器的硬件抽象层
    │
    ▼
Bus: I2C_TRANSFER() / SPI_*()
```

**上半区 (Upper Half) 职责**:
- 创建和管理设备节点 (`/dev/uorb/*`)
- 实现标准文件操作接口 (`file_operations`)：`open`、`read`、`ioctl`
- 管理多用户并发访问
- 维护环形缓冲区 (Ring Buffer) 用于数据交换
- 执行数据降采样 (Down-sampling) 和低功耗管理

**下半区 (Lower Half) 职责**:
- 实现 `sensor_ops_s` 操作集，定义传感器具体行为
- 通过 I2C/SPI 等总线与传感器寄存器交互
- 在中断或轮询模式下采集数据，推送至上半区环形缓冲区

> [!NOTE] uORB 自动电源管理
>
> 上半区自动通过 `activate` 回调管理传感器电源：
> - 第一个订阅者打开设备 → 上半区调用 `activate(true)`
> - 最后一个订阅者关闭设备 → 上半区调用 `activate(false)`
>
> 无需实现引用计数，上半区自动处理。

## 二、开发准备：代码与配置

### 关键文件位置

- **框架核心**:
  - `nuttx/drivers/sensors/sensor.c`: 传感器上半区实现
  - `nuttx/drivers/sensors/sensor_rpmsg.c`: RPMSG 下半区实现
  - `nuttx/drivers/sensors/usensor.c`: 用户空间传感器注册实现
- **头文件**:
  - `nuttx/include/nuttx/sensors/sensor.h`: 传感器内部数据类型定义
  - `nuttx/include/nuttx/sensors/ioctl.h`: `ioctl` 命令定义
  - `nuttx/include/nuttx/uorb.h`: uORB 统一消息结构定义

### 内核配置项 (Kconfig)

在 `menuconfig` 中启用以下配置：
- `CONFIG_SENSORS`: 启用 openvela 传感器框架
- `CONFIG_USENSORS`: 启用用户空间传感器定义与注册功能
- `CONFIG_SENSORS_RPMSG`: 启用多核传感器通信能力

### 驱动文件布局

```
nuttx/
├── drivers/sensors/
│   ├── mydevice_uorb.c      # 驱动实现 (lower-half)
│   ├── Make.defs             # Add CSRCS += mydevice_uorb.c
│   ├── CMakeLists.txt        # Add list(APPEND SRCS mydevice_uorb.c)
│   └── Kconfig               # Add CONFIG_SENSORS_MYDEVICE entry
├── include/nuttx/sensors/
│   └── mydevice.h            # 公共头文件：注册函数原型
└── boards/<arch>/<chip>/<board>/src/
    └── <board>_mydevice.c    # 板级初始化：获取 I2C 总线，调用注册
```

### 参考驱动与示例代码

在编写驱动前，务必先查看 in-tree 参考驱动。详见 `references/nuttx_nav_search.md` 中 Sensor 部分的示例路径。

## 三、关键数据结构

### 传感器类型与主题

openvela 预定义了 53 种标准传感器类型，覆盖大部分物理传感器。所有类型定义在 `include/nuttx/sensors/sensor.h` 中，同时也被用作 uORB 通信主题。

| Type Constant | Data Structure | Device Path |
|---------------|---------------|-------------|
| `SENSOR_TYPE_ACCELEROMETER` | `struct sensor_accel` | `/dev/uorb/sensor_accel0` |
| `SENSOR_TYPE_GYROSCOPE` | `struct sensor_gyro` | `/dev/uorb/sensor_gyro0` |
| `SENSOR_TYPE_MAGNETIC_FIELD` | `struct sensor_mag` | `/dev/uorb/sensor_mag0` |
| `SENSOR_TYPE_BAROMETER` | `struct sensor_baro` | `/dev/uorb/sensor_baro0` |
| `SENSOR_TYPE_AMBIENT_TEMPERATURE` | `struct sensor_temp` | `/dev/uorb/sensor_ambient_temp0` |
| `SENSOR_TYPE_HUMIDITY` | `struct sensor_humi` | `/dev/uorb/sensor_humi0` |
| `SENSOR_TYPE_LIGHT` | `struct sensor_light` | `/dev/uorb/sensor_light0` |
| `SENSOR_TYPE_PROXIMITY` | `struct sensor_prox` | `/dev/uorb/sensor_prox0` |
| `SENSOR_TYPE_RGB` | `struct sensor_rgb` | `/dev/uorb/sensor_rgb0` |

所有传感器数据结构的第一个字段都是 `uint64_t timestamp`（微秒，CLOCK_MONOTONIC）。

**示例：加速度计事件数据结构**

```c
struct sensor_event_accel
{
  uint64_t timestamp;       /* 时间戳，单位: 微秒 (us) */
  float x;                  /* X 轴加速度，单位: m/s^2 */
  float y;                  /* Y 轴加速度，单位: m/s^2 */
  float z;                  /* Z 轴加速度，单位: m/s^2 */
  float temperature;        /* 器件温度，单位: 摄氏度 (°C) */
};
```

### 下半区接口结构: `sensor_lowerhalf_s`

该结构是连接上半区与下半区的核心桥梁。驱动开发者需要实例化并填充此结构中的指定字段。

```c
struct sensor_lowerhalf_s
{
  /* --- 由下半区驱动填充 --- */
  int type;                              /* 必需: SENSOR_TYPE_xxx */
  unsigned long nbuffer;                 /* 必需: 环形缓冲区大小(事件数) */
  bool uncalibrated;                     /* 可选: true 表示上报未校准数据 */
  FAR const struct sensor_ops_s *ops;    /* 必需: 驱动操作集 */
  bool persist;                          /* 可选: true 表示通知类主题 */

  /* --- 由上半区填充，供下半区调用 --- */
  union
    {
      sensor_push_event_t push_event;    /* 推荐: 推送数据到环形缓冲区 */
      sensor_notify_event_t notify_event;/* 仅 fetch 模式: 通知数据就绪 */
    };

  CODE void (*sensor_lock)(FAR void *priv);
  CODE void (*sensor_unlock)(FAR void *priv);
  FAR void *priv;                        /* 上半区上下文指针 */
};
```

**字段说明**:

| 成员 | 填充方 | 描述 |
|------|--------|------|
| `type` | 下半区 | **必需**。传感器类型，如 `SENSOR_TYPE_ACCELEROMETER` |
| `nbuffer` | 下半区 | **必需**。环形缓冲区大小（事件数量）。设为 0 则使用 fetch 模式 |
| `uncalibrated` | 下半区 | **可选**。`true` 表示上报未校准数据，设备节点自动添加 `_uncal` 后缀 |
| `ops` | 下半区 | **必需**。指向 `sensor_ops_s` 操作集 |
| `persist` | 下半区 | **可选**。`true` 表示通知类主题 |
| `push_event` | 上半区 | **推荐**。下半区调用此函数将数据推送到环形缓冲区 |
| `notify_event` | 上半区 | 仅与 fetch 模式配合，通知上半区数据已就绪 |
| `sensor_lock/unlock` | 上半区 | 导出的锁，供下半区避免递归死锁（仅 `sensor_rpmsg` 使用） |
| `priv` | 上半区 | 上半区上下文私有指针，供 `push_event` 等函数内部使用 |

### 驱动实现模式

根据硬件特性选择：

- **单芯片单传感器**（如仅有三轴加速度的 IAM20381）：实例化一个 `sensor_lowerhalf_s` 并注册一次。
- **单芯片多传感器**（如包含加速度计+陀螺仪+磁力计的 ICM20948 IMU）：为每种传感器功能分别实例化 `sensor_lowerhalf_s`，调用 `sensor_register` 多次，注册为独立设备节点（如 `/dev/uorb/sensor_accel0`, `/dev/uorb/sensor_gyro0`）。

> [!IMPORTANT] Pattern selection for multi-axis IMU devices
>
> Many existing NuttX IMU drivers (BMI160, BMI270, MPU60x0, ICM42688, etc.) use
> standalone char device pattern instead of uORB. This is because:
>
> 1. IMU devices output accel + gyro data that must be read atomically with a
>    single timestamp. Splitting into separate uORB topics loses synchronization.
> 2. The combined data maps directly to a contiguous register block, enabling
>    efficient single-burst reads.
>
> **Rule of thumb**: Check existing in-tree drivers for the same device class
> first. If similar devices use char device pattern, follow that convention.
> If the NuttX community has migrated that device class to uORB, use uORB.
> Chardev is only allowed for multi-axis IMU where accel+gyro require atomic
> reads with a single timestamp AND in-tree drivers for the same class use chardev.

## 四、核心 API 与驱动操作集

### 上半区辅助 API

#### 设备注册与注销

```c
/* 注册/注销标准类型传感器 */
int sensor_register(FAR struct sensor_lowerhalf_s *dev, int devno);
void sensor_unregister(FAR struct sensor_lowerhalf_s *dev, int devno);

/* 注册/注销自定义类型传感器 */
int sensor_custom_register(FAR struct sensor_lowerhalf_s *dev,
                           FAR const char *path, unsigned long esize);
void sensor_custom_unregister(FAR struct sensor_lowerhalf_s *dev,
                              FAR const char *path);
```

- `sensor_register`: 注册标准类型传感器，在 `/dev/uorb/` 下生成节点（如 `sensor_accel0`）。`devno` 用于区分同类型多个设备。
- `sensor_custom_register`: 注册自定义类型传感器，允许指定字符设备路径和事件数据大小。

#### 获取时间戳

```c
static inline uint64_t sensor_get_timestamp(void);
```

返回微秒精度的系统时间戳，下半区驱动封装传感器事件时应调用此接口填充 `timestamp` 字段。

### 下半区操作集: `sensor_ops_s`

```c
struct sensor_ops_s
{
  CODE int (*open)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep);
  CODE int (*close)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep);
  CODE int (*activate)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, bool enable);
  CODE int (*set_interval)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, FAR unsigned long *period_us);
  CODE int (*batch)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, FAR unsigned long *latency_us);
  CODE int (*fetch)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, FAR char *buffer, size_t buflen);
  CODE int (*selftest)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, unsigned long arg);
  CODE int (*calibrate)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, unsigned long arg);
  CODE int (*set_calibvalue)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, unsigned long arg);
  CODE int (*get_info)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep, FAR struct sensor_device_info_s *info);
  CODE int (*flush)(FAR struct sensor_lowerhalf_s *lower, FAR struct file *filep);
  CODE int (*control)(FAR struct sensor_lowerhalf_s *lower, int cmd, unsigned long arg);
};
```

#### 操作函数详解

| Callback | Purpose | Required? |
|----------|---------|-----------|
| `open` / `close` | 打开/关闭设备。通常仅 `sensor_rpmsg` 使用，物理驱动不需要实现 | No |
| `activate` | 激活/禁用传感器，启动/停止数据采集的核心控制。**不应在内部调用 `push_event`** | **Yes** |
| `set_interval` | 设置采样周期(ODR)。`period_us` 为期望周期(微秒)，驱动应设置最接近硬件支持的值并**通过指针返回实际值** | **Yes** |
| `batch` | 设置批处理模式最大上报延迟。针对有硬件 FIFO 的传感器，允许数据缓存后批量上报以降低功耗 | Optional (FIFO) |
| `fetch` | 主动拉取单次数据。适用于非事件驱动场景。采用 `push_event` 的驱动此接口置 `NULL` | Conditional |
| `selftest` | 执行传感器自检。用于生产测试或设备诊断 | If HW supports |
| `calibrate` | 触发校准流程，通过 `arg` 返回校准结果 | If HW supports |
| `set_calibvalue` | 将外部校准数据写入传感器 | If HW supports |
| `get_info` | 获取设备信息（名称、版本、量程等） | Optional |
| `flush` | 清空硬件 FIFO 并立即上报。完成标志：调用 `push_event(..., 0)` 推送长度为 0 的事件 | If FIFO |
| `control` | 自定义控制通道，用于标准接口无法满足的私有 ioctl 命令 | Optional |

> [!IMPORTANT] Feature implementation rules based on datasheet
>
> - If the datasheet defines a **self-test** command or register → implement `selftest`
> - If the datasheet defines **sleep/standby/low-power** modes → `activate(false)` must enter lowest power state
> - If the datasheet provides **high-precision output** → must use full precision, not truncate
> - If the datasheet defines **diagnostic status** registers → check during init
> - These are not optional when the hardware supports them.

## 五、数据采集模式

根据传感器硬件特性和应用需求选择最合适的数据采集和上报模式。

> [!IMPORTANT] Data acquisition mode selection
>
> 1. **设备有 DATA_READY 中断引脚** → 使用中断驱动 push 模式 (ISR → LPWORK → push_event)，最高质量实现
> 2. **无中断，但可配置 ODR** → 使用 LPWORK 定时器驱动 push 模式 (work_queue 周期轮询)
> 3. **低 ODR 或强制测量模式传感器** (如单次测量) → 使用 fetch 模式
>
> Rules:
> - **传感器数据采集统一使用 LPWORK** — I2C/SPI 总线操作会阻塞，HPWORK 回调要求 < 1ms 且不可阻塞，不适合总线读取
> - HPWORK 仅用于不涉及总线操作的极短回调（如纯内存操作的事件通知）
> - 如果设备同时支持中断和轮询，优先使用中断驱动模式
> - 建议采样率高于 **25Hz** 的传感器配置中断引脚

### 模式 1: 中断模式 (推荐)

当中断发生时，在中断处理的下半部（worker thread）中通过 I2C/SPI 总线获取传感器数据，调用 `push_event` 推送到上半区环形缓冲区。

**ops 定义**:

```c
static const struct sensor_ops_s g_sensor_ops =
{
  .activate      = mydevice_activate,
  .set_interval  = mydevice_set_interval,
  /* No fetch → push mode */
};
```

**ISR → LPWORK → push_event 模式**:

```c
/* ISR: 最小化 — 提交下半部 */

static int mydevice_isr(int irq, FAR void *context, FAR void *arg)
{
  FAR struct mydevice_dev_s *priv = arg;
  work_queue(LPWORK, &priv->work, mydevice_worker, priv, 0);
  return OK;
}

/* 下半部: 线程上下文，可阻塞 */

static void mydevice_worker(FAR void *arg)
{
  FAR struct mydevice_dev_s *priv = arg;
  struct sensor_accel data;

  /* 通过 I2C/SPI 读取传感器数据 */

  mydevice_getregs(priv, REG_DATA_START, (FAR uint8_t *)&raw, 6);

  data.timestamp = sensor_get_timestamp();
  data.x = raw_to_accel(raw[0], raw[1]);
  data.y = raw_to_accel(raw[2], raw[3]);
  data.z = raw_to_accel(raw[4], raw[5]);

  /* 推送到上半区环形缓冲区 */

  priv->sensor_lower.push_event(priv->sensor_lower.priv,
                                &data, sizeof(data));
}
```

### 模式 2: 轮询模式

对于不支持硬件中断的传感器，通过定时轮询采集数据，然后调用 `push_event` 推送到环形缓冲区。

**ops 定义** (与中断模式相同):

```c
static const struct sensor_ops_s g_sensor_ops =
{
  .activate      = mydevice_activate,
  .set_interval  = mydevice_set_interval,
  /* No fetch → push mode */
};
```

**activate — 启动/停止 work_queue + 硬件电源**:

```c
static int mydevice_activate(FAR struct sensor_lowerhalf_s *lower,
                              FAR struct file *filep, bool enable)
{
  FAR struct mydevice_dev_s *priv =
    container_of(lower, FAR struct mydevice_dev_s, sensor_lower);

  if (enable)
    {
      /* 配置硬件为正常模式 */

      mydevice_set_normal_mode(priv);

      /* 启动周期性数据采集 */

      work_queue(LPWORK, &priv->work, mydevice_worker, priv,
                 priv->interval / USEC_PER_TICK);
    }
  else
    {
      /* 停止周期性数据采集（sync 确保 worker 不再运行） */

      work_cancel_sync(LPWORK, &priv->work);

      /* 设置硬件为低功耗模式 */

      mydevice_set_sleep_mode(priv);
    }

  priv->activated = enable;
  return OK;
}
```

**Worker 函数 — 自重入队列模式**:

```c
static void mydevice_worker(FAR void *arg)
{
  FAR struct mydevice_dev_s *priv = arg;
  struct sensor_accel data;

  /* 先重新入队，再读取数据
   * 下次调用自动使用更新后的 priv->interval
   */

  work_queue(LPWORK, &priv->work, mydevice_worker, priv,
             priv->interval / USEC_PER_TICK);

  /* 从硬件寄存器读取原始数据 */

  mydevice_getregs(priv, REG_DATA_START, (FAR uint8_t *)&raw, 6);

  data.timestamp = sensor_get_timestamp();
  data.x = raw_to_accel(raw[0], raw[1]);
  data.y = raw_to_accel(raw[2], raw[3]);
  data.z = raw_to_accel(raw[4], raw[5]);

  /* 推送到上半区环形缓冲区 */

  priv->sensor_lower.push_event(priv->sensor_lower.priv,
                                &data, sizeof(data));
}
```

**set_interval — 更新采样间隔**:

```c
static int mydevice_set_interval(FAR struct sensor_lowerhalf_s *lower,
                                  FAR struct file *filep,
                                  FAR unsigned long *period_us)
{
  FAR struct mydevice_dev_s *priv =
    container_of(lower, FAR struct mydevice_dev_s, sensor_lower);

  /* 查找最接近的硬件支持 ODR 并配置 */

  unsigned long actual_period = mydevice_find_closest_odr(priv, *period_us);
  mydevice_set_odr(priv, actual_period);

  /* 更新间隔 — 下次 worker 重入队列时使用新值 */

  priv->interval = actual_period;
  *period_us = actual_period;
  return OK;
}
```

### 模式 3: 主动获取模式 (fetch)

上层应用每次调用 `read()` 时，直接通过 I2C/SPI 总线读取寄存器。

> [!WARNING] 官方不推荐常规场景使用此模式
>
> 缺点：
> - 总线访问速度较慢，会阻塞上层应用
> - 获取到的数据可能是旧数据，不能准确反映传感器状态变化
>
> 仅适用于采样率极低、数据量小的传感器（如 BMP280 气压计）。

**ops 定义**:

```c
static const struct sensor_ops_s g_sensor_ops =
{
  .activate      = mydevice_activate,
  .fetch         = mydevice_fetch,
  .set_interval  = mydevice_set_interval,
};
```

**fetch 实现**:

```c
static int mydevice_fetch(FAR struct sensor_lowerhalf_s *lower,
                           FAR struct file *filep,
                           FAR char *buffer, size_t buflen)
{
  FAR struct mydevice_dev_s *priv =
    container_of(lower, FAR struct mydevice_dev_s, sensor_lower);

  struct sensor_baro data;

  if (buflen != sizeof(data))
    {
      return -EINVAL;
    }

  /* 通过 I2C/SPI 读取原始数据 */
  /* 应用补偿/校准 */

  data.timestamp   = sensor_get_timestamp();
  data.pressure    = compensated_pressure;
  data.temperature = compensated_temperature;

  memcpy(buffer, &data, sizeof(data));
  return buflen;
}
```

> [!NOTE] fetch 模式下上半区自动禁用环形缓冲区
>
> 使用 fetch 时，`nbuffer` 默认为 0，上半区不分配环形缓冲区。
> 当以非阻塞方式打开设备节点时，`fetch` 直接读取寄存器，`poll` 总是成功；
> 当以阻塞方式打开时，可使用 `poll` 监控 `POLLIN` 事件。

### 缓冲区大小建议 (nbuffer)

使用中断或轮询模式时，通过 `sensor_lowerhalf_s.nbuffer` 设置环形缓冲区大小：

- **高采样率传感器** (>25Hz): 建议设置为 2-3，应对调度延迟
- **低采样率传感器** (<25Hz): 设置为 1 即可

```c
priv->sensor_lower.nbuffer = 8;  /* 环形缓冲区容纳 8 个事件 */
```

## 六、框架特性

### 数据降采样 (Down-sampling)

数据降采样是上半区提供的核心能力，允许订阅者以低于硬件采样率的频率获取数据，无需驱动干预。

- **机制**: 发布者（驱动）以硬件设定速率写入环形缓冲区。订阅者请求数据时，上半区根据订阅者设置的频率智能选取数据点，跳过多余样本。
- **优势**:
  - **解耦**: 驱动只需以固定频率工作，无需为每个订阅者动态调整硬件采样率
  - **高效**: 避免不必要的数据拷贝和处理，降低 CPU 负载
  - **灵活**: 支持对齐和非对齐降采样

### 多核通信机制 (sensor_rpmsg)

通过 `sensor_rpmsg` 下半区驱动实现跨 CPU 核心的传感器数据共享，核心是 Proxy/Stub 模型。

- **Proxy**: 当一个核心上的应用订阅远程核心上的传感器时，在本地创建 Proxy 对象，代表远程发布者
- **Stub**: 在发布者所在核心上，为远程订阅创建 Stub 对象，代表远程订阅者
- **控制流** (订阅者 → 发布者): 本地订阅者修改采样率 → Proxy → RPMSG → Stub → 物理驱动 `set_interval`
- **数据流** (发布者 → 订阅者): 物理驱动采集数据 → Stub → RPMSG → Proxy → 订阅者
- **性能优化**: `sensor_rpmsg` 将一段时间内的消息打包批量发送，降低 IPC 频率和功耗

## 七、设备私有结构与注册

### 设备私有结构

```c
struct mydevice_dev_s
{
  struct sensor_lowerhalf_s sensor_lower;  /* 必须是第一个成员 (container_of) */
  FAR struct i2c_master_s *i2c;            /* I2C 总线接口 */
  struct work_s work;                      /* work_queue 句柄 (push 模式) */
  unsigned long interval;                  /* 采样间隔 (微秒) */
  uint8_t addr;                            /* I2C 设备地址 */
  int freq;                                /* I2C 总线频率 */
  bool activated;                          /* 设备电源状态 */

  /* 设备特定的校准、配置等 */
};
```

### 注册函数 (Public API)

```c
int mydevice_register(int devno, FAR struct i2c_master_s *i2c)
{
  FAR struct mydevice_dev_s *priv;
  int ret;

  priv = kmm_zalloc(sizeof(struct mydevice_dev_s));
  if (!priv)
    {
      snerr("Failed to allocate instance\n");
      return -ENOMEM;
    }

  priv->i2c  = i2c;
  priv->addr = MYDEVICE_I2C_ADDR;
  priv->freq = MYDEVICE_I2C_FREQ;

  priv->sensor_lower.ops     = &g_sensor_ops;
  priv->sensor_lower.type    = SENSOR_TYPE_ACCELEROMETER;
  priv->sensor_lower.nbuffer = 8;  /* Push mode: 环形缓冲区 */

  /* 验证芯片 ID */

  ret = mydevice_checkid(priv);
  if (ret < 0)
    {
      kmm_free(priv);
      return ret;
    }

  /* 初始化硬件（可能注册中断、初始化工作队列等） */

  ret = mydevice_initialize(priv);
  if (ret < 0)
    {
      goto err_init;
    }

  /* 注册到传感器上半区框架 */

  ret = sensor_register(&priv->sensor_lower, devno);
  if (ret < 0)
    {
      snerr("Failed to register driver: %d\n", ret);
      goto err_register;
    }

  return OK;

err_register:
  mydevice_uninitialize(priv);  /* 清理中断、工作队列等 initialize 中分配的资源 */
err_init:
  kmm_free(priv);
  return ret;
}
```

### 公共头文件

```c
#ifndef __INCLUDE_NUTTX_SENSORS_MYDEVICE_H
#define __INCLUDE_NUTTX_SENSORS_MYDEVICE_H

#include <nuttx/config.h>

#if defined(CONFIG_I2C) && defined(CONFIG_SENSORS_MYDEVICE)

struct i2c_master_s;

#ifdef __cplusplus
#define EXTERN extern "C"
extern "C"
{
#else
#define EXTERN extern
#endif

int mydevice_register(int devno, FAR struct i2c_master_s *i2c);

#undef EXTERN
#ifdef __cplusplus
}
#endif

#endif /* CONFIG_I2C && CONFIG_SENSORS_MYDEVICE */
#endif /* __INCLUDE_NUTTX_SENSORS_MYDEVICE_H */
```

## 八、完整 Bring-Up 流程

```
1. Kconfig: CONFIG_SENSORS_MYDEVICE=y in defconfig
       │
2. Make.defs / CMakeLists.txt: mydevice_uorb.c added to build
       │
3. Board init: board_app_initialize()
       │  calls board_mydevice_initialize(devno, busno)
       │
4. board_mydevice_initialize():
       │  i2c = stm32_i2cbus_initialize(busno)
       │  mydevice_register(devno, i2c)
       │
5. mydevice_register():
       │  kmm_zalloc(priv)
       │  priv->sensor_lower.ops = &g_sensor_ops
       │  priv->sensor_lower.type = SENSOR_TYPE_XXX
       │  mydevice_checkid(priv)
       │  mydevice_initialize(priv)
       │  sensor_register(&priv->sensor_lower, devno)
       │
6. sensor_register() creates /dev/uorb/sensor_<type><devno>
       │
7. Application: open("/dev/uorb/sensor_accel0", O_RDONLY)
       │  → upper-half open → lower-half activate(true)
       │  read() → upper-half read → circular buffer / fetch()
       │  close() → upper-half close → lower-half activate(false)
```

## 九、测试工具 sensortest

`sensortest` 是命令行测试工具，用于在系统运行时与传感器驱动交互，验证控制和数据读取功能。

```bash
sensortest <device_node> [options]
```

- `device_node`: 必需，如 `accel0`
- `-i <interval_us>`: 设置采样间隔（微秒）
- `-h`: 查看帮助

**常用命令**:

```bash
# 以默认采样率 (1Hz) 持续读取
sensortest accel0

# 以 20Hz (50000us) 读取加速度计数据
sensortest accel0 -i 50000
```

## 十、Cross References

- 主 SKILL.md — 驱动通识知识（编码规范、内核 API、中断规则、同步原语等）
- `references/nuttx_nav_search.md` — Sensor 驱动示例路径和 NuttX 树导航
- `references/chardev_pattern.md` — 独立字符设备驱动模式（用于 IMU 等特殊场景）
- `references/bus_access.md` — I2C/SPI 总线访问 API 参考
- `references/board_registration.md` — 板级驱动注册模式
