# USB 控制器参考源码查找指南

## 从 SoC Datasheet 定位控制器 IP 类型

在查找参考源码之前，必须先确定 SoC 使用的 USB 控制器 IP。以下方法按可靠性排序：

### 方法 1：Linux DTS compatible 字段（最快）

```bash
# 在 Linux 内核源码中搜索目标 SoC 的 DTS
grep -r "usb" arch/arm64/boot/dts/<vendor>/<soc>*.dts* | grep compatible

# 常见 compatible 字符串与 IP 的对应关系：
# "snps,dwc3"           → Synopsys DWC3
# "snps,dwc2"           → Synopsys DWC2
# "chipidea,usb2"       → ChipIdea (NXP i.MX 系列常见)
# "fsl,imx8mm-usb"      → ChipIdea with NXP Glue
# "cdns,usb3"           → Cadence CDNS3
# "mentor,musb"         → Mentor MUSB
# "renesas,usbhs"       → Renesas USBHS
# "samsung,exynos-dwc3" → DWC3 with Samsung Glue
```

### 方法 2：Datasheet USB 章节关键词

在 SoC datasheet 的 USB Controller 章节中查找以下线索：

| 关键词/特征 | 对应 IP |
|-------------|---------|
| "DesignWare", "DWC3", "XHCI + Device", TRB, Event Buffer | Synopsys DWC3 |
| "DesignWare", "DWC2", "OTG", FIFO, GNPTXFSIZ | Synopsys DWC2 |
| "ChipIdea", "EHCI", dQH, dTD | ChipIdea |
| "Cadence", "CDNS3", TRB | Cadence CDNS3 |
| "Inventra", "MUSB", "Mentor" | Mentor MUSB |

### 方法 3：寄存器命名风格

如果 datasheet 没有明确标注 IP vendor，可通过寄存器名推断：

| 寄存器名模式 | 对应 IP |
|-------------|---------|
| `GCTL`, `GSTS`, `GUSB2PHYCFG`, `DCFG`, `DCTL`, `DEPCMD` | DWC3 |
| `GOTGCTL`, `GAHBCFG`, `GNPTXFSIZ`, `DIEPCTL`, `DOEPCTL` | DWC2 |
| `USBCMD`, `USBSTS`, `ENDPTCTRL`, `ENDPTPRIME` | ChipIdea |
| `USB_CONF`, `EP_CFG`, `EP_CMD`, `USB_STS` | CDNS3 |
| `FADDR`, `POWER`, `TXCSR`, `RXCSR` | MUSB |

> 确定 IP 类型后，回到本文档对应的 Linux/Zephyr 驱动目录查找参考源码。

---

## Linux 内核源码

Linux USB gadget 驱动位于 `drivers/usb/gadget/udc/`，按控制器 IP 命名：

```
linux/drivers/usb/gadget/udc/
├── snps_udc_core.c     # Synopsys USB 2.0 UDC
├── renesas_usb3.c      # Renesas USB 3.0
├── cdns3/              # Cadence USB3 (cdns3_gadget.c)
├── tegra-xudc.c        # NVIDIA Tegra XUSB
├── net2280.c           # PLX NET2280/NET2272
├── fsl_udc_core.c      # Freescale/NXP ChipIdea
├── bdc/                # Broadcom BDC
└── ...
```

DWC 系列驱动单独在 `drivers/usb/dwc3/` 和 `drivers/usb/dwc2/`：

```
linux/drivers/usb/dwc3/
├── gadget.c            # 事件处理、RESET、ConnDone、submit
├── ep0.c               # EP0 控制传输状态机、SET_ADDRESS
├── core.c / core.h     # 初始化、寄存器定义、事件结构体
└── dwc3-imx8mp.c       # i.MX Glue 层（平台适配）

linux/drivers/usb/dwc2/
├── gadget.c            # DWC2 gadget 驱动
└── core.h              # 寄存器定义
```

