# Input Device Driver Pattern — NuttX 输入设备框架

本文档是 input 驱动子系统的完整参考，由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

## 文档约定

- `[!IMPORTANT]` 标注的是**必须遵守的规则**
- 代码模板是**推荐的参考实现**，不是唯一正确写法
- 对于已有项目的重构，应评估改动面和回归风险，不必强制对齐所有模板

---

## 一、框架概述

NuttX input 子系统包含 4 个独立的上半区框架。驱动开发者只需实现 lower-half 回调，上半区自动处理 VFS、缓冲、poll 通知。

### 驱动核心职责

- **驱动层**：硬件事件采集——中断处理、坐标/按键读取、事件上报
- **应用层**：手势识别、按键组合等高层逻辑，不属于驱动职责
- **多功能器件**（触摸+按键）：分别使用对应子框架注册为独立设备节点

### 子框架选择决策表

| 输入设备类型 | 子框架 | Upper-half | 设备节点 | 同步机制 | 数据模型 |
|-------------|--------|-----------|----------|----------|----------|
| 电容/电阻触摸屏 | **Touchscreen** | `touchscreen_upper.c` | `/dev/inputN` | spinlock | circbuf + event push |
| USB/I2C/PS2 鼠标 | **Mouse** | `mouse_upper.c` | `/dev/mouseN` | mutex | circbuf + event push |
| I2C/矩阵/GPIO 键盘 | **Keyboard** | `keyboard_upper.c` | `/dev/kbdN` | mutex | circbuf + event push |
| GPIO 按钮 | **Button** | `button_upper.c` | `/dev/btnN` | spinlock + mutex | 直接采样 + signal |

> [!IMPORTANT] 新驱动必须使用 upper-half 框架
>
> 当前 in-tree 的独立芯片驱动均为旧方式（直接实现 `file_operations`），不可作为架构参考。
> 框架层面的参考实现见 `uinput.c` 和 `goldfish_events.c`。

### 上下半区驱动模型

**Touch / Mouse / Keyboard**（事件流式模型）：

```
Application (read/poll)
    │
    ▼
VFS: /dev/inputN | /dev/mouseN | /dev/kbdN
    │
    ▼
Upper-half ← circbuf 缓冲 + poll 通知（Touch/Mouse 支持 grab 独占）
    │  事件上报: touch_event() / mouse_event() / keyboard_event()
    ▼
Lower-half: YOUR driver ← ISR → work_queue → 读硬件 → 构造事件 → 上报
    │
    ▼
Bus: I2C (见 bus_access.md) / SPI_*() / GPIO
```

**Button**（状态采样模型）：

```
Application (read/ioctl/poll)
    │
    ▼
VFS: /dev/btnN
    │
    ▼
Upper-half ← 中断去抖 + poll/signal 通知
    │  回调: bl_supported() / bl_buttons() / bl_enable()
    ▼
Lower-half: YOUR driver ← GPIO 状态读取 + 中断使能
```

---

## 二、开发准备：代码与配置

### 关键文件位置

- **上半区框架**: `nuttx/drivers/input/touchscreen_upper.c`, `mouse_upper.c`, `keyboard_upper.c`, `button_upper.c`
- **新框架参考**: `nuttx/drivers/input/uinput.c`（touch/kbd/mouse/btn 全覆盖）, `goldfish_events.c`（QEMU）
- **Button 通用下半区**: `nuttx/drivers/input/button_lower.c`
- **头文件**: `nuttx/include/nuttx/input/touchscreen.h`, `mouse.h`, `keyboard.h`, `buttons.h`

### Kconfig 依赖

总开关 `INPUT` 下有 4 个框架开关。芯片驱动必须用 `select` 启用对应框架：

```kconfig
config INPUT_MYTOUCH
	bool "MyVendor touch panel support"
	default n
	select INPUT_TOUCHSCREEN
	select I2C
```

### 驱动文件布局

```
nuttx/drivers/input/mydevice.c                     # 驱动实现
nuttx/include/nuttx/input/mydevice.h               # 公共头文件
boards/<arch>/<chip>/<board>/src/board_mydevice.c   # 板级注册
```

---

## 三、关键数据结构

### 3.1 Touchscreen

**lower-half 结构**（`include/nuttx/input/touchscreen.h`）：

