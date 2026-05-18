# Ethernet Network Driver Pattern — openvela 以太网驱动框架

本文档是 net (Ethernet) 驱动子系统的完整参考，由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

> 本文档基于多个生产级驱动总结，覆盖 SPI-Ethernet 外挂控制器和片上 MAC 控制器两种硬件拓扑。

> **适用范围**：仅适用于有线以太网驱动（SPI-Ethernet 外挂控制器、片上 MAC 控制器）。WiFi、蓝牙、LoRa 等无线网络设备走不同的驱动框架，不适用本文档。

## 一、框架概述

openvela 内置了一套轻量级的 TCP/IP 网络协议栈，并提供了一套网络驱动框架。通过该框架，内置的 TCP/IP 协议栈可以与芯片驱动交互，实现网络数据包的收发。

网络驱动架构中，openvela 提供了通用的**上半部分实现（Upper Half）**，厂商只需实现驱动的**下半部分（Lower Half）**，即可完成驱动适配工作。

### 上下半区驱动模型

```
Application (socket / send / recv / ioctl)
    │
    ▼
TCP/IP 协议栈 (NuttX net)
    │
    ▼
Upper-half: netdev framework ← 通用层，处理协议栈与驱动交互
    │  ifup / ifdown / transmit / receive
    ▼
Lower-half: YOUR driver ← 实现 netdev_ops_s 回调
    │
    ▼
Hardware: MAC / PHY / SPI-Ethernet Controller
```

### 硬件拓扑分类

| 拓扑 | 总线 | 典型芯片 | 特点 |
|------|------|---------|------|
| **SPI-Ethernet** | SPI | ENC28J60, W5500, LAN9250 | MAC+PHY 集成在外部芯片，通过 SPI 总线访问寄存器和收发数据 |
| **片上 MAC** | 寄存器直接访问 | 各 SoC 内置 MAC 控制器 | MAC 控制器集成在 SoC 内部，通过 HSPHY/SerDes/RGMII 连接外部 PHY |

片上 MAC 拓扑的典型硬件架构：

```
┌─────────────────────────────────────────────────┐
│                    SoC                          │
│                                                 │
│  CPU Core 0/1/...                               │
│      │                                          │
│      ▼                                          │
│  MAC Controller ──── DMA Engine                 │
│      │  (多通道 TX/RX)     (Scatter-Gather)     │
│      │                                          │
│      ▼                                          │
│  HSPHY (High-Speed PHY) ── SerDes               │
│      │                                          │
│      │  RGMII / SGMII                           │
└──────┼──────────────────────────────────────────┘
       │
       ▼
  External PHY
       │
       ▼
  RJ45 / Ethernet Switch
```

**上半区 (Upper Half) 职责**:
- 管理网络设备注册与生命周期
- 协调协议栈与驱动之间的数据包收发
- 管理 TX/RX quota（缓冲区配额）
- 处理 carrier on/off 链路状态通知

**下半区 (Lower Half) 职责**:
- 实现 `netdev_ops_s` 操作集
- 硬件初始化：MAC 控制器、HSPHY、外部 PHY、MDIO 配置
- 数据包发送（transmit）和接收（receive），支持多通道 DMA
- 中断处理：txdone / rxready 通知上层
- 链路状态管理：carrier on/off，支持重试机制
- 可选：PTP 时间戳、QBV 时间感知调度、ethtool 通道管理

### 实现前设计决策（AI 必须询问）

在开始编码前，AI 必须向用户确认以下两项关键架构决策。这些选择直接影响驱动的整体结构、复杂度和性能，无法在实现途中轻易切换。

#### 决策 1：缓冲区管理策略 — 统一 Zero-copy

片上 MAC 统一使用 Zero-copy 模式，通过 `netpkt_getdata` 直接获取 buffer 地址，经 VA→PA 转换后填入 DMA 描述符，避免数据拷贝。SPI-Ethernet 是例外，因 SPI 总线要求连续 buffer，需 copyin/copyout（详见本文档第 5.1/5.3 节）。

```
发送路径：App → 协议栈 → netpkt → getdata → VA→PA → DMA 描述符 → wire
接收路径：wire → DMA 直写 pkt buffer → 协议栈 → App
```

- 零拷贝，适合片上 MAC、千兆以太网等高吞吐场景
- 需要 `up_addrenv_va_to_pa()` 做虚拟地址到物理地址转换
- 必须在 DMA 操作前后执行 cache 维护：`up_clean_dcache` / `up_invalidate_dcache`
- 必须使用 `UP_DSB()` 确保描述符写入对 DMA 可见
- TX pkt 的释放时机：在 TX 完成中断中，确认 DMA 不再访问后才能 `netpkt_free`
- 需要 spinlock 保护描述符 ring 的并发访问
- 接收：在 ifup 时预分配 `netpkt_alloc` 填入 RX ring，DMA 完成后直接返回 pkt

#### 决策 2：中断处理模式

| 方案 | ISR 上下文 | 下半部 | 适用场景 | 延迟 | 安全性 |
|------|-----------|--------|---------|------|--------|
| **A: 纯 ISR** | 直接调用 rxready/txdone | 无 | 极简驱动、硬实时 | 最低 | 需确保上层回调中断安全 |
| **B: ISR + Work Queue** | 仅禁中断 | HPWORK 调度 worker | 通用推荐 | 中 | 安全，worker 在线程上下文 |
| **C: irq_attach_wqueue** | 可选 ISR 上半部 | 框架自动调度线程处理 | 需要 mutex/SPI 访问的驱动 | 中-高 | 最安全，线程上下文 |
| **D: 纯轮询** | 无中断 | LPWORK 定期轮询 | 调试用、无中断线的设备 | 高 | 安全，但 CPU 占用高 |

**各方案的代码结构对比**：

```c
/* 方案 A: 纯 ISR — 中断中直接通知上层 */
static int mydrv_isr(int irq, void *ctx, void *arg)
{
  netdev_lower_rxready(dev);   /* ⚠️ 需确认上层中断安全 */
  netdev_lower_txdone(dev);
  return OK;
}

/* 方案 B: ISR + Work Queue — 中断仅禁中断 + 调度 */
static int mydrv_isr(int irq, void *ctx, void *arg)
{
  disable_irq(priv);
  work_queue(HPWORK, &priv->irqwork, mydrv_worker, priv, 0);
  return OK;
}
static void mydrv_worker(void *arg)
{
  netdev_lower_rxready(dev);
  netdev_lower_txdone(dev);
  enable_irq(priv);
}

/* 方案 C: irq_attach_wqueue — 框架管理线程调度 */
static int mydrv_isr(int irq, void *ctx, void *arg)
{
  return IRQ_WAKE_THREAD;
}
static int mydrv_thread(int irq, void *ctx, void *arg)
{
  netdev_lower_rxready(dev);
  return OK;
}

/* 方案 D: 纯轮询 — 无中断 */
static void mydrv_poll_worker(void *arg)
{
  if (hw_has_rx_data(priv))
    netdev_lower_rxready(dev);
  if (hw_tx_complete(priv))
    netdev_lower_txdone(dev);
  work_queue(LPWORK, &priv->pollwork, mydrv_poll_worker, priv,
             MSEC2TICK(1));
}
```

