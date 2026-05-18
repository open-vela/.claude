# NuttX Driver Examples & Tree Navigation

本文档分为两部分：上半部按驱动类型分类提供芯片参考（开源项目）和骨架参考（NuttX in-tree）路径，下半部提供 NuttX 源码树的完整导航。

---

## Part 1: Driver References by Subsystem

每个子系统按两条搜索线组织：
- **芯片参考**（Linux/Zephyr）：搜索目标芯片型号，提取寄存器定义、初始化序列、时序参数。仅作为 datasheet 的交叉验证来源，最终以 datasheet 为准。
- **骨架参考**（NuttX in-tree）：作为代码生成的骨架模板，提供 NuttX API 调用模式、框架注册方式。

### Sensor Drivers

#### 芯片参考（按芯片型号搜索）

| Project | Driver Directory | URL | 搜索方式 |
|---------|-----------------|-----|---------|
| Linux Kernel | `drivers/iio/accel/`, `drivers/iio/gyro/`, `drivers/iio/pressure/`, `drivers/iio/light/`, `drivers/iio/magnetometer/`, `drivers/iio/proximity/`, `drivers/iio/temperature/`, `drivers/hwmon/` | https://github.com/torvalds/linux/tree/master/drivers/iio | 按芯片型号搜索（如 `bmi270`、`bmp280`） |
| Zephyr RTOS | `drivers/sensor/` | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/sensor | 按芯片型号搜索 |

重点提取：寄存器地址宏定义、初始化序列、SPI/I2C 帧格式、自检流程、FIFO 配置。

#### 骨架参考（NuttX API 模式）

**推荐骨架**（优先使用）:

| 驱动 | 路径 | 特点 |
|------|------|------|
| Goldfish Sensor | `nuttx/drivers/sensors/goldfish_sensor_uorb.c` | uORB 模式最佳参考，模拟器传感器 |
| BMP280 | `nuttx/drivers/sensors/bmp280_uorb.c` | 气压计，I2C，fetch 模式 |
| BMI160 | `nuttx/drivers/sensors/bmi160_uorb.c` | IMU，I2C/SPI，push 模式 |

**其他 In-Tree 驱动**:
```
nuttx/drivers/sensors/lis2dh.c           # 加速度计 (I2C/SPI)
nuttx/drivers/sensors/max31855.c         # 热电偶 (SPI, chardev)
nuttx/drivers/sensors/lsm6dso.c          # IMU (I2C/SPI)
```

**Vendor 驱动**（真实硬件参考）:
```
vendor/bes/drivers/best1600_ep/drivers/sensors/bmi270/
vendor/bes/drivers/best1600_ep/drivers/sensors/
```

### Timer / Watchdog / RTC Drivers

#### 芯片参考

| Project | Directory |
|---------|-----------|
| Linux Kernel | https://github.com/torvalds/linux/tree/master/drivers/clocksource |
| Zephyr RTOS | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/timer |

#### 骨架参考

<!-- 🔲 Planned: 待填充推荐骨架驱动 -->

```
nuttx/drivers/timers/              # Timer 驱动目录
```

### LED Drivers

#### 芯片参考

| Project | Directory |
|---------|-----------|
| Linux Kernel | https://github.com/torvalds/linux/tree/master/drivers/leds |
| Zephyr RTOS | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/led |

#### 骨架参考

<!-- 🔲 Planned: 待填充推荐骨架驱动 -->

```
nuttx/drivers/leds/                # LED 驱动目录
```

### Input Drivers

#### 芯片参考

| Project | Directory |
|---------|-----------|
| Linux Kernel | https://github.com/torvalds/linux/tree/master/drivers/input |
| Zephyr RTOS | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/input |

#### 骨架参考

使用 touch_register / mouse_register / keyboard_register 新框架的参考实现：

```
nuttx/drivers/input/uinput.c              # ✅ 推荐：touch/kbd/mouse/btn 全覆盖
nuttx/drivers/input/goldfish_events.c      # ✅ 推荐：QEMU 虚拟输入（touch+kbd+mouse）
nuttx/drivers/input/touchscreen_upper.c    # Touch 上半区框架实现
nuttx/drivers/input/mouse_upper.c          # Mouse 上半区框架实现
nuttx/drivers/input/keyboard_upper.c       # Keyboard 上半区框架实现
nuttx/drivers/input/button_upper.c         # Button 上半区框架实现
nuttx/drivers/input/button_lower.c         # Button 通用 GPIO 下半区
nuttx/include/nuttx/input/touchscreen.h    # Touch 数据结构 + API
nuttx/include/nuttx/input/mouse.h          # Mouse 数据结构 + API
nuttx/include/nuttx/input/keyboard.h       # Keyboard 数据结构 + API
nuttx/include/nuttx/input/buttons.h        # Button 数据结构 + API
```