```c
struct touch_lowerhalf_s
{
  uint8_t       maxpoint;  /* 【必需】最大触摸点数 */
  FAR void      *priv;     /* 【上半区自动填充】 */
  CODE int (*control)(FAR struct touch_lowerhalf_s *lower,
                      int cmd, unsigned long arg);   /* 【可选】自定义 ioctl */
  CODE ssize_t (*write)(FAR struct touch_lowerhalf_s *lower,
                        FAR const char *buffer, size_t buflen); /* 【可选】自定义 write */
};
```

**事件数据**：

```c
struct touch_point_s
{
  uint8_t  id;        /* 触摸点 ID，多点触控时唯一标识 */
  uint8_t  flags;     /* 见下方 flags 表 */
  int16_t  x, y;      /* 坐标 */
  int16_t  h, w;      /* 【可选】触摸区域尺寸 */
  uint16_t gesture;   /* 【可选】手势类型 */
  uint16_t pressure;  /* 【可选】压力值 */
  uint16_t dummy;
  uint64_t timestamp; /* 用 touch_get_time() 获取 */
};

struct touch_sample_s
{
  int32_t npoints;
  int32_t dummy;
  struct touch_point_s point[1]; /* 实际维度 npoints */
};

#define SIZEOF_TOUCH_SAMPLE_S(n) \
  (sizeof(struct touch_sample_s) + ((n) - 1) * sizeof(struct touch_point_s))
```

| Flag | Value | 含义 |
|------|-------|------|
| `TOUCH_DOWN` | 1<<0 | 新触摸接触 |
| `TOUCH_MOVE` | 1<<1 | 触摸移动 |
| `TOUCH_UP` | 1<<2 | 触摸释放 |
| `TOUCH_ID_VALID` | 1<<3 | ID 有效 |
| `TOUCH_POS_VALID` | 1<<4 | X/Y 有效 |
| `TOUCH_PRESSURE_VALID` | 1<<5 | 压力有效 |
| `TOUCH_SIZE_VALID` | 1<<6 | H/W 有效 |
| `TOUCH_GESTURE_VALID` | 1<<7 | 手势有效 |

### 3.2 Mouse

**lower-half 结构**（`include/nuttx/input/mouse.h`）：

```c
struct mouse_lowerhalf_s
{
  FAR void *priv;                    /* 【上半区自动填充】 */
  CODE int (*control)(...);          /* 【可选】自定义 ioctl */
  CODE int (*open)(...);             /* 【可选】仅首个 fd open 时调用 */
  CODE int (*close)(...);            /* 【可选】仅最后一个 fd close 时调用 */
  CODE ssize_t (*write)(...);        /* 【可选】自定义 write */
};
```

**事件数据**：`struct mouse_report_s { uint8_t buttons; uint8_t dummy; int16_t x, y, wheel; };`

- `buttons`: `MOUSE_BUTTON_1`(1<<0) / `_2`(1<<1) / `_3`(1<<2)

### 3.3 Keyboard

**lower-half 结构**（`include/nuttx/input/keyboard.h`）：

```c
struct keyboard_lowerhalf_s
{
  FAR void *priv;                    /* 【上半区自动填充】 */
  CODE int (*open)(...);             /* 【可选】每次 open 都调用 */
  CODE int (*close)(...);            /* 【可选】每次 close 都调用 */
  CODE ssize_t (*write)(...);        /* 【可选】自定义 write */
};
```

**事件数据**：`struct keyboard_event_s { uint32_t type; uint32_t code; };`

- `type`: `KEYBOARD_PRESS`(0) / `KEYBOARD_RELEASE`(1)
- `code`: X11 keycode，见 `x11_keysym.h`

### 3.4 Button

**lower-half 结构**（`include/nuttx/input/buttons.h`）：

```c
struct btn_lowerhalf_s
{
  CODE btn_buttonset_t (*bl_supported)
       (FAR const struct btn_lowerhalf_s *lower);  /* 【必需】返回支持的按钮位集合 */
  CODE btn_buttonset_t (*bl_buttons)
       (FAR const struct btn_lowerhalf_s *lower);  /* 【必需】返回当前按钮状态 */
  CODE void (*bl_enable)(FAR const struct btn_lowerhalf_s *lower,
                         btn_buttonset_t press, btn_buttonset_t release,
                         btn_handler_t handler, FAR void *arg);
                                             /* 【必需】使能/禁用中断 */
  CODE ssize_t (*bl_write)(FAR const struct btn_lowerhalf_s *lower,
                           FAR const char *buffer, size_t buflen);
                                             /* 【可选】自定义 write */
};
```

