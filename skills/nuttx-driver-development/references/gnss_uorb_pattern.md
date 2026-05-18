# GNSS uORB 驱动开发参考 (gnss_uorb_pattern.md)

> 本文档是 `nuttx-driver-development` skill 的子系统参考文档，专用于 GNSS 驱动开发。
> 通识知识（编码规范、锁、中断规则等）见主 SKILL.md，本文不重复。

---

## 一、框架概述

GNSS 驱动使用独立的 `gnss_uorb.c` 上半区框架（非通用 `sensor.c`），采用 upper-half / lower-half 分层架构。与通用 sensor 框架的核心区别：

1. **双数据通道**：同时提供 NMEA 文本流 (`/dev/ttyGNSS`) 和 uORB 结构化事件
2. **内置 NMEA 解析**：框架层集成 minmea 库，自动解析 GGA/RMC/GST/GSV
3. **多子 sensor**：单次注册自动创建 5 个 uORB 子 sensor + 1 个 tty 设备
4. **inject_data**：支持应用层向芯片注入辅助数据（AGPS/星历）

### 架构层次

```
┌─────────────────────────────────────────────────────────┐
│  应用层                                                  │
│  /dev/ttyGNSS0 (NMEA read/write/poll)                   │
│  /dev/sensor/gnss0, gnss_satellite0, gnss_measurement0  │
│  /dev/sensor/gnss_clock0, gnss_geofence0                │
├─────────────────────────────────────────────────────────┤
│  上半区 — gnss_uorb.c                                   │
│  nuttx/drivers/sensors/gnss_uorb.c                      │
│  职责: NMEA 解析, uORB 注册, circbuf, poll/read, tty    │
├─────────────────────────────────────────────────────────┤
│  下半区 — 芯片驱动 (实现 gnss_ops_s)                    │
│  职责: 芯片协议, 电源状态机, 数据采集, SDK 集成         │
├─────────────────────────────────────────────────────────┤
│  板级 BSP — board_ops + 引脚/电源配置                   │
│  职责: GPIO/SPI/UART 配置, 电源控制, 中断管理           │
└─────────────────────────────────────────────────────────┘
```

### 关键源文件

| 层级 | 路径 |
|------|------|
| 上半区框架 | `nuttx/drivers/sensors/gnss_uorb.c` |
| 框架头文件 | `nuttx/include/nuttx/sensors/gnss.h` |
| 芯片驱动 (示例) | `drivers/gnss/<chip>/driver/<chip>_drv.c` |
| 板级 BSP (示例) | `boards/<soc>/<product>/src/bsp/gnss/<chip>/` |
| Kconfig (示例) | `drivers/gnss/Kconfig` |

---

## 二、开发准备

### 文件布局

GNSS 驱动涉及四个目录层级（路径因项目而异，以下为典型布局）：

```
drivers/gnss/
├── include/nuttx/gnss/<chip>.h          # 公共头文件 (类型定义 + register 声明)
├── Kconfig                              # 芯片选择 + 接口选择
├── CMakeLists.txt                       # 编译规则
└── <chip>/
    ├── driver/<chip>_drv.c[xx]          # gnss_ops_s 实现
    ├── pal/                              # SDK 平台适配层 (如有闭源 SDK)
    └── lib/                              # 闭源库 (如有)

boards/<soc>/<product>/src/bsp/gnss/<chip>/
├── <chip>_board.c                       # board_ops 实现
├── <chip>_board.h                       # board_xxx_initialize() 声明
├── <chip>_board_cfg.c                   # 引脚配置常量
├── <chip>_device_cfg.c                  # 设备配置常量 (nbuffer/文件路径)
└── CMakeLists.txt
```

### 参考驱动

编写 GNSS 驱动前，查看以下参考：

- **骨架参考**：`gnss_uorb.c` 上半区 + `gnss.h` 接口定义
- **芯片参考**：搜索 in-tree 已有的 GNSS 芯片驱动，了解 ops 实现、消息队列、异步上电模式
- **板级参考**：搜索 `boards/` 下已有的 GNSS BSP，了解 GPIO 抽象和电源时序