### Analog Drivers (ADC/DAC)

#### 芯片参考

| Project | Directory |
|---------|-----------|
| Linux Kernel | https://github.com/torvalds/linux/tree/master/drivers/iio/adc |
| Zephyr RTOS | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/adc |

#### 骨架参考

<!-- 🔲 Planned: 待填充推荐骨架驱动 -->

```
nuttx/drivers/analog/              # ADC/DAC 驱动目录
```

### Serial Drivers (UART)

#### 芯片参考

| Project | Directory |
|---------|-----------|
| Linux Kernel | https://github.com/torvalds/linux/tree/master/drivers/tty/serial |
| Zephyr RTOS | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/serial |

#### 骨架参考

<!-- 🔲 Planned: 待填充推荐骨架驱动 -->

```
nuttx/drivers/serial/              # UART/Serial 驱动目录
```

### Vibrator Drivers

#### 芯片参考

| Project | Directory |
|---------|-----------|
| Linux Kernel | https://github.com/torvalds/linux/tree/master/drivers/input/misc |

#### 骨架参考

<!-- 🔲 Planned: 待填充推荐骨架驱动 -->

### Framebuffer / LCDC Drivers

#### 芯片参考（按芯片型号搜索）

| Project | Driver Directory | URL | 搜索方式 |
|---------|-----------------|-----|---------|
| Linux Kernel | `drivers/gpu/drm/` | https://github.com/torvalds/linux/tree/master/drivers/gpu/drm | 按芯片厂商搜索（如 `stm`、`sun4i`、`meson`），重点看 `probe()` = 初始化序列，`irq_handler` = 中断链路 |
| Zephyr RTOS | `drivers/display/` | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/display | 按芯片型号搜索 |

重点提取：LCDC 初始化序列、中断回调注册方式、显存分配方式、DSI/DPI 时序参数。

#### 骨架参考（NuttX API 模式）

**推荐骨架**（优先使用）:

| 驱动 | 路径 | 特点 |
|------|------|------|
| Sim Framebuffer | `nuttx/drivers/video/fb.c` + `nuttx/arch/sim/src/sim/sim_framebuffer.c` | 最简 FB 驱动，静态分配，up_fb* 接口 |
| AM335x LCDC | `nuttx/arch/arm/src/am335x/am335x_lcdc.c` | 真实 LCDC 硬件，静态分配，up_fb* 接口 |
| STM32 LTDC | `nuttx/arch/arm/src/stm32h7/stm32_ltdc.c` | 真实 LTDC 硬件，多层 overlay |

**Vendor 驱动**（真实硬件参考）:
```
vendor/bes/boards/common/src/bes_lcdc.c              # BES LCDC (Command/Video 屏)
```

> [!NOTE] Adding examples for a new subsystem

### USB Device Controller (DCD) Drivers

#### 芯片参考（按控制器 IP 搜索）

| Project | Driver Directory | URL | 搜索方式 |
|---------|-----------------|-----|---------|
| Linux Kernel (DWC3) | `drivers/usb/dwc3/` | https://github.com/torvalds/linux/tree/master/drivers/usb/dwc3 | gadget.c, ep0.c, core.h |
| Linux Kernel (DWC2) | `drivers/usb/dwc2/` | https://github.com/torvalds/linux/tree/master/drivers/usb/dwc2 | gadget.c, core.h |
| Linux Kernel (ChipIdea) | `drivers/usb/chipidea/` | https://github.com/torvalds/linux/tree/master/drivers/usb/chipidea | udc.c |
| Linux Kernel (CDNS3) | `drivers/usb/cdns3/` | https://github.com/torvalds/linux/tree/master/drivers/usb/cdns3 | cdns3-gadget.c |
| Linux Kernel (MUSB) | `drivers/usb/musb/` | https://github.com/torvalds/linux/tree/master/drivers/usb/musb | musb_gadget.c |
| Linux Kernel (Other UDC) | `drivers/usb/gadget/udc/` | https://github.com/torvalds/linux/tree/master/drivers/usb/gadget/udc | 按 IP 名搜索 |
| Zephyr RTOS | `drivers/usb/udc/` | https://github.com/zephyrproject-rtos/zephyr/tree/main/drivers/usb/udc | udc_dwc2.c, udc_mcux.c 等 |