> [!WARNING] Button 框架与其他三个差异很大
>
> - 无 circbuf，`read()` 直接调用 `bl_buttons()` 返回 `btn_buttonset_t`（uint32_t 位掩码）
> - 通过 `bl_enable()` 注册中断回调，上半区负责去抖（`CONFIG_INPUT_BUTTONS_DEBOUNCE_DELAY`）
> - 支持 signal 通知（`BTNIOC_REGISTER`），其他三个不支持
> - 上半区用 spinlock + mutex 混合同步：spinlock 保护 open 链表遍历（ISR 上下文），mutex 保护 `bl_enable` 调用（线程上下文）。`bl_buttons()` 可能在两种上下文中被调用

---

## 四、核心 API 与回调

### 4.1 注册 / 注销 / 事件上报

| 子框架 | 注册 | 注销 | 事件上报 | 上报参数 |
|--------|------|------|---------|---------|
| Touch | `touch_register(lower, path, nums)` | `touch_unregister(lower, path)` | `touch_event(priv, sample)` | `priv` = `lower->priv` |
| Mouse | `mouse_register(lower, path, nums)` | `mouse_unregister(lower, path)` | `mouse_event(priv, sample)` | `priv` = `lower->priv` |
| Keyboard | `keyboard_register(lower, path, nums)` | `keyboard_unregister(lower, path)` | `keyboard_event(lower, keycode, type)` | 传 `lower` 指针 |
| Button | `btn_register(devname, lower)` | — | — | 上半区通过回调采样 |

- `nums` = circbuf 可缓存的事件数，建议 8（单点触摸/鼠标/键盘）或 16（多点触摸）。Touch 的 circbuf 大小 = `nums * SIZEOF_TOUCH_SAMPLE_S(maxpoint)`
- `touch_get_time()` — 微秒级时间戳，填入 `touch_point_s.timestamp`
- `keyboard_translate_virtio_code(keycode)` — virtio keycode → X11 keycode
- `btn_lower_initialize(devname)` — 使用通用 GPIO 下半区（需 `CONFIG_INPUT_BUTTONS_LOWER`）

> [!IMPORTANT] 同步机制决定调用上下文
>
> - **Touch**: 内部用 `spinlock` → `touch_event()` 可在 ISR 中直接调用（但总线操作仍需 work_queue）
> - **Mouse / Keyboard**: 内部用 `mutex` → `mouse_event()` / `keyboard_event()` **必须在线程上下文**调用
> - 典型做法：ISR 中调度 work_queue，在 worker 中完成总线读取后调用 event 上报

### 4.2 上半区已处理的 ioctl 与回调差异

- **Touch**: 上半区已处理 `TSIOC_GRAB` 和 `TSIOC_GETMAXPOINTS`，其余转发到 `control` 回调
- **Mouse**: 上半区已处理 `MSIOC_GRAB`；`open`/`close` 仅首个/最后一个 fd 时调用
- **Keyboard**: `open`/`close` 每次都调用（无 `list_is_singular` 检查）；无 grab 支持
- **Button**: `BTNIOC_SUPPORTED`（查询按钮集合）、`BTNIOC_POLLEVENTS`（设置 poll 条件）、`BTNIOC_REGISTER`（注册 signal 通知）

### 4.3 Button 回调

| 回调 | 必需 | 说明 |
|------|------|------|
| `bl_supported` | **Yes** | 返回支持的按钮位集合 |
| `bl_buttons` | **Yes** | 返回当前状态。ISR 和线程上下文都会调用 |
| `bl_enable` | **Yes** | 使能/禁用中断。`press`/`release` 指定关心的按钮集合；`press=0, release=0, handler=NULL, arg=NULL` 时禁用所有中断 |
| `bl_write` | No | 自定义 write |

---

## 五、数据采集模式

### 模式 1: 中断驱动 push（Touch / Mouse / Keyboard）

ISR → HPWORK → 读硬件 → 构造事件 → 调用 xxx_event() → 上半区 circbuf_overwrite + poll_notify。

