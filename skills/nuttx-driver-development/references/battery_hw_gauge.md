# Hardware Battery Fuel Gauge IC Template

> **适用场景**：通过 I2C 与硬件电量计 IC 通信，IC 内部算法计算 SOC/电压/电流/温度。
> **代表驱动**：vendor `cw2218.c`、NuttX in-tree `bq27426.c`
> **前置依赖**：`battery_ops_reference.md`、`battery_charger_pattern.md`、`bus_access.md`
> **框架**：`battery_gauge`

## 1. 架构

```
Application (ioctl: read SOC/voltage/temp)
    │
    ▼
Upper-Half (battery_gauge framework)
    │
    ▼
Lower-Half (xxx_gauge.c) ──► I2C Bus ──► Gauge IC
    │
    ├── state/online: 读 IC 状态
    ├── voltage/capacity/current/temp: 读 IC 数据寄存器
    ├── chipid: 读 IC 芯片 ID
    ├── operate: 循环次数/SOH 等扩展查询
    └── 周期采样 (work_queue) → battery_gauge_changed() 上报
```

## 2. 文件布局

```
xxx_gauge.c         # Gauge Lower Half (8 ops + I2C 寄存器操作)
xxx_gauge_reg.h     # 寄存器地址/掩码定义
xxx_gauge.h         # 公共类型 (gauge_params 等)
board_xxx_gauge.c   # Board 层注册 (I2C bus + gauge profile 数据)
Kconfig
Make.defs
```

## 3. 寄存器头文件 (xxx_gauge_reg.h)

```c
#ifndef __DRIVERS_POWER_XXX_GAUGE_REG_H
#define __DRIVERS_POWER_XXX_GAUGE_REG_H

/* 芯片 ID */

#define XXX_REG_CHIP_ID           0x00
#define XXX_CHIP_ID_EXPECTED      0xA0

/* 电压寄存器 (2 bytes, 单位 uV 或 mV 取决于 IC) */

#define XXX_REG_VCELL_H           0x02
#define XXX_REG_VCELL_L           0x03

/* SOC 寄存器 */

#define XXX_REG_SOC_H             0x04
#define XXX_REG_SOC_L             0x05

/* 电流寄存器 (有符号, 2 bytes) */

#define XXX_REG_CURRENT_H         0x06
#define XXX_REG_CURRENT_L         0x07

/* 温度寄存器 */

#define XXX_REG_TEMP_H            0x08
#define XXX_REG_TEMP_L            0x09

/* 模式控制 */

#define XXX_REG_MODE              0x0A
#define XXX_MODE_ACTIVE           0x00
#define XXX_MODE_SLEEP            0xC0

/* Profile 写入区域 */

#define XXX_REG_PROFILE_BASE      0x10
#define XXX_PROFILE_SIZE          80    /* bytes */

/* IC 状态 */

#define XXX_REG_IC_STATE          0x70
#define XXX_IC_READY_MASK         0x0F
#define XXX_IC_READY_VALUE        0x00

/* 温度阈值 */

#define XXX_REG_TEMP_MAX          0x80
#define XXX_REG_TEMP_MIN          0x81
#define XXX_TEMP_MAX_DEFAULT      0xFF
#define XXX_TEMP_MIN_DEFAULT      0x14

#endif /* __DRIVERS_POWER_XXX_GAUGE_REG_H */
```

## 4. 驱动模板 (xxx_gauge.c)

