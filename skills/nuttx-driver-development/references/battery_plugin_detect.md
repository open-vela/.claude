# Plugin Detect Battery Charger Driver Template

> **适用场景**：仅检测充电器插拔状态，不控制充电参数。通过 GPIO 引脚电平判断充电器是否连接。
> **代表驱动**：vendor `plug_in.c`
> **前置依赖**：`battery_ops_reference.md`、`battery_charger_pattern.md`

## 1. 架构

```
GPIO Detect Pin (电平变化)
    │
    ▼
IRQ Handler → work_queue(LPWORK) → 读取引脚状态
    │
    ▼
battery_charger_changed() → Upper-Half → poll() 通知应用
    │
    ▼
(可选) deventbus → 通知 gauge/其他模块
```

## 2. 文件布局

```
xxx_plugin.c        # Charger Lower Half (11 ops，大部分返回 OK)
board_xxx_plugin.c  # Board 层注册 + 引脚配置
Kconfig
Make.defs
```

## 3. 驱动模板 (xxx_plugin.c)

```c
/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <debug.h>

#include <nuttx/kmalloc.h>
#include <nuttx/wqueue.h>
#include <nuttx/power/battery_charger.h>
#include <nuttx/power/battery_ioctl.h>

/* 中断处理方式二选一（由用户在输入收集表中选择） */

#ifdef CONFIG_XXX_USE_PINUTILS
#  include <nuttx/pinutils/pinutils.h>
#endif

#ifdef CONFIG_XXX_USE_DEVENTBUS
#  include <nuttx/deventbus/deventbus.h>
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct xxx_plugin_dev_s
{
  struct battery_charger_dev_s dev;     /* 必须是首成员 */
  struct work_s detect_work;            /* 检测工作队列 */
  struct work_s adapter_work;           /* 适配器状态工作队列 */
  bool adapter_state;                   /* 适配器在线状态 */

  /* === 方式 A: pinutils 框架 === */

#ifdef CONFIG_XXX_USE_PINUTILS
  FAR struct charger_config_s *config;
#endif

  /* === 方式 B: 裸 GPIO === */

#ifndef CONFIG_XXX_USE_PINUTILS
  uint32_t detect_pin;                  /* 检测引脚 pinset */
#endif
};

/****************************************************************************
 * 引脚读取（两种方式）
 *
 * 裸 GPIO 模式说明：
 *   NuttX 没有统一的 "board_gpio_read" API。每个 BSP 有自己的 GPIO 函数，
 *   命名规范为 {arch}_gpioread()，例如：
 *     - stm32_gpioread(pinset)
 *     - esp32_gpioread(pin)
 *     - bes_gpioread(pinset)
 *   适配时将下方 xxx_gpioread/xxx_configgpio/xxx_gpioirq_attach 替换为
 *   目标 BSP 的实际函数名。这些函数声明在 arch/<arch>/include/<chip>/xxx_gpio.h 中。
 *
 *   中断注册同理：
 *     - stm32_gpiosetevent(pinset, rising, falling, event, handler, arg)
 *     - bes_gpioirqattach(pinset, handler, arg)
 *   不同 BSP 的中断注册签名差异较大，必须查阅目标 BSP 头文件。
 ****************************************************************************/

/* 厂商 GPIO 函数声明（适配时替换为实际 BSP 头文件 include） */

/* #include <arch/chip/xxx_gpio.h> */

static int xxx_read_detect_pin(FAR struct xxx_plugin_dev_s *priv,
                               FAR bool *value)
{
#ifdef CONFIG_XXX_USE_PINUTILS
  struct pin_info_s *pinfo;

  pinfo = pinutils_get_pininfo(&priv->config->pin_ctl, "detect");
  if (pinfo == NULL)
    {
      baterr("ERROR: get detect pin failed\n");
      return -ENODEV;
    }

  return pinutils_pin_get(pinfo, value);
#else
  /* 裸 GPIO 方式：调用 BSP 层 GPIO 读取函数。
   * 替换 xxx_gpioread 为目标 BSP 的实际函数名，如：
   *   *value = stm32_gpioread(priv->detect_pin);
   *   *value = bes_gpioread(priv->detect_pin);
   */

  *value = xxx_gpioread(priv->detect_pin);
  return OK;
#endif
}

/****************************************************************************
 * 中断与工作队列
 ****************************************************************************/

static void xxx_detect_worker(FAR void *arg)
{
  FAR struct xxx_plugin_dev_s *priv = arg;

  /* 上报状态变化 */

  battery_charger_changed(&priv->dev, BATTERY_STATE_CHANGED);
  battery_charger_changed(&priv->dev, BATTERY_ONLINE_CHANGED);

#ifdef CONFIG_XXX_USE_DEVENTBUS
  /* 通知其他模块（如 gauge） */

  bool plug_state;

  if (xxx_read_detect_pin(priv, &plug_state) >= 0)
    {
      deventbus_send_msg(DEVENTBUS_AP_PLUGIN_STATE_MSG,
                                &plug_state, sizeof(bool));
    }
#endif
}

static void xxx_adapter_worker(FAR void *arg)
{
  FAR struct xxx_plugin_dev_s *priv = arg;

  battery_charger_changed(&priv->dev, BATTERY_ONLINE_CHANGED);
}

/* 中断回调 → 调度到 LPWORK */

#ifdef CONFIG_XXX_USE_PINUTILS
static int xxx_irq_handler(FAR struct ioexpander_dev_s *dev,
                           ioe_pinset_t pinset, FAR void *arg)
#else
static int xxx_irq_handler(int irq, FAR void *context, FAR void *arg)
#endif
{
  FAR struct xxx_plugin_dev_s *priv = arg;

  work_queue(LPWORK, &priv->detect_work,
             xxx_detect_worker, priv, 0);
  return OK;
}

/****************************************************************************
 * Charger Ops 实现 (完整 11 个)
 ****************************************************************************/

static int xxx_state(FAR struct battery_charger_dev_s *dev,
                     FAR int *status)
{
  FAR struct xxx_plugin_dev_s *priv =
    (FAR struct xxx_plugin_dev_s *)dev;
  bool plugged;
  int ret;

  ret = xxx_read_detect_pin(priv, &plugged);
  if (ret < 0)
    {
      *status = BATTERY_UNKNOWN;
      return ret;
    }

  *status = plugged ? BATTERY_CHARGING : BATTERY_DISCHARGING;
  return OK;
}

static int xxx_health(FAR struct battery_charger_dev_s *dev,
                      FAR int *health)
{
  *health = BATTERY_HEALTH_GOOD;
  return OK;
}

static int xxx_online(FAR struct battery_charger_dev_s *dev,
                      FAR bool *status)
{
  FAR struct xxx_plugin_dev_s *priv =
    (FAR struct xxx_plugin_dev_s *)dev;
  bool plugged;
  int ret;

  ret = xxx_read_detect_pin(priv, &plugged);
  if (ret < 0)
    {
      *status = false;
      return ret;
    }

#ifdef CONFIG_XXX_DETECT_HIGH_LEVEL
  *status = !plugged;  /* 高电平有效时取反 */
#else
  *status = plugged;
#endif

  return OK;
}

static int xxx_voltage(FAR struct battery_charger_dev_s *dev,
                       int value)
{
  return OK;
}

static int xxx_current(FAR struct battery_charger_dev_s *dev,
                       int value)
{
  return OK;
}

static int xxx_input_current(FAR struct battery_charger_dev_s *dev,
                             int value)
{
  return OK;
}

static int xxx_operate(FAR struct battery_charger_dev_s *dev,
                       uintptr_t param)
{
  FAR struct xxx_plugin_dev_s *priv =
    (FAR struct xxx_plugin_dev_s *)dev;
  FAR struct batio_operate_msg_s *msg =
    (FAR struct batio_operate_msg_s *)param;

  switch (msg->operate_type)
    {
      case BATIO_OPRTN_VBUS_STATE:
        priv->adapter_state = msg->u32;
        work_queue(LPWORK, &priv->adapter_work,
                   xxx_adapter_worker, priv, 0);
        break;
      default:
        return -ENOSYS;
    }

  return OK;
}

static int xxx_chipid(FAR struct battery_charger_dev_s *dev,
                      FAR unsigned int *value)
{
  *value = 0;
  return OK;
}

static int xxx_get_voltage(FAR struct battery_charger_dev_s *dev,
                           FAR int *value)
{
  return OK;
}

static int xxx_voltage_info(FAR struct battery_charger_dev_s *dev,
                            FAR int *value)
{
  return OK;
}

static int xxx_get_protocol(FAR struct battery_charger_dev_s *dev,
                            FAR int *value)
{
  return OK;
}

static const struct battery_charger_operations_s g_xxx_plugin_ops =
{
  xxx_state,
  xxx_health,
  xxx_online,
  xxx_voltage,
  xxx_current,
  xxx_input_current,
  xxx_operate,
  xxx_chipid,
  xxx_get_voltage,
  xxx_voltage_info,
  xxx_get_protocol,
};

/****************************************************************************
 * Public Functions
 ****************************************************************************/

#ifdef CONFIG_XXX_USE_PINUTILS
FAR struct battery_charger_dev_s *
xxx_plugin_initialize(FAR struct charger_config_s *config)
#else
FAR struct battery_charger_dev_s *
xxx_plugin_initialize(uint32_t detect_pin)
#endif
{
  FAR struct xxx_plugin_dev_s *priv;

  priv = kmm_zalloc(sizeof(struct xxx_plugin_dev_s));
  if (priv == NULL)
    {
      return NULL;
    }

  priv->dev.ops = &g_xxx_plugin_ops;

#ifdef CONFIG_XXX_USE_PINUTILS
  priv->config = config;

  /* 初始化检测引脚 + 注册中断 */

  struct pin_info_s *pinfo;

  pinfo = pinutils_get_pininfo(&config->pin_ctl, "detect");
  if (pinfo != NULL)
    {
      pinutils_pin_init(pinfo);
      pinutils_request_irq(pinfo, xxx_irq_handler, priv);
    }
#else
  priv->detect_pin = detect_pin;

  /* 裸 GPIO 模式：配置引脚为输入 + 注册双边沿中断。
   * 替换 xxx_configgpio / xxx_gpioirq_attach / xxx_gpioirq_enable
   * 为目标 BSP 的实际函数名。
   *
   * 示例（STM32）：
   *   stm32_configgpio(detect_pin);
   *   stm32_gpiosetevent(detect_pin, true, true, false,
   *                      xxx_irq_handler, priv);
   *
   * 示例（BES）：
   *   bes_configgpio(detect_pin | GPIO_INPUT | GPIO_INT_BOTHEDGES);
   *   irq_attach(irqno, xxx_irq_handler, priv);
   *   up_enable_irq(irqno);
   *
   * 不同 BSP 的中断注册方式差异很大，必须查阅目标 BSP 头文件。
   */

  xxx_configgpio(detect_pin);
  xxx_gpioirq_attach(detect_pin, xxx_irq_handler, priv);
  xxx_gpioirq_enable(detect_pin);
#endif

  /* 启动时立即上报一次当前状态 */

  work_queue(LPWORK, &priv->detect_work,
             xxx_detect_worker, priv, MSEC2TICK(100));

  return (FAR struct battery_charger_dev_s *)priv;
}
```

## 4. 与 Standalone/I2C 型的关键区别

| 维度 | Plugin Detect | Standalone | I2C 可控 |
|------|--------------|------------|---------|
| 充电控制 | 无 | PMU 硬件 | I2C 寄存器 |
| voltage/current ops | 返回 OK | 返回 OK | 真实 I2C 写入 |
| 状态来源 | GPIO 电平 | PMU SDK | I2C 状态寄存器 |
| 满充判断 | 无（硬件自行截止） | 软件实现 | 硬件 CHRG_STAT |
| 典型搭配 | + soft_gauge 或 hw_gauge | + battery_monitor | + battery_gauge |
