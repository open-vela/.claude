# NuttX Watchdog Driver Pattern

## 框架概述

NuttX watchdog 子系统采用标准的 upper-half/lower-half 分层架构：

- **Upper-half**（`nuttx/drivers/timers/watchdog.c`）：提供字符设备接口（open/close/ioctl），处理 IOCTL 命令分发，管理 automonitor 功能
- **Lower-half**（芯片/板级实现）：实现硬件相关的 watchdog 操作（start/stop/keepalive/settimeout 等）

```
应用层
  │  open("/dev/watchdog0") → ioctl(WDIOC_START/STOP/KEEPALIVE/...)
  ▼
Upper-half (nuttx/drivers/timers/watchdog.c)
  │  ops->start / ops->stop / ops->keepalive / ...
  ▼
Lower-half (芯片驱动实现 struct watchdog_ops_s)
  │  硬件寄存器操作
  ▼
Watchdog 硬件
```

## 关键头文件

```
nuttx/include/nuttx/timers/watchdog.h
```

## Lower-half Ops 结构体定义

```c
struct watchdog_ops_s
{
  /* Required methods */

  CODE int (*start)(FAR struct watchdog_lowerhalf_s *lower);
  CODE int (*stop)(FAR struct watchdog_lowerhalf_s *lower);

  /* Optional methods */

  CODE int (*keepalive)(FAR struct watchdog_lowerhalf_s *lower);
  CODE int (*getstatus)(FAR struct watchdog_lowerhalf_s *lower,
                        FAR struct watchdog_status_s *status);
  CODE int (*settimeout)(FAR struct watchdog_lowerhalf_s *lower,
                         uint32_t timeout);
  CODE xcpt_t (*capture)(FAR struct watchdog_lowerhalf_s *lower,
                         CODE xcpt_t handler);
  CODE int (*ioctl)(FAR struct watchdog_lowerhalf_s *lower, int cmd,
                    unsigned long arg);
};
```

### Ops 回调说明

| Ops | 必选/可选 | 说明 |
|-----|----------|------|
| `start` | **必选** | 启动 watchdog 定时器，开始倒计时 |
| `stop` | **必选** | 停止 watchdog 定时器 |
| `keepalive` | 可选 | 喂狗（ping/pet），重置倒计时到当前 timeout 值 |
| `getstatus` | 可选 | 获取当前状态（flags + timeout + timeleft） |
| `settimeout` | 可选 | 设置新的超时值（毫秒），并重置定时器 |
| `capture` | 可选 | 设置超时回调（替代复位行为），返回旧 handler |
| `ioctl` | 可选 | 处理 upper-half 未识别的自定义 IOCTL |

## Lower-half 状态结构体

```c
struct watchdog_lowerhalf_s
{
  FAR const struct watchdog_ops_s *ops;  /* Lower half operations */
  /* 驱动私有数据跟在后面（cast-compatible） */
};
```

典型的驱动私有结构体扩展模式：

```c
struct my_wdg_lowerhalf_s
{
  const struct watchdog_ops_s *ops;  /* 必须是第一个字段 */
  uint32_t timeout;                  /* 当前超时值（ms） */
  bool started;                      /* 是否已启动 */
  xcpt_t handler;                    /* 用户超时回调 */
  void *upper;                       /* upper-half handle（用于 unregister） */
  /* ... 其他硬件相关字段 ... */
};
```

## 设备注册 API

```c
/* 标准注册（无 automonitor） */
FAR void *watchdog_register(FAR const char *path,
                            FAR struct watchdog_lowerhalf_s *lower);

/* 带 oneshot automonitor */
FAR void *watchdog_register(FAR const char *path,
                            FAR struct watchdog_lowerhalf_s *lower,
                            FAR struct oneshot_lowerhalf_s *oneshot);

/* 带 timer automonitor */
FAR void *watchdog_register(FAR const char *path,
                            FAR struct watchdog_lowerhalf_s *lower,
                            FAR struct timer_lowerhalf_s *timer);
```