**Touch ISR → Worker 示例**：

```c
static int mytouch_isr(int irq, FAR void *context, FAR void *arg)
{
  FAR struct mytouch_dev_s *priv = arg;
  priv->board->irq_enable(priv->board, false);
  work_queue(HPWORK, &priv->work, mytouch_worker, priv, 0);
  return OK;
}
```

> [!NOTE] ISR 签名与板级 API 的兼容性
>
> `xcpt_t`（即 `int (*)(int, FAR void *, FAR void *)`）是标准硬件中断的推荐签名。但如果板级中断注册 API（如 `pinutils_request_irq`）要求 `ioe_callback_t` 等其他签名，驱动应遵循板级 API 的要求。

```c
static void mytouch_worker(FAR void *arg)
{
  FAR struct mytouch_dev_s *priv = arg;
  struct touch_sample_s *sample = &priv->sample[0];

  if (!priv->hold)
    {
      return;
    }

  nxsem_wait_uninterruptible(&priv->semlock);
  if (mytouch_read_and_parse(priv, sample) == OK)
    {
      touch_event(priv->lower.priv, sample);
    }

  nxsem_post(&priv->semlock);
  if (priv->hold)
    {
      priv->board->irq_enable(priv->board, true);
    }
}
```

**多点触控数据解析模板**（从芯片寄存器到 `touch_sample_s`）：

```c
static int mytouch_read_and_parse(FAR struct mytouch_dev_s *priv,
                                  FAR struct touch_sample_s *sample)
{
  uint8_t raw[MYTOUCH_MAX_POINTS * 6 + 2];
  uint8_t finger_num;
  uint8_t i;

  if (mytouch_i2c_read(priv, MYTOUCH_REG_DATA, raw, sizeof(raw)) < 0)
    {
      return -EIO;
    }

  finger_num = raw[0] & 0x0f;
  if (finger_num == 0 || finger_num > MYTOUCH_MAX_POINTS)
    {
      return -EAGAIN;
    }

  /* npoints 设为 maxpoint：上半区按 SIZEOF_TOUCH_SAMPLE_S(maxpoint) 分配 circbuf，
   * 未触摸的点 flags=0 表示无效，应用层据此过滤。
   */

  memset(sample, 0, SIZEOF_TOUCH_SAMPLE_S(MYTOUCH_MAX_POINTS));
  sample->npoints = MYTOUCH_MAX_POINTS;

  for (i = 0; i < finger_num; i++)
    {
      uint8_t *p   = &raw[2 + i * 6];  /* Chip-specific byte layout */
      uint8_t  evt = p[0] >> 6;  /* Chip-specific event code, adjust mask per datasheet */

      sample->point[i].id        = p[2] & 0x0f;
      sample->point[i].x         = (int16_t)(((p[0] & 0x0f) << 8) | p[1]);
      sample->point[i].y         = (int16_t)(((p[2] & 0xf0) << 4) | p[3]);
      sample->point[i].pressure  = 1;
      sample->point[i].timestamp = touch_get_time();

      switch (evt)
        {
          case 0:  sample->point[i].flags = TOUCH_DOWN | TOUCH_ID_VALID | TOUCH_POS_VALID; break;
          case 1:  sample->point[i].flags = TOUCH_UP   | TOUCH_ID_VALID | TOUCH_POS_VALID; break;
          case 2:  sample->point[i].flags = TOUCH_MOVE | TOUCH_ID_VALID | TOUCH_POS_VALID; break;
          default: sample->point[i].flags = TOUCH_ID_VALID | TOUCH_POS_VALID; break;
        }
    }

  return OK;
}
```

**Mouse / Keyboard 事件上报**：

```c
/* Mouse — 必须线程上下文 */
struct mouse_report_s report = { .buttons = btns, .x = dx, .y = dy, .wheel = 0 };
mouse_event(priv->lower.priv, &report);

/* Keyboard — 直接传标量，注意传 &lower 不是 priv */
keyboard_event(&priv->lower, keycode, KEYBOARD_PRESS);
```

### 模式 2: 状态采样（Button 专用）

Button 无 circbuf，上半区在中断回调中直接采样 + 通知：

