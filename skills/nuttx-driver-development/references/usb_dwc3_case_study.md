# i.MX95 DWC3 USB 适配案例

本文档记录了在 NXP FRDM-IMX95 EVK 上将 Synopsys DWC3 USB 3.0 设备控制器适配到 NuttX 的完整过程，
包括硬件信息、实现细节、修复的 9 个 bug，以及最终验证结果。

### 适用范围

| 范围 | 说明 |
|------|------|
| ✅ 直接适用 | Synopsys DWC3 控制器的 NuttX Device 模式适配（任何 SoC） |
| ✅ 高度参考 | CDNS3 控制器（同为 TRB DMA + Event Buffer 架构，EP0 状态机类似） |
| ⚠️ 部分参考 | DWC2/MUSB/ChipIdea — Bug 4/5/7 的 EP0 状态机经验不直接适用（这些 IP 的 EP0 由软件驱动），但 Bug 1/2/3/6/8 的经验具有通用性 |
| ❌ 不适用 | USB Host 驱动、USB Class Driver 开发 |

## 硬件信息

| 项目 | 值 |
|------|-----|
| 开发板 | NXP FRDM-IMX95 EVK |
| USB 控制器 | Synopsys DWC3 (snps,dwc3) |
| Core ID | 0x5533 rev 0x330b |
| DWC3 Core 基地址 | 0x4C100000 (64KB) |
| Glue Layer 基地址 | 0x4C1F0000 (32B) |
| PHY 基地址 | 0x4C1F0040 (0x40) |
| HSIO BLK | 0x4C010010 (4B) |
| IRQ | GIC SPI 175 (IMX9_IRQ_USB1) |
| 最终工作模式 | USB 2.0 High-Speed Device |
| USB 功能 | ADB (VID:PID = 18d1:4e11) |

### DWC3 寄存器空间

| 区域 | 偏移范围 | 说明 |
|------|----------|------|
| xHCI | 0x0000 - 0x7FFF | Host 模式（不使用） |
| Global | 0xC100 - 0xC6FF | 全局配置（GCTL, GSTS, GUSB2PHYCFG, GUSB3PIPECTL, GEVNT*） |
| Device | 0xC700 - 0xCBFF | Device 模式（DCFG, DCTL, DSTS, DEPCMD*） |

### PHY 寄存器 (0x4C1F0040)

| 寄存器 | 偏移 | 说明 |
|--------|------|------|
| PHY_CTRL0 | 0x00 | 参考时钟选择 (FSEL)、SSP 使能 |
| PHY_CTRL1 | 0x04 | PHY 复位控制 (RESET, ATERESET) |
| PHY_CTRL2 | 0x08 | TX 使能 (TXENABLEN0)、OTG 禁用 |
| PHY_CTRL3 | 0x0C | PHY 调谐参数 |
| PHY_CTRL4 | 0x10 | PCS TX de-emphasis |
| PHY_CTRL5 | 0x14 | D+/D- 上拉、PCS TX swing |
| PHY_CTRL6 | 0x18 | ALT_CLK 控制、RXTERM override |

## 文件结构

```
nuttx/arch/arm64/src/imx9/
├── hardware/imx9_dwc3.h      # 寄存器定义（~430 行）
├── imx9_dwc3dev.c             # 驱动主文件（~2450 行）
├── imx9_dwc3dev.h             # 对外头文件
├── Kconfig                    # IMX9_DWC3 + IMX9_DWC3_USB3
├── Make.defs                  # 编译规则
└── CMakeLists.txt             # CMake 规则

nuttx/boards/arm64/imx9/imx95-a55-evk/configs/usbdev/
└── defconfig                  # 板级配置
```

## 初始化流程

```
arm64_usbinitialize()
  ├── dwc3_glue_init()         # Glue 层：HSIO BLK 控制、软复位
  ├── dwc3_phy_init()          # PHY：时钟 24MHz、复位序列、TX 使能
  ├── dwc3_core_init()         # Core Reset、读 HWPARAMS、设 Device 模式
  │   ├── GCTL: PRTCAPDIR = Device
  │   ├── GUSB2PHYCFG: UTMI 16-bit、suspend 使能
  │   └── GUSB3PIPECTL: DELAYP1TRANS、DISRXDETINP3(USB2 only)
  ├── dwc3_device_init()       # DCFG(HighSpeed)、事件缓冲区、EP0 配置
  └── irq_attach(IRQ, dwc3_interrupt)
```

### PHY 初始化序列（必须在 Core init 之前）