- **返回值**：成功返回非 NULL handle（用于 `watchdog_unregister`），失败返回 NULL
- **调用时机**：在 `up_lateinitialize()` 或 `board_late_initialize()` 中调用
- **设备路径约定**：`/dev/watchdog0`、`/dev/watchdog1`... 或自定义路径如 `/dev/wdt_cpu0`

```c
void watchdog_unregister(FAR void *handle);
```

## IOCTL 命令表

| IOCTL | 值 | 参数 | 说明 |
|-------|-----|------|------|
| `WDIOC_START` | `_WDIOC(0x001)` | 无 | 启动 watchdog |
| `WDIOC_STOP` | `_WDIOC(0x002)` | 无 | 停止 watchdog |
| `WDIOC_GETSTATUS` | `_WDIOC(0x003)` | `struct watchdog_status_s *` | 获取状态 |
| `WDIOC_SETTIMEOUT` | `_WDIOC(0x004)` | `uint32_t`（毫秒） | 设置超时 |
| `WDIOC_CAPTURE` | `_WDIOC(0x005)` | `struct watchdog_capture_s *` | 设置超时回调 |
| `WDIOC_KEEPALIVE` | `_WDIOC(0x006)` | 无 | 喂狗 |
| `WDIOC_SETSOFT` | `_WDIOC(0x007)` | 无 | 设置为软件 watchdog |
| `WDIOC_SETWINDOW` | `_WDIOC(0x008)` | `struct watchdog_window_s *` | 设置窗口值 |
| `WDIOC_MINTIME` | `_WDIOC(0x080)` | `uint32_t`（毫秒） | 设置最小 ping 间隔 |

## 状态结构体

```c
struct watchdog_status_s
{
  uint32_t flags;     /* WDFLAGS_ACTIVE | WDFLAGS_RESET | WDFLAGS_CAPTURE */
  uint32_t timeout;   /* 当前超时设置（毫秒） */
  uint32_t timeleft;  /* 距离超时的剩余时间（毫秒） */
};

struct watchdog_capture_s
{
  CODE xcpt_t newhandler;  /* 新的超时回调 */
  CODE xcpt_t oldhandler;  /* 返回旧的超时回调 */
};

struct watchdog_window_s
{
  uint32_t lwindow;  /* 左窗口值（毫秒） */
  uint32_t rwindow;  /* 右窗口值（毫秒） */
};
```

## 状态标志位

| 标志 | 值 | 说明 |
|------|-----|------|
| `WDFLAGS_ACTIVE` | `(1 << 0)` | watchdog 正在运行 |
| `WDFLAGS_RESET` | `(1 << 1)` | 超时时执行系统复位 |
| `WDFLAGS_CAPTURE` | `(1 << 2)` | 超时时调用用户回调 |

## Automonitor 机制

NuttX watchdog 框架支持 automonitor 功能——由内核自动喂狗，无需应用层参与。通过 Kconfig 配置：

- `CONFIG_WATCHDOG_AUTOMONITOR_BY_ONESHOT` — 使用 oneshot timer 自动喂狗
- `CONFIG_WATCHDOG_AUTOMONITOR_BY_TIMER` — 使用 timer 自动喂狗
- `CONFIG_WATCHDOG_AUTOMONITOR_BY_WQUEUE` — 使用 work queue 自动喂狗
- `CONFIG_WATCHDOG_AUTOMONITOR_BY_CAPTURE` — 使用 capture 回调自动喂狗
- `CONFIG_WATCHDOG_AUTOMONITOR_BY_IDLE` — 在 idle 线程中自动喂狗

automonitor 在 `watchdog_register()` 时自动启动（如果配置了），lower-half 驱动无需额外处理。

## MCAL API → NuttX Ops 映射表

