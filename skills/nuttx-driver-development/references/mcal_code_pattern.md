# MCAL 到 NuttX 适配代码模板

## 代码风格

适配层代码（`frameworks/system/autocore/mcal/`）使用 WebKit 风格：
- 4 空格缩进
- K&R 大括号（左大括号在同一行）
- 生成后运行：`clang-format --style=WebKit -i <file>`
- 验证：`clang-format --style=WebKit -n --Werror <file>`

## 目录
- [源文件模板](#源文件模板)
- [头文件模板](#头文件模板)
- [ISR 路由模式](#isr-路由模式)
- [配置传递模式](#配置传递模式)
- [已有适配示例](#已有适配示例)

## 源文件模板

```c
/****************************************************************************
 * frameworks/system/autocore/mcal/<module>.c
 * Licensed under Apache License 2.0
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <assert.h>
#include <debug.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <nuttx/arch.h>
#include <nuttx/kmalloc.h>
/* Include NuttX framework header, e.g.: */
/* #include <nuttx/serial/serial.h> */

/* Include MCAL headers */
/* #include "<Module>.h" */

#include "<module>.h"

/****************************************************************************
 * Private Types
 ****************************************************************************/

/* Private device struct embedding NuttX device and MCAL state */

struct mcal_<module>_dev_s {
    struct <nuttx_dev_type>_s dev; /* NuttX device struct (MUST be first) */
    /* MCAL-specific state fields */
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

/* Declare all NuttX ops callbacks */

/****************************************************************************
 * Private Data
 ****************************************************************************/

/* NuttX ops structure */

static const struct <nuttx_ops>_s g_<module>_ops = {
    /* Map each ops callback to implementation */
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

/* Implement each NuttX ops callback by calling MCAL APIs */

/****************************************************************************
 * ISR Handlers
 ****************************************************************************/

/* ISR routing: NuttX IRQ handler → MCAL ISR function */

static int mcal_<module>_isr(int irq, void* context, void* arg)
{
    struct mcal_<module>_dev_s* priv = (struct mcal_<module>_dev_s*)arg;
    /* Call MCAL ISR, e.g.: <Module>_Isr<Type>(index); */
    return 0;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int mcal_<module>_initialize(
    const struct mcal_<module>_config_s* config,
    <Module>_ConfigType** mcal_config,
    size_t num)
{
    struct mcal_<module>_dev_s* priv;
    <Module>_ConfigType* ConfigPtr;
    int current_core;
    int ret;

    current_core = up_cpu_index();
    ConfigPtr = mcal_config[current_core];

    if (ConfigPtr != NULL) {
        <Module>_Init(ConfigPtr);
    }

    for (size_t i = 0; i < num; i++) {
        /* Filter by core */

        if (config[i].core_id != current_core) {
            continue;
        }

        priv = kmm_zalloc(sizeof(*priv));
        if (!priv) {
            return -ENOMEM;
        }

        /* Setup NuttX ops */

        priv->dev.ops = &g_<module>_ops;

        /* Attach ISRs */
        /* irq_attach(irq_num, mcal_<module>_isr, priv); */

        /* Register with NuttX */
        /* ret = <framework>_register(&priv->dev, config[i].devpath); */

        if (ret < 0) {
            kmm_free(priv);
            return ret;
        }
    }

    return OK;
}
```

## 头文件模板

```c
/****************************************************************************
 * frameworks/system/autocore/mcal/<module>.h
 * Licensed under Apache License 2.0
 ****************************************************************************/

#ifndef __FRAMEWORKS_SYSTEM_AUTOCORE_MCAL_<MODULE>_H
#define __FRAMEWORKS_SYSTEM_AUTOCORE_MCAL_<MODULE>_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>
/* Include NuttX framework header */
/* Include MCAL type headers as needed */

/****************************************************************************
 * Public Types
 ****************************************************************************/

/* Board-level configuration structure */

struct mcal_<module>_config_s {
    uint8_t core_id; /* Core affinity */
    /* Module-specific config: pins, channels, bus index, etc. */
};

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int mcal_<module>_initialize(
    const struct mcal_<module>_config_s* config,
    <Module>_ConfigType** mcal_config,
    size_t num);

#endif /* __FRAMEWORKS_SYSTEM_AUTOCORE_MCAL_<MODULE>_H */
```

## ISR 路由模式

MCAL 模块定义了形如 `<Module>_Isr<Type>(uint8 HwUnit)` 的 ISR 函数。适配层需要：

1. IRQ 号由板级配置结构体提供，来源于 MCAL 配置工具的 SRC/中断设置。适配层**不计算** IRQ 号——从板级配置接收。

2. 挂载 NuttX IRQ 处理函数：
   ```c
   irq_attach(config->irq, mcal_<module>_isr, priv);
   ```

3. 在 ISR 处理函数中调用 MCAL ISR：
   ```c
   static int mcal_<module>_isr(int irq, void* context, void* arg)
   {
       <Module>_Isr<Type>(hw_unit_index);
       return 0;
   }
   ```

4. 通过 NuttX API 使能中断：
   ```c
   up_enable_irq(config->irq);
   ```

## 配置传递模式

MCAL 配置通常按核区分。模式如下：

```c
/* EB 生成的配置是按核索引的数组 */

<Module>_ConfigType* ConfigPtr = mcal_config[up_cpu_index()];

if (ConfigPtr != NULL) {
    <Module>_Init(ConfigPtr);
}
```

### 板级文件组织

板级 MCAL 代码**不要直接写在 `tc4d9.c` 中**。所有 MCAL 模块的板级配置统一放在 `tc4d9_mcal.c` 中：

```
vendor/infineon/boards/aurix/tc4d9_evb_bmp/src/
├── tc4d9.c                    # 主板级文件，只调用各模块的 init 函数
├── tc4d9_mcal.c               # 所有 MCAL 模块的板级配置 + EB callback wrappers + init
└── ...
```

**`tc4d9_mcal.c` 中放置（所有 MCAL 模块共用此文件）**：
- 各模块的 `mcal_<module>_config_s` 配置数组定义
- EB notification callback wrappers（如 `Uart_Ch0_TxNotification`）
- 各模块的板级初始化函数（如 `tc4d9_mcal_uart_init()`、`tc4d9_mcal_wdg_init()`）
- 每个模块的配置和 init 函数用 `#ifdef CONFIG_AUTOCORE_MCAL_<MODULE>` 包裹

**`tc4d9.c` 中只做**：
- 在 `board_lateinitialize_phaseB()` 中按依赖顺序调用各模块的板级 init 函数

**规则**：
- 新增 MCAL 模块时，在 `tc4d9_mcal.c` 中追加该模块的配置数组和 init 函数（用 `#ifdef` 包裹）
- 在 `tc4d9.c` 的 `board_lateinitialize_phaseB()` 中添加对应的 init 调用
- 如果 `tc4d9_mcal.c` 不存在（首次添加 MCAL 模块），则创建它

```c
/* In tc4d9_mcal.c — all MCAL modules' board-level code */

#include <nuttx/config.h>
#include <sys/param.h>

#ifdef CONFIG_AURIX_MCAL
#include "mcal.h"

/* ============================================================
 * WDG Module
 * ============================================================ */

#ifdef CONFIG_AUTOCORE_MCAL_WDG
#include "wdg.h"

static const struct mcal_wdg_config_s g_mcal_wdg_config[] = {
    {
        .device_index       = 0,
        .core_id            = 0,
        .devpath            = "/dev/wdt_cpu0",
        .disable_allowed    = false,
        .default_mode       = WDGIF_SLOW_MODE,
        .initial_timeout_ms = 500,
        .slow_max_timeout_ms = 5000,
        .fast_max_timeout_ms = 1000,
    },
};

int tc4d9_mcal_wdg_init(void)
{
    return mcal_wdg_initialize(
        (const Wdg_ConfigType* const*)MCAL_WDG_CONFIG,
        g_mcal_wdg_config, nitems(g_mcal_wdg_config));
}
#endif /* CONFIG_AUTOCORE_MCAL_WDG */

/* ============================================================
 * UART Module (example — already exists in tc4d9.c, migrate here)
 * ============================================================ */

#ifdef CONFIG_AUTOCORE_MCAL_UART
/* ... uart config array, callbacks, init ... */
#endif

#endif /* CONFIG_AURIX_MCAL */
```

```c
/* In tc4d9.c — only call board-level init functions */

void board_lateinitialize_phaseB(void)
{
    /* Init order follows dependency: Port → MCU → Dma → Uart/Spi/Wdg/... */

#ifdef CONFIG_AUTOCORE_MCAL_WDG
    tc4d9_mcal_wdg_init();
#endif
    /* ... */
}
```

**好处**：
- `tc4d9.c` 保持简洁，只负责调用顺序编排
- 所有 MCAL 板级配置集中在一个文件中，便于统一维护
- 新增模块只需在 `tc4d9_mcal.c` 中追加 `#ifdef` 块，不会与其他模块产生 merge 冲突

## 通知回调模式

MCAL 模块使用通知回调（在 EB Tresos 中配置）来通知异步操作完成。回调函数名由 EB 生成（如 `Uart_Ch0_TxNotification`、`Uart_Ch0_StreamingNotification`）。

**架构**：适配层暴露通用的公开通知处理函数。板级文件实现 EB 命名的回调包装函数，分发到这些通用处理函数。这种分离是必要的，因为不同板子有不同的通道数量。

```c
/* In mcal_<module>.c — public generic handlers */

void mcal_<module>_tx_notify(uint8_t channel_id,
    <Module>_ErrorIdType error)
{
    /* Look up NuttX dev by channel_id, signal TX completion,
     * trigger NuttX to send more data, etc. */
}

void mcal_<module>_streaming_notify(uint8_t channel_id,
    <Module>_ErrorIdType error, uint16_t size)
{
    /* Look up NuttX dev by channel_id, push data to ring buffer,
     * notify NuttX framework. No re-arm needed for streaming. */
}
```

```c
/* In mcal_<module>.h — public declarations */

void mcal_<module>_tx_notify(uint8_t channel_id,
    <Module>_ErrorIdType error);
void mcal_<module>_streaming_notify(uint8_t channel_id,
    <Module>_ErrorIdType error, uint16_t size);
```

```c
/* In tc4d9_mcal.c — EB-named callback wrappers */

void <Module>_Ch0_TxNotification(const <Module>_ErrorIdType ErrorId)
{
  mcal_<module>_tx_notify(0, ErrorId);
}

void <Module>_Ch1_TxNotification(const <Module>_ErrorIdType ErrorId)
{
  mcal_<module>_tx_notify(1, ErrorId);
}

void <Module>_Ch0_StreamingNotification(const <Module>_ErrorIdType ErrorId,
                                        const <Module>_SizeType RxDataSize)
{
  mcal_<module>_streaming_notify(0, ErrorId, (uint16_t)RxDataSize);
}
/* ... more channels as configured in EB ... */
```

**要点：**
- EB 回调函数名放在板级 MCAL 文件（`tc4d9_mcal.c`）中，**不在**适配层中，也**不在** `tc4d9.c` 中
- 通用处理函数是 `public` 的——在 `.h` 头文件中声明
- 不同板子根据其通道数量实现不同数量的 EB 回调
- `<Module>_VariantExternals.h` 提供 EB 回调名的 `extern` 声明
- 当 EB 配置变更（增删通道、更改通知类型）时，更新板级 MCAL 文件中的回调包装
- 当某个通知类型不再使用（如切换到 streaming 后 RxNotification 不再需要），在 `<Module>_PBcfg.c` 中将其指针设为 `NULL_PTR`，从 `<Module>_VariantExternals.h` 中删除 `extern`，并从板级 MCAL 文件中删除该回调函数

## 错误上报模式（Det/Dem）

适配层使用与 MCAL 模块相同的 Det/Dem 控制宏来条件性地上报错误。这确保错误上报的启用/禁用与 MCAL 模块的配置保持一致。

**条件包含**：
```c
/* 条件包含开发/运行时错误追踪器 */

#if ((<MODULE>_DEV_ERROR_DETECT == STD_ON) || (<MODULE>_RUNTIME_ERROR_DETECT == STD_ON))
#include "Det.h"
#endif
```

**适配层 Service ID**（从 0x80 开始，避免与 MCAL SID 冲突）：
```c
#define MCAL_<MODULE>_SID_INITIALIZE (0x80U)
#define MCAL_<MODULE>_SID_RX_NOTIFY  (0x81U)
#define MCAL_<MODULE>_SID_TX_NOTIFY  (0x82U)
#define MCAL_<MODULE>_SID_ATTACH     (0x83U)
/* ... more as needed ... */
```

**开发错误**（`Det_ReportError`）— 用于参数校验失败：
```c
#if (<MODULE>_DEV_ERROR_DETECT == STD_ON)
    if (config == NULL) {
        (void)Det_ReportError(<MODULE>_MODULE_ID, <MODULE>_INSTANCE_ID,
            MCAL_<MODULE>_SID_INITIALIZE,
            <MODULE>_E_PARAM_POINTER);
        return -EINVAL;
    }
#endif
```

**运行时错误**（`Det_ReportRuntimeError`）— 用于运行时故障：
```c
#if (<MODULE>_RUNTIME_ERROR_DETECT == STD_ON)
    if (error != <MODULE>_E_NO_ERR) {
        DEBUGASSERT(error <= UINT8_MAX); /* Ensure error code fits in uint8 */
        (void)Det_ReportRuntimeError(<MODULE>_MODULE_ID, <MODULE>_INSTANCE_ID,
            MCAL_<MODULE>_SID_RX_NOTIFY,
            (uint8)error);
    }
#endif
```

关键规则：
- 使用 MCAL 模块自身的 `MODULE_ID`、`INSTANCE_ID` 和错误码
- 使用 MCAL 模块自身的 `<MODULE>_DEV_ERROR_DETECT` / `<MODULE>_RUNTIME_ERROR_DETECT` 宏
- 适配层 SID 从 0x80 开始，避免与 MCAL 定义的 SID 冲突
- `Dem_SetEventStatus` 由 MCAL 模块内部处理；适配层不直接调用

## 已有适配示例

学习以下文件中的已有模式：

| 文件 | 模式 |
|------|------|
| `vendor/infineon/chips/aurix/mcal/aurix_mcal_i2c.c` | 完整的 MCAL 到 NuttX I2C 适配，含 ISR、DMA、ops |
| `vendor/infineon/chips/aurix/mcal/aurix_mcal_qspi.c` | MCAL SPI 适配，含 CS 引脚处理 |
| `vendor/infineon/chips/aurix/mcal/aurix_mcal_dma.c` | 简单的 MCAL DMA 初始化包装 |
| `frameworks/system/autocore/mcal/mcu.c` | MCAL MCU 到 NuttX board_reset/board_reset_cause |
| `vendor/infineon/chips/aurix/aurix_uart.c` | iLLD UART 适配（serial 框架参考） |
| `vendor/infineon/chips/aurix/aurix_i2c.c` | iLLD I2C 适配（I2C 框架参考） |

## 代码生成自检清单（强制）

> **执行时机**：适配层 `.c` 文件生成完毕后、进入 D.3.5 验证之前，必须逐项对照此清单。任何未通过项必须立即修复后再继续。

### 错误上报（Det/Dem）

- [ ] 条件包含 `Det.h`：使用模块自身的 `<MODULE>_DEV_ERROR_DETECT` / `<MODULE>_RUNTIME_ERROR_DETECT` 宏控制
- [ ] 适配层 Service ID 从 `0x80` 开始定义，且每个**公开函数**（initialize、notify handler）都有对应 SID
- [ ] `initialize` 函数中对 NULL 指针参数做 `Det_ReportError` 校验（在 `DEV_ERROR_DETECT == STD_ON` 条件下）
- [ ] `initialize` 函数中对 `num == 0` 做 `Det_ReportError` 校验
- [ ] 所有 MCAL API 调用失败路径（返回 `E_NOT_OK` 或非预期值）有 `Det_ReportRuntimeError`（在 `RUNTIME_ERROR_DETECT == STD_ON` 条件下）
- [ ] 通知回调中的错误参数（`error != E_NO_ERR`）有 `Det_ReportRuntimeError`
- [ ] 资源分配失败（`kmm_zalloc` 返回 NULL）有 `Det_ReportRuntimeError` 或至少有 `wderr` 日志

### API 名称与符号隔离

- [ ] 适配层 `.c` 中只使用 AUTOSAR 标准 API 名称（通过 `<Module>.h` wrapper），无厂商特定名称（如 `Wdg_17_Wtu_*`）
- [ ] 无硬件寄存器直接访问（`MODULE_*`、`SRC_*`、`Ifx*` 等符号不出现在适配层中）
- [ ] IRQ 号、HW unit 索引等硬件特定值通过板级 `config` 结构体传入，不硬编码

### 回调与板级分离

- [ ] EB 命名的通知回调包装函数（如 `Uart_Ch0_TxNotification`）在**板级文件**中，不在适配层中
- [ ] 适配层只暴露通用处理函数（如 `mcal_<module>_tx_notify(channel_id, error)`）
- [ ] 如果模块无通知回调（如 WDG），此项标记 N/A

### 代码结构

- [ ] include 顺序：`nuttx/config.h` → NuttX 标准头（errno/stdbool/stdint）→ NuttX 框架头（watchdog.h/serial.h）→ MCAL 头（通过 wrapper）→ 本模块头
- [ ] 私有结构体第一个字段是 NuttX 框架要求的基类型（如 `const struct watchdog_ops_s *ops`）
- [ ] ops 结构体中所有**必选**回调已实现（参考对应 pattern 文档的"必选 ops"章节）
- [ ] `initialize` 函数按核过滤实例（`config[i].core_id != current_core` 则 skip）
- [ ] 注册失败时正确清理已分配资源（kmm_free），不泄漏内存

### 风格

- [ ] 已运行 `clang-format --style=WebKit -i` 格式化
- [ ] SPDX-License-Identifier 在文件第一个注释块中
- [ ] 无骨架参考驱动的残留名称（如参考 uart.c 则搜索 `uart`/`UART` 确认无残留）