> **AI 询问模板**：中断处理模式需要确认：A/B/C/D？建议片上 MAC 多通道用 B 或 C，SPI-Ethernet 用 C，PCI 设备用 B。

**选择方案 B 的实现要点**：
- ISR 中必须先禁用硬件中断，防止 work queue 尚未执行时中断再次触发
- Worker 完成处理后重新使能中断
- HPWORK 用于收发路径（低延迟），LPWORK 用于链路检测等非紧急任务

**选择方案 C 的实现要点**：
- ISR 上半部返回 `IRQ_WAKE_THREAD` 才会唤醒线程处理函数
- 返回 `OK` 或 `IRQ_HANDLED` 则不唤醒线程
- ISR 上半部可以传 NULL，此时中断直接唤醒线程
- 需要配置 `CONFIG_<CHIP>_ENET_ISR_WQUEUE` 和优先级

## 二、开发准备

### Kconfig 配置

```kconfig
# 网络协议栈配置
CONFIG_NET=y
CONFIG_NET_TCP=y
CONFIG_NET_UDP=y
CONFIG_NET_ICMP=y
CONFIG_NET_IPv4=y
CONFIG_NET_ETHERNET=y

# 驱动相关
CONFIG_NETDEVICES=y
CONFIG_NETDEV_IOCTL=y

# 调试日志
CONFIG_DEBUG_NET=y
CONFIG_DEBUG_NET_ERROR=y
# CONFIG_DEBUG_NET_WARN=y
# CONFIG_DEBUG_NET_INFO=y
```

日志宏：`nerr()` / `nwarn()` / `ninfo()`

#### 驱动自身 Kconfig 条目

**SPI-Ethernet 控制器示例：**

```kconfig
config NET_MYETHDEV
	bool "MyVendor Ethernet Controller support"
	default n
	select NETDEVICES
	---help---
		Enable driver for the MyVendor Ethernet Controller.
```

**片上 MAC 控制器示例：**

```kconfig
config <CHIP>_ENET
	bool "<CHIP> ENET"
	depends on NET && NETDEVICES
	default n

if <CHIP>_ENET

config <CHIP>_ENET_ISR_WQUEUE
	bool "ENET ISR WQUEUE"
	default n

config <CHIP>_ENET_PTP
	bool "<CHIP> ENET PTP"
	select ARCH_HAVE_NETDEV_TIMESTAMP
	default n

config <CHIP>_ENET_QBV
	bool "<CHIP> ENET QBV"
	default n

config <CHIP>_ENET_USE_PHY
	bool "<CHIP> ENET Use Phy"
	default n

config <CHIP>_ENET_MAC_ADDR
	hex "<CHIP> ENET MAC Address"
	default 0x020400000413

config <CHIP>_ENET_NTX0DESC
	int "TX Descriptor Count"
	default 4

config <CHIP>_ENET_NRX0DESC
	int "RX Descriptor Count"
	default 4

config <CHIP>_ENET_CSUM_OL
	bool "IPv4/6 TCP/UDP/ICMP checksum offload"
	default y

endif # <CHIP>_ENET
```

### 文件布局

**SPI-Ethernet（放在 `drivers/net/`）：**

```
nuttx/
├── drivers/net/
│   ├── myethdev.c            # 驱动实现
│   ├── Make.defs             # CSRCS += myethdev.c
│   ├── CMakeLists.txt        # list(APPEND SRCS myethdev.c)
│   └── Kconfig               # CONFIG_NET_MYETHDEV
├── include/nuttx/net/
│   └── myethdev.h            # 公共头文件：注册函数原型
└── boards/<arch>/<chip>/<board>/src/
    └── <board>_myethdev.c    # 板级初始化
```

**片上 MAC（放在 `vendor/<vendor>/chips/<chip>/`）：**

```
vendor/<vendor>/chips/<chip>/
├── <chip>_enet.c             # 驱动实现
├── <chip>_enet.h             # 公共头文件（含 config 结构和注册函数）
├── Kconfig                   # CONFIG_<CHIP>_ENET 及子选项
├── Make.defs                 # 条件编译
└── CMakeLists.txt

vendor/<vendor>/boards/<chip>/<board>/src/
└── <board>.c                 # 板级配置表和初始化调用
```

## 三、关键数据结构

### 3.1 netdev_ops_s — 驱动操作集

头文件：`include/nuttx/net/netdev_lowerhalf.h`

```c
struct netdev_ops_s
{
  CODE int (*ifup)(FAR struct netdev_lowerhalf_s *dev);
  CODE int (*ifdown)(FAR struct netdev_lowerhalf_s *dev);
  CODE int (*transmit)(FAR struct netdev_lowerhalf_s *dev,
                       FAR netpkt_t *pkt);
  CODE FAR netpkt_t *(*receive)(FAR struct netdev_lowerhalf_s *dev);
  CODE int (*addmac)(FAR struct netdev_lowerhalf_s *dev,
                     FAR const uint8_t *mac);
  CODE int (*rmmac)(FAR struct netdev_lowerhalf_s *dev,
                    FAR const uint8_t *mac);
  CODE int (*ioctl)(FAR struct netdev_lowerhalf_s *dev,
                    int cmd, unsigned long arg);
  CODE void (*reclaim)(FAR struct netdev_lowerhalf_s *dev);
};
```

| 回调 | 必需 | 说明 |
|------|------|------|
| `ifup` | Yes | 启动网络设备，配置 MAC/PHY，使能中断 |
| `ifdown` | Yes | 关闭网络设备，禁用中断，释放资源 |
| `transmit` | Yes | 发送数据包，驱动返回发送结果 |
| `receive` | Yes | 从驱动获取接收到的数据包 |
| `addmac` / `rmmac` | No | 组播 MAC 地址过滤 |
| `ioctl` | No | 其他控制命令（MDIO 读写、QBV 配置等） |
| `reclaim` | No | TX Quota 耗尽时上层调用，用于轮询模式资源回收 |

### 3.2 netdev_lowerhalf_s — 网络设备下半区结构

```c
struct netdev_lowerhalf_s
{
  FAR const struct netdev_ops_s *ops;       /* 驱动操作集 (必需) */
  atomic_int quota[NETPKT_TYPENUM];         /* 驱动可持有的最大 buffer 数 */
  struct net_driver_s netdev;               /* 内嵌的网络设备结构 */
#ifdef CONFIG_NETDEV_ETHTOOL_IOCTL
  FAR const struct ethtool_ops_s *eth_ops;  /* ethtool 操作集 (可选) */
#endif
  int rxtype;                               /* 接收模式 */
};
```

**quota 说明**：
- `quota[NETPKT_TX]`：TX 配额用完时上层不再调用 transmit
- `quota[NETPKT_RX]`：RX 配额用完后 `netpkt_alloc` 会失败
- 多描述符 DMA：设为描述符数量（如 `CONFIG_<CHIP>_ENET_NTX0DESC = 4`）
- 逐包处理：设为 1

