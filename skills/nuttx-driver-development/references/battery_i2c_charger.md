# I2C Battery Charger Driver Template

> **适用场景**：通过 I2C 总线控制充电参数（电压/电流/输入限流），读取充电状态和故障信息。
> **代表驱动**：BQ25618 `bq25618.c`、SC89620 `sc89620.c`
> **前置依赖**：`battery_ops_reference.md`、`battery_charger_pattern.md`、`bus_access.md`

## 1. 架构

```
Application (ioctl: set voltage/current)
    │
    ▼
Upper-Half (battery_charger framework)
    │
    ▼
Lower-Half (xxx_charger.c) ──► I2C Bus ──► Charger IC
    │
    ├── state/health/online: 读状态寄存器
    ├── voltage/current/input_current: 写控制寄存器
    ├── operate: VBUS 状态处理
    └── IRQ (可选): 充电器中断 → work_queue 上报
```

## 2. 文件布局

```
xxx_charger.c       # Charger Lower Half (11 ops + I2C 寄存器操作)
xxx_charger_reg.h   # 寄存器地址/掩码/枚举定义
board_xxx_charger.c # Board 层注册 (I2C bus + 中断引脚)
Kconfig             # 配置项
Make.defs           # 编译规则
```

## 3. 寄存器头文件规范 (xxx_charger_reg.h)

命名规范：`{芯片}_{寄存器}_{字段}_{MASK|SHIFT|值}`

```c
#ifndef __DRIVERS_POWER_XXX_CHARGER_REG_H
#define __DRIVERS_POWER_XXX_CHARGER_REG_H

/* I2C 重试次数 */

#define XXX_I2C_RETRY_NUM               3

/* 芯片 ID */

#define XXX_PART_INFO_REG               0x0B
#define XXX_DEV_ID_MASK                 0x78
#define XXX_DEV_ID_SHIFT                3
#define XXX_DEV_ID_EXPECTED             0x05

/* 充电电流设置寄存器 */

#define XXX_REG_CHARGE_CURRENT          0x02
#define XXX_REG_CHARGE_CURRENT_MASK     0x3F
#define XXX_REG_CHARGE_CURRENT_SHIFT    0
#define XXX_ICHG_STEP_MA                10    /* 每步 10mA */
#define XXX_ICHG_MIN_MA                 0
#define XXX_ICHG_MAX_MA                 1500

/* 充电电压设置寄存器 */

#define XXX_REG_CHARGE_VOLTAGE          0x04
#define XXX_REG_CHARGE_VOLTAGE_MASK     0x7F
#define XXX_REG_CHARGE_VOLTAGE_SHIFT    0
#define XXX_VBAT_BASE_MV                3500
#define XXX_VBAT_STEP_MV                10
#define XXX_VBAT_MIN_MV                 3500
#define XXX_VBAT_MAX_MV                 4770

/* 输入电流限制寄存器 */

#define XXX_REG_INPUT_CURRENT           0x06
#define XXX_REG_INPUT_CURRENT_MASK      0x1F
#define XXX_REG_INPUT_CURRENT_SHIFT     0
#define XXX_IINDPM_BASE_MA              100
#define XXX_IINDPM_STEP_MA              100

/* 充电控制寄存器 */

#define XXX_REG_CHG_CTRL                0x16
#define XXX_REG_CHG_CTRL_EN_MASK        0x20
#define XXX_REG_CHG_CTRL_EN_SHIFT       5
#define XXX_REG_CHG_CTRL_HIZ_MASK       0x10
#define XXX_REG_CHG_CTRL_HIZ_SHIFT      4
#define XXX_REG_WD_RST_MASK             0x04
#define XXX_REG_WD_RST_SHIFT            2
#define XXX_REG_WD_TMR_MASK             0x03
#define XXX_REG_WD_TMR_DISABLE          0

/* 状态寄存器 */

#define XXX_REG_STATUS_0                0x1D
#define XXX_REG_STATUS_1                0x1E
#define XXX_VBUS_GOOD_MASK              0x40
#define XXX_CHG_STAT_MASK               0x38
#define XXX_CHG_STAT_SHIFT              3

enum xxx_chg_state_e
{
  XXX_CHG_NOT_CHARGING = 0,
  XXX_CHG_TRICKLE,
  XXX_CHG_PRE_CHARGE,
  XXX_CHG_FAST_CHARGE,
  XXX_CHG_TAPER,
  XXX_CHG_TOP_OFF,
  XXX_CHG_TERMINATED,
};

/* 故障寄存器 */

#define XXX_REG_FAULT                   0x1F
#define XXX_VBAT_FAULT_MASK             0x80
#define XXX_VBUS_FAULT_MASK             0x18
#define XXX_VBUS_FAULT_SHIFT            3

/* 复位寄存器 */

#define XXX_REG_RESET                   0x17
#define XXX_RESET_BIT                   0x80

#endif /* __DRIVERS_POWER_XXX_CHARGER_REG_H */
```

