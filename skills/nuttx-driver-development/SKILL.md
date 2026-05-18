---
name: nuttx-driver-development
description: Create, update, review, or audit NuttX RTOS C device drivers. Covers sensor (uORB), char device, network (ethernet, MAC, PHY, DMA, netdev, SPI-Ethernet, CAN, SocketCAN, CAN FD, CAN bus, LIN, LIN bus, LIN master, LIN slave), framebuffer (fb, LCDC, display controller), LCD (SPI LCD, I2C LCD, ST7789, SSD1306, ILI9341, lcd_dev, lcd_framebuffer), USB (Host Controller Driver and device Controller Driver), audio (I2S smart PA, power amplifier, audio_comp), power/battery (charger, monitor, gauge), MCAL (AUTOSAR MCAL adaptation, EB Tresos, MCAL upper-half bridging), I2C/SPI bus, Kconfig/build integration, and board registration. Use for driver implementation, code audit, sensor/timer/LED/ethernet/CAN/LIN/fb/LCD/audio/PA/battery subsystems, or board bringup.
---

# NuttX Driver Development

Follow the full driver lifecycle in order. Keep edits small and testable.

> [!TIP] This skill can be used standalone or via the `driver-workflow` agent (`agents/driver-workflow.agent.md`) for end-to-end driver development with automated reference matching, requirements generation, and PR submission.

## Table of Contents

