# NuttX USB Device Controller Driver (DCD) Pattern

将任意 USB 控制器的 Device 模式适配到 NuttX，实现标准 USB 功能（ADB、CDC-ACM、MTP 等）。

> 本文档是 `nuttx-driver-development` skill 的子系统 reference，通过 Driver Type Dispatch Table 按需加载。

### 适用范围

| 范围 | 说明 |
|------|------|
| ✅ 适用 | USB Device Controller (DCD) 驱动适配：将 USB 控制器的 Device 模式接入 NuttX usbdev 框架 |
| ❌ 不适用 | USB Host Controller (HCD) 驱动 — 使用 `usbhost` 框架，架构完全不同 |
| ❌ 不适用 | USB Class Driver 开发（CDC-ACM、ADB、MTP 等）— 在 `nuttx/drivers/usbdev/` 下，基于已有 DCD 工作 |
| ❌ 不适用 | USB PHY 驱动 — 通常作为 DCD 初始化的一部分内联实现，不独立成驱动 |
| ❌ 不适用 | USB OTG 角色切换逻辑 — 需要同时涉及 Host 和 Device 模式 |

> 如果你的需求属于上述"不适用"范围，本文档中的 EP0 状态机、usbdev_ops_s 等内容不适用于你的场景。

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Phase 1: Hardware Investigation](#phase-1-hardware-investigation)
3. [Phase 2: Framework Scaffolding](#phase-2-framework-scaffolding)
4. [Phase 3: EP0 Control Transfer](#phase-3-ep0-control-transfer)
5. [Phase 4: Bulk/Interrupt Data Transfer](#phase-4-bulkinterrupt-data-transfer)
6. [Phase 5: Code Format Check](#phase-5-code-format-check)
7. [Phase 6: Debug & Optimization](#phase-6-debug--optimization)
8. [USB 3.0 SuperSpeed Adaptation](#usb-30-superspeed-adaptation)
9. [Requirements.md Template](#requirementsmd-template-for-driver-workflow-agent)
10. [References](#references)

---

## Architecture Overview

USB DCD 驱动不同于 sensor/timer 等使用 upper-half/lower-half 模式的驱动。USB DCD 直接实现 NuttX `usbdev` 框架定义的两组回调接口，由 USB class driver（如 CDC-ACM、ADB、composite）通过这些接口与硬件交互。

```
Application (adb / CDC-ACM / MTP)
    │
    ▼
  USB Class Driver (composite / cdcacm / adb)
    │  usbdev_ops_s + usbdev_epops_s
    ▼
  USB DCD Driver (your driver)  ← 你实现这个
    │
    ▼
  USB Controller Hardware (DWC3 / ChipIdea / DWC2 / MUSB / CDNS3)
```

### 与其他驱动子系统的关键差异

| 维度 | Sensor/Timer 等 | USB DCD |
|------|-----------------|---------|
| 框架模式 | Upper-half / Lower-half | 直接实现 usbdev 接口 |
| 注册方式 | `xxx_register()` | `arm64_usbinitialize()` 等架构入口 |
| 文件位置 | `nuttx/drivers/<subsystem>/` | `nuttx/arch/<arch>/src/<chip>/` |
| 总线 | I2C / SPI | 无外部总线，直接操作寄存器 |
| 复杂度 | 中等（~500-1500 行） | 高（~2000-4000 行） |
| 状态机 | 简单或无 | EP0 控制传输状态机（核心难点） |

### 驱动模式选择

USB DCD 驱动只有一种模式：**直接实现 `usbdev_ops_s` + `usbdev_epops_s` 接口**。不存在 uORB / chardev 的选择问题。

架构锁定规则（供 driver-workflow agent 使用）：
- USB 子系统 → **必须 usbdev 接口模式**
- 不适用 uORB / chardev / RPMsg 等其他模式

---

## Phase 1: Hardware Investigation

开始适配前，必须收集以下信息：

| 项目 | 获取方式 | 示例 (DWC3) |
|------|----------|-------------|
| USB 控制器 IP 类型 | SoC datasheet / Linux DTS compatible | Synopsys DWC3 (`snps,dwc3`) |
| 寄存器基地址 | DTS / datasheet | Core: 0x4C100000, Glue: 0x4C1F0000 |
| IRQ 号 | DTS / datasheet | GIC SPI 175 |
| PHY 类型与基地址 | DTS / PHY driver | 0x4C1F0040 (imx8mp-usb-phy) |
| 支持速度 | datasheet | SS/HS/FS |
| 端点数量 | datasheet / HWPARAMS 寄存器 | 16 物理端点 (8 逻辑 × 2 方向) |
| 传输机制 | datasheet | TRB 环形 DMA / DTD 链表 / FIFO |
| Linux 驱动路径 | kernel source | `drivers/usb/dwc3/gadget.c` |

### 参考源码搜索优先级

1. **Linux 内核同 IP 的 gadget 驱动** — 最权威的寄存器操作参考
2. **Zephyr RTOS 同 IP 的 USB device 驱动** — RTOS 视角，结构更接近 NuttX
3. **SoC vendor 的 Linux DTS** — 地址、IRQ、PHY 配置
4. **NuttX 已有的同系列驱动** — 框架集成参考
5. **IP vendor 的 databook** — 寄存器详细说明

> 查找参考驱动的详细方法见 `usb_source_lookup.md`。

### 不同控制器 IP 的差异

> 以下对比适用于所有 USB DCD 控制器 IP 的选型和适配规划。各列的具体经验来源不同：DWC3 列来自 i.MX95 实战验证，其余列来自 Linux 内核驱动和 datasheet 分析。

| 特性 | DWC3 (Synopsys) | ChipIdea | DWC2/MUSB | CDNS3 |
|------|-----------------|----------|-----------|-------|
| 传输机制 | TRB 环形 DMA | DTD 链表 DMA | FIFO / DMA | TRB DMA |
| 端点命令 | DEPCMD 寄存器命令 | 直接寄存器操作 | 直接寄存器操作 | 直接寄存器操作 |
| 事件机制 | Event Buffer 环形缓冲区 | 状态寄存器 + 中断 | 状态寄存器 + 中断 | 中断寄存器 |
| EP0 状态控制 | 硬件驱动（需等 XferNotReady） | 软件驱动 | 软件驱动 | 软件驱动 |
| 速度协商 | DSTS 寄存器 | PORTSC 寄存器 | Power 寄存器 | USB_STS |

> DWC3 的 EP0 状态机由硬件驱动，必须等待 XferNotReady 事件才能进入下一阶段。
> ChipIdea/DWC2 的 EP0 由软件驱动，收到 SETUP 后软件决定何时发送数据/状态。
> 这是适配不同控制器时最大的差异点。

---

## Phase 2: Framework Scaffolding

### File Structure

新建文件：
```
nuttx/arch/<arch>/src/<chip>/
├── hardware/<chip>_usb.h      # 寄存器地址和位域定义
├── <chip>_usbdev.c            # 驱动主文件
└── <chip>_usbdev.h            # 驱动头文件（对外接口）
```

修改文件：`Kconfig`、`Make.defs`、`CMakeLists.txt`、板级 `defconfig`。

### NuttX usbdev 接口（必须实现）

驱动需实现两组回调：

**usbdev_ops_s（设备级）：**

| 回调 | 说明 | 必须 |
|------|------|------|
| `allocep` | 分配端点 | ✅ |
| `freeep` | 释放端点 | ✅ |
| `getframe` | 获取 SOF 帧号 | ✅ |
| `wakeup` | 远程唤醒 | ✅ |
| `selfpowered` | 设置自供电状态 | ✅ |
| `pullup` | D+ 上拉控制（枚举起点） | ✅ |

**usbdev_epops_s（端点级）：**

| 回调 | 说明 | 必须 |
|------|------|------|
| `configure` | 配置端点（类型、方向、maxpacket） | ✅ |
| `disable` | 禁用端点 | ✅ |
| `allocreq` / `freereq` | 分配/释放传输请求 | ✅ |
| `allocbuffer` / `freebuffer` | 分配/释放 DMA 缓冲区（需 CONFIG_USBDEV_DMA） | 条件 |
| `submit` | 提交传输请求（核心：请求→硬件传输） | ✅ |
| `cancel` | 取消传输请求 | ✅ |
| `stall` | 设置/清除端点 STALL | ✅ |

**入口函数（架构相关）：**

| 架构 | 入口函数 |
|------|----------|
| ARM64 | `arm64_usbinitialize()` |
| ARM | `arm_usbinitialize()` |
| RISC-V | `riscv_usbinitialize()` |
| Xtensa | `xtensa_usbinitialize()` |

由 NuttX 启动流程自动调用，在此完成硬件初始化并注册中断。

### Initialization Sequence (Strict Order)

```
1. Glue 层初始化（时钟、电源、引脚）
2. PHY 初始化（时钟选择、复位序列、TX 使能）  ← 必须在 Core init 之前！
3. 控制器 Core Reset
4. 控制器模式设置（Device 模式）
5. 内部 PHY 接口配置（UTMI 位宽、suspend 等）
6. 事件/中断机制初始化
7. EP0 配置
8. 注册中断处理函数
9. 等待 pullup() 被调用后才使能 D+ 上拉
```

> **教训**：PHY 必须在 Core init 之前完成初始化，否则控制器读寄存器可能返回全 0 或挂死。

### Core Data Structures

```c
/* TRB (DWC3/CDNS3) 或 DTD (ChipIdea) — 硬件传输描述符 */
struct <chip>_usb_trb_s
{
  uint32_t bpl;   /* Buffer Pointer Low */
  uint32_t bph;   /* Buffer Pointer High */
  uint32_t size;  /* Transfer Size */
  uint32_t ctrl;  /* Control (HWO, LST, CHN, TRBCTL, IOC) */
};

/* USB 请求容器 */
struct <chip>_usb_req_s
{
  struct usbdev_req_s req;        /* Standard USB request */
  struct <chip>_usb_req_s *flink; /* Singly linked list */
};

/* 端点结构体 */
struct <chip>_usb_ep_s
{
  struct usbdev_ep_s ep;          /* Standard endpoint */
  struct <chip>_usb_s *priv;      /* Back reference */
  struct <chip>_usb_req_s *head;  /* Request queue head */
  struct <chip>_usb_req_s *tail;  /* Request queue tail */
  /* ... TRB/DTD array, DMA address, state flags ... */
};

/* 控制器结构体 */
struct <chip>_usb_s
{
  struct usbdev_s usbdev;                 /* NuttX USB device */
  struct usbdevclass_driver_s *driver;    /* Bound class driver */
  uintptr_t base;                         /* Register base */
  int irq;                                /* IRQ number */
  /* Event buffer, EP0 state, endpoint array, device state ... */
};
```

### Kconfig Design Principles

- 新控制器用独立 config 符号（如 DWC3 用 `IMX9_DWC3`，不复用 `IMX9_USBDEV`）
- `select USBDEV` 自动拉入框架
- 速度模式用子选项控制（如 `IMX9_DWC3_USB3` 控制 SuperSpeed）
- 与同 SoC 上其他 USB 控制器互斥时，在 help 中说明

---

## Phase 3: EP0 Control Transfer

EP0 枚举是适配成功的第一个里程碑，也是最容易出 bug 的地方。

### EP0 State Machine

```
IDLE → [SETUP] → SETUP_PHASE
  → [wLength > 0] → DATA_PHASE → STATUS_PHASE → IDLE  (3-stage)
  → [wLength == 0] → STATUS_PHASE → IDLE               (2-stage)
```

### Standard Request Handling

| 请求 | 谁处理 | 说明 |
|------|--------|------|
| SET_ADDRESS | **驱动直接处理** | 写地址寄存器，**绝不分发给 class driver** |
| GET_DESCRIPTOR | Class driver | 驱动分发 SETUP，class driver 通过 EP_SUBMIT 回数据 |
| SET_CONFIGURATION | Class driver | 驱动分发，class driver 配置端点 |
| 其他标准请求 | Class driver | 驱动分发 |

> **教训**：SET_ADDRESS 分发给 composite driver 会返回错误导致 EP0 STALL。

### 2-stage vs 3-stage Control Transfer

这是 EP0 最常见的 bug 来源：

| 类型 | 特征 | 数据阶段 | 状态阶段 |
|------|------|----------|----------|
| 2-stage | wLength=0 | 无 | IN ZLP |
| 3-stage IN | wLength>0, bmRequestType bit7=1 | IN 数据 | OUT ZLP |
| 3-stage OUT | wLength>0, bmRequestType bit7=0 | OUT 数据 | IN ZLP |

关键规则：
- 用 `three_stage_setup` 标志区分 2-stage 和 3-stage
- 状态阶段的 TRB/TD 类型可能不同（如 DWC3: CONTROL_STATUS2 vs CONTROL_STATUS3）
- 某些控制器（如 DWC3）要求等待硬件 "ready" 信号后才能发送状态阶段

### EP_SUBMIT(len=0) Semantics (Critical Trap)

当 class driver 对 EP0 IN 调用 `EP_SUBMIT(len=0)` 时：
- 3-stage OUT 传输：状态阶段 IN ZLP → 正常发送
- 2-stage 传输：表示 "处理完成" → **不要发硬件传输**，直接完成请求

> **教训**：对 2-stage 的 len=0 也发 TRB/TD 会产生多余传输，阻塞后续命令。

---

## Phase 4: Bulk/Interrupt Data Transfer

### Submit Flow

```
1. class driver 调用 EP_SUBMIT(req)
2. 加入端点请求队列
3. 端点空闲时准备硬件传输（填充 TRB/TD/FIFO，启动传输命令）
4. 中断到来时：读结果 → 设 req->xfrd/result → 调 req->callback → 启动队列下一个
```

### DMA Alignment Requirements

- 事件/描述符缓冲区：通常需要 4096 字节页对齐
- TRB/TD 数组：通常需要 16 或 64 字节对齐
- 数据缓冲区：通常需要 cache line 对齐

> **教训**：DWC3 事件缓冲区从 64 字节对齐改为 4096 字节对齐后才稳定工作。

### defconfig Key Configurations

```
CONFIG_<CHIP>_USB=y
CONFIG_USBDEV=y
CONFIG_USBDEV_DUALSPEED=y        # HS 必须开，否则 bulk maxpacket=64 而非 512
CONFIG_USBDEV_DMA=y              # DMA 控制器必须开
CONFIG_USBADB=y                  # ADB 功能示例
```

> **教训**：不开 `USBDEV_DUALSPEED` 导致 HS 模式 bulk maxpacket=64，host 报 `invalid maxpacket`。

---

## Phase 5: Code Format Check

提交前必须通过 NuttX checkpatch。以下命令可直接复制执行：

```bash
# 前置检查：确认在 nuttx 根目录（checkpatch.sh 依赖相对路径）
test -f tools/checkpatch.sh || { echo "ERROR: 请先 cd 到 nuttx 根目录"; exit 1; }

# 方式 1：检查单个文件（替换 <your_file> 为实际路径）
tools/checkpatch.sh -f arch/<arch>/src/<chip>/<chip>_usbdev.c

# 方式 2：检查 git 暂存区的所有修改
git diff HEAD -- '*.c' '*.h' | tools/checkpatch.sh -

# 方式 3：循环修复直到零 error（仅检查 error，忽略 warning）
while true; do
  output=$(tools/checkpatch.sh -f "$TARGET_FILE" 2>&1)
  errors=$(echo "$output" | grep -c "^ERROR:")
  echo "$output"
  echo "--- Errors remaining: $errors ---"
  [ "$errors" -eq 0 ] && break
  echo "Fix the above errors and press Enter to re-check..."
  read
done
```

error 必须全部修复，warning 尽量修复。循环检查直到零错误。

---

## Phase 6: Debug & Optimization

### Debug Tool Priority

1. `usbtrace` — NuttX 内置 USB trace，零成本首选
2. `_alert()` — 早期初始化阶段保证输出
3. Host 端 `dmesg` / `lsusb -v` — host 视角枚举过程
4. 寄存器 dump — 确认硬件状态
5. USB 协议分析仪 — 最后手段

### usbtrace 使用指南

#### defconfig 配置

```
CONFIG_USBDEV_TRACE=y            # 启用 USB trace 功能
CONFIG_USBDEV_TRACE_NRECORDS=128 # trace buffer 条目数（默认 128，复杂场景建议 256-512）
CONFIG_USBDEV_TRACE_STRINGS=y    # 启用 trace 事件的可读字符串（调试阶段建议开启）
CONFIG_USBDEV_TRACE_INITIALIDSET=0x00ff  # 初始事件过滤掩码（见下方说明）
```

#### 事件过滤掩码（INITIALIDSET）

通过位掩码选择性记录事件，避免 buffer 被高频事件淹没：

| 位 | 事件类型 | 说明 | 建议 |
|----|----------|------|------|
| bit0 | INIT | 初始化事件 | 初始调试开启 |
| bit1 | EP | 端点操作（configure/submit/complete） | 数据传输调试开启 |
| bit2 | DEV | 设备事件（RESET/SUSPEND/RESUME） | 始终开启 |
| bit3 | CLASS | Class driver 事件 | 功能调试开启 |
| bit4 | DRIVER | DCD 驱动内部事件 | 底层调试开启 |
| bit5 | INT | 中断事件 | 高频，仅在需要时开启 |

常用组合：
- `0x0007`：初始化 + 端点 + 设备事件（枚举调试）
- `0x001f`：除中断外全部（功能调试）
- `0x003f`：全部事件（仅短时间使用，buffer 会很快满）

#### NSH 命令

```bash
nsh> usbtrace           # 显示当前 trace buffer 内容
nsh> usbtrace -r        # 显示并清空 buffer
```

#### 在驱动代码中添加自定义 trace 点

```c
#include <nuttx/usb/usbdev_trace.h>

/* 在关键路径添加 trace */
usbtrace(TRACE_DEVCONFIGURE, epno);       /* 端点配置 */
usbtrace(TRACE_EPSUBMIT, privep->epphy);  /* 请求提交 */
usbtrace(TRACE_EPCOMPLETE, epno);         /* 传输完成 */
```

> **提示**：枚举失败时，先用 `0x0007` 过滤看设备事件和 EP0 操作，定位卡在哪个阶段。

### Common Issues

| 现象 | 可能原因 | 排查方法 |
|------|----------|----------|
| 无 USB 事件 | PHY 未初始化/时钟未开/D+ 未上拉 | 检查 PHY 寄存器、pullup 调用 |
| RESET 后无 SETUP | EP0 未配置/事件机制未工作 | dump 事件缓冲区/中断状态 |
| GET_DESCRIPTOR 失败 | EP0 状态机 bug/TRB 类型错误 | 对照 Linux 驱动 EP0 处理 |
| SET_ADDRESS 后 STALL | SET_ADDRESS 被分发给 class driver | 驱动直接处理 |
| 枚举成功功能不工作 | maxpacket 错误/DUALSPEED 未开 | `lsusb -v` 检查描述符 |
| 数据偶尔错乱 | DMA 对齐不足/cache 一致性 | 检查对齐，加 cache flush |
| 热插拔无法恢复 | RESET 未终止旧传输 | RESET 时先 END_TRANSFER 所有 EP |

### Enumeration Success Verification

```bash
lsusb                    # 看到设备 VID:PID
lsusb -v -d VID:PID     # 描述符正确
dmesg | tail -20         # 无 error
adb devices              # ADB: 显示 device（非 offline）
```

---

## USB 3.0 SuperSpeed Adaptation

在 USB 2.0 完全工作后再适配 SuperSpeed：

```
CONFIG_USBDEV_SUPERSPEED=y       # SS 描述符和 maxpacket
CONFIG_<CHIP>_USB3=y             # 控制器特定 SS 开关
```

要点：
- SS bulk maxpacket=1024（HS=512, FS=64）
- 需额外 PHY tuning（TX de-emphasis、swing）
- 部分 SoC 需 Type-C mux 配置
- SS 链路训练失败时必须能回退到 USB 2.0

> **教训**：i.MX95 直接启用 SS 导致 LTSSM 死锁，根因是缺 Type-C mux 和 PHY tuning。先调通 USB 2.0 再逐步启用 SS。

---

## Requirements.md Template (for driver-workflow agent)

当通过 driver-workflow agent 的 Mode A 流程开发 USB DCD 驱动时，Step B 生成的 requirements.md 应包含以下 USB 专属章节：

### 硬件信息（必填）

从 datasheet / DTS 提取：
- 控制器 IP 类型和 compatible 字符串
- 寄存器基地址（Core、Glue、PHY）
- IRQ 号
- 端点数量和类型
- 传输机制（TRB / DTD / FIFO）
- 支持速度（SS / HS / FS）

### 功能 Checklist（USB DCD 专用）

- [x] PHY 初始化（强制）
- [x] Core Reset + Device 模式设置（强制）
- [x] EP0 控制传输状态机（强制）
- [x] SET_ADDRESS 驱动直接处理（强制）
- [x] Bulk IN/OUT 数据传输（强制）
- [x] pullup() D+ 上拉控制（强制）
- [x] 热插拔支持（RESET/Disconnect/ConnDone 事件处理）
- [ ] USB 3.0 SuperSpeed — 如硬件支持则列出，默认不勾选（先调通 USB 2.0）
- [ ] 远程唤醒 — 如需要则勾选
- [ ] OTG 支持 — 如需要则勾选

### 实现约束（USB DCD 专属硬规则）

1. **PHY 必须在 Core init 之前初始化**
2. **SET_ADDRESS 必须由驱动直接处理，禁止分发给 class driver**
3. **必须区分 2-stage 和 3-stage 控制传输**
4. **DMA 缓冲区对齐必须满足硬件要求**
5. **USB 2.0 必须先调通，再适配 USB 3.0**
6. **必须开启 CONFIG_USBDEV_DUALSPEED（HS 模式）**

---

## References

### Related Reference Documents

| Reference | Description | When to Load |
|-----------|-------------|-------------|
| `usb_dwc3_case_study.md` | i.MX95 DWC3 完整适配记录，含 9 个 bug 修复 | 适配 DWC3 或需要具体 bug 案例时 |
| `usb_source_lookup.md` | Linux/Zephyr/NuttX USB 源码查找方法和目录结构 | Phase 1 调研阶段查找参考驱动时 |
| `usb_bes_v2_lessons.md` | USB DCD 适配常见陷阱（HAL 重初始化、速度检测、VBUS 调用链、回调设计、增量编译） | 在 vendor HAL 层之上适配或重构 USB DCD 驱动时 |

### NuttX In-Tree USB DCD Drivers (Skeleton References)

| 驱动 | 路径 | 控制器 IP | 特点 |
|------|------|-----------|------|
| i.MX9 DWC3 | `nuttx/arch/arm64/src/imx9/imx9_dwc3dev.c` | Synopsys DWC3 | TRB DMA, Event Buffer, 最完整参考 |
| i.MX9 ChipIdea | `nuttx/arch/arm64/src/imx9/imx9_usbdev.c` | ChipIdea | DTD 链表, 直接寄存器 |
| STM32 OTG FS | `nuttx/arch/arm/src/stm32/stm32_otgfsdev.c` | DWC2 | FIFO 模式 |
| SAM V7 USBHS | `nuttx/arch/arm/src/samv7/sam_usbdevhs.c` | SAM USBHS | DMA + FIFO |
| LPC17/40 | `nuttx/arch/arm/src/lpc17xx_40xx/lpc17_40_usbdev.c` | LPC USB | 简单 FIFO |

### Linux Kernel USB Gadget References

| 控制器 IP | Linux 驱动路径 |
|-----------|---------------|
| DWC3 | `drivers/usb/dwc3/gadget.c`, `ep0.c`, `core.h` |
| DWC2 | `drivers/usb/dwc2/gadget.c`, `core.h` |
| ChipIdea | `drivers/usb/chipidea/udc.c` |
| CDNS3 | `drivers/usb/cdns3/cdns3-gadget.c` |
| MUSB | `drivers/usb/musb/musb_gadget.c` |