```c
1. PHY_CTRL0: FSEL = 0x2a (24MHz)
2. PHY_CTRL6: 清除 ALT_CLK_SEL, ALT_CLK_EN
3. PHY_CTRL1: 清除 VDATSRCENB0/VDATDETENB0, 置位 RESET/ATERESET
4. PHY_CTRL0: 置位 REF_SSP_EN
5. PHY_CTRL2: 置位 TXENABLEN0, OTG_DISABLE
6. udelay(10)
7. PHY_CTRL1: 清除 RESET/ATERESET
8. PHY_CTRL6: 清除 RXTERM_OVERRIDE_SEL
```

## DWC3 DEPCMD 端点命令

DWC3 通过 DEPCMD 寄存器向端点发送命令，参数顺序：par0→DEPCMDPAR0, par1→DEPCMDPAR1, par2→DEPCMDPAR2。

| 命令 | 编码 | 说明 |
|------|------|------|
| DEPCFG | 0x01 | 配置端点（类型、maxpacket、FIFO 号） |
| DEPXFERCFG | 0x02 | 配置传输资源数量 |
| DEPSTRTXFER | 0x06 | 启动传输（提供 TRB 地址） |
| DEPUPDXFER | 0x07 | 更新传输（追加 TRB） |
| DEPENDXFER | 0x08 | 结束传输 |
| DEPSTALL | 0x04 | 设置 STALL |
| DEPCSTALL | 0x05 | 清除 STALL |
| DEPSTARTCFG | 0x09 | 开始端点配置序列 |

## TRB (Transfer Request Block)

```c
struct imx9_dwc3_trb_s {
  uint32_t bpl;   /* Buffer Pointer Low */
  uint32_t bph;   /* Buffer Pointer High */
  uint32_t size;  /* Transfer Size */
  uint32_t ctrl;  /* HWO | LST | CHN | CSP | TRBCTL | IOC | ISP_IMI */
};
```

TRBCTL 类型：Normal=1, Setup=2, Status2=3, Status3=4, Data=5

## 事件处理

DWC3 使用 Event Buffer 环形缓冲区，所有事件（设备事件 + 端点事件）写入同一缓冲区。

中断处理流程：
```
1. 读 GEVNTCOUNT 获取待处理事件字节数
2. 屏蔽事件（GEVNTSIZ |= INTMASK）
3. 从事件缓冲区 memcpy 到本地缓存
4. ACK 所有事件（写 GEVNTCOUNT）
5. 遍历本地缓存，按 bit0 区分：
   - bit0=0: 端点事件 → dwc3_handle_ep_event()
   - bit0=1: 设备事件 → dwc3_handle_dev_event()
6. 取消屏蔽（GEVNTSIZ &= ~INTMASK）
```

## 已修复的 9 个 Bug

### Bug 1: 事件类型极性反转
- 现象：设备事件被当作端点事件处理
- 根因：`DWC3_EVENT_IS_DEV_SPECIFIC(e)` 定义为 `((e) & 1) == 0`，但 bit0=1 才是设备事件
- 修复：改为 `((e) & 1) != 0`
- 教训：**仔细核对 databook 中事件格式的 bit 定义**

### Bug 2: 事件缓冲区对齐不足
- 现象：事件数据偶尔出现乱码
- 根因：64 字节对齐不够，DWC3 DMA 需要 4096 字节页对齐
- 修复：事件缓冲区改为 4096 字节对齐，采用 memcpy 从 DMA 缓冲区读取
- 教训：**DMA 缓冲区对齐要求查 databook，不要猜**

### Bug 3: USB RESET 未终止 EP0 传输
- 现象：RESET 后 STARTTRANSFER 失败（EP 仍在忙）
- 根因：RESET handler 直接重启 Setup 而没有先 END_TRANSFER
- 修复：RESET 时先 `dwc3_ep_end_transfer()` EP0 OUT/IN，再重启 Setup
- 教训：**RESET 必须先清理所有进行中的传输**

### Bug 4: Status TRB 类型错误
- 现象：SET_ADDRESS 的状态阶段硬件不响应
- 根因：状态 TRB 类型选择基于方向，但正确做法是 2-stage 用 CONTROL_STATUS2，3-stage 用 CONTROL_STATUS3
- 修复：添加 `three_stage_setup` 标志，根据是否有数据阶段选择 TRB 类型
- 教训：**2-stage 和 3-stage 的状态阶段处理完全不同**

### Bug 5: 2-stage 传输过早发送状态
- 现象：SET_ADDRESS 状态阶段 STARTTRANSFER 命令失败
- 根因：DWC3 要求等待 XferNotReady(STATUS) 事件后才能启动状态阶段
- 修复：在 XferNotReady handler 中检测 STATUS 阶段，此时才调用 start_status()
- 教训：**DWC3 的 EP0 状态机由硬件驱动，软件必须等待硬件信号**