重点提取：寄存器定义、事件处理、EP0 状态机、submit 流程、DMA 对齐要求。
同时搜索 SoC Glue 层驱动（如 `dwc3-imx8mp.c`）和 PHY 驱动（如 `phy-fsl-imx8mq-usb.c`）。

#### 骨架参考（NuttX In-Tree）

**推荐骨架**（按控制器 IP 选择）:

| 驱动 | 路径 | 控制器 IP | 特点 |
|------|------|-----------|------|
| i.MX9 DWC3 | `nuttx/arch/arm64/src/imx9/imx9_dwc3dev.c` | Synopsys DWC3 | TRB DMA, Event Buffer, 最完整参考 |
| i.MX9 ChipIdea | `nuttx/arch/arm64/src/imx9/imx9_usbdev.c` | ChipIdea | DTD 链表, 直接寄存器 |
| STM32 OTG FS | `nuttx/arch/arm/src/stm32/stm32_otgfsdev.c` | DWC2 | FIFO 模式 |
| SAM V7 USBHS | `nuttx/arch/arm/src/samv7/sam_usbdevhs.c` | SAM USBHS | DMA + FIFO |
| LPC17/40 | `nuttx/arch/arm/src/lpc17xx_40xx/lpc17_40_usbdev.c` | LPC USB | 简单 FIFO |

**其他 In-Tree 驱动**:
```bash
# 搜索所有 usbdev 驱动（兼容 Kiro grepSearch 工具和 shell 环境）
# Shell 方式：
find nuttx/arch -type f -name "*usbdev*.c" 2>/dev/null | sort
find nuttx/arch -type f -name "*dwc3*.c" -o -name "*dwc2*.c" -o -name "*chipidea*.c" 2>/dev/null | sort

# Kiro grepSearch 方式（推荐，更快更稳定）：
# query: "usbdev_ops_s", includePattern: "arch/**/*.c"
# query: "arm64_usbinitialize\|arm_usbinitialize", includePattern: "arch/**/*.c"
```

> 详细的 USB DCD 适配指南见 `references/usbdev_dcd_pattern.md`，
> DWC3 具体案例见 `references/usb_dwc3_case_study.md`。

> [!NOTE] Adding examples for a new subsystem
>
> 当为新的驱动子系统添加参考示例时：
> 1. 先填充芯片参考（Linux/Zephyr 链接）
> 2. 再填充骨架参考（NuttX in-tree 推荐驱动 + vendor 驱动）
> 3. 确认开源参考链接有效
> 4. 移除 `<!-- 🔲 Planned -->` 注释

---

## Part 2: NuttX Tree — Search and Navigation

### Quick Search Commands

```sh
# 按关键字搜索 C 文件
find nuttx/drivers -name "*.c" | xargs grep -l "KEYWORD"

# 查找子系统头文件
ls nuttx/include/nuttx/sensors/
ls nuttx/include/nuttx/i2c/
ls nuttx/include/nuttx/spi/

# 查找板级注册示例
grep -rl "mydevice_register" nuttx/boards/

# 查找使用特定总线的驱动
grep -rl "I2C_TRANSFER" nuttx/drivers/sensors/
grep -rl "SPI_SELECT" nuttx/drivers/sensors/

# 查找所有 sensor 驱动
ls nuttx/drivers/sensors/*.c

# 查找所有 I2C sensor 驱动
grep -l "i2c_master_s" nuttx/drivers/sensors/*.c

# 查找所有 SPI sensor 驱动
grep -l "spi_dev_s" nuttx/drivers/sensors/*.c

# 查找 uORB sensor 驱动
ls nuttx/drivers/sensors/*_uorb.c

# 查找特定 sensor 的板级注册
grep -rl "bmp280_register\|bmi160_register" nuttx/boards/

# 查找 Kconfig 条目
grep -A5 "SENSORS_BMP280" nuttx/drivers/sensors/Kconfig

# 查找启用某 sensor 的 defconfig
grep -rl "CONFIG_SENSORS_BMP280=y" nuttx/boards/
```

### Driver Sources