## 4. 驱动模板 (xxx_charger.c)

```c
/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <sys/types.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <debug.h>

#include <nuttx/kmalloc.h>
#include <nuttx/signal.h>
#include <nuttx/wqueue.h>
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/power/battery_charger.h>
#include <nuttx/power/battery_ioctl.h>

#include "xxx_charger_reg.h"

#if defined(CONFIG_BATTERY_CHARGER) && defined(CONFIG_I2C)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct xxx_ic_state_s
{
  uint8_t vbus_stat;
  uint8_t chrg_stat;
  bool    online;
  uint8_t bat_fault;
  uint8_t chrg_fault;
};

struct xxx_charger_dev_s
{
  struct battery_charger_dev_s dev;    /* 必须是首成员 */
  FAR struct charger_config_s *config; /* 硬件配置 (I2C + 引脚) */
  FAR struct charger_params_s *desc;   /* 充电参数 */
};

/****************************************************************************
 * I2C 寄存器访问 (参考 bus_access.md)
 ****************************************************************************/

static int xxx_getreg8(FAR struct xxx_charger_dev_s *priv,
                       uint8_t regaddr, FAR uint8_t *regval)
{
  struct i2c_msg_s msg[2];
  int retries;
  int err;

  msg[0].frequency = priv->config->client.freq;
  msg[0].addr      = priv->config->client.addr;
  msg[0].buffer    = &regaddr;
  msg[0].length    = 1;
  msg[0].flags     = I2C_M_NOSTOP;

  msg[1].frequency = priv->config->client.freq;
  msg[1].addr      = priv->config->client.addr;
  msg[1].buffer    = regval;
  msg[1].length    = 1;
  msg[1].flags     = I2C_M_READ;

  for (retries = 0; retries < XXX_I2C_RETRY_NUM; retries++)
    {
      err = I2C_TRANSFER(priv->config->client.adapter,
                         msg, 2);
      if (err >= 0)
        {
          return OK;
        }

      /* 仅对总线忙（-EBUSY）或资源暂不可用（-EAGAIN）重试。
       * 硬件 NACK 等其他错误直接返回，重试无意义。
       * 延迟 10ms 等待总线恢复（参考 Linux bq27xxx 驱动）。
       */

      if (err != -EBUSY && err != -EAGAIN)
        {
          break;
        }

      nxsig_usleep(10000);
      baterr("I2C read retry %d err=%d\n", retries, err);
    }

  return err;
}

static int xxx_putreg8(FAR struct xxx_charger_dev_s *priv,
                       uint8_t regaddr, uint8_t regval)
{
  struct i2c_config_s config;
  uint8_t buffer[2];

  config.frequency = priv->config->client.freq;
  config.address   = priv->config->client.addr;
  config.addrlen   = 7;

  buffer[0] = regaddr;
  buffer[1] = regval;

  return i2c_write(priv->config->client.adapter,
                   &config, buffer, 2);
}

/* 读-改-写 通用模式 */

static int xxx_modify_reg(FAR struct xxx_charger_dev_s *priv,
                          uint8_t regaddr, uint8_t mask,
                          uint8_t shift, uint8_t value)
{
  uint8_t regval;
  int ret;

  ret = xxx_getreg8(priv, regaddr, &regval);
  if (ret < 0)
    {
      return ret;
    }

  regval &= ~mask;
  regval |= (value << shift) & mask;

  return xxx_putreg8(priv, regaddr, regval);
}

/****************************************************************************
 * 芯片操作
 ****************************************************************************/

static int xxx_detect_device(FAR struct xxx_charger_dev_s *priv)
{
  uint8_t val;
  uint8_t part_no;
  int ret;

  ret = xxx_getreg8(priv, XXX_PART_INFO_REG, &val);
  if (ret < 0)
    {
      baterr("ERROR: Failed to read chip ID: %d\n", ret);
      return ret;
    }

  part_no = (val & XXX_DEV_ID_MASK) >> XXX_DEV_ID_SHIFT;
  if (part_no != XXX_DEV_ID_EXPECTED)
    {
      baterr("ERROR: Unexpected chip ID: 0x%02x\n", part_no);
      return -ENODEV;
    }

  batinfo("Chip ID verified: 0x%02x\n", part_no);
  return OK;
}

static int xxx_reset(FAR struct xxx_charger_dev_s *priv)
{
  uint8_t regval;
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_RESET, &regval);
  if (ret < 0)
    {
      return ret;
    }

  regval |= XXX_RESET_BIT;
  ret = xxx_putreg8(priv, XXX_REG_RESET, regval);
  if (ret < 0)
    {
      return ret;
    }

  nxsig_usleep(500);

  /* 部分芯片 RESET 位读回始终为 1，需手动清除 */

  regval &= ~XXX_RESET_BIT;
  return xxx_putreg8(priv, XXX_REG_RESET, regval);
}

static int xxx_disable_watchdog(FAR struct xxx_charger_dev_s *priv)
{
  return xxx_modify_reg(priv, XXX_REG_CHG_CTRL,
                        XXX_REG_WD_TMR_MASK, 0,
                        XXX_REG_WD_TMR_DISABLE);
}

static int xxx_get_state(FAR struct xxx_charger_dev_s *priv,
                         FAR struct xxx_ic_state_s *state)
{
  uint8_t status0;
  uint8_t status1;
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_STATUS_0, &status0);
  if (ret < 0)
    {
      return ret;
    }

  ret = xxx_getreg8(priv, XXX_REG_STATUS_1, &status1);
  if (ret < 0)
    {
      return ret;
    }

  state->online    = (status1 & XXX_VBUS_GOOD_MASK) != 0;
  state->chrg_stat = (status1 & XXX_CHG_STAT_MASK) >> XXX_CHG_STAT_SHIFT;
  state->bat_fault = (status0 & XXX_VBAT_FAULT_MASK) != 0;

  return OK;
}

#ifdef CONFIG_DEBUG_XXX_CHARGER
static int xxx_dump_regs(FAR struct xxx_charger_dev_s *priv)
{
  uint8_t val;
  int ret;
  int i;

  for (i = 0; i <= 0x1F; i++)
    {
      ret = xxx_getreg8(priv, i, &val);
      if (ret >= 0)
        {
          batinfo("REG[0x%02x] = 0x%02x\n", i, val);
        }
    }

  return OK;
}
#else
#  define xxx_dump_regs(priv)
#endif

/****************************************************************************
 * 参数设置
 ****************************************************************************/

static int xxx_setvolt(FAR struct xxx_charger_dev_s *priv, int mv)
{
  int idx;

  if (mv < XXX_VBAT_MIN_MV || mv > XXX_VBAT_MAX_MV)
    {
      baterr("ERROR: Voltage %d mV out of range\n", mv);
      return -EINVAL;
    }

  idx = (mv - XXX_VBAT_BASE_MV) / XXX_VBAT_STEP_MV;
  return xxx_modify_reg(priv, XXX_REG_CHARGE_VOLTAGE,
                        XXX_REG_CHARGE_VOLTAGE_MASK,
                        XXX_REG_CHARGE_VOLTAGE_SHIFT, idx);
}

static int xxx_setcurr(FAR struct xxx_charger_dev_s *priv, int ma)
{
  int idx;

  if (ma < XXX_ICHG_MIN_MA || ma > XXX_ICHG_MAX_MA)
    {
      baterr("ERROR: Current %d mA out of range\n", ma);
      return -EINVAL;
    }

  idx = ma / XXX_ICHG_STEP_MA;
  return xxx_modify_reg(priv, XXX_REG_CHARGE_CURRENT,
                        XXX_REG_CHARGE_CURRENT_MASK,
                        XXX_REG_CHARGE_CURRENT_SHIFT, idx);
}

static int xxx_set_input_current(FAR struct xxx_charger_dev_s *priv,
                                 int ma)
{
  int idx;

  idx = (ma - XXX_IINDPM_BASE_MA) / XXX_IINDPM_STEP_MA;
  return xxx_modify_reg(priv, XXX_REG_INPUT_CURRENT,
                        XXX_REG_INPUT_CURRENT_MASK,
                        XXX_REG_INPUT_CURRENT_SHIFT, idx);
}

static int xxx_hw_init(FAR struct xxx_charger_dev_s *priv)
{
  int ret;

  ret = xxx_set_input_current(priv, priv->desc->iindpm);
  if (ret < 0) return ret;

  ret = xxx_setcurr(priv, priv->desc->charge_curr);
  if (ret < 0) return ret;

  ret = xxx_setvolt(priv, priv->desc->cv_vol);
  if (ret < 0) return ret;

  xxx_dump_regs(priv);
  batinfo("HW init success\n");
  return OK;
}

/****************************************************************************
 * Charger Ops 实现 (完整 11 个)
 ****************************************************************************/

static int xxx_state(FAR struct battery_charger_dev_s *dev,
                     FAR int *status)
{
  FAR struct xxx_charger_dev_s *priv =
    (FAR struct xxx_charger_dev_s *)dev;
  struct xxx_ic_state_s state;
  int ret;

  ret = xxx_get_state(priv, &state);
  if (ret < 0)
    {
      *status = BATTERY_UNKNOWN;
      return ret;
    }

  if (!state.online)
    {
      *status = BATTERY_DISCHARGING;
    }
  else if (state.chrg_stat == XXX_CHG_TERMINATED)
    {
      *status = BATTERY_FULL;
    }
  else if (state.chrg_stat == XXX_CHG_NOT_CHARGING)
    {
      *status = BATTERY_IDLE;
    }
  else
    {
      *status = BATTERY_CHARGING;
    }

  return OK;
}

static int xxx_health(FAR struct battery_charger_dev_s *dev,
                      FAR int *health)
{
  FAR struct xxx_charger_dev_s *priv =
    (FAR struct xxx_charger_dev_s *)dev;
  struct xxx_ic_state_s state;
  int ret;

  ret = xxx_get_state(priv, &state);
  if (ret < 0)
    {
      *health = BATTERY_HEALTH_UNKNOWN;
      return ret;
    }

  if (state.bat_fault)
    {
      *health = BATTERY_HEALTH_OVERVOLTAGE;
    }
  else
    {
      *health = BATTERY_HEALTH_GOOD;
    }

  return OK;
}

static int xxx_online(FAR struct battery_charger_dev_s *dev,
                      FAR bool *status)
{
  *status = true;
  return OK;
}

static int xxx_voltage(FAR struct battery_charger_dev_s *dev,
                       int value)
{
  return xxx_setvolt((FAR struct xxx_charger_dev_s *)dev, value);
}

static int xxx_current(FAR struct battery_charger_dev_s *dev,
                       int value)
{
  return xxx_setcurr((FAR struct xxx_charger_dev_s *)dev, value);
}

static int xxx_input_current(FAR struct battery_charger_dev_s *dev,
                             int value)
{
  return xxx_set_input_current(
    (FAR struct xxx_charger_dev_s *)dev, value);
}

static int xxx_operate(FAR struct battery_charger_dev_s *dev,
                       uintptr_t param)
{
  return -ENOSYS;
}

static int xxx_chipid(FAR struct battery_charger_dev_s *dev,
                      FAR unsigned int *value)
{
  FAR struct xxx_charger_dev_s *priv =
    (FAR struct xxx_charger_dev_s *)dev;
  uint8_t val;
  int ret;

  ret = xxx_getreg8(priv, XXX_PART_INFO_REG, &val);
  if (ret < 0)
    {
      return ret;
    }

  *value = (val & XXX_DEV_ID_MASK) >> XXX_DEV_ID_SHIFT;
  return OK;
}

static int xxx_get_voltage(FAR struct battery_charger_dev_s *dev,
                           FAR int *value)
{
  return -ENOSYS;
}

static int xxx_voltage_info(FAR struct battery_charger_dev_s *dev,
                            FAR int *value)
{
  return -ENOSYS;
}

static int xxx_get_protocol(FAR struct battery_charger_dev_s *dev,
                            FAR int *value)
{
  return -ENOSYS;
}

static const struct battery_charger_operations_s g_xxx_ops =
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

FAR struct battery_charger_dev_s *
xxx_charger_initialize(FAR struct charger_config_s *config,
                       FAR struct charger_params_s *desc)
{
  FAR struct xxx_charger_dev_s *priv;
  int ret;

  priv = kmm_zalloc(sizeof(struct xxx_charger_dev_s));
  if (priv == NULL)
    {
      return NULL;
    }

  priv->dev.ops = &g_xxx_ops;
  priv->config  = config;
  priv->desc    = desc;

  /* 复位芯片 */

  ret = xxx_reset(priv);
  if (ret < 0)
    {
      baterr("ERROR: Reset failed: %d\n", ret);
      goto err;
    }

  /* 禁用 watchdog（否则芯片回到 standalone 模式） */

  ret = xxx_disable_watchdog(priv);
  if (ret < 0)
    {
      baterr("ERROR: Disable WDT failed: %d\n", ret);
      goto err;
    }

  /* 校验芯片 ID */

  ret = xxx_detect_device(priv);
  if (ret < 0)
    {
      goto err;
    }

  /* 写入充电参数 */

  ret = xxx_hw_init(priv);
  if (ret < 0)
    {
      goto err;
    }

  return (FAR struct battery_charger_dev_s *)priv;

err:
  kmm_free(priv);
  return NULL;
}

#endif /* CONFIG_BATTERY_CHARGER && CONFIG_I2C */
```