```
GPIO 中断 → btn_interrupt() → [可选去抖 wdog] → btn_sample()
  → bl_buttons() 读状态 → 计算 press/release → poll_notify() + signal
```

- `read()` 每次直接调用 `bl_buttons()` 返回实时状态
- `CONFIG_INPUT_BUTTONS_DEBOUNCE_DELAY > 0` 时用 watchdog timer 去抖
- 应用通过 `BTNIOC_POLLEVENTS` 过滤关心的按钮，通过 `BTNIOC_REGISTER` 注册 signal

### 关键行为总结

| 行为 | Touch/Mouse/Keyboard | Button |
|------|---------------------|--------|
| 缓冲 | circbuf_overwrite（满时覆盖最旧） | 无缓冲，直接采样 |
| 多 fd | 每个 open 独立 circbuf，事件广播 | 每个 open 独立 poll/signal 配置 |
| 阻塞 read | 阻塞等待 waitsem / O_NONBLOCK 返回 -EAGAIN | 直接返回当前状态 |
| grab | Touch/Mouse 支持（TSIOC_GRAB/MSIOC_GRAB） | 不支持 |

---

## 六、框架特性

### 6.1 Grab 独占模式（仅 Touch / Mouse）

```c
int enable = 1;
ioctl(fd, TSIOC_GRAB, enable);   /* Touch */
ioctl(fd, MSIOC_GRAB, enable);   /* Mouse */
```

启用后事件仅发送到 grab 持有者，其他 fd 不再收到数据。同一时间只能有一个 grab。

### 6.2 uinput 虚拟输入设备

启用 `CONFIG_UINPUT_TOUCH` / `_BUTTONS` / `_KEYBOARD` / `_MOUSE` 后，创建 `/dev/utouch`、`/dev/ubutton`、`/dev/ukeyboard`、`/dev/umouse`。通过 `write()` 注入事件到 upper-half，用于无硬件测试。启用 `CONFIG_UINPUT_RPMSG` 后支持跨核事件传输。

### 6.3 PM 电源管理（Touch 示例）

触摸驱动的 PM 状态机：`pm_notify` → suspend/resume → 控制中断和电源。

```c
#ifdef CONFIG_PM
static void mytouch_suspend(FAR struct mytouch_dev_s *priv)
{
  priv->board->irq_enable(priv->board, false);
  mytouch_enter_sleep(priv);
  priv->hold = false;
  if (priv->board->power_enable != NULL)
    {
      priv->board->power_enable(priv->board, false);
    }
}

static void mytouch_resume(FAR struct mytouch_dev_s *priv)
{
  if (priv->board->power_enable != NULL)
    {
      priv->board->power_enable(priv->board, true);
      usleep(10 * 1000);
    }

  mytouch_wake_up(priv);
  priv->hold = true;
  priv->board->irq_enable(priv->board, true);
}

static void mytouch_pm_notify(FAR struct pm_callback_s *cb,
                              int domain, enum pm_state_e pmstate)
{
  FAR struct mytouch_dev_s *priv =
    container_of(cb, struct mytouch_dev_s, pm);

  nxsem_wait_uninterruptible(&priv->semlock);
  if (pmstate != priv->current_state)
    {
      switch (pmstate)
        {
          case PM_NORMAL:  mytouch_resume(priv);  break;
          case PM_IDLE:
          case PM_STANDBY:
          case PM_SLEEP:   mytouch_suspend(priv); break;
          default: break;
        }

      priv->current_state = pmstate;
    }

  nxsem_post(&priv->semlock);
}
#endif
```

> [!NOTE] PM 回调使用 `container_of` 从 `cb` 反推 `priv`，天然支持多实例。不要用全局指针替代。
> 这里 `container_of` 是安全的：`pm_callback_s` 不是结构体首成员，但 `container_of` 只做指针偏移运算，不涉及 `typeof` 首成员优化问题（见 `coding_rules.md`）。

---

## 七、设备私有结构与注册

### 7.1 Touch 完整示例（I2C 触摸屏）

**公共头文件** `include/nuttx/input/mytouch.h`：