```
nuttx/drivers/
├── 1wire/          # 1-Wire bus drivers
├── aie/            # AI engine drivers
├── analog/         # ADC, DAC, comparator drivers
├── audio/          # Audio codec and I2S drivers
├── bch/            # Block-to-character driver
├── binder/         # Android Binder IPC
├── can/            # CAN bus drivers
├── clk/            # Clock framework
├── contactless/    # NFC/contactless drivers
├── crypto/         # Cryptographic hardware
├── devfreq/        # Device frequency scaling
├── devicetree/     # Device tree support
├── dma/            # DMA engine drivers
├── dma-buf/        # DMA buffer management
├── dummy/          # Dummy/null drivers
├── eeprom/         # EEPROM drivers
├── efuse/          # eFuse drivers
├── hwtracing/      # Hardware tracing
├── i2c/            # I2C bus framework & multiplexers
├── i2s/            # I2S audio bus
├── i3c/            # I3C bus framework
├── input/          # Input devices (touch, buttons, keyboard)
├── ioexpander/     # I/O expander drivers (GPIO, PCA9555, etc.)
├── ipcc/           # Inter-processor communication
├── lcd/            # LCD display drivers
├── leds/           # LED drivers (PWM, GPIO, WS2812, etc.)
├── loop/           # Loop device
├── math/           # Math coprocessor drivers
├── misc/           # Miscellaneous drivers (dev/null, dev/zero, etc.)
├── mmcsd/          # MMC/SD card drivers
├── modem/          # Modem drivers
├── motor/          # Motor control drivers
├── mtd/            # Memory Technology Device (flash, NAND, NOR)
├── net/            # Network device drivers
├── note/           # Instrumentation/trace note drivers
├── pci/            # PCI bus drivers
├── pinctrl/        # Pin control/mux drivers
├── pipes/          # Pipe and FIFO drivers
├── power/          # Power management (battery, regulator, PM)
├── rc/             # Remote control drivers
├── regmap/         # Register map abstraction
├── reset/          # Reset controller drivers
├── rf/             # RF drivers
├── rmt/            # Remote control transceiver
├── rpmsg/          # Remote processor messaging
├── rptun/          # Remote processor tunneling
├── safety/         # Safety drivers
├── segger/         # Segger RTT/SystemView
├── sensors/        # ★ Sensor drivers (I2C/SPI/1-Wire)
├── sent/           # SENT protocol drivers
├── serial/         # UART/serial drivers
├── spi/            # SPI bus framework
├── syslog/         # System log drivers
├── tee/            # Trusted Execution Environment
├── thermal/        # Thermal management
├── timers/         # Timer, watchdog, RTC drivers
├── trace32/        # Lauterbach Trace32 support
├── usbdev/         # USB device drivers
├── usbhost/        # USB host drivers
├── usbmisc/        # USB miscellaneous
├── usbmonitor/     # USB monitor
├── usrsock/        # User-space socket drivers
├── vhost/          # VirtIO host drivers
├── video/          # Video/camera drivers
├── virtio/         # VirtIO drivers
└── wireless/       # WiFi, Bluetooth, IEEE802.15.4, LoRa
```

### Header Files

```
nuttx/include/nuttx/
├── sensors/
│   ├── sensor.h          # ★ Sensor framework (sensor_lowerhalf_s, sensor_ops_s)
│   ├── bmp280.h          # BMP280 barometer
│   ├── bmi160.h          # BMI160 IMU
│   ├── lis2dh.h          # LIS2DH accelerometer
│   ├── max31855.h        # MAX31855 thermocouple
│   └── ...               # Many more sensor headers
├── i2c/
│   └── i2c_master.h      # ★ I2C master interface (i2c_master_s, i2c_msg_s)
├── spi/
│   └── spi.h             # ★ SPI interface (spi_dev_s, SPI_* macros)
├── fs/
│   ├── fs.h              # ★ VFS (file_operations, register_driver)
│   └── ioctl.h           # IOCTL definitions
├── drivers/
│   └── drivers.h         # Common driver utilities
└── kmalloc.h             # Kernel memory allocation (kmm_zalloc, kmm_free)
```

### Board Examples

```
nuttx/boards/
├── arm/
│   ├── stm32/
│   │   ├── common/src/         # Shared board code for STM32
│   │   │   ├── stm32_bmp280.c  # BMP280 registration example
│   │   │   ├── stm32_bmi160.c  # BMI160 registration example
│   │   │   └── ...
│   │   ├── stm32f4discovery/src/
│   │   ├── nucleo-f446re/src/
│   │   └── ...
│   ├── rp2040/
│   │   └── common/src/
│   │       └── rp2040_bmp280.c
│   └── ...
├── xtensa/
│   └── esp32/
│       └── common/src/
│           └── esp32_bmp280.c
├── risc-v/
└── sim/                        # Simulator board (good for testing)
```