### 3.3 设备私有结构定义

**SPI-Ethernet 简单模式：**

```c
struct <chip>_priv_s
{
  struct netdev_lowerhalf_s dev;  /* 必须第一个成员 */
  FAR struct spi_dev_s *spi;
  int irq;
  bool link_up;
  bool bifup;
  struct work_s irqwork;
  struct work_s link_work;
};
```

**片上 MAC 高级模式：**

```c
struct <chip>_enet_dev_s
{
  struct netdev_lowerhalf_s           dev;          /* 必须第一个成员 */
  struct <chip>_enet_module_config_s  config_d;
  bool                                ifup;
  bool                                mac_ready;
  const struct <chip>_enet_config_s  *config;       /* 板级配置指针 */
  struct work_s                       init_work;
  unsigned int                        link_retry_count;
  unsigned int                        link_max_retries;
  bool                                channel[NUM_RX_QUEUES];
  netpkt_queue_t                      tx_pending[MAX_TX_DESCRIPTORS];

  /* RX ring 管理（Zero-copy 模式必需） */
  volatile RxDescr                   *rx_ring[NUM_RX_CHANNELS];
  FAR netpkt_t                       *rx_pkt[NUM_RX_CHANNELS][MAX_RX_DESCRIPTORS];
  int                                 rx_idx[NUM_RX_CHANNELS];

#ifdef CONFIG_<CHIP>_ENET_PTP
  bool                                ptp_flag;
  void                               *ptp_pkt;
  mutex_t                             ptp_lock;
  struct work_s                       ptp_timeout_work;
#endif
};
```

### 3.4 板级配置结构（片上 MAC）

分为必填和可选两组字段：

```c
struct <chip>_enet_config_s
{
  /* --- 必填 --- */
  uint8_t  core_id;                      /* CPU 核心 ID */
  uint8_t  port;                         /* MAC 端口号 */
  uint8_t  nchans;                       /* 通道数 */
  bool     main_core;                    /* 是否主核 */
  int      port_rate;                    /* 端口速率 */
  bool     enable_queues[NUM_RX_QUEUES]; /* 各队列使能 */
  int      rx_irq[NUM_RX_CHANNELS];     /* RX 中断号 */
  int      tx_irq[NUM_TX_CHANNELS];     /* TX 中断号 */

  /* --- 可选（按需启用） --- */
  int      phy_interface_mode;           /* RGMII / SGMII / RMII */
  int      hsphy_speed;
  bool     use_phy;
  uint8_t  phy_addr;
  void    *phy_reset_port;
  uint8_t  phy_reset_pin;
  bool     enable_ptp;
  bool     enable_pps;
  uint32_t ptp_freq;
  bool     enable_qbv;
  void    *qbv_cfg;
  uint16_t sys_irq;
  uint8_t  (*rx_buffer)[8][1528];
  uint8_t  channel_cpu_map[8];
  bool     configure_mdio_pins;
  void    *mdio_pins;
  const void *hsphy_cfg;
};
```

### 3.5 片上 MAC 驱动常见类型陷阱

#### 3.5.1 DMA 描述符 Read 格式 vs Write-back 格式

大多数现代 MAC IP（Synopsys DWC XGMAC、Cadence GEM、TI CPSW 等）的 DMA 描述符使用同一块内存，但在 **驱动写入（Read 格式）** 和 **DMA 回写（Write-back 格式）** 时，字段含义完全不同。

```
          ┌─────── 同一块 32-bit 内存 ───────┐
   Read 格式（驱动 → DMA）          Write-back 格式（DMA → 驱动）
   ├── IOC (Interrupt on Completion)   ├── PL  (Packet Length, 14 bit)
   ├── OWN (Ownership)                ├── CDA (Context Descriptor Available)
   └── (无 PL 字段)                   ├── ES  (Error Summary)
                                       └── OWN
```

```c
/* ❌ 错误：Read 格式没有 PL */
int pkt_size = descr->RDES3.R.PL;

/* ✅ 正确：使用 Write-back 格式 */
int pkt_size = descr->RDES3.W.PL;
```

#### 3.5.2 vendor HAL 类型陷阱：union / boolean / bitfield 混淆

```c
/* ❌ 名字像 boolean，实际是 union */
mdio_cfg.clause22 = TRUE;
/* ✅ */ mdio_cfg.clause22.raw = (1u << port);

/* ❌ 名字像 enum，实际是 boolean */
ts_cfg.updateMethod = TIMESTAMP_UPDATE_METHOD_FINE;
/* ✅ */ ts_cfg.updateMethod = TRUE;

/* ❌ 名字像 enum，实际是 bitfield */
ts_cfg.snapshotType = SNAPSHOT_TYPE_MASTER;
/* ✅ */ ts_cfg.snapshotType = 1;
```

| 你猜的名字 | 实际字段名 | 规律 |
|-----------|-----------|------|
| `enablePtpOverIpv4` | `enablePtpOverIpv4Udp` | 全称更长 |
| `subSeconds` | `subSecondIncrementValue` | 全称更长 |
| `fixedModePPSOutput` | 不存在 | 拆成多个字段组合 |

**通用验证方法**：写驱动前先用 `grep -rn 'fieldName' vendor_hal_headers/` 确认字段名和类型。

#### 3.5.3 NuttX 与 Linux 接口差异

| 概念 | Linux | NuttX |
|------|-------|-------|
| 通道管理 | `ethtool_channels` 用 `combined_count` (整数) | `ethtool_chns2` 用 `combined_chns_map` (bitmap) |
| IOB 队列初始化 | `skb_queue_head_init()` | **不存在** `iob_queue_init()`，`kmm_zalloc` 零初始化即可 |
| 延时函数 | `usleep_range()` | `nxsig_usleep()` (需 `#include <nuttx/signal.h>`) |

#### 3.5.4 MAC 地址来源选择

```c
#ifdef CONFIG_<CHIP>_ENET_MAC_ADDR
  uint64_t mac = CONFIG_<CHIP>_ENET_MAC_ADDR;
  for (int i = 0; i < 6; i++)
    dev->d_mac.ether.ether_addr_octet[i] = (mac >> (40 - 8 * i)) & 0xff;
#else
  uint32_t uid = read_chip_uid();
  dev->d_mac.ether.ether_addr_octet[0] = 0x02;  /* locally administered */
  memcpy(&dev->d_mac.ether.ether_addr_octet[2], &uid, 4);
#endif
```

#### 3.5.5 netpkt_queue_t 初始化

NuttX 内核**不提供** `iob_queue_init()` 函数。

```c
/* ❌ 此函数不存在 */
iob_queue_init(&priv->tx_pending[ch]);

/* ✅ kmm_zalloc 已清零，或显式 memset */
priv = kmm_zalloc(sizeof(*priv));
```

#### 3.5.6 vendor HAL API 签名验证方法论

```bash
# 编码前必做
grep -B2 -A8 'functionName\b' vendor_headers/*.h   # 确认函数签名
grep -rn 'EnumPrefix_' vendor_headers/*.h           # 确认枚举成员
grep -B2 -A30 'typedef struct.*ConfigStruct' vendor_headers/*.h  # 确认字段
```