```c
/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <sys/types.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <debug.h>
#include <fixedmath.h>

#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/wqueue.h>
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/power/battery_gauge.h>
#include <nuttx/power/battery_ioctl.h>

#include "xxx_gauge_reg.h"

#if defined(CONFIG_BATTERY_GAUGE) && defined(CONFIG_I2C)

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define XXX_I2C_RETRY_NUM         3
#define XXX_POLL_INTERVAL_MS      5000   /* 周期采样间隔 */
#define XXX_INIT_RETRY_MAX        30     /* IC 就绪等待次数 */
#define XXX_INIT_RETRY_DELAY_MS   100

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct xxx_gauge_dev_s
{
  struct battery_gauge_dev_s dev;          /* 必须是首成员 */
  FAR struct i2c_master_s *i2c;            /* I2C 总线 */
  uint8_t addr;                            /* I2C 地址 */
  uint32_t freq;                           /* I2C 频率 */
  struct work_s work;                      /* 周期采样 */
  mutex_t lock;

  /* 缓存数据（由 worker 周期更新） */

  int voltage;                             /* mV */
  int soc;                                 /* 0-100 */
  int current_ma;                          /* mA (有符号) */
  int temp;                                /* 0.1°C */
  int last_soc;                            /* 上次上报 SOC */
  int last_temp;                           /* 上次上报温度 */
  bool online;                             /* IC 是否就绪 */

  /* Profile 参数（可选，部分 IC 需要） */

  FAR const unsigned char *profile;        /* gauge profile 数据 */
  int profile_size;
};

/****************************************************************************
 * I2C 寄存器访问
 ****************************************************************************/

static int xxx_getreg8(FAR struct xxx_gauge_dev_s *priv,
                       uint8_t regaddr, FAR uint8_t *regval,
                       int len)
{
  struct i2c_msg_s msg[2];
  int retries;
  int err;

  msg[0].frequency = priv->freq;
  msg[0].addr      = priv->addr;
  msg[0].buffer    = &regaddr;
  msg[0].length    = 1;
  msg[0].flags     = 0;

  msg[1].frequency = priv->freq;
  msg[1].addr      = priv->addr;
  msg[1].buffer    = regval;
  msg[1].length    = len;
  msg[1].flags     = I2C_M_READ;

  for (retries = 0; retries < XXX_I2C_RETRY_NUM; retries++)
    {
      err = I2C_TRANSFER(priv->i2c, msg, 2);
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
    }

  return err;
}

static int xxx_putreg8(FAR struct xxx_gauge_dev_s *priv,
                       uint8_t regaddr, uint8_t regval)
{
  struct i2c_msg_s msg;
  uint8_t buffer[2];

  buffer[0] = regaddr;
  buffer[1] = regval;

  msg.frequency = priv->freq;
  msg.addr      = priv->addr;
  msg.buffer    = buffer;
  msg.length    = 2;
  msg.flags     = 0;

  return I2C_TRANSFER(priv->i2c, &msg, 1);
}

/****************************************************************************
 * IC 操作
 ****************************************************************************/

static int xxx_get_chipid(FAR struct xxx_gauge_dev_s *priv,
                          FAR unsigned int *id)
{
  uint8_t val;
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_CHIP_ID, &val, 1);
  if (ret < 0)
    {
      return ret;
    }

  *id = val;
  return OK;
}

static int xxx_get_voltage(FAR struct xxx_gauge_dev_s *priv,
                           FAR int *mv)
{
  uint8_t buf[2];
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_VCELL_H, buf, 2);
  if (ret < 0)
    {
      return ret;
    }

  /* TODO: 转换公式根据 IC datasheet 调整 */

  *mv = ((int)buf[0] << 8 | buf[1]) * 5 / 16;  /* 示例 */
  return OK;
}

static int xxx_get_soc(FAR struct xxx_gauge_dev_s *priv,
                       FAR int *soc)
{
  uint8_t buf[2];
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_SOC_H, buf, 2);
  if (ret < 0)
    {
      return ret;
    }

  *soc = buf[0];  /* 高字节 = 整数部分 */
  return OK;
}

static int xxx_get_current(FAR struct xxx_gauge_dev_s *priv,
                           FAR int *ma)
{
  uint8_t buf[2];
  int16_t raw;
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_CURRENT_H, buf, 2);
  if (ret < 0)
    {
      return ret;
    }

  raw = (int16_t)((buf[0] << 8) | buf[1]);

  /* TODO: 转换公式根据 IC datasheet 调整 */

  *ma = raw * 305 / 1000;  /* 示例 */
  return OK;
}

static int xxx_get_temp(FAR struct xxx_gauge_dev_s *priv,
                        FAR int *temp_01c)
{
  uint8_t buf[2];
  int ret;

  ret = xxx_getreg8(priv, XXX_REG_TEMP_H, buf, 2);
  if (ret < 0)
    {
      return ret;
    }

  /* TODO: 转换公式根据 IC datasheet 调整 */

  *temp_01c = (int)buf[0] * 10 - 400;  /* 示例：偏移 40°C */
  return OK;
}

static int xxx_active(FAR struct xxx_gauge_dev_s *priv)
{
  return xxx_putreg8(priv, XXX_REG_MODE, XXX_MODE_ACTIVE);
}

static int xxx_sleep(FAR struct xxx_gauge_dev_s *priv)
{
  return xxx_putreg8(priv, XXX_REG_MODE, XXX_MODE_SLEEP);
}

/****************************************************************************
 * Profile 写入（部分 IC 需要，如 CW2218）
 ****************************************************************************/

static int xxx_write_profile(FAR struct xxx_gauge_dev_s *priv)
{
  int i;
  int ret;

  if (priv->profile == NULL || priv->profile_size == 0)
    {
      return OK;  /* 无 profile，跳过 */
    }

  /* 进入配置模式 */

  ret = xxx_sleep(priv);
  if (ret < 0)
    {
      return ret;
    }

  nxsig_usleep(50000);

  /* 逐字节写入 profile */

  for (i = 0; i < priv->profile_size; i++)
    {
      ret = xxx_putreg8(priv, XXX_REG_PROFILE_BASE + i,
                        priv->profile[i]);
      if (ret < 0)
        {
          baterr("ERROR: Write profile[%d] failed: %d\n", i, ret);
          return ret;
        }
    }

  /* 退出配置模式 */

  ret = xxx_active(priv);
  if (ret < 0)
    {
      return ret;
    }

  /* 等待 IC 就绪 */

  for (i = 0; i < XXX_INIT_RETRY_MAX; i++)
    {
      uint8_t state;

      nxsig_usleep(XXX_INIT_RETRY_DELAY_MS * 1000);
      ret = xxx_getreg8(priv, XXX_REG_IC_STATE, &state, 1);
      if (ret >= 0 &&
          (state & XXX_IC_READY_MASK) == XXX_IC_READY_VALUE)
        {
          batinfo("IC ready after %d ms\n",
                  (i + 1) * XXX_INIT_RETRY_DELAY_MS);
          return OK;
        }
    }

  baterr("ERROR: IC not ready after profile write\n");
  return -ETIMEDOUT;
}

/****************************************************************************
 * 周期采样 Worker
 ****************************************************************************/

static void xxx_gauge_worker(FAR void *arg)
{
  FAR struct xxx_gauge_dev_s *priv = arg;
  bool soc_changed = false;
  bool temp_changed = false;

  nxmutex_lock(&priv->lock);

  xxx_get_voltage(priv, &priv->voltage);
  xxx_get_soc(priv, &priv->soc);
  xxx_get_current(priv, &priv->current_ma);
  xxx_get_temp(priv, &priv->temp);

  /* 锁内判断是否需要上报，锁外调用 changed()。
   * battery_gauge_changed() 内部会获取 upper-half 的 batlock，
   * 如果在持有 priv->lock 的情况下调用，可能导致 AB-BA 死锁
   * （upper-half ioctl 路径：batlock → ops → priv->lock）。
   */

  if (priv->soc != priv->last_soc)
    {
      priv->last_soc = priv->soc;
      soc_changed = true;
    }

  if (priv->temp != priv->last_temp)
    {
      priv->last_temp = priv->temp;
      temp_changed = true;
    }

  nxmutex_unlock(&priv->lock);

  /* 锁外上报，避免与 upper-half batlock 死锁 */

  if (soc_changed)
    {
      battery_gauge_changed(&priv->dev, BATTERY_CAPACITY_CHANGED);
    }

  if (temp_changed)
    {
      battery_gauge_changed(&priv->dev, BATTERY_TEMPERATURE_CHANGED);
    }

  work_queue(LPWORK, &priv->work, xxx_gauge_worker,
             priv, MSEC2TICK(XXX_POLL_INTERVAL_MS));
}

/****************************************************************************
 * Gauge Ops 实现 (完整 8 个)
 ****************************************************************************/

static int xxx_op_state(FAR struct battery_gauge_dev_s *dev,
                        FAR int *status)
{
  *status = BATTERY_IDLE;
  return OK;
}

static int xxx_op_online(FAR struct battery_gauge_dev_s *dev,
                         FAR bool *status)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *status = priv->online;
  return OK;
}

static int xxx_op_voltage(FAR struct battery_gauge_dev_s *dev,
                          FAR b16_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *value = itob16(priv->voltage);
  return OK;
}

static int xxx_op_capacity(FAR struct battery_gauge_dev_s *dev,
                           FAR b16_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *value = itob16(priv->soc);
  return OK;
}

static int xxx_op_current(FAR struct battery_gauge_dev_s *dev,
                          FAR b16_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *value = itob16(priv->current_ma);
  return OK;
}

static int xxx_op_temp(FAR struct battery_gauge_dev_s *dev,
                       FAR b8_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  /* temp 单位 0.1°C → b8 (整数部分为 °C) */

  *value = itob8(priv->temp / 10);
  return OK;
}

static int xxx_op_chipid(FAR struct battery_gauge_dev_s *dev,
                         FAR unsigned int *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  return xxx_get_chipid(priv, value);
}

static int xxx_op_operate(FAR struct battery_gauge_dev_s *dev,
                          FAR int *param)
{
  FAR struct batio_operate_msg_s *msg =
    (FAR struct batio_operate_msg_s *)param;

  switch (msg->operate_type)
    {
      case BATIO_OPRTN_CAPACITY:
      case BATIO_OPRTN_CYCLE_COUNT:
      case BATIO_OPRTN_CYCLE_LEVEL:
        break;
      default:
        return -ENOSYS;
    }

  return OK;
}

static const struct battery_gauge_operations_s g_xxx_gauge_ops =
{
  xxx_op_state,
  xxx_op_online,
  xxx_op_voltage,
  xxx_op_capacity,
  xxx_op_current,
  xxx_op_temp,
  xxx_op_chipid,
  xxx_op_operate,
};

/****************************************************************************
 * Public Functions
 ****************************************************************************/

FAR struct battery_gauge_dev_s *
xxx_gauge_initialize(FAR struct i2c_master_s *i2c,
                     uint8_t addr, uint32_t freq,
                     FAR const unsigned char *profile,
                     int profile_size)
{
  FAR struct xxx_gauge_dev_s *priv;
  unsigned int chipid;
  int ret;

  DEBUGASSERT(i2c != NULL);

  priv = kmm_zalloc(sizeof(struct xxx_gauge_dev_s));
  if (priv == NULL)
    {
      return NULL;
    }

  priv->dev.ops      = &g_xxx_gauge_ops;
  priv->i2c          = i2c;
  priv->addr         = addr;
  priv->freq         = freq;
  priv->profile      = profile;
  priv->profile_size = profile_size;
  priv->last_soc     = -1;
  priv->last_temp    = -1;

  nxmutex_init(&priv->lock);

  /* 校验芯片 ID */

  ret = xxx_get_chipid(priv, &chipid);
  if (ret < 0 || chipid != XXX_CHIP_ID_EXPECTED)
    {
      baterr("ERROR: Chip ID mismatch: 0x%02x\n", chipid);
      goto err;
    }

  /* 写入 profile（如有） */

  ret = xxx_write_profile(priv);
  if (ret < 0)
    {
      baterr("ERROR: Profile write failed: %d\n", ret);
      goto err;
    }

  priv->online = true;

  /* 启动周期采样 */

  work_queue(LPWORK, &priv->work, xxx_gauge_worker, priv, 0);

  return (FAR struct battery_gauge_dev_s *)priv;

err:
  nxmutex_destroy(&priv->lock);
  kmm_free(priv);
  return NULL;
}

#endif /* CONFIG_BATTERY_GAUGE && CONFIG_I2C */
```

