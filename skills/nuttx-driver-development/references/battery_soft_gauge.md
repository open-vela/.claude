# Software Battery Fuel Gauge Template

> **适用场景**：无硬件电量计 IC，通过 ADC 采样电压 + SOC-V 查表计算电量百分比。可选 NTC 温度采样和 KVDB 持久化。
> **代表驱动**：vendor `soft_gauge.c`
> **前置依赖**：`battery_ops_reference.md`、`battery_charger_pattern.md`

## Table of Contents

1. [架构](#1-架构)
2. [文件布局](#2-文件布局)
3. [头文件模板 (xxx_soft_gauge.h)](#3-头文件模板-xxx_soft_gaugeh)
4. [驱动模板 (xxx_soft_gauge.c)](#4-驱动模板-xxx_soft_gaugec)
5. [Board 层 SOC-V 表数据示例](#5-board-层-soc-v-表数据示例)
6. [用户需提供的信息](#6-用户需提供的信息)

## 1. 架构

```
ADC 电压通道 (/dev/batt_voltage)
    │
    ▼
周期采样 (work_queue LPWORK)
    │
    ├── 读取电压 → SOC-V 查表 → 计算 SOC
    ├── 读取温度 (NTC, 可选) → 选择温度段 SOC-V 表
    ├── 充电/放电状态 → 调整 SOC 更新策略
    └── KVDB 持久化 SOC (可选)
    │
    ▼
battery_gauge_changed() → Upper-Half → poll() 通知应用
```

## 2. 文件布局

```
xxx_soft_gauge.c    # Gauge Lower Half (8 ops)
xxx_soft_gauge.h    # 常量定义、SOC-V 表结构
board_xxx_gauge.c   # Board 层注册 + SOC-V 表数据
Kconfig
Make.defs
```

## 3. 头文件模板 (xxx_soft_gauge.h)

```c
#ifndef __DRIVERS_POWER_XXX_SOFT_GAUGE_H
#define __DRIVERS_POWER_XXX_SOFT_GAUGE_H

#include <nuttx/config.h>
#include <stdint.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define XXX_GAUGE_DEFAULT_PERIOD_MS     210000  /* 默认采样周期 3.5min */
#define XXX_GAUGE_LOW_SOC_THRESHOLD     10      /* 低电量阈值 */
#define XXX_GAUGE_FULL_HOLD_CYCLES      3       /* 满电保持周期数 */

/* ADC 设备路径 */

#define XXX_DEVPATH_BAT_VOLTAGE         "/dev/batt_voltage"
#define XXX_DEVPATH_SKIN_TEMP           "/dev/temp_skin"  /* 可选 */

/* 充电器插入时的电压补偿 (uV) */

#define XXX_CUT_POWER_OFFSET_UV         50000

/* ADC 坏值标记（与 ADC 驱动约定，见 ADC upper-half 文档） */

#define XXX_ADC_BAD_VALUE               0xFFFF

/****************************************************************************
 * Public Types
 ****************************************************************************/

/* 插拔状态 */

enum xxx_plug_state_e
{
  XXX_PLUG_OUT = 0,
  XXX_PLUG_IN,
  XXX_PLUG_UNKNOWN,
};

/* 单段 SOC-V 表（按温度分段） */

struct xxx_soc_v_table_s
{
  int temp_min;                    /* 温度下限 (0.1°C) */
  int temp_max;                    /* 温度上限 (0.1°C) */
  int count;                       /* 表项数 (= SOC 百分比数) */
  FAR const uint32_t *soc_v_table; /* 电压数组 (uV)，索引 0=100%，末尾=0% */
};

/* SOC-V 表列表（多温度段） */

struct xxx_soc_v_list_s
{
  int count;                                /* 温度段数 */
  FAR struct xxx_soc_v_table_s *tables;     /* 各段表 */
};

/* NTC 温度配置（可选） */

struct xxx_ntc_config_s
{
  int count;                       /* NTC 查表大小 */
  FAR const float *ntc_table;      /* 电阻→温度对照表 */
};

#endif /* __DRIVERS_POWER_XXX_SOFT_GAUGE_H */
```

## 4. 驱动模板 (xxx_soft_gauge.c)

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
#include <fcntl.h>
#include <fixedmath.h>
#include <time.h>

#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/wqueue.h>
#include <nuttx/analog/ioctl.h>
#include <nuttx/power/battery_gauge.h>
#include <nuttx/power/battery_ioctl.h>

#ifdef CONFIG_KVDB
#  include <kvdb.h>
#endif

#ifdef CONFIG_XXX_USE_DEVENTBUS
#  include <nuttx/deventbus/deventbus.h>
#endif

#include "xxx_soft_gauge.h"

#if defined(CONFIG_BATTERY_GAUGE)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct xxx_gauge_dev_s
{
  struct battery_gauge_dev_s dev;          /* 必须是首成员 */
  FAR struct xxx_soc_v_list_s *soc_list;  /* SOC-V 表 */
  FAR struct xxx_ntc_config_s *ntc_cfg;   /* NTC 配置（可选） */
  struct work_s work;                      /* 周期采样工作 */
  mutex_t lock;

  int soc;                                 /* 当前 SOC (0-100) */
  int soc_read;                            /* 上次上报的 SOC */
  int batt_voltage;                        /* 当前电压 (uV) */
  int batt_temp;                           /* 当前温度 (0.1°C) */
  int last_cap;                            /* 上次上报的 SOC */
  int last_batt_temp;                      /* 上次上报的温度 */

  uint8_t charge_status;                   /* BATTERY_CHARGING 等 */
  int plugin_state;                        /* xxx_plug_state_e */
  uint64_t period_time;                    /* 当前采样周期 (ms) */
  uint64_t set_period_time;                /* 定时器 tick */
  bool soc_init_flag;                      /* SOC 是否已初始化 */
  bool init_flag;                          /* 首次采样标记 */
  uint8_t full_hold_cnt;                   /* 满电保持计数 */

#ifdef CONFIG_XXX_USE_DEVENTBUS
  struct deventbus_notifier plugin_notifier;
#endif
};

/****************************************************************************
 * ADC 电压读取
 ****************************************************************************/

static int xxx_read_voltage(FAR int *volt_uv)
{
  struct file fd;
  int16_t data = 0;
  int ret;

  ret = file_open(&fd, XXX_DEVPATH_BAT_VOLTAGE,
                  O_RDONLY | O_NONBLOCK);
  if (ret < 0)
    {
      baterr("ERROR: open %s failed\n", XXX_DEVPATH_BAT_VOLTAGE);
      return ret;
    }

  if (file_ioctl(&fd, ANIOC_TRIGGER, 0) == 0)
    {
      ret = file_read(&fd, &data, sizeof(data));
      if (ret < 0)
        {
          file_close(&fd);
          return ret;
        }

      /* ADC 坏值检测：驱动返回 XXX_ADC_BAD_VALUE 表示本次采样无效，
       * 直接返回 -EIO，调用方负责保留上一次有效读数，禁止据此计算 SOC。
       */

      if ((uint16_t)data == XXX_ADC_BAD_VALUE)
        {
          file_close(&fd);
          baterr("ERROR: ADC returned bad value\n");
          return -EIO;
        }

      /* TODO: 缩放因子根据硬件调整 */

      *volt_uv = (int)data * 4900;  /* 示例：ADC 值 × 4.9 × 1000 */
    }

  file_close(&fd);
  return OK;
}

/****************************************************************************
 * SOC-V 查表
 ****************************************************************************/

static FAR const uint32_t *xxx_get_soc_v_table(
  FAR struct xxx_gauge_dev_s *priv, FAR int *count)
{
  int i;

  /* 无温度采样时 batt_temp 始终为 0，直接走 fallback。
   * 有温度采样时按温度段匹配。
   */

  if (priv->ntc_cfg != NULL)
    {
      for (i = 0; i < priv->soc_list->count; i++)
        {
          FAR struct xxx_soc_v_table_s *t = &priv->soc_list->tables[i];

          if (priv->batt_temp >= t->temp_min &&
              priv->batt_temp < t->temp_max)
            {
              *count = t->count;
              return t->soc_v_table;
            }
        }
    }

  /* 无温度采样 或 无匹配温度段：使用第一段（常温表） */

  *count = priv->soc_list->tables[0].count;
  return priv->soc_list->tables[0].soc_v_table;
}

static int xxx_find_soc(FAR struct xxx_gauge_dev_s *priv)
{
  FAR const uint32_t *table;
  int count;
  int i;

  table = xxx_get_soc_v_table(priv, &count);
  if (table == NULL)
    {
      return 0;
    }

  /* table[0] = 100% 电压, table[count-1] = 0% 电压 */

  if ((uint32_t)priv->batt_voltage >= table[0])
    {
      return 100;
    }

  if ((uint32_t)priv->batt_voltage <= table[count - 1])
    {
      return 0;
    }

  for (i = 1; i < count; i++)
    {
      if ((uint32_t)priv->batt_voltage > table[i] &&
          (uint32_t)priv->batt_voltage <= table[i - 1])
        {
          return count - i;
        }
    }

  return 0;
}

/****************************************************************************
 * SOC 更新策略
 ****************************************************************************/

static void xxx_update_soc(FAR struct xxx_gauge_dev_s *priv)
{
  int soc_from_table;
  int ret;

  /* ADC 读取失败（含坏值）→ 保留上一次 SOC，跳过本轮更新，
   * 避免把无效电压代入查表导致 SOC 跳变。
   */

  ret = xxx_read_voltage(&priv->batt_voltage);
  if (ret < 0)
    {
      batwarn("gauge: skip SOC update (ADC read failed: %d)\n", ret);
      priv->period_time = XXX_GAUGE_DEFAULT_PERIOD_MS;
      priv->set_period_time = MSEC2TICK(priv->period_time);
      return;
    }

  soc_from_table = xxx_find_soc(priv);

  if (priv->charge_status == BATTERY_CHARGING ||
      priv->charge_status == BATTERY_FULL)
    {
      /* 充电中：SOC 只升不降 */

      priv->full_hold_cnt = 0;
      if (priv->soc < 100)
        {
          if (soc_from_table > priv->soc)
            {
              priv->soc++;
            }
        }
    }
  else
    {
      /* 放电中：SOC 只降不升 */

      if (soc_from_table < priv->soc && priv->soc > 0)
        {
          if (priv->soc == 100)
            {
              priv->full_hold_cnt++;
            }

          if (priv->soc != 100 ||
              priv->full_hold_cnt > XXX_GAUGE_FULL_HOLD_CYCLES)
            {
              priv->full_hold_cnt = 0;
              priv->soc--;
            }
        }
    }

  /* 钳位 */

  if (priv->soc < 0)
    {
      priv->soc = 0;
    }

  if (priv->soc > 100)
    {
      priv->soc = 100;
    }

  priv->period_time = XXX_GAUGE_DEFAULT_PERIOD_MS;
  priv->set_period_time = MSEC2TICK(priv->period_time);
}

/****************************************************************************
 * 周期采样 Worker
 *
 * 锁与上报配合规则：
 *   battery_gauge_changed() 内部会获取 upper-half 的 batlock，
 *   如果在持有 priv->lock 的情况下调用，可能导致 AB-BA 死锁
 *   （upper-half ioctl 路径：batlock → ops → priv->lock）。
 *   因此：先在锁内完成数据更新，unlock 后再调用 changed()。
 ****************************************************************************/

static void xxx_gauge_worker(FAR void *arg)
{
  FAR struct xxx_gauge_dev_s *priv = arg;
  bool soc_changed = false;

  nxmutex_lock(&priv->lock);

  if (priv->init_flag)
    {
      /* 首次：从电压计算初始 SOC */

      priv->init_flag = false;
      if (xxx_read_voltage(&priv->batt_voltage) < 0)
        {
          /* 首次采样失败：延后 init，下一轮重试。保持 init_flag = true 以便重试。 */

          priv->init_flag = true;
          goto out_unlock;
        }

      priv->soc = xxx_find_soc(priv);

#ifdef CONFIG_KVDB
      /* 有 KVDB：尝试从持久化值恢复，与实时值差距过大时用实时值 */

      int kv_soc = property_get_int32("persist.charger.soc", -1);
      if (kv_soc >= 0 && kv_soc <= 100)
        {
          if (abs(kv_soc - priv->soc) <= 20)
            {
              priv->soc = kv_soc;
            }
        }
#else
      /* 无 KVDB：直接使用电压查表值作为初始 SOC。
       * 注意：重启后 SOC 可能跳变（充电中重启会从高电压算出高 SOC，
       * 但实际可能因充电器在线导致电压偏高）。
       * 如果 plugin_state 已知为 PLUG_IN，可减去补偿电压后重新计算：
       */

      if (priv->plugin_state == XXX_PLUG_IN)
        {
          priv->batt_voltage -= XXX_CUT_POWER_OFFSET_UV;
          priv->soc = xxx_find_soc(priv);
        }
#endif

      priv->soc_init_flag = true;
    }
  else
    {
      xxx_update_soc(priv);

#ifdef CONFIG_KVDB
      property_set_int32("persist.charger.soc", priv->soc);
#endif
    }

  priv->soc_read = priv->soc;

  /* 检查是否需要上报（在锁内判断，锁外上报） */

  if (priv->soc_read != priv->last_cap)
    {
      priv->last_cap = priv->soc_read;
      soc_changed = true;
    }

out_unlock:
  nxmutex_unlock(&priv->lock);

  /* 锁外调用 changed()，避免与 upper-half batlock 死锁 */

  if (soc_changed)
    {
      battery_gauge_changed(&priv->dev, BATTERY_CAPACITY_CHANGED);
    }

  /* 继续周期采样 */

  work_queue(LPWORK, &priv->work, xxx_gauge_worker,
             priv, priv->set_period_time);
}

/****************************************************************************
 * deventbus 插拔通知（可选）
 ****************************************************************************/

#ifdef CONFIG_XXX_USE_DEVENTBUS
static void xxx_plugin_cb(FAR struct deventbus_notifier *nb,
                           FAR void *data, uint32_t size)
{
  FAR struct xxx_gauge_dev_s *priv =
    container_of(nb, struct xxx_gauge_dev_s, plugin_notifier);
  FAR bool *state = (FAR bool *)data;

  if (data == NULL)
    {
      return;
    }

  priv->plugin_state = *state ? XXX_PLUG_IN : XXX_PLUG_OUT;
}
#endif

/****************************************************************************
 * Gauge Ops 实现 (完整 8 个)
 ****************************************************************************/

static int xxx_gauge_state(FAR struct battery_gauge_dev_s *dev,
                           FAR int *status)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *status = priv->charge_status;
  return OK;
}

static int xxx_gauge_online(FAR struct battery_gauge_dev_s *dev,
                            FAR bool *status)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *status = priv->soc_init_flag;
  return OK;
}

static int xxx_gauge_voltage(FAR struct battery_gauge_dev_s *dev,
                             FAR b16_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  /* batt_voltage 单位 uV，转换为 mV 后转 b16 */

  *value = itob16(priv->batt_voltage / 1000);
  return OK;
}

static int xxx_gauge_capacity(FAR struct battery_gauge_dev_s *dev,
                              FAR b16_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  *value = itob16(priv->soc_read);
  return OK;
}

static int xxx_gauge_current(FAR struct battery_gauge_dev_s *dev,
                             FAR b16_t *value)
{
  return -ENOSYS;  /* 软件电量计无电流测量 */
}

static int xxx_gauge_temp(FAR struct battery_gauge_dev_s *dev,
                          FAR b8_t *value)
{
  FAR struct xxx_gauge_dev_s *priv =
    (FAR struct xxx_gauge_dev_s *)dev;

  /* batt_temp 单位 0.1°C，转换为 b8 */

  *value = itob8(priv->batt_temp / 10);
  return OK;
}

static int xxx_gauge_chipid(FAR struct battery_gauge_dev_s *dev,
                            FAR unsigned int *value)
{
  *value = 0;
  return OK;
}

static int xxx_gauge_operate(FAR struct battery_gauge_dev_s *dev,
                             FAR int *param)
{
  FAR struct batio_operate_msg_s *msg =
    (FAR struct batio_operate_msg_s *)param;

  switch (msg->operate_type)
    {
      case BATIO_OPRTN_CAPACITY:
      case BATIO_OPRTN_CYCLE_COUNT:
        break;
      default:
        return -ENOSYS;
    }

  return OK;
}

static const struct battery_gauge_operations_s g_xxx_gauge_ops =
{
  xxx_gauge_state,
  xxx_gauge_online,
  xxx_gauge_voltage,
  xxx_gauge_capacity,
  xxx_gauge_current,
  xxx_gauge_temp,
  xxx_gauge_chipid,
  xxx_gauge_operate,
};

/****************************************************************************
 * Public Functions
 ****************************************************************************/

FAR struct battery_gauge_dev_s *
xxx_soft_gauge_initialize(FAR struct xxx_soc_v_list_s *soc_list,
                          FAR struct xxx_ntc_config_s *ntc_cfg)
{
  FAR struct xxx_gauge_dev_s *priv;

  priv = kmm_zalloc(sizeof(struct xxx_gauge_dev_s));
  if (priv == NULL)
    {
      baterr("ERROR: Failed to allocate gauge\n");
      return NULL;
    }

  priv->dev.ops      = &g_xxx_gauge_ops;
  priv->soc_list     = soc_list;
  priv->ntc_cfg      = ntc_cfg;
  priv->init_flag    = true;
  priv->plugin_state = XXX_PLUG_UNKNOWN;
  priv->last_cap     = -1;
  priv->set_period_time = 0;  /* 首次立即执行 */

  nxmutex_init(&priv->lock);

#ifdef CONFIG_XXX_USE_DEVENTBUS
  priv->plugin_notifier.msg_type = DEVENTBUS_AP_PLUGIN_STATE_MSG;
  priv->plugin_notifier.notifier_call = xxx_plugin_cb;
  deventbus_register(&priv->plugin_notifier);
#endif

  /* 启动首次采样 */

  work_queue(LPWORK, &priv->work, xxx_gauge_worker, priv, 0);

  return (FAR struct battery_gauge_dev_s *)priv;
}

#endif /* CONFIG_BATTERY_GAUGE */
```

## 5. Board 层 SOC-V 表数据示例

```c
/* 25°C 常温 SOC-V 表 (uV)，索引 0=100%，索引 100=0% */

static const uint32_t g_soc_v_25c[] =
{
  4200000, 4180000, 4160000, /* ... 100 个值 ... */ 3200000
};

static struct xxx_soc_v_table_s g_soc_tables[] =
{
  { .temp_min = -200, .temp_max = 100,
    .count = 101, .soc_v_table = g_soc_v_low_temp },
  { .temp_min = 100,  .temp_max = 450,
    .count = 101, .soc_v_table = g_soc_v_25c },
  { .temp_min = 450,  .temp_max = 600,
    .count = 101, .soc_v_table = g_soc_v_high_temp },
};

static struct xxx_soc_v_list_s g_soc_list =
{
  .count  = 3,
  .tables = g_soc_tables,
};
```

## 6. 用户需提供的信息

| 信息 | 说明 | 示例 |
|------|------|------|
| ADC 电压设备路径 | NuttX ADC 驱动注册的路径 | `/dev/batt_voltage` |
| ADC 缩放因子 | raw → uV 的转换公式 | `data * 4900` |
| SOC-V 表 | 按温度分段，每段 101 个电压值 | 见上方示例 |
| 温度采样（可选） | NTC 设备路径 | `/dev/temp_skin` |
| SOC 持久化（可选） | KVDB key 名 | `persist.charger.soc` |
| 充电器状态来源 | deventbus 消息类型 | `DEVENTBUS_AP_PLUGIN_STATE_MSG` |
