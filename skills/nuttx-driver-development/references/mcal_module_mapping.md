# MCAL Module to NuttX Framework Mapping

## Table of Contents
- [Module Directory Mapping](#module-directory-mapping)
- [Cross-Module Dependencies](#cross-module-dependencies)
- [Search Paths](#search-paths)

## Module Directory Mapping

| User Input | MCAL Directory Name | NuttX Framework | iLLD Reference File |
|-----------|-------------------|----------------|-------------------|
| uart | `Uart` | serial (`uart_ops_s`) | `aurix_uart.c` |
| spi | `Spi` | SPI (`spi_ops_s`) | `aurix_qspi.c` / `aurix_spi.c` |
| i2c | `I2c` | I2C (`i2c_ops_s`) | `aurix_i2c.c` |
| adc | `Adc` | ADC (`adc_ops_s`) | `aurix_evadc.c` |
| can | `Can_17_McmCan` | CAN (`can_ops_s`) | `aurix_mcmcan.c` |
| dma | `Dma` | Internal (no upper-half) | `aurix_dma.c` |
| gpt | `Gpt` | Timer (`timer_ops_s`) | `aurix_egtm_tom_timer.c` |
| icu | `Icu` | Capture (`cap_ops_s`) | `aurix_egtm_capture.c` |
| pwm | `Pwm_17_TimerIp` | PWM (`pwm_ops_s`) | `aurix_egtm_pwm.c` / `aurix_gtm_pwm.c` |
| port | `Port` | GPIO (`gpio_operations_s`) | `aurix_port.c` |
| wdg | `Wdg_17_Wtu` | Watchdog (`watchdog_ops_s`) | `aurix_wdg.c` |
| lin | `Lin_17_AscLin` | serial (LIN mode) | `aurix_lin.c` |
| eth | `Eth_17_Geth` | Ethernet (`netdev_ops_s`) | `aurix_enet.c` |
| mcu | `Mcu` | board_reset/board_reset_cause | (existing: `mcu.c`) |

## Cross-Module Dependencies

When adapting a module, also check and adapt these related integrations.

**⚠️ 重要**：对于每个硬依赖模块，如果板上未启用，必须在文档中输出该依赖模块的 EB 配置步骤。

### Dependency Type Legend

| 类型 | 符号 | 说明 |
|------|------|------|
| 硬依赖（AUTOSAR 架构级） | 🔴 | AUTOSAR 规范要求由独立模块负责，缺失则功能完全不工作 |
| 硬依赖（功能级） | 🟠 | 特定功能模式下必需，可通过切换模式规避 |
| 软依赖 | 🟢 | 增强功能，非必需 |

### Uart
- 🔴 **Port（引脚复用）**：MCAL Uart 不含任何引脚配置代码（AUTOSAR 设计）。TX/RX 引脚的 PADCFG（模式、方向、上下拉）和 ASCLIN IOCR（ALTI 输入选择）必须由外部配置。
  - **EB 配置方案**：在 Port 模块中添加 UART TX/RX 引脚配置
    1. Port → PortConfigSet → PortContainer → 添加 PortPin
    2. TX 引脚：PortPinDirection = PORT_PIN_OUT, PortPinMode = 对应 ALT 模式（如 ASCLIN3_TX = ALT3）
    3. RX 引脚：PortPinDirection = PORT_PIN_IN, PortPinMode = 对应 ALT 模式, PortPinInputPull = PULL_UP
    4. 分区映射：Port 配置需映射到与 Uart 相同的 EcucPartition
    5. 初始化顺序：`Port_Init()` 必须在 `Uart_Init()` 之前调用
  - **替代方案（不启用 MCAL Port）**：在适配层中使用 iLLD 函数手动配置引脚
    1. 在 `mcal_uart_config_s` 中添加引脚配置字段（tx_pin, rx_pin, tx_mode, rx_mode, pad_driver）
    2. 在 `mcal_uart_initialize()` 中 `Uart_Init()` 之后调用 `IfxAsclin_initTxPin()` / `IfxAsclin_initRxPin()`
    3. ⚠️ 必须在 `Uart_Init()` 之后调用，因为 `IfxAsclin_initRxPin()` 会写 ASCLIN IOCR 寄存器，需要模块时钟（CLC）已使能
    4. 需要在 `mcal_uart.h` 中 `#include <Asclin/Std/IfxAsclin.h>`
- 🟠 **Dma（DMA 传输）**：如果 Uart 配置为 DMA 模式（EB 中 UartChannelTransmitMode/ReceiveMode = DMA），则依赖 Dma 模块
  - `Dma_Init()` 必须在 `Uart_Init()` 之前调用
  - EB 中需配置对应的 DMA 通道
- 🟢 **syslog 集成**：如果 UART 用作控制台，需适配 `tricore_lowputc()`, `up_putc()`, `tricore_earlyserialinit()`

### Spi
- 🟠 **Dma（DMA 传输）**：SPI 在 DMA 模式下依赖 Dma 模块
  - **EB 配置方案**：
    1. Dma → DmaChannelConfig → 为 SPI TX/RX 各添加一个 DMA 通道
    2. 配置 DMA 通道的源/目标地址、传输宽度、优先级
    3. Spi 模块中 SpiChannelAssignment 引用对应的 DMA 通道 ID
    4. 分区映射：Dma 配置需映射到与 Spi 相同的 EcucPartition
    5. 初始化顺序：`Dma_Init()` 必须在 `Spi_Init()` 之前调用
  - **替代方案**：在 EB 中将 SpiChannelTransmitMode 设为 INTERRUPT 模式（非 DMA），无需 Dma 模块
- 🔴 **Port（引脚复用）**：SPI CLK/MOSI/MISO/CS 引脚配置
  - **EB 配置方案**：同 Uart → Port，为每个 SPI 引脚添加 PortPin 配置
  - **替代方案**：使用 iLLD `IfxQspi_initXxxPin()` 函数手动配置

### I2c
- 🟠 **Dma（DMA 传输）**：I2C 在 DMA 模式下依赖 Dma 模块
  - 配置方式同 Spi → Dma
  - **替代方案**：使用 INTERRUPT 模式
- 🔴 **Port（引脚复用）**：I2C SDA/SCL 引脚配置（需要开漏模式 + 外部上拉）
  - **EB 配置方案**：
    1. SDA 引脚：PortPinDirection = PORT_PIN_IN_OUT, PortPinOutputMode = OPEN_DRAIN
    2. SCL 引脚：PortPinDirection = PORT_PIN_OUT, PortPinOutputMode = OPEN_DRAIN
  - **替代方案**：使用 iLLD `IfxI2c_initSdaPin()` / `IfxI2c_initSclPin()` 函数

### Can
- 🔴 **Port（引脚复用）**：CAN TX/RX 引脚配置
  - **EB 配置方案**：同 Uart → Port
  - **替代方案**：使用 iLLD `IfxCan_initTxPin()` / `IfxCan_initRxPin()` 函数
- 🟢 **Port/Dio（收发器控制）**：CAN 收发器 STB（Standby）引脚控制
  - 通过 GPIO 输出控制收发器使能/待机
  - 可在板级代码中直接操作 Port 寄存器，不需要 MCAL Port 模块

### Eth
- 🔴 **Port（引脚复用）**：RMII/RGMII 接口引脚配置（多达 10+ 引脚）
  - 强烈建议使用 MCAL Port 模块，手动配置引脚数量过多
- 🟢 **PHY 管理**：PHY 复位引脚、MDIO/MDC 通信
- 🟢 **Gpt/Timer**：PHY link 状态轮询定时器

### Adc
- 🟠 **Dma（DMA 传输）**：ADC 结果 DMA 传输
- 🟢 **Gpt/Timer**：ADC 触发源（定时器触发采样）
- 🔴 **Port（引脚复用）**：ADC 模拟输入引脚配置（通常为默认模拟功能，可能不需要额外配置）

### Pwm
- 🔴 **Port（引脚复用）**：PWM 输出引脚配置
  - **EB 配置方案**：配置输出引脚为对应 ALT 模式
  - **替代方案**：使用 iLLD `IfxEgtm_initTomPin()` / `IfxEgtm_initAtomPin()` 函数

### Dma（作为被依赖模块的 EB 配置通用步骤）

当其他模块依赖 Dma 模块时，以下是 Dma 模块的通用 EB 配置步骤：

1. **添加 Dma 模块**：在 EB Tresos 项目中添加 Dma 模块（如果尚未添加）
2. **创建 DmaChannelConfig**：Dma → DmaConfig → DmaChannelConfig
3. **为每个使用 DMA 的外设添加通道**：
   - `DmaChannelId`：唯一 ID
   - `DmaChannelTransferType`：SINGLE / LINKED_LIST / AUTO_SWITCH
   - `DmaTransferWidth`：8 / 16 / 32 bit
   - `DmaSourceAddress` / `DmaDestinationAddress`：外设寄存器地址或内存地址
4. **分区映射**：将 Dma 配置映射到使用该 DMA 通道的模块所在的 EcucPartition
5. **初始化调用**：在 `board_lateinitialize_phaseB()` 中添加 `Dma_Init()` 调用，必须在所有使用 DMA 的模块之前

### Port（作为被依赖模块的 EB 配置通用步骤）

当其他模块依赖 Port 模块时，以下是 Port 模块的通用 EB 配置步骤：

1. **添加 Port 模块**：在 EB Tresos 项目中添加 Port 模块（如果尚未添加）
2. **创建 PortConfigSet**：Port → PortConfigSet
3. **添加 PortContainer**：每个物理端口（如 P00, P01, P02...）对应一个 PortContainer
4. **添加 PortPin**：在对应的 PortContainer 中为每个需要配置的引脚添加 PortPin
   - `PortPinId`：全局唯一的引脚 ID
   - `PortPinDirection`：PORT_PIN_IN / PORT_PIN_OUT / PORT_PIN_IN_OUT
   - `PortPinInitialMode`：引脚的初始功能模式（ALT0-ALT7，具体值参考芯片数据手册的 Port I/O 章节）
   - `PortPinLevelValue`：初始电平（PORT_PIN_LEVEL_LOW / PORT_PIN_LEVEL_HIGH）
   - `PortPinInputPull`：输入上下拉（NO_PULL / PULL_UP / PULL_DOWN）
   - `PortPinOutputMode`：输出模式（PUSH_PULL / OPEN_DRAIN）
   - `PortPinPadDriver`：驱动强度（SPEED_GRADE_1 ~ SPEED_GRADE_4）
5. **分区映射**：将 Port 配置映射到需要使用的 EcucPartition
6. **生成代码**：生成后检查 `Port_PBcfg.c` 中的引脚配置数组
7. **初始化调用**：在 `board_lateinitialize_phaseB()` 中添加 `Port_Init()` 调用，必须在所有依赖 Port 的模块之前

## Search Paths

### MCAL Static Code (read-only, do not modify)
```
vendor/infineon/chips/aurix/mcal/mcal_code/<ModuleName>/ssc/src/<Module>.c
vendor/infineon/chips/aurix/mcal/mcal_code/<ModuleName>/ssc/inc/<Module>.h
```

### EB-Generated Dynamic Code (read-only)
```
vendor/infineon/boards/aurix/common_code/mcal/mcal_dynamic_code/src/<Module>_PBcfg.c
vendor/infineon/boards/aurix/common_code/mcal/mcal_dynamic_code/src/<Module>_Data.c
vendor/infineon/boards/aurix/common_code/mcal/mcal_dynamic_code/inc/<Module>_Cfg.h
vendor/infineon/boards/aurix/common_code/mcal/mcal_dynamic_code/inc/<Module>_PBcfg.h
```

### iLLD Reference (study for adaptation patterns)
```
vendor/infineon/chips/aurix/aurix_<module>.c
vendor/infineon/chips/aurix/aurix_<module>.h
```

### Existing MCAL Adaptation Wrappers (study for MCAL bridging patterns)
```
vendor/infineon/chips/aurix/mcal/aurix_mcal_<module>.c
vendor/infineon/chips/aurix/mcal/aurix_mcal_<module>.h
```
Note: Do NOT use `aurix_mcal_*` files as MCAL source — they are adaptation wrappers.

### Board-Level Configuration
```
vendor/infineon/boards/aurix/tc4d9_evb_bmp/src/tc4d9.c              # Main board init (call order only)
vendor/infineon/boards/aurix/tc4d9_evb_bmp/src/tc4d9_mcal.c        # MCAL board config + EB callbacks + init
vendor/infineon/boards/aurix/tc4d9_evb/src/
vendor/infineon/boards/aurix/tc4d7_evb/src/
```

### Generated Configuration
```
out/generated/
```

### Output Location
```
frameworks/system/autocore/mcal/<module>.c
frameworks/system/autocore/mcal/<module>.h
```