## 5. 初始化序列要点

硬件电量计 IC 的初始化通常包含：

1. **芯片 ID 校验** — 读取 ID 寄存器，确认 IC 型号
2. **Profile 写入**（部分 IC 需要）— 将电池特性参数写入 IC 内部 RAM
   - 进入 sleep/config 模式
   - 逐字节写入 profile 数据
   - 退出到 active 模式
   - 等待 IC 就绪（轮询状态寄存器）
3. **温度阈值配置** — 设置过温/低温保护阈值
4. **启动周期采样** — work_queue 定时读取 SOC/电压/电流/温度

## 6. 与软件电量计的关键区别

| 维度 | 硬件电量计 (本模板) | 软件电量计 (battery_soft_gauge.md) |
|------|-------------------|----------------------------------|
| SOC 算法 | IC 内部（库仑计 + 电压修正） | 软件 SOC-V 查表 |
| 精度 | 高（±2%） | 低（±5-10%） |
| 电流测量 | 有（IC 内置） | 无 |
| 温度补偿 | IC 自动 | 需要多段 SOC-V 表 |
| Profile | 需要写入电池特性参数 | 不需要 |
| 成本 | 高（额外 IC） | 低（仅 ADC） |
| I2C 依赖 | 是 | 否（用 NuttX ADC 设备） |

## 7. 用户需提供的信息

| 信息 | 说明 | 示例 |
|------|------|------|
| IC 型号 | 用于搜索参考驱动和 datasheet | CW2218, BQ27426 |
| I2C 地址 | 7-bit 地址 | 0x64 |
| 芯片 ID 寄存器 | 地址 + 期望值 | REG=0x00, ID=0xA0 |
| 寄存器表 | 电压/SOC/电流/温度/模式/状态寄存器 | 见 reg.h 模板 |
| 数据转换公式 | 寄存器原始值 → 物理量的转换 | `raw * 305 / 1000` (mA) |
| Profile 数据 | 电池特性参数（如有） | 80 bytes binary |
| 采样周期 | 周期读取间隔 | 5000ms |