---

## 三、关键数据结构

### 下半区操作集: `gnss_ops_s`

```c
struct gnss_ops_s
{
  CODE int (*activate)(FAR struct gnss_lowerhalf_s *lower,
                       FAR struct file *filep, bool enable);
  CODE int (*set_interval)(FAR struct gnss_lowerhalf_s *lower,
                           FAR struct file *filep,
                           FAR uint32_t *period_us);
  CODE int (*get_info)(FAR struct gnss_lowerhalf_s *lower,
                       FAR struct file *filep,
                       FAR struct sensor_device_info_s *info);
  CODE int (*control)(FAR struct gnss_lowerhalf_s *lower,
                      FAR struct file *filep,
                      int cmd, unsigned long arg);
  CODE ssize_t (*inject_data)(FAR struct gnss_lowerhalf_s *lower,
                              FAR struct file *filep,
                              FAR const void *buffer, size_t buflen);
};
```

| Callback | Purpose | Required? |
|----------|---------|-----------|
| `activate` | 激活/禁用 GNSS 芯片，启动/停止定位 | **Yes** |
| `set_interval` | 设置定位上报周期 (微秒) | Optional |
| `get_info` | 获取设备信息 (名称/版本/厂商) | Optional |
| `control` | 自定义控制命令 (运动模式/辅助数据文件信息) | Optional |
| `inject_data` | 注入辅助数据 (AGPS 星历/RTO/LTO) | Optional |

> [!IMPORTANT] activate 是唯一必须实现的 ops
>
> - `activate(true)` 必须完成芯片上电 + 启动定位引擎
> - `activate(false)` 必须完全断电以降低功耗
> - 实现中**禁止阻塞** — 上电/固件下载等耗时操作必须通过 `work_queue` 异步执行

### 下半区接口结构: `gnss_lowerhalf_s`

```c
struct gnss_lowerhalf_s
{
  /* --- 由下半区驱动填充 --- */
  FAR const struct gnss_ops_s *ops;    /* 必需: 驱动操作集 */

  /* --- 由上半区 gnss_register() 填充，供下半区调用 --- */
  gnss_push_data_t  push_data;        /* 推送 NMEA 文本数据 */
  gnss_push_event_t push_event;       /* 推送结构化 sensor 事件 */
  FAR void *priv;                     /* 上半区上下文指针 */
};
```

| 成员 | 填充方 | 描述 |
|------|--------|------|
| `ops` | 下半区 | **必需**。指向 `gnss_ops_s` 操作集 |
| `push_data` | 上半区 | 推送 NMEA 原始数据到 ttyGNSS circbuf，框架自动解析 |
| `push_event` | 上半区 | 推送结构化数据到指定子 sensor 的 uORB 环形缓冲区 |
| `priv` | 上半区 | 上半区上下文，调用 push_data/push_event 时传入 |

> [!WARNING] push_data / push_event 由 gnss_register() 内部赋值
>
> 驱动层**禁止自行赋值**这两个函数指针。在 `gnss_register()` 返回后才可调用。

### uORB 子 sensor 索引

```c
#define SENSOR_GNSS_IDX_GNSS               0  /* 定位数据 (sensor_gnss) */
#define SENSOR_GNSS_IDX_GNSS_SATELLITE     1  /* 卫星信息 (sensor_gnss_satellite) */
#define SENSOR_GNSS_IDX_GNSS_MEASUREMENT   2  /* 原始测量 */
#define SENSOR_GNSS_IDX_GNSS_CLOCK         3  /* 时钟信息 */
#define SENSOR_GNSS_IDX_GNSS_GEOFENCE      4  /* 地理围栏 */
#define SENSOR_GNSS_IDX_GNSS_MAX           5
```

---

## 四、核心 API

### 设备注册

```c
int gnss_register(FAR struct gnss_lowerhalf_s *dev, int devno,
                  uint32_t nbuffer[], size_t count);
```

- `dev`: 下半区实例，`ops` 必须已赋值
- `devno`: 设备编号，用于区分多个 GNSS 芯片
- `nbuffer[]`: 每个子 sensor 的 circbuf 大小（事件数），数组长度 5
- `count`: **必须等于** `SENSOR_GNSS_IDX_GNSS_MAX` (5)