```c
#ifndef __INCLUDE_NUTTX_INPUT_MYTOUCH_H
#define __INCLUDE_NUTTX_INPUT_MYTOUCH_H

#include <nuttx/config.h>

#if defined(CONFIG_I2C) && defined(CONFIG_INPUT_MYTOUCH)

#include <nuttx/input/touchscreen.h>

struct i2c_master_s;

struct mytouch_board_s
{
  int  (*irq_attach)(FAR const struct mytouch_board_s *board,
                     xcpt_t isr, FAR void *arg);
  void (*irq_enable)(FAR const struct mytouch_board_s *board, bool enable);
  void (*nreset)(FAR const struct mytouch_board_s *board, bool nstate);
  void (*power_enable)(FAR const struct mytouch_board_s *board, bool enable);
};

int mytouch_register(FAR const char *devpath,
                     FAR struct i2c_master_s *i2c,
                     uint8_t addr,
                     FAR const struct mytouch_board_s *board);

#endif /* CONFIG_I2C && CONFIG_INPUT_MYTOUCH */
#endif /* __INCLUDE_NUTTX_INPUT_MYTOUCH_H */
```

**私有结构体** — `lower` 放第一个成员，便于直接转换（见 `coding_rules.md`）：

```c
struct mytouch_dev_s
{
  struct touch_lowerhalf_s          lower;
  FAR struct i2c_master_s          *i2c;
  uint8_t                           addr;
  FAR const struct mytouch_board_s *board;
  struct work_s                     work;
  mutex_t                           lock;    /* I2C bus mutex */
  sem_t                             semlock; /* Worker/PM serialization */
  bool                              hold;
#ifdef CONFIG_PM
  struct pm_callback_s              pm;
  enum pm_state_e                   current_state;
#endif
  struct touch_sample_s             sample[0];
};
```

**注册函数**：

```c
int mytouch_register(FAR const char *devpath,
                     FAR struct i2c_master_s *i2c, uint8_t addr,
                     FAR const struct mytouch_board_s *board)
{
  FAR struct mytouch_dev_s *priv;
  int ret;

  priv = kmm_zalloc(sizeof(struct mytouch_dev_s) +
                    SIZEOF_TOUCH_SAMPLE_S(MYTOUCH_MAX_POINTS));
  if (priv == NULL)
    {
      return -ENOMEM;
    }

  priv->i2c   = i2c;   priv->addr  = addr;  priv->board = board;
  priv->lower.maxpoint = MYTOUCH_MAX_POINTS;
  priv->lower.control  = mytouch_control;
  priv->hold           = true;
  nxmutex_init(&priv->lock);
  nxsem_init(&priv->semlock, 0, 1);

  ret = mytouch_init_chip(priv);          /* reset → checkid → configure */
  if (ret < 0)
    {
      goto err_free;
    }

  ret = board->irq_attach(board, mytouch_isr, priv);
  if (ret < 0)
    {
      goto err_free;
    }

  board->irq_enable(board, true);

  ret = touch_register(&priv->lower, devpath, MYTOUCH_BUF_NUM);
  if (ret < 0)
    {
      goto err_irq;
    }

#ifdef CONFIG_PM
  priv->pm.notify = mytouch_pm_notify;
  /* PM registration is board/product specific. */
#endif
  return OK;

err_irq:
  board->irq_enable(board, false);
err_free:
  nxmutex_destroy(&priv->lock);
  nxsem_destroy(&priv->semlock);
  kmm_free(priv);
  return ret;
}
```

I2C 访问见 `bus_access.md`。ISR/Worker/数据解析见第五章。PM 状态机见 §6.3。

> [!NOTE] 板级接口的灵活性
>
> 以上模板适用于新驱动开发。对于已有项目中多个驱动共享板级抽象层的情况，保持现有板级接口是合理的选择。重构时应评估改动面和回归风险。

### 7.2 Mouse / Keyboard / Button 差异

**Mouse** — 结构体用 `mouse_lowerhalf_s`（无 `maxpoint`），注册用 `mouse_register(&priv->lower, devpath, 8)`。

**Keyboard** — 结构体用 `keyboard_lowerhalf_s`，注册用 `keyboard_register(&priv->lower, devpath, 8)`。事件上报注意传 `&lower` 不是 `priv`。

**Button** — 完全不同，使用静态全局实例：

```c
static const struct btn_lowerhalf_s g_mybtn_lower =
{
  .bl_supported = mybtn_supported,
  .bl_buttons   = mybtn_buttons,
  .bl_enable    = mybtn_enable,
};

int mybtn_register(FAR const char *devname)
{
  return btn_register(devname, &g_mybtn_lower);
}
```