| 编译器报错 | 原因 |
|-----------|------|
| `too many/few arguments` | 函数签名版本不匹配 |
| `has no member named 'xxx'` | 字段在此 HAL 版本中改名或不存在 |
| `incompatible type` | 字段是 union/bitfield，不是预期的 boolean/enum |

**核心原则**：始终以实际头文件定义为准，不要依赖 vendor 文档或示例代码推测。

## 四、核心 API

### 4.1 设备注册与注销

```c
int netdev_lower_register(FAR struct netdev_lowerhalf_s *dev,
                          enum net_lltype_e lltype);  /* 传 NET_LL_ETHERNET */
int netdev_lower_unregister(FAR struct netdev_lowerhalf_s *dev);
```

### 4.2 链路状态通知

```c
void netdev_lower_carrier_on(FAR struct netdev_lowerhalf_s *dev);
void netdev_lower_carrier_off(FAR struct netdev_lowerhalf_s *dev);
```

### 4.3 收发完成通知

```c
void netdev_lower_rxready(FAR struct netdev_lowerhalf_s *dev);
void netdev_lower_txdone(FAR struct netdev_lowerhalf_s *dev);
```

### 4.4 NetPKT Buffer 接口

```c
/* 分配 / 释放 */
FAR netpkt_t *netpkt_alloc(FAR struct netdev_lowerhalf_s *dev,
                           enum netpkt_type_e type);
void netpkt_free(FAR struct netdev_lowerhalf_s *dev,
                 FAR netpkt_t *pkt, enum netpkt_type_e type);

/* 直接获取 buffer 地址（零拷贝） */
FAR uint8_t *netpkt_getdata(dev, pkt);
FAR uint8_t *netpkt_getbase(pkt);

/* 备用：copyin/copyout（分片包或 SPI-Ethernet 场景） */
int netpkt_copyin(dev, pkt, src, len, offset);
int netpkt_copyout(dev, dest, pkt, len, offset);

/* 数据长度 */
void netpkt_setdatalen(dev, pkt, len);
unsigned int netpkt_getdatalen(dev, pkt);

/* 分片检测与 IOB 操作 */
bool netpkt_is_fragmented(pkt);
int iob_count(pkt);
void iob_add_queue(pkt, queue);
FAR netpkt_t *iob_remove_queue(queue);
```

#### Buffer 内存布局

```
TX: [reserved][  tx data (L2 hdr + payload)  ][free]
               ^data     datalen

RX: [reserved][rx head][  rx data  ][free]
                       ^data  datalen
```

#### 分片包遍历

```c
uint32_t descr_num = iob_count(pkt);
netpkt_t *frag = pkt;
for (int i = 0; i < (int)(descr_num - 1); i++)
  {
    frag = frag->io_flink;
    /* 将 frag->io_data 写入下一个 DMA 描述符 */
  }
```

## 五、数据收发模式

### 5.1 发送 — SPI-Ethernet（例外：需要 copyout）

> SPI-Ethernet 是 Zero-copy 的例外场景：SPI 控制器需要在数据前插入私有 txhead，
> 且 SPI 总线要求连续 buffer，因此必须 copyout 到本地 buffer 后再通过 SPI 发送。
> 片上 MAC 走 5.2 的 Zero-copy 路径。

```c
static int <chip>_transmit(FAR struct netdev_lowerhalf_s *dev,
                           FAR netpkt_t *pkt)
{
  FAR struct <chip>_priv_s *priv = (FAR struct <chip>_priv_s *)dev;
  unsigned int len = netpkt_getdatalen(dev, pkt);

  if (netpkt_is_fragmented(pkt))
    {
      uint8_t devbuf[1600];
      netpkt_copyout(dev, devbuf + sizeof(struct <chip>_txhead_s),
                     pkt, len, 0);
      /* 填充 txhead，通过 SPI 发送 devbuf */
    }
  else
    {
      FAR uint8_t *databuf = netpkt_getdata(dev, pkt);
      /* 通过 SPI 发送 databuf */
    }

  return OK;
}
```

**反模式**：

```c
/* ❌ 忘记处理分片包 — 大包数据截断 */
FAR uint8_t *databuf = netpkt_getdata(dev, pkt);  /* 分片时只拿到第一段 */
send_to_hw(databuf, netpkt_getdatalen(dev, pkt));

/* ❌ transmit 中释放 pkt — 上层还需要用 */
netpkt_free(dev, pkt, NETPKT_TX);  /* 不该在这里释放 */
```

### 5.2 发送 — 多通道 DMA 模式（片上 MAC）

```c
static int <chip>_transmit(FAR struct netdev_lowerhalf_s *dev,
                           FAR netpkt_t *pkt)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;
  uint8_t channel = 0;

  if (mac_is_disabled(priv))  /* vendor-specific: check MAC module enable state */
    return -ENETDOWN;

  /* 通道选择：VLAN → PTP → 默认 */
  FAR struct eth_8021qhdr_s *vlan_hdr =
    (FAR struct eth_8021qhdr_s *)netpkt_getdata(dev, pkt);

  if (vlan_hdr->tpid == HTONS(TPID_8021QVLAN))
    channel = (ntohs(vlan_hdr->tci) >> VLAN_PRIO_SHIFT) % NUM_TX_CHANNELS;
  else
    {
      FAR struct eth_hdr_s *eth_hdr = (FAR struct eth_hdr_s *)vlan_hdr;
      channel = (eth_hdr->type == HTONS(ETHERTYPE_PTP))
                ? PTP_CHANNEL : UNTAGGED_CHANNEL;
    }

  if (!priv->channel[channel])
    return -ECHRNG;

  volatile TxDescr *descr = get_actual_tx_descriptor(priv, channel);  /* vendor-specific: get current TX descriptor */
  if (descr->OWN != 0)
    return -EBUSY;

  /* 填充描述符（scatter-gather + zero-copy） */
  FAR uint8_t *va = netpkt_getdata(dev, pkt);
  uintptr_t pa = up_addrenv_va_to_pa(va);
  up_clean_dcache((uintptr_t)va, (uintptr_t)va + pkt->io_len);

  descr->ADDR = (uint32_t)pa;
  descr->LEN = pkt->io_len + NET_LL_HDRLEN(&priv->dev.netdev);

  uint32_t descr_num = iob_count(pkt);
  netpkt_t *frag = pkt;
  for (int i = 0; i < (int)(descr_num - 1); i++)
    {
      frag = frag->io_flink;
      volatile TxDescr *next = get_next_descriptor(priv, channel);  /* vendor-specific */
      while (next->OWN != 0);

      FAR uint8_t *frag_va = &frag->io_data[0];
      up_clean_dcache((uintptr_t)frag_va,
                      (uintptr_t)frag_va + frag->io_len);
      next->ADDR = (uint32_t)up_addrenv_va_to_pa(frag_va);
      next->LEN = frag->io_len + frag->io_offset;
    }

  UP_DSB();  /* 确保描述符写入对 DMA 可见 */
  iob_add_queue(pkt, &priv->tx_pending[channel]);
  start_dma_transmit(priv, channel, descr_num);  /* vendor-specific: kick DMA engine */
  return OK;
}
```