### 查找步骤

1. 根据 SoC DTS 中 USB 节点的 `compatible` 字段定位驱动（如 `snps,dwc3` → `drivers/usb/dwc3/`）
2. Glue 层驱动通常在同目录下以 `dwc3-<vendor>.c` 命名，包含平台特定的时钟、PHY、电源配置
3. PHY 驱动在 `drivers/phy/` 下按 vendor 分目录（如 `drivers/phy/freescale/phy-fsl-imx8mq-usb.c`）
4. DTS 文件在 `arch/<arch>/boot/dts/` 下，提供寄存器地址、IRQ、PHY 引用等硬件信息

## Zephyr 源码

Zephyr USB device 驱动位于 `drivers/usb/udc/`（UDC = USB Device Controller）：

```
zephyr/drivers/usb/udc/
├── udc_dwc2.c          # Synopsys DWC2 (STM32, ESP32-S2/S3 等)
├── udc_nrf.c           # Nordic nRF USBD
├── udc_mcux.c          # NXP MCUXpresso (ChipIdea/EHCI)
├── udc_it82xx2.c       # ITE IT82xx2
├── udc_renesas_ra.c    # Renesas RA
├── udc_smartbond.c     # Dialog Smartbond
├── udc_stm32.c         # STM32 USB FS
├── udc_virtual.c       # 虚拟 UDC（测试用）
└── udc_common.c        # 公共框架
```

### 查找步骤

1. 在 Zephyr GitHub 仓库搜索控制器 IP 名称：`https://github.com/zephyrproject-rtos/zephyr`
2. 重点关注 `drivers/usb/udc/` 目录下的 `udc_<ip>.c`
3. Zephyr 的 UDC 框架与 NuttX 的 usbdev 框架类似（都是上下半部分离），初始化流程、EP0 状态机、submit 逻辑可直接参考
4. Zephyr DTS binding 在 `dts/bindings/usb/` 下，可辅助确认寄存器地址和 PHY 配置

> 注意：Zephyr 可能没有所有控制器的驱动（如 DWC3 在 Zephyr 中暂无），此时以 Linux 为主要参考。

## NuttX 已有驱动

NuttX 已有的 USB device 驱动可作为框架集成参考：

```bash
# 搜索已有的 usbdev 驱动
find nuttx/arch -name "*usbdev*" -o -name "*usb*dev*" | head -20
```

常见位置：
- `nuttx/arch/arm64/src/imx9/imx9_usbdev.c` — ChipIdea (i.MX9)
- `nuttx/arch/arm/src/stm32/stm32_otgfsdev.c` — STM32 OTG FS
- `nuttx/arch/arm/src/samv7/sam_usbdevhs.c` — SAM V7 USBHS
- `nuttx/arch/arm/src/lpc17xx_40xx/lpc17_40_usbdev.c` — LPC17/40

## 不同控制器 IP 的差异对照

| 特性 | DWC3 (Synopsys) | ChipIdea | DWC2/MUSB |
|------|-----------------|----------|-----------|
| 传输机制 | TRB 环形 DMA | DTD 链表 DMA | FIFO / DMA |
| 端点命令 | DEPCMD 寄存器命令 | 直接寄存器操作 | 直接寄存器操作 |
| 事件机制 | Event Buffer 环形缓冲区 | 状态寄存器 + 中断 | 状态寄存器 + 中断 |
| EP0 状态控制 | 硬件驱动（需等 XferNotReady） | 软件驱动 | 软件驱动 |
| 速度协商 | DSTS 寄存器 | PORTSC 寄存器 | Power 寄存器 |

> DWC3 的 EP0 状态机由硬件驱动，必须等待 XferNotReady 事件才能进入下一阶段。
> ChipIdea/DWC2 的 EP0 由软件驱动，收到 SETUP 后软件决定何时发送数据/状态。
> 这是适配不同控制器时最大的差异点。