注册后自动创建的设备节点：

| 设备节点 | 数据类型 | 说明 |
|----------|----------|------|
| `/dev/ttyGNSS<n>` | NMEA 文本 | read/write/poll，应用可直接读取原始 NMEA |
| `/dev/sensor/gnss<n>` | `struct sensor_gnss` | 结构化定位数据 (经纬度/高度/精度) |
| `/dev/sensor/gnss_satellite<n>` | `struct sensor_gnss_satellite` | 可见卫星信息 |
| `/dev/sensor/gnss_measurement<n>` | `struct sensor_gnss_measurement` | 原始伪距测量 |
| `/dev/sensor/gnss_clock<n>` | `struct sensor_gnss_clock` | GNSS 时钟信息 |
| `/dev/sensor/gnss_geofence<n>` | `struct sensor_gnss_geofence` | 地理围栏事件 |

### 数据推送 API

```c
/* 推送 NMEA 文本 — 框架自动解析并生成 uORB 事件 */
void push_data(FAR void *priv, FAR const void *data,
               size_t bytes, bool is_nmea);

/* 推送结构化事件到指定子 sensor */
void push_event(FAR void *priv, FAR const void *data,
                size_t bytes, int type);
```

调用方式：

```c
/* 推送 NMEA — 框架解析 GGA/RMC → sensor_gnss, GSV → satellite */
lower->push_data(lower->priv, nmea_buf, len, true);

/* 推送已解析的结构化数据 — 跳过框架解析 */
lower->push_event(lower->priv, &gnss_data, sizeof(gnss_data),
                  SENSOR_TYPE_GNSS);
```

---

## 五、数据交互模式

GNSS 驱动有两种数据推送模式，**二选一**：

### 模式 1: NMEA 推送 (推荐)

驱动将原始 NMEA 语句推送给框架，框架使用 minmea 库自动解析。

```c
/* 驱动收到芯片 NMEA 数据后 */
lower->push_data(lower->priv, "$GNGGA,123519,4807.038,N,...*47\r\n",
                 len, true);  /* is_nmea = true */
```

**框架解析规则**：
- GGA + RMC **联合触发**：两者都收到后才推送一次 `sensor_gnss` 事件
- GSV **独立触发**：每收到完整 GSV 序列即推送 `sensor_gnss_satellite`
- GST 用于填充精度字段 (hdop/vdop)

**优势**：驱动实现简单，无需自行解析 NMEA
**限制**：依赖 minmea 库，仅支持标准 NMEA 语句

### 模式 2: 结构化事件推送

驱动自行解析数据，直接推送结构化事件。

```c
struct sensor_gnss gnss_data;
memset(&gnss_data, 0, sizeof(gnss_data));
gnss_data.timestamp = sensor_get_timestamp();
gnss_data.latitude  = 48.117300f;
gnss_data.longitude = 11.516700f;
gnss_data.altitude  = 520.0f;

lower->push_event(lower->priv, &gnss_data, sizeof(gnss_data),
                  SENSOR_TYPE_GNSS);
```

**优势**：支持非标准协议芯片，可推送框架不支持的字段
**限制**：驱动需自行维护 GGA+RMC 联合逻辑

> [!WARNING] 两种模式禁止混用
>
> 同一数据不可既推 NMEA 又推结构化事件，否则上层收到重复数据。
> 选择标准：芯片输出标准 NMEA → 模式 1；芯片输出私有协议 → 模式 2。

### ttyGNSS 双向通道

`/dev/ttyGNSS` 同时支持读和写：
- **read**: 应用读取 NMEA 原始数据（来自 `push_data`）
- **write**: 应用写入数据，框架调用 `inject_data` ops 传递给芯片（用于 AGPS 注入）

---

## 六、框架特性

### NMEA 自动解析 (minmea)

`gnss_uorb.c` 内置 minmea 解析器，支持以下语句：