**反模式**：

```c
/* ❌ 忘记将 pkt 加入 pending 队列 — TX 完成中断时无法释放，内存泄漏 */
/* ❌ PTP 包在普通 TX 完成路径释放 — 丢失时间戳，应暂存到 ptp_pkt */
```

#### TX 完成中断处理

```c
static int <chip>_txisr(int irq, void *context, void *arg)
{
  FAR struct <chip>_enet_dev_s *priv = arg;
  int channel = irq - priv->config->tx_irq[0];

  netpkt_t *pkt = iob_remove_queue(&priv->tx_pending[channel]);
  if (pkt != NULL)
    netpkt_free(&priv->dev, pkt, NETPKT_TX);

  return IRQ_WAKE_THREAD;
}

static int <chip>_txhandler(int irq, void *context, void *arg)
{
  FAR struct <chip>_enet_dev_s *priv = arg;
  for (int i = 0; i < NUM_TX_CHANNELS; i++)
    {
      if (tx_interrupt_pending(priv, i))
        {
          clear_tx_interrupt(priv, i);
          if (tx_interrupt_enabled(priv, i))
            { netdev_lower_txdone(&priv->dev); break; }
        }
    }
  return OK;
}
```

### 5.3 接收 — SPI-Ethernet（例外：需要 copyin）

> SPI-Ethernet 的 RX buffer 在硬件侧，必须 copyin 到 netpkt。
> 片上 MAC 走 5.4 的 Zero-copy 路径。

```c
static FAR netpkt_t *<chip>_receive(FAR struct netdev_lowerhalf_s *dev)
{
  FAR netpkt_t *pkt = netpkt_alloc(dev, NETPKT_RX);
  if (pkt == NULL)
    return NULL;  /* ⚠️ 必须返回 NULL */

  uint8_t *data = get_rx_buffer(priv);          /* vendor-specific: get HW RX buffer */
  int pkt_size = get_rx_frame_size(priv);       /* vendor-specific: read frame length */
  if (pkt_size >= 0)
    {
      netpkt_copyin(dev, pkt, data, pkt_size, 0);
      netpkt_setdatalen(dev, pkt, pkt_size);
    }

  free_rx_buffer(priv);  /* vendor-specific: release HW RX buffer */
  return pkt;
}
```

**反模式**：

```c
/* ❌ alloc 失败后 continue — quota 耗尽后死循环 */
if (pkt == NULL) continue;  /* 应该 break */

/* ❌ 忘记 setdatalen — 上层收到长度为 0 的包 */
netpkt_copyin(dev, pkt, data, pkt_size, 0);
/* 缺少 netpkt_setdatalen(dev, pkt, pkt_size); */
```

### 5.4 接收 — 多通道 Zero-copy 模式（片上 MAC）

Zero-copy 接收流程：ifup 时预分配 pkt 填入 RX ring → DMA 直写 pkt buffer →
receive 返回已填充的 pkt → 重新分配新 pkt 补入 ring。

```c
/* ifup 中预分配 RX ring（每个通道每个描述符一个 pkt） */

static void <chip>_rx_ring_init(FAR struct <chip>_enet_dev_s *priv,
                                int channel)
{
  for (int i = 0; i < CONFIG_<CHIP>_ENET_NRX0DESC; i++)
    {
      netpkt_t *pkt = netpkt_alloc(&priv->dev, NETPKT_RX);
      FAR uint8_t *va = netpkt_getdata(&priv->dev, pkt);
      uintptr_t pa = up_addrenv_va_to_pa(va);

      volatile RxDescr *descr = &priv->rx_ring[channel][i];
      descr->RDES0.R.ADDR = (uint32_t)pa;
      descr->RDES3.R.OWN = 1;          /* 交给 DMA */
      descr->RDES3.R.IOC = 1;          /* 完成时中断 */
      priv->rx_pkt[channel][i] = pkt;  /* 记录 pkt 指针 */
    }

  UP_DSB();
}

/* receive 回调：取出 DMA 已写入的 pkt，补入新 pkt */

static FAR netpkt_t *<chip>_receive(FAR struct netdev_lowerhalf_s *dev)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;

  if (mac_is_disabled(priv))  /* vendor-specific */
    return NULL;

  for (int channel = 0; channel < NUM_RX_CHANNELS; channel++)
    {
      if (!priv->channel[channel])
        continue;

      int idx = priv->rx_idx[channel];
      volatile RxDescr *descr = &priv->rx_ring[channel][idx];

      if (descr->RDES3.W.OWN != 0)
        continue;  /* DMA 尚未完成 */

      /* 取出已接收的 pkt */

      netpkt_t *pkt = priv->rx_pkt[channel][idx];
      int pkt_size = descr->RDES3.W.PL;  /* ⚠️ Write-back 格式 */

      FAR uint8_t *va = netpkt_getdata(&priv->dev, pkt);
      up_invalidate_dcache((uintptr_t)va, (uintptr_t)va + pkt_size);
      netpkt_setdatalen(&priv->dev, pkt, pkt_size);

      /* 分配新 pkt 补入 ring */

      netpkt_t *new_pkt = netpkt_alloc(&priv->dev, NETPKT_RX);
      if (new_pkt == NULL)
        break;  /* ⚠️ 必须 break */

      FAR uint8_t *new_va = netpkt_getdata(&priv->dev, new_pkt);
      descr->RDES0.R.ADDR = (uint32_t)up_addrenv_va_to_pa(new_va);
      descr->RDES3.R.OWN = 1;
      descr->RDES3.R.IOC = 1;
      UP_DSB();

      priv->rx_pkt[channel][idx] = new_pkt;
      priv->rx_idx[channel] = (idx + 1) % CONFIG_<CHIP>_ENET_NRX0DESC;

      return pkt;
    }

  return NULL;
}
```

#### RX 中断处理

```c
static int <chip>_rxhandler(int irq, void *context, void *arg)
{
  FAR struct <chip>_enet_dev_s *priv = arg;
  int channel = irq - priv->config->rx_irq[0];

  if (channel < 0 || channel >= NUM_RX_CHANNELS)
    return ERROR;

  clear_rx_interrupt(priv, channel);
  if (rx_interrupt_enabled(priv, channel))
    netdev_lower_rxready(&priv->dev);

  return OK;
}
```

## 六、链路状态管理

### 轮询模式

```c
static void <chip>_link_worker(FAR void *arg)
{
  FAR struct <chip>_priv_s *priv = arg;
  bool link_up = <chip>_phy_read_link_status(priv);  /* vendor-specific: read PHY link register */

  if (link_up && !priv->link_up)
    { priv->link_up = true; netdev_lower_carrier_on(&priv->dev); }
  else if (!link_up && priv->link_up)
    { priv->link_up = false; netdev_lower_carrier_off(&priv->dev); }

  work_queue(LPWORK, &priv->link_work,
             <chip>_link_worker, priv, MSEC2TICK(1000));
}
```