| 差异 | Touch/Mouse/Keyboard | Button |
|------|---------------------|--------|
| 实例 | 动态 `kmm_zalloc` | 静态全局 `const struct btn_lowerhalf_s` |
| 注册 | `xxx_register(&lower, path, nums)` | `btn_register(devname, &lower)` |
| 中断 | ISR → work_queue → event | ISR → 直接调用 `handler(&lower, arg)` |

---

## 八、完整 Bring-Up 流程

以 Touch 为例（其他子框架替换对应的 register 函数和 Kconfig 即可）：

```
1. Kconfig: CONFIG_INPUT_MYTOUCH=y (select INPUT_TOUCHSCREEN + I2C)
2. Make.defs + CMakeLists.txt: mytouch.c added to build
3. Board init → board_mytouch_initialize("/dev/input0", busno)
4. board_mytouch_initialize():
     i2c = stm32_i2cbus_initialize(busno)
     mytouch_register("/dev/input0", i2c, 0x5A, &g_mytouch_board)
5. mytouch_register():
     kmm_zalloc → init_chip → irq_attach → touch_register
6. touch_register() creates /dev/input0
7. App: open → poll → read → close
```

**Make.defs**（标准模板见 SKILL.md §Implementation）：

```makefile
ifeq ($(CONFIG_INPUT_MYTOUCH),y)
  CSRCS += mytouch.c
endif
```

**板级注册**（完整模板见 `board_registration.md`）：

```c
int board_mytouch_initialize(FAR const char *devpath, int busno)
{
  FAR struct i2c_master_s *i2c = stm32_i2cbus_initialize(busno);
  return (i2c == NULL) ? -ENODEV :
         mytouch_register(devpath, i2c, 0x5A, &g_mytouch_board);
}
```

Button 板级注册（通用 GPIO 下半区）：`btn_lower_initialize("/dev/btn0");`

---

## 九、测试方法

### 9.1 uinput 虚拟设备测试

defconfig 启用 `CONFIG_UINPUT_TOUCH=y` 后，写入事件到 `/dev/utouch`：

```c
int fd = open("/dev/utouch", O_WRONLY);
struct touch_sample_s sample;
sample.npoints = 1;
sample.point[0].flags = TOUCH_DOWN | TOUCH_POS_VALID;
sample.point[0].x = 100;
sample.point[0].y = 200;
sample.point[0].timestamp = 0;
write(fd, &sample, SIZEOF_TOUCH_SAMPLE_S(1));
close(fd);
```

### 9.2 应用层读取示例

```c
/* Touch */
int fd = open("/dev/input0", O_RDONLY);
struct pollfd pfd = { .fd = fd, .events = POLLIN };
char buf[SIZEOF_TOUCH_SAMPLE_S(5)];
poll(&pfd, 1, -1);
read(fd, buf, sizeof(buf));

/* Mouse */
struct mouse_report_s rpt;
read(open("/dev/mouse0", O_RDONLY), &rpt, sizeof(rpt));

/* Keyboard */
struct keyboard_event_s evt;
read(open("/dev/kbd0", O_RDONLY), &evt, sizeof(evt));

/* Button */
btn_buttonset_t btns;
read(open("/dev/btn0", O_RDONLY), &btns, sizeof(btns));
```

---

## 十、Cross References

- `SKILL.md` — Driver Type Dispatch Table 路由入口
- `references/bus_access.md` — I2C/SPI 总线访问模式
- `references/coding_rules.md` — 编码规范、内核 API、同步原语、编译陷阱
- `references/board_registration.md` — 板级注册模式（含 input 设备模板）
- `references/nuttx_nav_search.md` — 驱动示例搜索 + NuttX 源码树导航
- `references/driver_review_checklist.md` — 6 维审查清单
- **Upper-half 源码**: `touchscreen_upper.c`, `mouse_upper.c`, `keyboard_upper.c`, `button_upper.c`
- **参考实现**: `uinput.c`（全覆盖）, `goldfish_events.c`（QEMU）, `button_lower.c`（通用 GPIO 下半区）
- **头文件**: `include/nuttx/input/touchscreen.h`, `mouse.h`, `keyboard.h`, `buttons.h`