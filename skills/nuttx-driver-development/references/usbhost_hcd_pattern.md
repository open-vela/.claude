# NuttX USB Host Controller Driver (HCD) 适配指南

> 本文档是 `nuttx-driver-development` skill 的子系统 reference，通过 Driver Type Dispatch Table 按需加载。
> 覆盖将某个芯片的 USB Host Controller 适配到 NuttX 的完整流程：控制器识别、HCD 架构选择、
> `usbhost_driver_s` / `usbhost_connection_s` 接口实现、PHY 初始化、板级集成、class driver 注册。
>
> **适用场景**: 将一个新 SoC 的 USB 控制器以 host 模式接入 NuttX，使其能枚举和驱动 USB 设备
> （如 U 盘、鼠标、键盘等）。Class driver（MSC、HID 等）已由 NuttX 提供，本文档聚焦 HCD 层。

## 目录

1. [架构概览](#架构概览)
2. [前置分析：识别控制器类型](#前置分析识别控制器类型)
3. [核心接口](#核心接口)
4. [实现步骤](#实现步骤)
5. [文件布局](#文件布局)
6. [Kconfig 与构建集成](#kconfig-与构建集成)
7. [板级初始化](#板级初始化)
8. [调试与验证](#调试与验证)
9. [审查要点](#审查要点)
10. [参考驱动](#参考驱动)

---

## 架构概览

NuttX USB host 子系统采用三层架构，HCD 是最底层：

```
Application
    │  open / read / write / ioctl / poll / close
    ▼
  VFS (/dev/sda, /dev/mouse0, /dev/ttyACM0 ...)
    │
    ▼
  USB Host Class Driver（NuttX 已提供：MSC、HID、CDC-ACM 等）
    │  通过 DRVR_* 宏调用 HCD
    ▼
  USB Host Controller Driver (HCD) ← 你实现的部分
    │  实现 usbhost_driver_s + usbhost_connection_s
    │  管理控制器硬件：寄存器、DMA、中断、PHY
    ▼
  USB 物理总线（USB 2.0 / USB 3.0）
```

**HCD 的职责**:
- 初始化 USB 控制器硬件（时钟、PHY、寄存器）
- 实现 `usbhost_driver_s` 接口供 class driver 调用（端点管理、数据传输）
- 实现 `usbhost_connection_s` 接口供 waiter 线程调用（等待设备连接、枚举）
- 检测 root hub 端口状态变化（设备插拔）
- 管理 USB 传输（control / bulk / interrupt / isochronous）

**与 class driver 的关系**:
- Class driver（`usbhost_storage.c`、`usbhost_hidmouse.c` 等）通过 `DRVR_*` 宏调用 HCD
- HCD 不需要知道上层是什么设备，只需正确完成 USB 传输
- Class driver 的注册通过 `usbhost_registerclass()` 完成，与 HCD 无关

---

## 前置分析：识别控制器类型

适配 HCD 前，必须先确认目标 SoC 的 USB 控制器 IP 类型。这决定了：
- 能否复用现有 HCD 代码
- 需要写多少新代码
- 寄存器级别的参考来源

### 常见 USB 控制器 IP

| 控制器 IP | 协议标准 | 典型 SoC | NuttX 现有支持 | 复用策略 |
|-----------|---------|---------|---------------|---------|
| Synopsys DWC2 (OTG) | EHCI-like 私有 | STM32, EFM32, ESP32-S2/S3 | `efm32_usbhost.c`, `stm32_otgfshost.c` | 可复用，改寄存器基地址和时钟 |
| Synopsys DWC3 | xHCI (host 模式) | i.MX95, RK3588 | 无（仅有 device 模式） | 需新写，参考 `usbhost_xhci_pci.c` |
| ChipIdea/ARC | EHCI 兼容 | i.MXRT, i.MX6/7/8 | `imxrt_ehci.c` | 可复用，改基地址和 PHY |
| OHCI | OHCI 标准 | LPC17xx/54xx | `lpc17_40_usbhost.c` | 可复用 |
| xHCI (PCI) | xHCI 标准 | x86 PC, QEMU | `usbhost_xhci_pci.c` | 需拆分 PCI 层，复用 xHCI 核心 |
| MUSB | 私有 | TI AM335x | 无 | 需全新实现 |
| MAX3421E | SPI 外挂 | 任意（SPI 总线） | `usbhost_max3421e.c` | 已完整支持 |

### 识别方法

1. **查 SoC datasheet/TRM**: 搜索 "USB" 章节，找到控制器 IP 名称
2. **查寄存器基地址**: 对比已知 IP 的寄存器布局
3. **查 Linux 内核 DTS**: `compatible` 字段直接标明 IP 类型
   ```
   usb@4c100000 { compatible = "snps,dwc3"; };  → DWC3
   usb@402e0000 { compatible = "fsl,imxrt-ehci"; };  → ChipIdea EHCI
   ```
4. **查 NuttX 现有代码**: `arch/<arch>/src/<chip>/hardware/` 下是否已有 USB 寄存器定义

### 决策树

```
目标 SoC 的 USB 控制器 IP 是什么？
│
├─ DWC2 → 参考 stm32/efm32 HCD，改基地址+PHY+时钟
├─ ChipIdea EHCI → 参考 imxrt_ehci.c，改基地址+PHY
├─ DWC3 (host 模式) → 参考 usbhost_xhci_pci.c 的 xHCI 核心逻辑
│   └─ DWC3 host 模式内部走 xHCI 协议
│      需要：xHCI 核心 + DWC3 glue layer（PHY、时钟、模式切换）
├─ OHCI → 参考 lpc17/lpc54 HCD
├─ xHCI (非 PCI) → 从 usbhost_xhci_pci.c 拆出 xHCI 核心
└─ 其他/私有 → 需全新实现，参考最接近的 HCD 骨架
```

---

## 核心接口

HCD 需要实现两个核心接口结构体。

### usbhost_connection_s — 连接监控接口

```c
/* 定义在 include/nuttx/usb/usbhost.h */
struct usbhost_connection_s
{
  CODE int (*wait)(FAR struct usbhost_connection_s *conn,
                   FAR struct usbhost_hubport_s **hport);
  CODE int (*enumerate)(FAR struct usbhost_connection_s *conn,
                        FAR struct usbhost_hubport_s *hport);
};
```

| 回调 | 职责 | 阻塞 |
|------|------|------|
| `wait` | 阻塞等待 root hub 端口状态变化（设备插入/拔出），返回变化的 hport | 是 |
| `enumerate` | 对新连接的设备执行枚举（reset port → set address → get descriptors → find class driver） | 是 |

`wait` + `enumerate` 由 `usbhost_waiter` 线程循环调用。

### usbhost_driver_s — HCD 操作接口

```c
/* 定义在 include/nuttx/usb/usbhost.h */
struct usbhost_driver_s
{
  CODE int (*ep0configure)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep0, uint8_t funcaddr,
             uint8_t speed, uint16_t maxpacketsize);
  CODE int (*epalloc)(FAR struct usbhost_driver_s *drvr,
             FAR const struct usbhost_epdesc_s *epdesc,
             FAR usbhost_ep_t *ep);
  CODE int (*epfree)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep);
  CODE int (*alloc)(FAR struct usbhost_driver_s *drvr,
             FAR uint8_t **buffer, FAR size_t *maxlen);
  CODE int (*free)(FAR struct usbhost_driver_s *drvr,
             FAR uint8_t *buffer);
  CODE int (*ioalloc)(FAR struct usbhost_driver_s *drvr,
             FAR uint8_t **buffer, size_t buflen);
  CODE int (*iofree)(FAR struct usbhost_driver_s *drvr,
             FAR uint8_t *buffer);
  CODE int (*ctrlin)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep0,
             FAR const struct usb_ctrlreq_s *req,
             FAR uint8_t *buffer);
  CODE int (*ctrlout)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep0,
             FAR const struct usb_ctrlreq_s *req,
             FAR const uint8_t *buffer);
  CODE ssize_t (*transfer)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep, FAR uint8_t *buffer,
             size_t buflen);
#ifdef CONFIG_USBHOST_ASYNCH
  CODE int (*asynch)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep, FAR uint8_t *buffer,
             size_t buflen, usbhost_asynch_t callback,
             FAR void *arg);
#endif
  CODE int (*cancel)(FAR struct usbhost_driver_s *drvr,
             usbhost_ep_t ep);
#ifdef CONFIG_USBHOST_HUB
  CODE int (*connect)(FAR struct usbhost_driver_s *drvr,
             FAR struct usbhost_hubport_s *hport,
             bool connected);
#endif
  CODE void (*disconnect)(FAR struct usbhost_driver_s *drvr,
             FAR struct usbhost_hubport_s *hport);
};
```

#### 各回调职责速查

| 回调 | 职责 | 调用者 | 阻塞 |
|------|------|--------|------|
| `ep0configure` | 配置 EP0（设置设备地址、速度、最大包大小） | 枚举流程 | 否 |
| `epalloc` | 为非 EP0 端点分配 HCD 资源（TD ring、QH 等） | class driver connect | 否 |
| `epfree` | 释放端点资源 | class driver disconnect | 否 |
| `alloc` | 分配小 buffer（用于控制请求描述符） | class driver | 否 |
| `free` | 释放 alloc 的 buffer | class driver | 否 |
| `ioalloc` | 分配大 buffer（用于数据传输，可能需 DMA 对齐） | class driver | 否 |
| `iofree` | 释放 ioalloc 的 buffer | class driver | 否 |
| `ctrlin` | 执行 Control IN 传输（setup + data + status） | class driver / 枚举 | 是 |
| `ctrlout` | 执行 Control OUT 传输 | class driver | 是 |
| `transfer` | 执行同步 Bulk/Interrupt 传输 | class driver | 是 |
| `asynch` | 执行异步传输（需 `CONFIG_USBHOST_ASYNCH`） | class driver | 否 |
| `cancel` | 取消挂起的传输 | class driver disconnect | 否 |
| `connect` | 通知 HCD 外部 hub 端口状态变化（需 `CONFIG_USBHOST_HUB`） | hub driver | 否 |
| `disconnect` | 通知 HCD 设备断开 | 枚举流程 | 否 |

---

## 实现步骤

### Step 1: 确定复用策略

根据前置分析的决策树，确定代码复用策略：

**策略 A — 复用现有 HCD（改基地址+PHY）**:
适用于 DWC2、ChipIdea EHCI、OHCI 等已有 NuttX 支持的 IP。
1. 复制最接近的 HCD 源文件到 `arch/<arch>/src/<chip>/`
2. 修改寄存器基地址（从 SoC memory map 获取）
3. 修改 PHY 初始化（时钟使能、PHY 配置寄存器）
4. 修改中断号（从 SoC 中断表获取）
5. 修改 DMA 约束（对齐要求、地址映射）

**策略 B — 拆分+适配（xHCI 核心 + platform glue）**:
适用于 DWC3 host 模式、非 PCI xHCI 等。
1. 从 `usbhost_xhci_pci.c` 提取 xHCI 协议核心逻辑（ring 管理、命令、传输、事件处理）
2. 替换 PCI 层为 platform 层（固定地址映射、GIC 中断、platform 时钟）
3. 添加 DWC3 glue layer（PHY init、GCTL 模式切换、glue 寄存器配置）

**策略 C — 全新实现**:
适用于无现有支持的私有 IP。
1. 以 `usbhost_xhci_pci.c` 或 `imxrt_ehci.c` 为骨架参考
2. 按目标控制器的 TRM 实现所有寄存器操作
3. 实现完整的 `usbhost_driver_s` + `usbhost_connection_s`

### Step 2: 实现控制器初始化

控制器初始化通常包含以下子步骤（以 DWC3 为例）：

```c
static int mychip_usbhost_initialize(void)
{
  /* 1. 使能时钟和电源域 */
  mychip_clock_enable(USB_CLOCK);
  mychip_power_enable(USB_POWER_DOMAIN);

  /* 2. PHY 初始化 */
  mychip_usb_phy_init();

  /* 3. 控制器模式设置（OTG 控制器需要） */
  /* DWC3: GCTL.PRTCAPDIR = HOST */
  /* ChipIdea: USBMODE = HOST */
  mychip_set_host_mode();

  /* 4. 控制器复位 */
  mychip_controller_reset();

  /* 5. 读取控制器参数（端口数、slot 数等） */
  mychip_get_hw_params();

  /* 6. 分配 HCD 内部数据结构 */
  mychip_alloc_hcd_memory();

  /* 7. 配置中断 */
  irq_attach(USB_IRQ, mychip_interrupt, priv);
  up_enable_irq(USB_IRQ);

  /* 8. 启动控制器 */
  mychip_controller_start();

  /* 9. 探测已连接的设备 */
  mychip_probe_ports();

  /* 10. 启动 waiter 线程 */
  usbhost_waiter_initialize(&conn);

  return OK;
}
```

### Step 3: 实现 root hub 端口管理

Root hub 端口管理是 HCD 的核心职责之一：

```c
/* wait 回调 — 阻塞等待端口状态变化 */
static int mychip_wait(FAR struct usbhost_connection_s *conn,
                       FAR struct usbhost_hubport_s **hport)
{
  /* 等待端口状态变化信号量 */
  nxsem_wait(&priv->pscsem);

  /* 找到状态变化的端口 */
  for (i = 0; i < priv->nports; i++)
    {
      if (priv->rhport[i].connected != priv->rhport[i].prev_connected)
        {
          *hport = &priv->rhport[i].hport.hport;
          return OK;
        }
    }
}

/* 中断处理 — 检测端口状态变化 */
static int mychip_interrupt(int irq, void *context, void *arg)
{
  /* 读取中断状态 */
  /* 如果是端口状态变化中断 → schedule work */
  /* work 中：读取 PORTSC，更新 connected 状态，post pscsem */
}
```

### Step 4: 实现传输引擎

传输引擎是 HCD 最复杂的部分，需要根据控制器类型实现：

**xHCI 类（DWC3 host）**: 使用 TRB ring
- Command Ring: 发送 slot enable/disable、address device、configure endpoint 等命令
- Transfer Ring: 每个端点一个，放置 Normal/Control/Isoc TRB
- Event Ring: 控制器写入完成事件，HCD 轮询处理

**EHCI 类（ChipIdea）**: 使用 QH + qTD 链表
- Async Schedule: QH 环形链表，用于 Control 和 Bulk 传输
- Periodic Schedule: Frame List + QH，用于 Interrupt 和 Isochronous 传输

**关键实现要点**:
- `ctrlin`/`ctrlout`: 构造 Setup + Data + Status 阶段的传输描述符，提交给控制器，等待完成
- `transfer`: 构造 Normal 传输描述符，提交，等待完成
- `asynch`: 同 transfer 但不等待，设置回调
- 所有传输 buffer 必须满足 DMA 对齐要求
- 传输完成通过中断 → work queue → 信号量/回调通知

### Step 5: 实现 buffer 分配

```c
/* alloc — 分配小 buffer（控制请求用） */
static int mychip_alloc(FAR struct usbhost_driver_s *drvr,
                        FAR uint8_t **buffer, FAR size_t *maxlen)
{
  /* 某些控制器需要 DMA 对齐的 buffer */
  *buffer = kmm_memalign(DCACHE_LINESIZE, MYCHIP_CTRLREQ_BUFSIZE);
  *maxlen = MYCHIP_CTRLREQ_BUFSIZE;
  return *buffer ? OK : -ENOMEM;
}

/* ioalloc — 分配大 buffer（数据传输用） */
static int mychip_ioalloc(FAR struct usbhost_driver_s *drvr,
                          FAR uint8_t **buffer, size_t buflen)
{
  *buffer = kmm_memalign(DCACHE_LINESIZE, buflen);
  return *buffer ? OK : -ENOMEM;
}
```

**DMA 对齐注意事项**:
- 如果 SoC 有 D-Cache，buffer 必须按 cache line 对齐
- 某些控制器（如 xHCI）要求 TRB ring 64 字节对齐
- 使用 `CONFIG_IMX9_DMA_ALLOC` 等 SoC 专用分配器（如果可用）

### Step 6: 注册 class driver 并启动 waiter

```c
/* 板级初始化中调用 */
void board_usbhost_initialize(void)
{
  FAR struct usbhost_connection_s *conn;

  /* 1. 初始化 HCD，返回 connection 接口 */
  conn = mychip_usbhost_initialize(0);

  /* 2. 注册需要的 class driver */
#ifdef CONFIG_USBHOST_MSC
  usbhost_msc_initialize();
#endif
#ifdef CONFIG_USBHOST_HIDMOUSE
  usbhost_mouse_init();
#endif
#ifdef CONFIG_USBHOST_HIDKBD
  usbhost_kbdinit();
#endif
#ifdef CONFIG_USBHOST_HUB
  usbhost_hub_initialize();
#endif

  /* 3. 启动 waiter 线程（如果 HCD init 中未启动） */
  /* usbhost_waiter_initialize(conn); */
}
```

> **注意**: 如果使用 `CONFIG_USBHOST_WAITER`，class driver 注册会在
> `usbhost_drivers_initialize()` 中自动完成（由 waiter 调用），
> 板级代码只需初始化 HCD 和启动 waiter。

---

## 文件布局

HCD 驱动的文件布局与普通设备驱动不同，代码位于 `arch/` 目录下：

### 策略 A（复用现有 HCD，改基地址+PHY）

```
nuttx/
├── arch/<arch>/src/<chip>/
│   ├── <chip>_usbhost.c          # HCD 实现（从参考 HCD 复制+修改）
│   ├── <chip>_usbhost.h          # HCD 内部头文件（可选）
│   ├── hardware/<chip>_usbotg.h  # USB 控制器寄存器定义
│   ├── Make.defs                 # 添加 CHIP_CSRCS
│   ├── CMakeLists.txt            # 添加 list(APPEND SRCS ...)
│   └── Kconfig                   # 添加 CONFIG_<CHIP>_USBHOST
└── boards/<arch>/<chip>/<board>/src/
    └── <board>_usbhost.c         # 板级 USB host 初始化
```

### 策略 B（xHCI 核心 + platform glue）

```
nuttx/
├── drivers/usbhost/
│   ├── usbhost_xhci.c           # xHCI 核心逻辑（平台无关，从 PCI 版拆出）
│   ├── usbhost_xhci.h           # xHCI 核心头文件（已存在）
│   ├── usbhost_xhci_trace.c     # xHCI trace（已存在）
│   └── usbhost_xhci_pci.c       # PCI platform glue（已存在）
├── arch/<arch>/src/<chip>/
│   ├── <chip>_usbhost.c          # Platform glue（DWC3 glue + xHCI 核心调用）
│   ├── hardware/<chip>_dwc3.h    # DWC3 寄存器定义（可能已存在）
│   ├── Make.defs
│   ├── CMakeLists.txt
│   └── Kconfig
└── boards/<arch>/<chip>/<board>/src/
    └── <board>_usbhost.c
```

> **注意**: 策略 B 的理想做法是将 xHCI 核心从 `usbhost_xhci_pci.c` 拆分为
> 平台无关的 `usbhost_xhci.c`，但如果拆分工作量太大，也可以直接在
> `arch/<arch>/src/<chip>/` 下实现完整的 xHCI + DWC3 glue，
> 后续再重构拆分。

---

## Kconfig 与构建集成

### Kconfig 模板

```kconfig
config <CHIP>_USBHOST
	bool "USB Host Support"
	default n
	depends on USBHOST
	select USBHOST_HAVE_ASYNCH
	---help---
		Enable USB host controller driver for <CHIP>.

if <CHIP>_USBHOST

config <CHIP>_USBHOST_REGDEBUG
	bool "USB Host Register-Level Debug"
	default n
	depends on DEBUG_USB_INFO
	---help---
		Enable low-level register debug output.

config <CHIP>_USBHOST_PKTDUMP
	bool "USB Host Packet Dump"
	default n
	depends on DEBUG_USB_INFO
	---help---
		Dump the contents of each packet transferred.

endif # <CHIP>_USBHOST
```

**依赖关系注意**:
- 必须 `depends on USBHOST`（NuttX USB host 框架总开关）
- 如果实现了 `asynch` 回调：`select USBHOST_HAVE_ASYNCH`
- 如果需要 DMA 分配器：`select <CHIP>_DMA_ALLOC`
- 与 USB device 模式互斥时：`depends on !<CHIP>_USBDEV`（或允许 OTG 共存）

### Make.defs

```makefile
ifeq ($(CONFIG_<CHIP>_USBHOST),y)
  CHIP_CSRCS += <chip>_usbhost.c
endif
```

### CMakeLists.txt

```cmake
if(CONFIG_<CHIP>_USBHOST)
  list(APPEND SRCS <chip>_usbhost.c)
endif()
```

---

## 板级初始化

### 典型板级 USB host 初始化

```c
/* boards/<arch>/<chip>/<board>/src/<board>_usbhost.c */

#include <nuttx/usb/usbhost.h>
#include "<chip>_usbhost.h"

static struct usbhost_connection_s *g_usbconn;

int board_usbhost_initialize(void)
{
  int ret;

  /* Initialize the USB host controller driver */

  g_usbconn = <chip>_usbhost_initialize(0);
  if (!g_usbconn)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize USB host\n");
      return -ENODEV;
    }

  /* Start the USB host waiter thread */

  ret = usbhost_waiter_initialize(g_usbconn);
  if (ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to start USB waiter: %d\n", ret);
    }

  return ret;
}
```

### 在 bringup 中调用

```c
/* boards/<arch>/<chip>/<board>/src/<board>_bringup.c */

int <board>_bringup(void)
{
  ...
#ifdef CONFIG_USBHOST
  ret = board_usbhost_initialize();
  if (ret < 0)
    {
      syslog(LOG_ERR, "ERROR: board_usbhost_initialize failed: %d\n", ret);
    }
#endif
  ...
}
```

### defconfig 最小配置

```
# USB Host 框架
CONFIG_USBHOST=y
CONFIG_USBHOST_WAITER=y

# HCD 驱动
CONFIG_<CHIP>_USBHOST=y

# Class drivers（按需启用）
CONFIG_USBHOST_MSC=y           # U 盘
CONFIG_USBHOST_HIDMOUSE=y      # USB 鼠标
CONFIG_USBHOST_HIDKBD=y        # USB 键盘
CONFIG_USBHOST_CDCACM=y        # USB 转串口
CONFIG_USBHOST_HUB=y           # USB Hub（可选）

# 依赖项
CONFIG_SCHED_HPWORK=y          # 高优先级 work queue（中断下半部）
CONFIG_SCHED_LPWORK=y          # 低优先级 work queue（waiter 线程）

# DMA（如果需要）
CONFIG_GRAN=y
CONFIG_<CHIP>_DMA_ALLOC=y
```

---

## 调试与验证

### 验证步骤

1. **HCD 初始化**: 确认控制器初始化成功，无 crash
   ```
   nsh> dmesg  # 查看启动日志中的 USB 初始化信息
   ```

2. **设备检测**: 插入 USB 设备，确认检测到连接
   ```
   # 应看到类似日志：
   # usbhost: Connected on port 0, speed=HIGH
   ```

3. **设备枚举**: 确认设备枚举成功
   ```
   # 应看到类似日志：
   # usbhost_enumerate: Enumeration complete
   # usbhost_findclass: Found class driver for ...
   ```

4. **功能验证**:
   ```
   # MSC（U 盘）
   nsh> ls /dev/sd*
   nsh> mount -t vfat /dev/sda1 /mnt
   nsh> ls /mnt

   # HID Mouse
   nsh> cat /dev/mouse0

   # HID Keyboard
   nsh> cat /dev/kbd0
   ```

### 常见问题排查

| 现象 | 可能原因 | 排查方法 |
|------|---------|---------|
| 控制器初始化超时 | PHY 未正确初始化、时钟未使能 | 检查 PHY 寄存器、时钟门控 |
| 设备插入无反应 | 端口状态变化中断未使能、VBUS 未供电 | 检查 PORTSC 寄存器、VBUS GPIO |
| 枚举失败 | Control 传输超时、设备地址设置失败 | 开启 `CONFIG_DEBUG_USB`，检查 setup packet |
| 传输错误 | DMA 对齐问题、buffer 不在 DMA 可达地址 | 检查 buffer 地址和对齐 |
| 设备断开后 crash | 引用计数错误、未取消挂起传输 | 检查 disconnect 流程 |

### 调试配置

```
CONFIG_DEBUG_USB=y
CONFIG_DEBUG_USB_ERROR=y
CONFIG_DEBUG_USB_WARN=y
CONFIG_DEBUG_USB_INFO=y
CONFIG_USBHOST_TRACE=y
CONFIG_USBHOST_TRACE_NRECORDS=128
```

---

## 审查要点

HCD 驱动除了通用的 6 维审查外，还需关注以下 HCD 特有问题：

### 控制器初始化
- [ ] 时钟和电源域在使用前已使能
- [ ] PHY 初始化完成后有足够的稳定等待时间
- [ ] 控制器模式正确设置（host / device / OTG）
- [ ] 控制器复位后等待复位完成标志
- [ ] 中断在控制器就绪后才使能

### 端口管理
- [ ] VBUS 供电控制正确（host 模式需要供电）
- [ ] 端口复位时序符合 USB 规范（至少 50ms）
- [ ] 端口速度检测正确（Low/Full/High/Super）
- [ ] 端口状态变化通过信号量通知 waiter 线程

### 传输安全
- [ ] 所有传输 buffer 满足 DMA 对齐要求
- [ ] D-Cache 在 DMA 传输前后正确 invalidate/flush
- [ ] 传输超时有合理的默认值
- [ ] 传输完成中断在 work queue 中处理，不在 ISR 中做阻塞操作
- [ ] cancel 能正确取消挂起的传输并唤醒等待者

### 资源管理
- [ ] 端点资源（QH/TD/TRB ring）正确分配和释放
- [ ] 设备断开时释放所有关联的端点资源
- [ ] 控制器 remove/deinit 时释放所有内存和中断

### 并发安全
- [ ] HCD 全局状态用 mutex 保护
- [ ] 端口状态变化用 critical section 保护
- [ ] 传输完成信号量正确使用（不会丢失 post）

### 跨架构移植（arm32 → arm64）

> 源自 imx95 USB Host EHCI 从 imxrt (arm32) 移植到 imx9 (arm64) 的实战经验。
> 以下问题在 arm32 上不暴露，但在 arm64 上必然触发。

- [ ] **结构体对齐重算**: 所有需要硬件对齐的结构体（QH/qTD/TRB），指针从 4→8 字节会改变 sizeof，必须用测试程序验证 `sizeof(struct) % alignment == 0`，不能凭心算
- [ ] **DMA 数据 cache flush 完整性**: 逐一检查每个写入 EHCI/xHCI 寄存器的 DMA 地址，确认对应的数据结构在写入寄存器之前已经 `up_flush_dcache()`。特别注意初始化阶段——arm32 可能因 dcache 未开启而不暴露遗漏
- [ ] **指针/整数转换**: 所有 `(void *)uint32_var` 和 `(uint32_t)ptr` 必须通过 `(uintptr_t)` 中间转换，否则 `-Werror=int-to-pointer-cast` 编译失败
- [ ] **格式化字符串**: `size_t` 在 arm64 上是 64-bit，`%d` 改 `%zu`；`uintptr_t` 用 `PRIxPTR` 或 `%lx`
- [ ] **头文件差异**: `arm_internal.h` → `arm64_internal.h`，`ARCH_DCACHE_LINESIZE` → `ARMV8A_DCACHE_LINESIZE`，`enter_critical_section()` 需要 `#include <nuttx/spinlock.h>`
- [ ] **线程栈大小翻倍**: arm64 栈帧比 arm32 大很多（64-bit 寄存器、更多 callee-saved），所有 HCD 相关线程（waiter、class driver polling）栈至少 4096 字节，默认的 1024/1536 会溢出
- [ ] **physramaddr 宏**: 确认目标平台的虚拟地址到物理地址映射是否为 identity map，如果不是需要实现真正的地址转换

### 板级初始化完整性
- [ ] 所有 defconfig 中启用的 class driver（MSC/CDCACM/HUB/HIDMOUSE/HIDKBD）在板级代码中都有对应的 `usbhost_xxx_initialize()` 调用
- [ ] class driver 注册必须在 `imx9_ehci_initialize()` 之前完成（否则已连接的设备会因找不到 class driver 而枚举失败）
- [ ] USBNC 配置（过流保护、VBUS 极性）在 EHCI 初始化之前完成

---

## 常见 Bug 模式（实战经验）

### Bug 1: EHCI Host System Error — 初始化阶段 cache flush 遗漏

**触发条件**: arm64 + MMU + D-Cache 开启，EHCI 控制器启动后立即 Host System Error

**根因**: 初始化代码中写入 QH/frame list 数据后，部分数据只 flush 了 async head 的 cache，遗漏了 periodic frame list 和 interrupt QH 的 flush。控制器通过 DMA 读到 cache 中的旧数据（全零），触发 Host System Error 并自动 halt。

**检查方法**: 在 `imx9_ehci_initialize()` 中搜索所有 `HCOR->asynclistaddr` 和 `HCOR->periodiclistbase` 的写入点，确认每个写入点之前都有对应数据结构的 `up_flush_dcache()`。

**修复模板**:
```c
/* 写入 PERIODICLISTBASE 之前，必须 flush intrhead 和 framelist */
up_flush_dcache((uintptr_t)&g_intrhead.hw,
  (uintptr_t)&g_intrhead.hw + sizeof(struct ehci_qh_s));
up_flush_dcache((uintptr_t)g_framelist,
  (uintptr_t)g_framelist + FRAME_LIST_SIZE * sizeof(uint32_t));
imx9_putreg(imx9_swap32(physaddr), &HCOR->periodiclistbase);
```

### Bug 2: QH/qTD 结构体对齐失败 — arm64 指针大小变化

**触发条件**: arm32 → arm64 移植，`DEBUGASSERT((sizeof(struct xxx_qh_s) & 0x1f) == 0)` 失败

**根因**: 结构体中包含指针成员，arm64 上指针从 4 字节变 8 字节，加上编译器 padding，总大小不再是 32 的倍数。

**检查方法**: 写一个 host 端测试程序，用目标架构的编译器编译，打印 `sizeof` 和 `offsetof` 验证。不要心算。

### Bug 3: 线程栈溢出 — arm64 栈帧更大

**触发条件**: arm64 上运行一段时间后随机 crash，`ps` 显示线程栈 100% used

**根因**: arm64 的函数调用栈帧比 arm32 大（64-bit 寄存器保存、更多 callee-saved 寄存器），默认的 1024/1536 字节栈不够。

**修复**: `CONFIG_USBHOST_STACKSIZE=4096`，所有 class driver 的 polling 线程栈也至少 4096。

---

## 参考驱动

### NuttX in-tree HCD

| 驱动 | 路径 | 控制器 IP | 适合参考的场景 |
|------|------|----------|--------------|
| i.MXRT EHCI | `arch/arm/src/imxrt/imxrt_ehci.c` | ChipIdea EHCI | EHCI 兼容控制器、完整的 QH/qTD 管理 |
| i.MX95 EHCI | `arch/arm64/src/imx9/imx9_ehci.c` | ChipIdea EHCI (arm64) | arm64 EHCI 移植参考、DMA cache flush 模板 |
| xHCI PCI | `drivers/usbhost/usbhost_xhci_pci.c` | xHCI (PCI) | xHCI/DWC3 host、TRB ring 管理 |
| STM32 OTG FS | `arch/arm/src/stm32/stm32_otgfshost.c` | DWC2 | DWC2 OTG 控制器 |
| EFM32 USB | `arch/arm/src/efm32/efm32_usbhost.c` | DWC2 | DWC2 变体 |
| LPC17 OHCI | `arch/arm/src/lpc17xx_40xx/lpc17_40_usbhost.c` | OHCI | OHCI 控制器 |
| LPC54 OHCI | `arch/arm/src/lpc54xx/lpc54_usb0_ohci.c` | OHCI | OHCI 变体 |
| MAX3421E | `drivers/usbhost/usbhost_max3421e.c` | MAX3421E (SPI) | SPI 外挂 USB host |
| SAM D5/E5 | `arch/arm/src/samd5e5/sam_usb.c` | SAM USB | Microchip SAM USB host |

### Linux 内核参考（芯片级）

| 驱动 | 路径 | 用途 |
|------|------|------|
| DWC3 core | `drivers/usb/dwc3/core.c` | DWC3 初始化、PHY、模式切换 |
| DWC3 host | `drivers/usb/dwc3/host.c` | DWC3 host 模式 glue |
| xHCI core | `drivers/usb/host/xhci.c` | xHCI 协议实现参考 |
| EHCI core | `drivers/usb/host/ehci-hcd.c` | EHCI 协议实现参考 |
| i.MX USB glue | `drivers/usb/dwc3/dwc3-imx8mp.c` | i.MX 系列 DWC3 glue 参考 |

### DWC3 Host 模式适配特别说明

DWC3 控制器在 host 模式下内部走 xHCI 协议。适配要点：

1. **模式切换**: 设置 `GCTL.PRTCAPDIR = HOST`（值为 1）
2. **xHCI 寄存器空间**: DWC3 base + 某个偏移量处是标准 xHCI 寄存器
3. **PHY 初始化**: USB2 PHY（UTMI+）和可选的 USB3 PHY（PIPE）
4. **Glue layer**: SoC 厂商通常有额外的 glue 寄存器（电源控制、OC 极性等）
5. **参考**: 现有 `imx9_dwc3dev.c` 中的 `dwc3_phy_init()` / `dwc3_glue_init()` /
   `dwc3_core_init()` 可直接复用于 host 模式初始化