### 异步重试模式（片上 MAC）

```c
static void <chip>_init_worker(FAR void *arg)
{
  FAR struct <chip>_enet_dev_s *priv = arg;

  int ret = <chip>_ethlink_up(priv);
  if (ret < 0)
    {
      priv->link_retry_count++;
      if (priv->link_retry_count < priv->link_max_retries)
        {
          work_queue(LPWORK, &priv->init_work,
                     <chip>_init_worker, priv, MSEC2TICK(100));
          return;
        }
      nerr("Link detection timeout\n");
      priv->link_retry_count = 0;
      return;
    }

  ret = <chip>_serdeslink(priv);
  if (ret < 0)
    {
      priv->link_retry_count++;
      if (priv->link_retry_count < priv->link_max_retries)
        { work_queue(LPWORK, &priv->init_work,
                     <chip>_init_worker, priv, MSEC2TICK(100)); return; }
    }

  netdev_lower_carrier_on(&priv->dev);
}
```

### ifup — 片上 MAC 完整流程（带错误回滚）

```c
static int <chip>_ifup(FAR struct netdev_lowerhalf_s *dev)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;
  int ret;

  if (priv->ifup)
    return OK;  /* 幂等 */

  /* Step 1: 多核同步 */
  if (!priv->config->main_core)
    {
      while (!shared_data->eth_sync_barrier)
        up_udelay(CONFIG_USEC_PER_TICK);
      UP_DMB();
    }

  /* Step 2: 主核硬件初始化（带回滚） */
  if (priv->config->main_core)
    {
      ret = <chip>_hsphy_init(priv);
      if (ret < 0)
        return ret;

      ret = <chip>_mac_init(priv);
      if (ret < 0)
        {
          <chip>_hsphy_deinit(priv);  /* ✅ 回滚 HSPHY */
          return ret;
        }

      <chip>_mdio_init(priv);
      <chip>_extphy_init(priv);
      <chip>_timestamp_init(priv);
      <chip>_qbv_init(priv);

      UP_DMB();
      shared_data->eth_sync_barrier = 1;
      priv->mac_ready = true;
    }

  /* Step 3: 使能中断 */
  for (int j = 0; j < NUM_RX_CHANNELS; j++)
    if (priv->channel[j])
      up_enable_irq(priv->config->rx_irq[j]);
  for (int j = 0; j < NUM_TX_CHANNELS; j++)
    if (priv->channel[j])
      up_enable_irq(priv->config->tx_irq[j]);

  /* Step 4: 使能 MAC + 异步链路检测 */
  <chip>_mac_rece_trans_en(priv);
  priv->ifup = true;
  work_queue(LPWORK, &priv->init_work, <chip>_init_worker, priv, 0);

  return OK;
}
```

**反模式**：

```c
/* ❌ ifup 失败后不清理已初始化的资源 */
init_mac(priv);           /* 成功 */
int ret = init_phy(priv); /* 失败 */
if (ret < 0)
  return ret;             /* ❌ MAC 已初始化但没有 deinit */
```

### ifdown

```c
static int <chip>_ifdown(FAR struct netdev_lowerhalf_s *dev)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;
  if (!priv->ifup)
    return OK;

  /* 1. 禁用中断 */
  for (int j = 0; j < NUM_RX_CHANNELS; j++)
    if (priv->channel[j])
      up_disable_irq(priv->config->rx_irq[j]);
  for (int j = 0; j < NUM_TX_CHANNELS; j++)
    if (priv->channel[j])
      up_disable_irq(priv->config->tx_irq[j]);

  /* 2. 取消异步工作 + 禁用 MAC */
  work_cancel(LPWORK, &priv->init_work);
  disable_mac_module(priv);  /* vendor-specific: disable MAC TX/RX */

  /* 3. 清理 TX pending 队列中残留的 pkt（防止内存泄漏） */
  for (int ch = 0; ch < NUM_TX_CHANNELS; ch++)
    {
      netpkt_t *pkt;
      while ((pkt = iob_remove_queue(&priv->tx_pending[ch])) != NULL)
        netpkt_free(&priv->dev, pkt, NETPKT_TX);
    }

  priv->ifup = false;
  shared_data->eth_sync_barrier = 0;
  return OK;
}
```

## 七、中断通道映射

```c
for (channel = 0; channel < NUM_RX_CHANNELS; channel++)
  if (config->enable_queues[channel])
    irq_attach(config->rx_irq[channel], <chip>_rxhandler, priv);

for (channel = 0; channel < NUM_TX_CHANNELS; channel++)
  if (config->enable_queues[channel])
    irq_attach(config->tx_irq[channel], <chip>_txhandler, priv);

/* 通道号 = irq - config->rx_irq[0] */
```

ISR Work Queue 模式：

```c
irq_attach_wqueue(config->rx_irq[ch], NULL, <chip>_rxhandler, priv,
                  CONFIG_<CHIP>_ISR_WQUEUE_PRIORITY);
irq_attach_wqueue(config->tx_irq[ch], <chip>_txisr, <chip>_txhandler,
                  priv, CONFIG_<CHIP>_ISR_WQUEUE_PRIORITY);
```

## 八、高级特性

### 8.1 PTP / IEEE 1588 时间戳

#### 时间戳初始化

```c
static void <chip>_timestamp_init(FAR struct <chip>_enet_dev_s *priv)
{
  mac->TIMESTAMP_CONTROL.TSENA = 1;
  mac->TIMESTAMP_CONTROL.TSCTRLSSR = 1;
  mac->TIMESTAMP_CONTROL.TSCFUPDT = 1;   /* 精细更新 */

  mac->SUB_SECOND_INCREMENT.SSINC = (uint32)(1E9 / ptp_freq);
  mac->TIMESTAMP_ADDEND.TSAR =
    (uint32)((double)(1ull << 32) /
             (double)(mac_clock_freq / (float)ptp_freq));

  mac->SYSTEM_TIME_SECONDS_UPDATE = 0;
  mac->SYSTEM_TIME_NANOSECONDS_UPDATE = 0;
  mac->TIMESTAMP_CONTROL.TSINIT = 1;
  while (mac->TIMESTAMP_CONTROL.TSINIT);

  mac->INTERRUPT_ENABLE.TSIE = 1;
}
```

#### TX 时间戳捕获

PTP 包发送后不立即释放 pkt，暂存等待时间戳中断：

```c
static void <chip>_ptp_pending(FAR struct netdev_lowerhalf_s *dev,
                               FAR netpkt_t *pkt, int channel)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;

  work_cancel(HPWORK, &priv->ptp_timeout_work);

  nxmutex_lock(&priv->ptp_lock);
  if (priv->ptp_pkt != NULL)
    {
      netpkt_free(&priv->dev, priv->ptp_pkt, NETPKT_TX);
    }

  priv->ptp_pkt = pkt;  /* ✅ 赋值在 lock 内，防止 timeout worker 竞态 */
  nxmutex_unlock(&priv->ptp_lock);

  work_queue(HPWORK, &priv->ptp_timeout_work,
             <chip>_ptp_timeout_worker, priv, MSEC2TICK(PTP_TIMEOUT_MS));
}
```