1. [Preparation](#preparation)
   - Clarify scope and expectations
   - Find reference drivers
   - Determine driver pattern
   - Layout driver files
2. [Implementation](#implementation)
   - Write driver C implementation
   - Write public header file
   - Kconfig and build system integration
   - Board-level registration
3. [NuttX Coding & Kernel API Rules](#nuttx-coding--kernel-api-rules)
4. [Code Review](#code-review)
5. [References](#references)

---

## Preparation

### 1) Clarify scope and expectations

- Identify the NuttX driver subsystem (sensor, serial, timer, analog, etc.).
- Confirm target bus (I2C, SPI, 1-Wire, etc.) and required hardware properties.
- Clarify the NuttX source tree location. The driver code lives under `nuttx/drivers/`.

### 2) Find reference drivers

> [!NOTE] Two types of reference drivers serve different purposes
>
> - **骨架参考（NuttX API 模式）**: 优先使用 NuttX in-tree 同类驱动作为代码骨架，确保符合 NuttX 的 upper-half/lower-half 架构和 VFS 集成方式
> - **芯片参考（寄存器/时序验证）**: 优先使用 Linux/Zephyr 同芯片驱动，用于交叉验证 datasheet 的寄存器定义、初始化序列、SPI 帧格式等芯片专属知识
>
> When used standalone: 手动搜索两类参考驱动
> When used with `driver-workflow` agent: Step A.2 自动完成两条线的并行搜索

Always look at the in-tree NuttX drivers before doing any work. There might already be a driver that (best scenario at the top):

1. Is exactly the same part as the one you are working on
2. Targets a very similar part from the same vendor (e.g., BMP280 vs BME680)
3. Is the same device subsystem as yours (e.g., another I2C sensor)

If a reference driver is found, use it as a **skeleton template**:

1. Copy the source file structure (function signatures, bus access pattern, registration flow, file layout)
2. Replace all device-specific content: register definitions, chip ID values, vendor/chip names, init sequences, calibration logic
3. After generating code, grep for the original chip name — zero matches required
4. **Do NOT copy register addresses or chip-specific constants** — these must come from the target device's datasheet

Re-use these files/folders whenever possible:
- Header files in `include/nuttx/sensors/`, `include/nuttx/i2c/`, etc.
- Existing board-level registration patterns in `boards/`

If no suitable reference driver exists:

1. Use the dispatch table below to load the subsystem-specific pattern reference
2. Use the most similar subsystem's reference driver as template (e.g., use accelerometer driver as template for gyro)
3. Follow the architectural pattern (uORB vs chardev) strictly from the loaded reference
4. For 🔲 Planned subsystems, use `chardev_pattern.md` (Production-Quality Skeleton section) as the starting point

Here are the relevant folders to search for examples:

```
nuttx/
├── drivers/          # Device-specific driver sources. Search here first
│   ├── sensors/      # Sensor drivers (I2C/SPI/1-Wire)
│   ├── serial/       # UART/serial drivers
│   ├── i2c/          # I2C bus framework
│   ├── spi/          # SPI bus framework
│   ├── timers/       # Timer/watchdog/RTC drivers
│   ├── analog/       # ADC/DAC drivers
│   ├── leds/         # LED drivers
│   ├── input/        # Input device drivers
│   └── ...           # Many more subsystems
├── include/nuttx/    # Public header files for driver APIs
│   ├── sensors/      # Sensor driver headers & sensor_lowerhalf_s
│   ├── i2c/          # I2C master interface (i2c_master_s)
│   ├── spi/          # SPI interface (spi_dev_s)
│   └── fs/           # VFS file_operations, register_driver()
├── boards/           # Board-specific initialization & driver registration
└── arch/             # Architecture-specific lower-half bus implementations
```

Use `references/nuttx_nav_search.md` for driver examples and navigating the NuttX repo.

### 3) Determine Driver Pattern

NuttX uses a **POSIX VFS-based** driver model. Every device appears as a file under `/dev/`. Choose the appropriate pattern based on driver subsystem type:

#### Driver Type Dispatch Table

Based on the identified subsystem, load the corresponding reference document for subsystem-specific implementation details:

| Subsystem | Pattern | Reference Document | Status |
|-----------|---------|-------------------|--------|
| **sensors** (accel, gyro, baro, temp, humi, light, mag, prox, IMU...) | Upper-Half / Lower-Half (uORB) | `references/sensor_uorb_pattern.md` | ✅ Available |
| **gnss** (GPS, GNSS, satellite positioning) | Upper-Half / Lower-Half (gnss_uorb) | `references/gnss_uorb_pattern.md` | ✅ Available |
| **net** (Ethernet, SPI-Ethernet) | Upper-Half / Lower-Half (netdev) | `references/eth_netdev_pattern.md` | ✅ Available |
| **can** (CAN, CAN FD, SocketCAN) | Upper-Half / Lower-Half (netdev) | `references/can_netdev_pattern.md` | ✅ Available |
| **lin** (LIN, LIN bus, LIN master/slave) | Upper-Half / Lower-Half (netdev, over SocketCAN) | `references/lin_netdev_pattern.md` | ✅ Available |
| **fb** (framebuffer, LCDC, display controller, 显示输出通道) | Upper-Half / Lower-Half (fb_vtable_s) | `references/fb_pattern.md` | ✅ Available |
| **lcd** (SPI/I2C LCD panel, ST7789, SSD1306, ILI9341...) | Upper-Half / Lower-Half (lcd_dev_s) | `references/lcd_pattern.md` | ✅ Available |
| **usb** (DWC3, ChipIdea, DWC2, MUSB, CDNS3...) | usbdev Interface (DCD) | `references/usbdev_dcd_pattern.md` | ✅ Available |
| **audio/PA** (I2S smart PA amplifier: AW88266A, CS35L42, FS1818, FS1999...) | Upper-Half / Lower-Half (audio_comp) | `references/pa_amplifier_pattern.md` | ✅ Available |
| **timers** (timer, watchdog, RTC) | Upper-Half / Lower-Half | `references/wdg_pattern.md` (watchdog); 待创建: `timer_pattern.md` (timer/RTC) | ✅ Partial |
| **leds** (GPIO LED, PWM LED, WS2812) | Upper-Half / Lower-Half | 待创建: `led_pattern.md` | 🔲 Planned |
| **input** (touchscreen, buttons, keyboard, mouse) | Upper-Half / Lower-Half | `references/input_pattern.md` | ✅ Available |
| **analog** (ADC, DAC) | Upper-Half / Lower-Half | 待创建: `analog_pattern.md` | 🔲 Planned |
| **serial** (UART) | Upper-Half / Lower-Half | 待创建: `serial_pattern.md` | 🔲 Planned |
| **power/battery** (charger, monitor, gauge) | Upper-Half / Lower-Half | `references/battery_charger_pattern.md` (入口) + `references/battery_ops_reference.md` (必须同时加载) | ✅ Available |
| **vibrator** | Character Device | 待创建: `vibrator_pattern.md` | 🔲 Planned |
| **usbhost-hcd** (EHCI, xHCI, DWC2, DWC3, OHCI...) | USB Host Controller Driver | `references/usbhost_hcd_pattern.md` | ✅ Available |
| **mcal** (AUTOSAR MCAL → NuttX: Uart, Spi, I2c, Adc, Can, Dma, Gpt, Icu, Pwm, Port, Wdg, Lin, Eth) | MCAL Adaptation Layer | `references/mcal_autosar_pattern.md` | ✅ Available |
| **Other / Unknown** | Standalone Character Device | `references/chardev_pattern.md` | ✅ Built-in |

> [!IMPORTANT] How to use this dispatch table
>
> 1. Identify the driver subsystem from the user's request
> 2. If the subsystem has an **✅ Available** reference → load that reference document for subsystem-specific architecture, data structures, callbacks, and registration API
>    - **Special case: USB subsystem** → The USB DCD driver is located in `nuttx/arch/<arch>/src/<chip>/`, not in `nuttx/drivers/`. The documentation loading rules are as follows:
>      - **Must be loaded** `references/usbdev_dcd_pattern.md` — The core reference for all USB DCD adapters (usbdev interface, EP0 state machine, DMA alignment, debug methods)
>      - **Must be loaded (vendor HAL adaptation)** `references/usb_bes_v2_lessons.md` — When adapting a USB DCD driver on top of a vendor HAL layer (not writing bare-metal register code), load this document for common pitfalls: HAL re-init after bus reset, speed detection, VBUS call chain tracing, callback context design, and incremental build traps. These lessons apply to all vendor HAL-based USB DCD work, not only BES.
>      - **Load on demand** `references/usb_source_lookup.md` — Load when you need to find the reference driver source code or when you are unsure of the controller IP type (Phase 1 research phase).
>      - **Load on demand** `references/usb_dwc3_case_study.md` — Load when the target controller is DWC3 or CDNS3 (TRB architecture); other IPs can be skipped, but it is recommended to browse the Bug Pattern Summary table to understand common bug patterns.
>    - **Special case: MCAL subsystem** → The MCAL adaptation layer bridges AUTOSAR MCAL modules to NuttX upper-half drivers. It is located in `frameworks/system/autocore/mcal/`, not in `nuttx/drivers/`. The documentation loading rules are as follows:
>      - **Must be loaded** `references/mcal_autosar_pattern.md` — The core reference for all MCAL adaptations (9-step workflow, architecture, code generation rules, EB Tresos config, build verification)
>      - **Must be loaded** `references/mcal_code_pattern.md` — C code templates for adaptation files (struct layout, ops callbacks, init function, ISR routing, notification callbacks, Det/Dem error reporting)
>      - **Must be loaded** `references/mcal_module_mapping.md` — MCAL→NuttX framework mapping table, cross-module dependency list with EB config steps, NuttX ops structures, MCAL API patterns, search paths
> 3. If the subsystem is **🔲 Planned** → fall back to Pattern B (standalone character device) below, and search in-tree drivers under `nuttx/drivers/<subsystem>/` for reference implementations
> 4. The reference document contains ALL subsystem-specific knowledge (data structures, callback details, registration API, data delivery modes, etc.). The main SKILL.md only contains cross-subsystem generic knowledge.

> [!NOTE] Adding a new driver subsystem reference
>
> To add support for a new driver subsystem (e.g., timers, leds, input):
> 1. Create `references/<subsystem>_pattern.md` following the structure of `sensor_uorb_pattern.md`
> 2. Include: architecture overview, key data structures, callback table, data delivery modes, registration API, complete examples
> 3. Update the Driver Type Dispatch Table above to change status from 🔲 to ✅
> 4. Add example driver paths to `references/nuttx_nav_search.md` under the corresponding subsystem section
> 5. The main SKILL.md does NOT need other modifications — the dispatch mechanism handles routing automatically

#### Pattern A: Upper-Half / Lower-Half — Preferred for Subsystem Drivers

Most NuttX driver subsystems (sensors, timers, serial, audio, etc.) use an upper-half/lower-half split. The upper-half provides the VFS interface and common logic; you only implement the lower-half callbacks.

```
Application
    │  open/read/write/ioctl/poll
    ▼
  VFS (/dev/...)
    │
    ▼
  Upper-half (provided by NuttX)  ← Generic, handles VFS + common logic
    │  subsystem-specific callbacks
    ▼
  Lower-half (your driver)  ← You implement these
    │
    ▼
  Bus API (I2C_TRANSFER / SPI_*)
```

**When to use**: Always prefer this when the subsystem provides an upper-half framework. Load the corresponding reference document from the dispatch table for implementation details.

#### Pattern B: Standalone Character Device Driver

The simplest pattern. The driver directly implements `struct file_operations` and registers with `register_driver()`.

```
Application
    │  open/read/write/ioctl/close
    ▼
  VFS (/dev/mydevN)
    │
    ▼
  file_operations  ← Your driver implements these
    │
    ▼
  Bus API (I2C_TRANSFER / SPI_LOCK+SPI_SELECT+SPI_RECVBLOCK)
```

**When to use**: Only when the device doesn't fit any existing subsystem, or when existing in-tree drivers for the same device class already use this pattern (e.g., some vibrator or custom peripheral drivers).

See `references/chardev_pattern.md` for standalone character device implementation details.

### 4) Layout the driver files

Define the files that will be created/modified. The exact layout depends on the subsystem — refer to the loaded reference document for subsystem-specific examples.

General layout:

```
nuttx/
├── drivers/<subsystem>/
│   ├── mydevice.c            # Driver implementation
│   ├── Make.defs             # Add CSRCS += mydevice.c
│   ├── CMakeLists.txt        # Add list(APPEND SRCS mydevice.c)
│   └── Kconfig               # Add CONFIG_<SUBSYSTEM>_MYDEVICE entry
├── include/nuttx/<subsystem>/
│   └── mydevice.h            # Public header: register function prototype
└── boards/<arch>/<chip>/<board>/src/
    └── <board>_mydevice.c    # Board-level init: get bus, call register
```

## Implementation

> [!info] TLDR
>
> - Implement C driver (lower-half callbacks OR file_operations)
> - Create public header file with register function prototype
> - Add Kconfig entry for driver enable/disable
> - Add source to Make.defs and CMakeLists.txt
> - Write board-level registration code

### Write the Driver C Implementation

For subsystem-specific implementation (sensor uORB, timer, LED, etc.), load the corresponding reference document from the Driver Type Dispatch Table above.

The following sections cover **cross-subsystem generic patterns** that apply to all driver types.

#### Bus Access (I2C / SPI)

For I2C/SPI register read/write helpers, bulk transfer patterns, SPI frame protocols (8-bit and 16-bit), and SPI mode detection — read `references/bus_access.md` when writing bus access code.

### Write the Public Header File

Create `include/nuttx/<subsystem>/mydevice.h`. The header content depends on the driver pattern:

- **Upper-half/lower-half drivers**: Header contains only the register function prototype
- **Standalone char device drivers**: Header also defines data structures for `read()`

See `references/chardev_pattern.md` for char device header examples.

### Kconfig

Add your driver entry to the appropriate `Kconfig` file (e.g., `drivers/<subsystem>/Kconfig`):

```kconfig
config <SUBSYSTEM>_MYDEVICE
	bool "MyVendor MYDEVICE sensor/device support"
	default n
	---help---
		Enable driver for the MyVendor MYDEVICE.

if <SUBSYSTEM>_MYDEVICE

choice
	prompt "MYDEVICE Interface"
	default <SUBSYSTEM>_MYDEVICE_SPI

config <SUBSYSTEM>_MYDEVICE_I2C
	bool "MYDEVICE I2C Interface"
	select I2C
	---help---
		Enables support for the I2C interface

config <SUBSYSTEM>_MYDEVICE_SPI
	bool "MYDEVICE SPI Interface"
	select SPI
	---help---
		Enables support for the SPI interface

endchoice

endif # <SUBSYSTEM>_MYDEVICE
```

Key Kconfig conventions:
- Use `select I2C` or `select SPI` to auto-enable bus dependency
- Use `depends on` for soft dependencies
- Group device-specific options inside `if ... endif`
- Provide `---help---` text for every user-visible option
- For devices supporting both I2C and SPI, use a `choice` block
- Default interface should match the most common usage (check reference designs)

### Make.defs

Add your source file to `drivers/<subsystem>/Make.defs`:

```makefile
ifeq ($(CONFIG_<SUBSYSTEM>_MYDEVICE),y)
  CSRCS += mydevice.c
endif
```

> [!NOTE] File naming convention
>
> - Upper-half/lower-half drivers with framework: `mydevice_uorb.c` (sensors), `mydevice.c` (others)
> - Standalone char device drivers: `mydevice.c`
> - Check existing in-tree drivers for the naming convention used in your subsystem.
> - The Make.defs entry does NOT need to be under an `ifeq ($(CONFIG_I2C),y)` guard
>   if the Kconfig already uses `select I2C` — the bus will always be enabled.

The Make.defs file **must** end with the DEPPATH/VPATH/CFLAGS block (already present in existing files).

### CMakeLists.txt

Add your source file to `drivers/<subsystem>/CMakeLists.txt`:

```cmake
if(CONFIG_<SUBSYSTEM>_MYDEVICE)
  list(APPEND SRCS mydevice.c)
endif()
```

### Board-Level Registration

See `references/board_registration.md` for complete board-level registration patterns for both upper-half/lower-half and standalone char device drivers.

## NuttX Coding & Kernel API Rules

For all coding style rules, kernel API usage, initialization timing, work queue selection, interrupt patterns, synchronization primitives, RPMsg rules, and error cleanup patterns — read `references/coding_rules.md` when writing or reviewing driver code.

Quick reference (always run after generating code):

```bash
nuttx/tools/checkpatch.sh -f <your_file.c>
```

## Code Review

6-dimension review covering initialization, power management, API usage,
critical section & ISR, runtime environment, and coding style.

### Review Process

1. Read `.c`, `.h`, Kconfig, Make.defs, CMakeLists.txt. Identify driver pattern
   (uORB sensor / standalone chardev / RPMsg / other upper-half/lower-half).
2. Run 6-dimension checklist — mark each item PASS / WARN / FAIL.
   See [driver_review_checklist.md](references/driver_review_checklist.md).
3. Run nxstyle check: `nuttx/tools/checkpatch.sh -f <file.c>` (skip if unavailable).
4. Generate structured report (template in checklist reference).
5. For each FAIL/WARN: provide line number + fix snippet + rule reference.

### Review Rules

- FAIL items must be fixed before submission. Auto-fix and re-check until zero FAIL.
- WARN items are listed for user decision.
- If more than 5 issues found on file save, recommend running full review.

## References

### Subsystem-Specific Patterns

| Reference | Description | Status |
|-----------|-------------|--------|
| `references/sensor_uorb_pattern.md` | Sensor driver pattern using uORB upper-half (accel, gyro, baro, temp, humi, light, mag, prox, IMU) | ✅ |
| `references/can_netdev_pattern.md` | CAN SocketCAN netdev driver pattern (CAN 2.0B, CAN FD, SocketCAN network device adaptation) | ✅ |
| `references/lin_netdev_pattern.md` | LIN SocketCAN netdev driver pattern (LIN master/slave, sleep/wakeup, over SocketCAN network adaptation) | ✅ |
| `references/fb_pattern.md` | Framebuffer driver pattern (LCDC, GPU, display controller with dedicated VRAM) | ✅ |
| `references/lcd_pattern.md` | LCD driver pattern (SPI/I2C LCD panels: ST7789, SSD1306, ILI9341, etc.) | ✅ |
| `references/usbdev_dcd_pattern.md` | USB DCD driver pattern (DWC3, ChipIdea, DWC2, MUSB, CDNS3) with related refs: `usb_dwc3_case_study.md`, `usb_source_lookup.md` | ✅ |
| `references/pa_amplifier_pattern.md` | I2S smart PA amplifier driver pattern (AW88266A, CS35L42, FS1818, FS1999, audio_comp integration) | ✅ |
| `references/wdg_pattern.md` | Watchdog driver pattern (NuttX upper-half/lower-half, MCAL API mapping) | ✅ Available |
| 待创建: `timer_pattern.md` | Timer/RTC driver pattern | 🔲 Planned |
| 待创建: `led_pattern.md` | LED driver pattern (GPIO, PWM, WS2812) | 🔲 Planned |
| `references/input_pattern.md` | Input device driver pattern (touchscreen, buttons, keyboard, mouse) | ✅ Available |
| 待创建: `analog_pattern.md` | ADC/DAC driver pattern | 🔲 Planned |
| 待创建: `serial_pattern.md` | UART/serial driver pattern | 🔲 Planned |
| 待创建: `vibrator_pattern.md` | Vibrator driver pattern | 🔲 Planned |
| `references/usbhost_hcd_pattern.md` | USB Host Controller Driver 适配 (EHCI, xHCI, DWC2, DWC3, OHCI) | ✅ |

### MCAL/AUTOSAR Adaptation References

| Reference | Description | Status |
|-----------|-------------|--------|
| `references/mcal_autosar_pattern.md` | MCAL to NuttX adaptation pattern (9-step workflow, architecture, EB Tresos config, build verification) | ✅ |
| `references/mcal_code_pattern.md` | MCAL adaptation C code templates (struct layout, ops callbacks, ISR routing, notification callbacks, Det/Dem error reporting) | ✅ |
| `references/mcal_module_mapping.md` | MCAL→NuttX framework mapping table, cross-module dependencies with EB config steps, NuttX ops structures, MCAL API patterns | ✅ |

### Cross-Subsystem References

Load these references based on the task at hand:

| Reference | When to Load | Description |
|-----------|-------------|-------------|
| `references/chardev_pattern.md` | When using Pattern B (standalone char device) or as fallback for 🔲 Planned subsystems | Standalone character device driver pattern |
| `references/bus_access.md` | When writing I2C/SPI register access code | I2C message model, SPI 8/16-bit frame protocols, bulk transfer patterns |
| `references/coding_rules.md` | When writing or reviewing any driver code | Coding style, kernel API, interrupt rules, synchronization, RPMsg, error cleanup |
| `references/board_registration.md` | When writing board-level init code | Board init timing, I2C/SPI registration templates, defconfig |
| `references/nuttx_nav_search.md` | When searching for reference drivers or navigating the source tree | Driver examples by subsystem + NuttX tree navigation |
| `references/driver_review_checklist.md` | When reviewing driver code (Step D.3 / Mode C) | 6-dimension checklist, common pitfalls, report template |