### Bug 6: SET_ADDRESS 分发给 class driver
- 现象：SET_ADDRESS 后 EP0 STALL
- 根因：composite driver 不处理 SET_ADDRESS，返回错误
- 修复：驱动直接处理 SET_ADDRESS（写 DCFG 寄存器），不分发
- 教训：**SET_ADDRESS 永远由 DCD 驱动直接处理**

### Bug 7: EP_SUBMIT(len=0) 对 2-stage 传输的处理
- 现象：SET_CONFIGURATION 状态阶段被阻塞
- 根因：class driver 提交 len=0 表示"处理完成"，但驱动将其当作数据 TRB 发送
- 修复：EP0 IN + len=0 + 2-stage → 直接完成请求，不发 TRB
- 教训：**理解 NuttX class driver 的 EP_SUBMIT 语义**

### Bug 8: Bulk MaxPacket 64→512
- 现象：Host 报 `bulk endpoint has invalid maxpacket 64`，ADB offline
- 根因：defconfig 未启用 `CONFIG_USBDEV_DUALSPEED`
- 修复：添加 `CONFIG_USBDEV_DUALSPEED=y`
- 教训：**HS 模式必须开 DUALSPEED，否则描述符用 FS 的 maxpacket**

### Bug 9: USB 3.0 SS.Inactive 死锁
- 现象：启用 SuperSpeed 后 LTSSM 进入 SS.Inactive，USB 2.0 也不工作
- 根因：i.MX95 的 SS 链路训练需要 Type-C mux 配置和 PHY tuning 参数
- 临时方案：DCFG 始终 HighSpeed，GUSB3PIPECTL 设 DISRXDETINP3 强制 USB 2.0
- 教训：**USB 3.0 SuperSpeed 依赖完整的 PHY/mux 配置链，缺一不可**

## Bug Pattern Summary

按 bug 类型分类，方便其他控制器 IP 的适配者快速识别同类问题：

| 类型 | Bug # | 简述 | 通用性 |
|------|-------|------|--------|
| **EP0 状态机** | Bug 4, 5, 7 | Status TRB 类型错误、过早发送状态、EP_SUBMIT(len=0) 语义 | ⚠️ DWC3 特有（硬件驱动 EP0），但 2-stage/3-stage 区分逻辑通用 |
| **标准请求处理** | Bug 6 | SET_ADDRESS 被分发给 class driver | ✅ 所有 DCD 通用 |
| **DMA 对齐** | Bug 2 | 事件缓冲区对齐不足导致数据乱码 | ✅ 所有 DMA 控制器通用（具体对齐值因 IP 而异） |
| **事件/中断解析** | Bug 1 | 事件类型极性反转 | ✅ 通用教训：位域定义必须逐 bit 核对 databook |
| **热插拔/RESET** | Bug 3 | RESET 未终止 EP0 传输 | ✅ 所有 DCD 通用：RESET 必须先清理进行中的传输 |
| **配置遗漏** | Bug 8 | 未开 DUALSPEED 导致 maxpacket 错误 | ✅ 所有 DCD 通用：HS 模式必须开 DUALSPEED |
| **SS 链路训练** | Bug 9 | LTSSM 死锁，缺 Type-C mux 和 PHY tuning | ⚠️ SS 控制器通用，但具体配置因 SoC 而异 |

> 适配新控制器时，建议先逐条检查"✅ 通用"类 bug 是否已规避，再关注 IP 特有问题。

## 与 ChipIdea 驱动的对比

| 特性 | ChipIdea (imx9_usbdev.c) | DWC3 (imx9_dwc3dev.c) |
|------|--------------------------|------------------------|
| IP 核 | ChipIdea EHCI | Synopsys DWC3 |
| 最高速度 | USB 2.0 HS | USB 3.0 SS |
| 传输机制 | DQH/DTD 链表 | TRB 环形缓冲区 |
| 端点命令 | 直接寄存器操作 | DEPCMD 命令接口 |
| 事件机制 | 状态寄存器轮询 | Event Buffer 环形缓冲区 |
| EP0 控制 | 软件驱动 | 硬件驱动（需等 XferNotReady） |
| 配置选项 | IMX9_USBDEV | IMX9_DWC3 |

## 参考驱动

Linux 内核 DWC3 驱动（最权威参考）：
- `drivers/usb/dwc3/gadget.c` — 事件处理、RESET、ConnDone、submit
- `drivers/usb/dwc3/ep0.c` — EP0 控制传输状态机、SET_ADDRESS
- `drivers/usb/dwc3/core.h` — 事件结构体、寄存器定义
- `drivers/usb/dwc3/dwc3-imx8mp.c` — i.MX Glue 层
- `drivers/phy/freescale/phy-fsl-imx8mq-usb.c` — USB PHY 驱动

NuttX 已有参考：
- `nuttx/arch/arm64/src/imx9/imx9_usbdev.c` — ChipIdea 驱动（框架集成参考）