#### PTP 超时保护 worker

超时后释放暂存的 ptp_pkt，防止时间戳中断丢失导致 pkt 永远无法释放：

```c
static void <chip>_ptp_timeout_worker(FAR void *arg)
{
  FAR struct <chip>_enet_dev_s *priv = arg;

  nxmutex_lock(&priv->ptp_lock);
  if (priv->ptp_pkt != NULL)
    {
      nwarn("PTP TX timestamp timeout, releasing pkt\n");
      netpkt_free(&priv->dev, priv->ptp_pkt, NETPKT_TX);
      priv->ptp_pkt = NULL;
    }
  nxmutex_unlock(&priv->ptp_lock);
}
```

#### RX 时间戳读取

```c
/* receive 回调开头检查 TX 时间戳中断 */
if (mac->INTERRUPT_STATUS.TSIS)
  {
    work_cancel(HPWORK, &priv->ptp_timeout_work);
    ts.tv_nsec = mac->TX_TIMESTAMP_STATUS_NANOSECONDS;
    ts.tv_sec = mac->TX_TIMESTAMP_STATUS_SECONDS;

    nxmutex_lock(&priv->ptp_lock);
    pkt = priv->ptp_pkt;
    if (pkt != NULL)
      { pkt->io_time = ts; priv->ptp_pkt = NULL; }
    nxmutex_unlock(&priv->ptp_lock);
    return pkt;
  }

/* RX context descriptor 时间戳 */
if (descr->RDES3.CTXT && !descr->RDES3.OWN)
  if (descr->RDES3.TSA && !descr->RDES3.TSD)
    {
      pkt->io_time.tv_nsec = descr->RDES0.RTSL;
      pkt->io_time.tv_sec = descr->RDES1.RTSH;
    }
```

#### PTP 常见陷阱

| 陷阱 | 解决方案 |
|------|---------|
| 寄存器字段类型误判（boolean 非 enum） | 查 HAL 头文件结构体定义 |
| 字段名不匹配 | `grep` 搜索确认全名 |
| PPS 固定模式没有单一字段 | 阅读 PPS 配置结构体所有字段 |
| TX 时间戳丢失 | 设置超时 work_queue 保护 |
| RX 时间戳位置 | 检查 CDA/TSA 位判断可用性 |

### 8.2 QBV — IEEE 802.1Qbv 时间感知调度

```c
static int <chip>_qbv_init(FAR struct <chip>_enet_dev_s *priv,
                           FAR struct qbv_config *cfg)
{
  if (!cfg->enable)
    return OK;

  write_gcl(priv, GCL_BASE_CONFIG, BTR_LOW, cfg->basetimens);
  write_gcl(priv, GCL_BASE_CONFIG, BTR_HIGH, cfg->basetimes);
  write_gcl(priv, GCL_BASE_CONFIG, CTR_LOW, cfg->cycletimens);
  write_gcl(priv, GCL_BASE_CONFIG, CTR_HIGH, cfg->cycletimes);
  write_gcl(priv, GCL_BASE_CONFIG, LLR, cfg->gcllen);

  for (int i = 0; i < cfg->gcllen; i++)
    {
      uint32_t data = ((uint32_t)cfg->gate[i].gateon << 24)
                      | cfg->gate[i].timeinterval;
      write_gcl(priv, GCL_GATE_CONFIG, i, data);
    }

  mac->MTL.EST_CONTROL.SSWL = 1;
  mac->MTL.EST_CONTROL.EEST = 1;
  return OK;
}
```

### 8.3 ethtool 通道管理

```c
static int <chip>_getchns(FAR struct netdev_lowerhalf_s *dev,
                          FAR struct ethtool_chns2 *chns)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;
  chns->combined_chns_map = 0;
  for (int i = 0; i < NUM_RX_CHANNELS; i++)
    if (priv->channel[i])
      chns->combined_chns_map |= (1 << i);
  return OK;
}

static int <chip>_setchns(FAR struct netdev_lowerhalf_s *dev,
                          FAR struct ethtool_chns2 *chns)
{
  FAR struct <chip>_enet_dev_s *priv = (FAR struct <chip>_enet_dev_s *)dev;
  for (int i = 0; i < NUM_RX_CHANNELS; i++)
    {
      bool want_on = (chns->combined_chns_map >> i) & 1;
      if (want_on && !priv->channel[i])
        {
          up_enable_irq(priv->config->rx_irq[i]);
          up_enable_irq(priv->config->tx_irq[i]);
          start_receiver(priv, i);
          start_transmitter(priv, i);
          priv->channel[i] = true;
        }
      else if (!want_on && priv->channel[i])
        {
          up_disable_irq(priv->config->rx_irq[i]);
          up_disable_irq(priv->config->tx_irq[i]);
          stop_receiver(priv, i);
          stop_transmitter(priv, i);
          priv->channel[i] = false;
        }
    }
  return OK;
}
```

### 8.4 MDIO 接口

```c
static void <chip>_mdio_init(FAR struct <chip>_enet_dev_s *priv)
{
  set_mdio_pins(priv);
  mdio_config.clockRangeSel = CLK_DIV_102;
  if (priv->config->use_phy)
    mdio_config.clause22 = 0;           /* Clause 45 */
  else
    mdio_config.clause22 = 0x1000000;   /* Clause 22 */
  init_mdio(priv, SINGLE_TRANSFER, &mdio_config);
}
```

### 8.5 多核同步

```c
/* 主核 */
if (priv->config->main_core)
  {
    <chip>_hsphy_init(priv);
    <chip>_mac_init(priv);
    UP_DMB();
    shared_data->eth_sync_barrier = 1;
  }

/* 非主核 */
if (!priv->config->main_core)
  {
    while (!shared_data->eth_sync_barrier)
      up_udelay(CONFIG_USEC_PER_TICK);
    UP_DMB();
  }
```

### 8.6 Checksum Offload

```c
mac_config->port[port].mac.enableIpcCheck = CONFIG_<CHIP>_ENET_CSUM_OL;
```

## 九、设备注册

### 9.1 SPI-Ethernet 注册

```c
static const struct netdev_ops_s g_<chip>_ops =
{
  .ifup     = <chip>_ifup,
  .ifdown   = <chip>_ifdown,
  .transmit = <chip>_transmit,
  .receive  = <chip>_receive,
};

int <chip>_netdev_register(FAR struct spi_dev_s *spi)
{
  FAR struct <chip>_priv_s *priv = kmm_zalloc(sizeof(struct <chip>_priv_s));
  if (priv == NULL)
    return -ENOMEM;

  priv->spi = spi;
  priv->dev.ops = &g_<chip>_ops;
  priv->dev.quota[NETPKT_TX] = 1;
  priv->dev.quota[NETPKT_RX] = 1;

  return netdev_lower_register(&priv->dev, NET_LL_ETHERNET);
}
```

### 9.2 片上 MAC 注册