| NMEA 语句 | 解析目标 | 触发条件 |
|-----------|----------|----------|
| GGA | `sensor_gnss` (经纬度/高度/卫星数) | 与 RMC 联合 |
| RMC | `sensor_gnss` (速度/航向/日期) | 与 GGA 联合 |
| GST | `sensor_gnss` (精度: hdop/vdop) | 附加到下次 gnss 事件 |
| GSV | `sensor_gnss_satellite` (卫星详情) | 独立触发 |

### 多子 sensor 自动注册

单次 `gnss_register()` 调用自动创建 5 个 uORB sensor + 1 个 tty 设备。`nbuffer[]` 数组控制每个子 sensor 的环形缓冲区大小：

```c
uint32_t nbuffer[SENSOR_GNSS_IDX_GNSS_MAX] = {
  1,   /* gnss: 定位数据，通常 1Hz，1 即可 */
  25,  /* satellite: GSV 数据量大，需要更大缓冲 */
  50,  /* measurement: 原始测量，高频 */
  1,   /* clock */
  1,   /* geofence */
};
```

### inject_data 辅助数据注入

应用通过 `write(/dev/ttyGNSS)` 注入辅助数据，框架转发到 `ops->inject_data`：

| 辅助数据类型 | 用途 | 典型有效期 |
|-------------|------|-----------|
| LTO (Long-Term Orbit) | 离线星历，加速冷启动 | 7 天 |
| RTO (Real-Time Orbit) | 在线星历，快速定位 | 数小时 |
| NV (Non-Volatile) | 芯片状态持久化 | 永久 |
| FW (Firmware) | 芯片固件更新 | N/A |

---

## 七、设备私有结构与注册

### 设备私有结构

```c
struct gnss_chip_dev_s
{
  struct gnss_lowerhalf_s gnss;          /* 必须: gnss 下半区实例 */
  FAR const struct gnss_chip_config_s *config; /* 聚合配置 */
  struct work_s work;                    /* work_queue 句柄 */
  mqd_t msg_mq;                         /* 消息队列 (命令调度) */
  int opencount;                         /* 引用计数 */
  bool activated;                        /* 芯片电源状态 */

  /* 子 sensor 数据缓存 */
  struct sensor_gnss gnss_data;
  struct sensor_gnss_satellite sat_data;

  /* 芯片特定字段 */
};
```

> [!IMPORTANT] gnss_lowerhalf_s 不要求是第一个成员
>
> 与 sensor 框架不同，GNSS 框架通过 `gnss_register` 传入指针，
> 不依赖 `container_of` 从 lowerhalf 反推私有结构。
> 但建议仍放在首位以保持一致性。

### 注册函数

```c
int gnss_chip_register(int devno, FAR struct gnss_chip_config_s *config)
{
  FAR struct gnss_chip_dev_s *priv;
  int ret;

  priv = kmm_zalloc(sizeof(struct gnss_chip_dev_s));
  if (!priv)
    {
      return -ENOMEM;
    }

  priv->config = config;
  priv->gnss.ops = &g_gnss_chip_ops;

  /* 初始化硬件 (SPI/UART 总线, 消息队列等) */

  ret = gnss_chip_hw_init(priv);
  if (ret < 0)
    {
      goto err_init;
    }

  /* 注册到 GNSS 上半区框架
   * nbuffer 来自 device_config，count 必须 == 5
   */

  ret = gnss_register(&priv->gnss, devno,
                      config->dev_cfg->nbuffer,
                      SENSOR_GNSS_IDX_GNSS_MAX);
  if (ret < 0)
    {
      goto err_register;
    }

  return OK;

err_register:
  gnss_chip_hw_deinit(priv);
err_init:
  kmm_free(priv);
  return ret;
}
```

### 板级初始化入口

```c
/* 板级 BSP 提供 */
int board_gnss_chip_initialize(int devno, int busno)
{
  FAR struct gnss_chip_config_s *config;

  /* 查找板级配置 + 设备配置 */

  config = gnss_chip_find_config(devno);
  if (!config)
    {
      return -ENODEV;
    }

  /* 获取 SPI/I2C 总线 */

  config->spi = board_spibus_initialize(busno);

  return gnss_chip_register(devno, config);
}
```