| NuttX Ops | MCAL API（AUTOSAR 标准名） | 说明 |
|-----------|---------------------------|------|
| `start` | `Wdg_SetMode(WDGIF_SLOW_MODE)` + `Wdg_SetTriggerCondition(timeout)` | MCAL WDG Init 后默认处于 DefaultMode，start 确保进入活跃模式并设置初始 trigger |
| `stop` | `Wdg_SetMode(WDGIF_OFF_MODE)` | 关闭 watchdog（需 `DisableAllowed=TRUE`） |
| `keepalive` | `Wdg_SetTriggerCondition(current_timeout)` | 重新触发 watchdog 计时 |
| `getstatus` | 无直接对应 | 从适配层内部状态返回 |
| `settimeout` | `Wdg_SetTriggerCondition(new_timeout)` | 设置新超时并触发 |
| `capture` | 无直接对应 | MCAL WDG 无用户回调机制，仅适配层内部记录 handler |
| `ioctl` | — | 可扩展支持 `WDIOC_SETWINDOW` 映射到 MCAL 窗口百分比 |

### MCAL WDG 模式说明

AUTOSAR WDG 定义三种模式（`WdgIf_ModeType`）：

| 模式 | 含义 | 对应 NuttX 行为 |
|------|------|----------------|
| `WDGIF_OFF_MODE` | 关闭 watchdog | `stop` ops |
| `WDGIF_SLOW_MODE` | 慢速模式（长超时） | 默认运行模式 |
| `WDGIF_FAST_MODE` | 快速模式（短超时） | 可通过 ioctl 切换 |

### MCAL WDG 关键行为

1. **`Wdg_Init(ConfigPtr)`** — 初始化 WDG 模块，进入 `DefaultMode`（通常是 SLOW_MODE）。Init 后 WDG **立即开始计时**。
2. **`Wdg_SetTriggerCondition(timeout)`** — 设置下一个超时周期（毫秒）。必须在窗口期内调用，否则被拒绝。
3. **`Wdg_SetMode(mode)`** — 切换模式。切换到 OFF_MODE 需要 `DisableAllowed=TRUE`。
4. **窗口机制** — WDG 有窗口百分比配置（`WindowSizePercentage[3]`），在窗口外调用 SetTriggerCondition 会报错。

### 适配层设计要点

1. **Init 后自动启动问题**：MCAL `Wdg_Init()` 后 WDG 立即开始计时，但 NuttX 框架期望 `start` ops 才启动。适配层需要在 Init 后立即开始内部喂狗（或依赖 automonitor），直到用户显式 start。
2. **stop 可能被拒绝**：如果 EB 配置 `DisableAllowed=FALSE`，`Wdg_SetMode(WDGIF_OFF_MODE)` 会返回 `E_NOT_OK`。适配层 `stop` ops 应返回 `-EPERM`。
3. **timeout 截断**：MCAL 有 `FastModeMaxTimeout` 和 `SlowModeMaxTimeout` 限制。如果用户请求的 timeout 超过当前模式最大值，适配层应截断到最大值并返回成功（或返回 `-EINVAL`）。
4. **无中断**：MCAL WDG 模块没有 ISR，不需要中断路由。超时直接触发硬件复位（或 NMI trap）。

## 参考实现

- **iLLD 参考**：`vendor/infineon/chips/aurix/aurix_wdg.c` — 使用 iLLD API 实现的 NuttX watchdog lower-half
- **NuttX in-tree 参考**：`nuttx/drivers/timers/watchdog.c` — upper-half 实现
- **MCAL 适配骨架**：`frameworks/system/autocore/mcal/uart.c` — MCAL UART 适配层（结构参考）

## Kconfig 配置

```kconfig
config WATCHDOG
    bool "Watchdog Timer Support"
    default n

config WATCHDOG_AUTOMONITOR
    bool "Auto-monitor watchdog"
    default n
    depends on WATCHDOG
```

Lower-half 驱动通常需要：
```kconfig
config AUTOCORE_MCAL_WDG
    bool "Enable the MCAL Watchdog module"
    default n
    depends on WATCHDOG
```