```c
int <chip>_enet_initialize(FAR struct net_driver_s **dev,
                           FAR const struct <chip>_enet_config_s *config,
                           size_t num, bool main_core)
{
  int core_id = up_cpu_index();

  for (int i = 0; i < num; i++)
    {
      const struct <chip>_enet_config_s *cfg = config + i;
      if (cfg->core_id != core_id || cfg->nchans == 0 ||
          cfg->main_core != main_core)
        continue;

      FAR struct <chip>_enet_dev_s *priv =
        kmm_zalloc(sizeof(struct <chip>_enet_dev_s));
      if (priv == NULL)
        return -ENOMEM;

      priv->dev.ops              = &g_<chip>_enet_ops;
#ifdef CONFIG_NETDEV_ETHTOOL_IOCTL
      priv->dev.eth_ops          = &g_<chip>_eth_ops;
#endif
      priv->dev.quota[NETPKT_TX] = CONFIG_<CHIP>_ENET_NTX0DESC;
      priv->dev.quota[NETPKT_RX] = CONFIG_<CHIP>_ENET_NRX0DESC;
      priv->dev.rxtype           = NETDEV_RX_DIRECT;
      priv->config               = cfg;

      set_mac_address(priv->dev.netdev.d_mac.ether.ether_addr_octet);
      snprintf(priv->dev.netdev.d_ifname, IFNAMSIZ, "eth%d", 0);

      int ret = netdev_lower_register(&priv->dev, NET_LL_ETHERNET);
      if (ret < 0)
        { kmm_free(priv); return ret; }

      dev[cfg->port] = &priv->dev.netdev;

      for (int ch = 0; ch < NUM_RX_CHANNELS; ch++)
        {
          priv->channel[ch] = cfg->enable_queues[ch];
          if (cfg->enable_queues[ch])
            irq_attach(cfg->rx_irq[ch], <chip>_rxhandler, priv);
        }
      for (int ch = 0; ch < NUM_TX_CHANNELS; ch++)
        if (cfg->enable_queues[ch])
          irq_attach(cfg->tx_irq[ch], <chip>_txhandler, priv);
    }

  return OK;
}
```

## 十、Bring-Up 验证 Checklist

### 编译阶段

- [ ] `make -C nuttx` 编译通过，无 warning
- [ ] `nuttx/tools/checkpatch.sh -f <driver>.c` 代码风格通过

### 基础功能

- [ ] `ifup eth0` 成功，`ifconfig` 显示正确的 MAC 地址和 IP
- [ ] `ping <target_ip>` 连通（先测小包 56 字节）
- [ ] `ping -s 1400 <target_ip>` 大包连通（接近 MTU）
- [ ] 反向 ping：从对端 ping 本设备

### 吞吐量

- [ ] `iperf -s` + 对端 `iperf -c <ip> -t 30` TCP 打流无报错
- [ ] `iperf -c <server_ip> -t 30` 本端发送无 ENOMEM
- [ ] UDP 打流：`iperf -c <ip> -u -b 100M -t 30` 丢包率可接受

### 链路状态

- [ ] 拔网线后 `ifconfig` 显示 link down / carrier off
- [ ] 插网线后自动恢复，`ping` 恢复正常
- [ ] 反复拔插 10 次无 crash

### 压力测试

- [ ] 长时间 iperf（>10 分钟）无内存泄漏（`free` 命令监控）
- [ ] iperf 打流期间拔插网线，恢复后继续打流
- [ ] 多次 `ifdown eth0` + `ifup eth0` 循环无异常

### 片上 MAC 补充

- [ ] 各通道独立 iperf 打流正常
- [ ] VLAN 包按优先级正确路由到对应通道
- [ ] ethtool 动态启用/禁用通道后收发正常
- [ ] PTP TX 时间戳中断正常触发
- [ ] PTP 超时保护：pkt 在超时后正确释放
- [ ] 主核 ifup 后非主核 ifup 成功
- [ ] 两个核心同时 iperf 打流无冲突

## 十一、常见运行时问题排查

### iperf 报 ENOMEM (error 12)

TCP 连接成功但发送时内存不足。IOB pool 或 TCP 缓冲区不够。

```kconfig
CONFIG_IOB_NBUFFERS=256
CONFIG_NET_TCP_WRITE_BUFSIZE=4096
CONFIG_NET_TCP_NWRBCHAINS=16
```

### TCP Window Full

接收方消费数据太慢，IOB 资源有限。

```kconfig
CONFIG_IOB_NBUFFERS=256
CONFIG_NET_TCP_RECVBUF_SIZE=65535
```

### ECONNREFUSED (error 111)

对端 iperf server 未启动、端口不匹配、或链路不通。先 `ping` 确认连通性。

### ifup 后 ping 不通

按以下顺序逐步排查：

1. `ifconfig` → 检查 IP 非 `0.0.0.0`，MAC 非全零
2. 检查 carrier → `ninfo` 日志中应有 `"Link UP"` 或 `carrier_on` 调用
3. 对端抓包 → `tcpdump -i ethX arp` 确认本端 ARP request 是否发出
4. 检查 RX → `ninfo` 日志中应有 `rxready` 调用，若无则 RX 中断未触发
5. 检查 TX → 确认 DMA 描述符 OWN bit 在发送后被硬件清除
6. 检查 cache → Zero-copy 模式下 TX 前是否调用了 `up_clean_dcache`，RX 后是否调用了 `up_invalidate_dcache`

### 收发正常但偶发 crash

常见原因：DMA 描述符并发未加锁、TX 完成中断释放了 PTP pending 的 pkt、cache 未维护、中断中调用非中断安全 API。

## 十二、Cross References

### 本文档依赖

| 文档 | 用途 |
|------|------|
| `SKILL.md` | 主入口：Driver Type Dispatch Table、通用开发流程 |
| `bus_access.md` | SPI-Ethernet 设备的总线访问模式 |
| `coding_rules.md` | 编码规范、内核 API、中断规则、work_queue 选择 |
| `board_registration.md` | 板级注册模式、defconfig 配置 |

### 关键头文件

| 头文件 | 内容 |
|--------|------|
| `include/nuttx/net/netdev_lowerhalf.h` | `netdev_ops_s`、`netdev_lowerhalf_s`、NetPKT API |
| `include/nuttx/net/ethernet.h` | 以太网帧定义 |
| `include/nuttx/ethtool.h` | ethtool ioctl 定义 |
| `include/nuttx/timers/ptp_clock.h` | PTP 时钟接口 |

### 参考驱动

| 驱动 | 路径 | 说明 |
|------|------|------|
| **片上 MAC 参考** | `vendor/<vendor>/chips/<chip>/<chip>_enet.c` | 多通道 DMA、PTP、QBV、多核同步 |
| sim netdriver | `arch/sim/src/sim/sim_netdriver.c` | 最简实现参考 |
| ENC28J60 | `drivers/net/enc28j60.c` | SPI-Ethernet 参考 |
| W5500 | `drivers/net/w5500.c` | SPI-Ethernet 参考 |
| LAN9250 | `drivers/net/lan9250.c` | SPI-Ethernet 参考 |