---

## 八、完整 Bring-Up 流程

```
1. Kconfig: CONFIG_SENSORS_GNSS=y, CONFIG_DRIVERS_GNSS_<CHIP>=y
       │
2. CMakeLists.txt / Make.defs: 芯片驱动源文件加入编译
       │
3. Board init: board_app_initialize()
       │  calls board_gnss_chip_initialize(devno, busno)
       │
4. board_gnss_chip_initialize():
       │  查找 board_cfg + device_cfg
       │  获取 SPI/UART 总线
       │  gnss_chip_register(devno, config)
       │
5. gnss_chip_register():
       │  kmm_zalloc(priv)
       │  priv->gnss.ops = &g_gnss_chip_ops
       │  gnss_chip_hw_init(priv)
       │  gnss_register(&priv->gnss, devno, nbuffer, 5)
       │
6. gnss_register() 创建:
       │  /dev/ttyGNSS<n>
       │  /dev/sensor/gnss<n>
       │  /dev/sensor/gnss_satellite<n>
       │  /dev/sensor/gnss_measurement<n>
       │  /dev/sensor/gnss_clock<n>
       │  /dev/sensor/gnss_geofence<n>
       │
7. 应用层:
       open("/dev/sensor/gnss0") → upper-half → activate(true)
       read() → circbuf → sensor_gnss 数据
       open("/dev/ttyGNSS0") → 读取原始 NMEA
       write("/dev/ttyGNSS0") → inject_data → 注入 AGPS
       close() → activate(false) → 芯片断电
```

### activate 异步上电模式 (推荐)

```c
static int gnss_chip_activate(FAR struct gnss_lowerhalf_s *lower,
                               FAR struct file *filep, bool enable)
{
  FAR struct gnss_chip_dev_s *priv = /* 获取私有结构 */;

  if (enable)
    {
      if (priv->opencount++ == 0)
        {
          /* 首次激活: 异步上电，禁止阻塞 */

          work_queue(LPWORK, &priv->work,
                     gnss_chip_poweron_worker, priv, 0);
        }
    }
  else
    {
      if (--priv->opencount == 0)
        {
          /* 最后一个关闭: 异步断电 */

          work_queue(LPWORK, &priv->work,
                     gnss_chip_poweroff_worker, priv, 0);
        }
    }

  return OK;
}
```

---

## 九、测试方法

### sensortest 命令行

```bash
# 读取 GNSS 定位数据 (1Hz)
sensortest gnss0

# 读取卫星信息
sensortest gnss_satellite0

# 设置 2Hz 上报
sensortest gnss0 -i 500000
```

### ttyGNSS 原始 NMEA

```bash
# 读取原始 NMEA 语句
cat /dev/ttyGNSS0

# 注入辅助数据
cat /data/misc/gnss/LTO2.dat > /dev/ttyGNSS0
```

### 验证 Checklist

- [ ] `/dev/ttyGNSS0` 可 open/read/poll，输出标准 NMEA
- [ ] `/dev/sensor/gnss0` 可订阅，收到 `sensor_gnss` 结构化数据
- [ ] `/dev/sensor/gnss_satellite0` 可订阅，收到卫星信息
- [ ] `activate(true)` 后芯片上电，开始定位
- [ ] `activate(false)` 后芯片完全断电，电流降至 μA 级
- [ ] 多次 open/close 引用计数正确，不会提前断电或泄漏
- [ ] AGPS 注入后冷启动 TTFF (Time To First Fix) < 10s

---

## 十、Cross References

- 主 SKILL.md — 驱动通识知识（编码规范、内核 API、中断规则、同步原语等）
- `references/sensor_uorb_pattern.md` — 通用 sensor uORB 模式（对比 GNSS 的差异）
- `references/board_registration.md` — 板级驱动注册模式
- `references/bus_access.md` — I2C/SPI 总线访问 API 参考
- `references/nuttx_nav_search.md` — NuttX 源码树导航与示例路径
- 芯片特定 skill — 如有具体芯片的 skill 文档，参考其中的 SDK 集成、板级 BSP 详细规则