## 5. 初始化序列要点

I2C 可控型的初始化必须按以下顺序：

1. `xxx_reset()` — 复位芯片到默认状态
2. `xxx_disable_watchdog()` — **必须**，否则芯片超时后回到 standalone 模式
3. `xxx_detect_device()` — 读取芯片 ID 寄存器，校验是否匹配
4. `xxx_hw_init()` — 按 descriptor 参数写入所有控制寄存器
5. （可选）`xxx_dump_regs()` — 调试模式下打印所有寄存器

## 6. descriptor 参数结构体

```c
/* 通常定义在公共头文件中，如 charger_desc_cfg.h */

struct charger_params_s
{
  int vindpm;          /* 输入电压下限 (mV) */
  int iindpm;          /* 输入电流限制 (mA) */
  int charge_curr;     /* 充电电流 (mA) */
  int cv_vol;          /* 恒压充电电压 (mV) */
  int pre_curr;        /* 预充电电流 (mA) */
  int iterm_curr;      /* 终止电流 (mA) */
};

struct charger_config_s
{
  struct i2c_client_s client;   /* I2C 地址/频率/adapter */
  struct pin_ctl_s pin_ctl;     /* GPIO/中断引脚配置 */
};
```

Board 层传入：

```c
static struct charger_params_s g_charger_desc =
{
  .vindpm      = 4500,
  .iindpm      = 500,
  .charge_curr = 400,
  .cv_vol      = 4200,
  .pre_curr    = 160,
  .iterm_curr  = 60,
};
```
