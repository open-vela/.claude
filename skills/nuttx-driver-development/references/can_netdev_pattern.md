# CAN SocketCAN Network Driver Pattern — openvela CAN 驱动网络子系统适配框架

本文档是 CAN (SocketCAN) 驱动子系统的完整参考，由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

> 本文档覆盖片上 CAN 控制器（MCAN/bxCAN/FlexCAN 等）适配 NuttX 网络子系统（SocketCAN）的完整模式。

> **适用范围**：仅适用于通过 NuttX **网络子系统（SocketCAN）** 暴露的 CAN 驱动。传统字符设备 CAN 驱动 (`/dev/canX` 通过 `drivers/can/can.c` upper-half) 走不同的框架，不适用本文档。

## 目录

1. [框架概述](#一框架概述)
2. [开发准备](#二开发准备)
3. [关键数据结构](#三关键数据结构)
4. [核心 API](#四核心-api)
5. [数据收发模式](#五数据收发模式)
6. [中断处理](#六中断处理)
7. [CAN 控制器状态管理](#七can-控制器状态管理)
8. [错误处理](#八错误处理)
9. [设备注册](#九设备注册)
10. [高级特性](#十高级特性)
11. [Bring-Up 验证 Checklist](#十一bring-up-验证-checklist)
12. [常见运行时问题排查](#十二常见运行时问题排查)
13. [Cross References](#十三cross-references)

---

## 一、框架概述

### SocketCAN vs 字符设备 CAN

NuttX 提供两套 CAN 驱动模型：

| 模型 | 接口 | 用户 API | 头文件 | 注册函数 |
|------|------|---------|--------|---------|
| **SocketCAN（本文档）** | 网络子系统 | `socket()` / `bind()` / `sendmsg()` / `recvmsg()` | `netdev_lowerhalf.h` | `netdev_lower_register()` |
| 字符设备 CAN | VFS | `open()` / `read()` / `write()` / `ioctl()` | `nuttx/can/can.h` | `can_register()` |

**选择原则**：新项目优先使用 SocketCAN 模型，它提供 Linux 兼容的 socket API，支持多播（多个 socket 监听同一接口）、软件过滤、CAN FD、错误帧等高级特性。

### 上下半区驱动模型

```
Application (socket / bind / sendmsg / recvmsg / setsockopt / poll)
    │
    ▼
SocketCAN 协议栈 (net/can/)
    │  can_sockif / can_sendmsg / can_recvmsg / can_input
    ▼
Upper-half: netdev framework ← 通用层，管理收发 quota、链路状态
    │  ifup / ifdown / transmit / receive
    ▼
Lower-half: YOUR CAN driver ← 实现 netdev_ops_s 回调
    │
    ▼
Hardware: CAN Controller (MCAN / bxCAN / FlexCAN / etc.)
    │
    ▼
CAN Bus (Physical Layer: CAN 2.0B / CAN FD)
```

### CAN 与 Ethernet netdev 的关键差异

| 维度 | Ethernet | CAN (SocketCAN) |
|------|----------|-----------------|
| 链路类型 | `NET_LL_ETHERNET` | `NET_LL_CAN` |
| 帧结构 | `struct eth_hdr_s` + payload | `struct can_frame` / `struct canfd_frame` |
| 最大帧长 | MTU 1500 + 14 字节头 | CAN: 16 字节 (`CAN_MTU`) / CAN FD: 72 字节 (`CANFD_MTU`) |
| 地址 | MAC 地址 (6 字节) | CAN ID (11-bit 或 29-bit)，无 MAC 地址 |
| IOB 使用 | 可能分片（`netpkt_is_fragmented`） | **不分片**，单个 IOB 装入整帧 |
| DMA | 常用，需要 scatter-gather | 通常不用 DMA，帧短小直接寄存器读写 |
| TX 完成 | DMA 完成后释放 pkt | 硬件 TX buffer 传输完成后释放 pkt |
| RX 模式 | DMA 直写 → Zero-copy | 从硬件 FIFO/buffer 读取 → copyin 到 netpkt |
| 链路状态 | PHY link up/down | CAN bus on/off/sleep，bus-off 恢复 |
| 错误帧 | 无 | `CAN_ERR_FLAG` 错误帧，bus-off / error-passive / error-warning |
| `d_features` | checksum offload 等 | `NETDEV_RX_STAMP`（时间戳） |
| quota[NETPKT_TX] | 通常 = TX 描述符数量 | = 硬件 TX buffer 数量 |
| quota[NETPKT_RX] | 通常 = RX 描述符数量 | 通常 = 1（逐帧处理） |

### 实现前设计决策（AI 必须询问）

#### 决策 1：CAN 帧缓冲策略

CAN 帧极短（最大 72 字节），不存在 Ethernet 那样的 DMA scatter-gather 需求。CAN SocketCAN 驱动统一使用 **copyin 模式**：从硬件 RX FIFO/buffer 中读取帧 → 拷入 netpkt。TX 路径通过 `netpkt_getdata()` 获取帧指针 → 写入硬件 TX buffer。

```
TX: App → 协议栈 → netpkt → getdata → frame 指针 → 写入 HW TX buffer → wire
RX: wire → HW RX FIFO/buffer → 读取到 netpkt buffer → 协议栈 → App
```

#### 决策 2：中断处理模式

| 方案 | 说明 | 适用场景 |
|------|------|---------|
| **A: 纯 ISR** | ISR 中直接调用 `netdev_lower_rxready` / `netdev_lower_txdone` | CAN 帧短小，ISR 处理快速，通用推荐 |
| **B: ISR + Work Queue** | ISR 仅设标志 + 调度 worker | 高负载场景，避免长时间占用中断 |
| **C: irq_attach_wqueue** | 框架管理的线程化中断 | 需要 mutex/SPI 访问的场景 |

**CAN 驱动推荐**：方案 A（纯 ISR 直接通知），因为 CAN 帧处理极快，无需延迟到 worker。如需复杂的中断后处理（如错误轮询），可搭配 HPWORK 做周期性工作。

#### 决策 3：RX 处理模式 (rxtype)

| 模式 | 说明 | 配置 |
|------|------|------|
| `NETDEV_RX_THREAD` | 上层创建专用 RX 线程，ISR 中 `rxready` 唤醒线程来调用 `receive()` | 默认，安全 |
| `NETDEV_RX_DIRECT` | ISR 中直接调用 `receive()` 回调 | 低延迟，需配合 `irq_attach_wqueue` 使用 |

> **AI 询问模板**：CAN 驱动的 RX 模式需要确认：`NETDEV_RX_THREAD`（默认安全）还是 `NETDEV_RX_DIRECT`（低延迟但需 ISR wqueue）？

---

## 二、开发准备

### Kconfig 配置

```kconfig
# 网络协议栈配置
CONFIG_NET=y
CONFIG_NET_CAN=y                          # 使能 SocketCAN
CONFIG_NET_CAN_CANFD=y                    # 使能 CAN FD（可选）
CONFIG_NET_CAN_RAW_FILTER_MAX=32          # 每个 socket 最大过滤器数
CONFIG_NET_CAN_ERRORS=y                   # 使能错误帧上报
CONFIG_NET_CAN_SOCK_OPTS=y
CONFIG_NET_CANPROTO_OPTIONS=y

# CAN socket / conn 资源
CONFIG_CAN_PREALLOC_CONNS=4
CONFIG_CAN_ALLOC_CONNS=4
CONFIG_CAN_MAX_CONNS=8

# 通用 send/recv 缓冲
CONFIG_NET_SEND_BUFSIZE=128
CONFIG_NET_RECV_BUFSIZE=128
CONFIG_NET_CAN_SEND_BUFSIZE=128
CONFIG_NET_CAN_RECV_BUFSIZE=128

# CAN 写缓冲
CONFIG_NET_CAN_WRITE_BUFFERS=y
CONFIG_NET_CAN_NBUFFERS=8

# IOB 双池配置（pool1 大包 / pool2 小包）
CONFIG_MM_IOB=y
CONFIG_IOB_NPOOLS=2
CONFIG_IOB_NBUFFERS=64
CONFIG_IOB_BUFSIZE=512
CONFIG_IOB_NBUFFERS2=196
CONFIG_IOB_BUFSIZE2=96
CONFIG_IOB_ALIGNMENT=8
CONFIG_IOB_THROTTLE=8

# 驱动相关
CONFIG_NETDEVICES=y
CONFIG_NETDEV_IOCTL=y
CONFIG_NETDEV_CAN_FILTER_IOCTL=y

# 调试日志
CONFIG_DEBUG_CAN=y
CONFIG_DEBUG_CAN_ERROR=y
# CONFIG_DEBUG_CAN_WARN=y
# CONFIG_DEBUG_CAN_INFO=y
```

日志宏：`canerr()` / `canwarn()` / `caninfo()` 以及网络层 `nerr()` / `nwarn()` / `ninfo()`

#### 驱动自身 Kconfig 条目

```kconfig
config <CHIP>_XXXCAN
	bool "<CHIP> XXXCAN CAN Controller"
	depends on NET && NET_CAN && NETDEVICES
	default n
	---help---
		Enable driver for <CHIP> XXXCAN CAN controller
		using the NuttX SocketCAN network interface.

if <CHIP>_XXXCAN

config <CHIP>_XXXCAN_SOCKET
	bool "<CHIP> XXXCAN SocketCAN support"
	default y

config <CHIP>_XXXCAN_ISR_WQUEUE
	bool "Use ISR Work Queue for interrupt handling"
	default n
	---help---
		Use irq_attach_wqueue for threaded interrupt handling
		instead of direct ISR.

endif # <CHIP>_XXXCAN
```

### IOB 双池配置约束

当前平台使用双池 IOB（mempool）架构：

- `g_iob`（pool1，大包池）：用于 Ethernet 等较大报文
- `g_iob2`（pool2，小包池）：用于 CAN 这类短帧场景

CAN SocketCAN 驱动的关键约束是：**单帧必须装入单个 IOB2 buffer**。因此应检查 `CONFIG_IOB_BUFSIZE2`（而不是 `CONFIG_IOB_BUFSIZE`）：

```c
static_assert(CONFIG_IOB_BUFSIZE2 >= sizeof(struct canfd_frame) +
              CONFIG_NET_LL_GUARDSIZE, "iob2 size too small for CAN FD");
```

参考当前 `.config`：

```kconfig
CONFIG_MM_IOB=y
CONFIG_IOB_NPOOLS=2
CONFIG_IOB_NBUFFERS=64
CONFIG_IOB_BUFSIZE=512
CONFIG_IOB_NBUFFERS2=196
CONFIG_IOB_BUFSIZE2=96
CONFIG_IOB_ALIGNMENT=8
CONFIG_IOB_THROTTLE=8
```

说明：

- CAN FD 最大帧长 `sizeof(struct canfd_frame)=72` 字节
- 加上 `CONFIG_NET_LL_GUARDSIZE` 后，`CONFIG_IOB_BUFSIZE2=96` 仍可覆盖
- CAN 帧应优先使用小包池 `g_iob2`，以避免占用大包池资源
- `CONFIG_IOB_NBUFFERS2` 决定可并发使用的 IOB2 buffer 数量

### 文件布局

**片上 CAN 控制器（放在 vendor 目录）：**

```
vendor/<vendor>/chips/<chip>/
├── <chip>_xxxcan_net.c       # SocketCAN 网络驱动实现
├── <chip>_xxxcan.c           # CAN 硬件底层（初始化、寄存器操作）
├── <chip>_xxxcan.h           # 公共头文件（config 结构和注册函数）
├── Kconfig
├── Make.defs
└── CMakeLists.txt

vendor/<vendor>/boards/<chip>/<board>/src/
└── <board>.c                 # 板级配置表和初始化调用
```

**通用 CAN 控制器驱动（放在 arch 或 drivers 目录）：**

```
nuttx/
├── arch/<arch>/src/<chip>/
│   └── <chip>_can_sock.c     # SocketCAN 网络驱动实现
├── drivers/can/
│   ├── Kconfig
│   └── <device>.c            # 通用 CAN 控制器（如 SPI-CAN）
└── include/nuttx/
    └── can.h                 # CAN 帧定义、错误码
```

---

## 三、关键数据结构

### 3.1 netdev_ops_s — 驱动操作集（CAN 专用子集）

CAN SocketCAN 驱动只需实现 `netdev_ops_s` 中的核心回调：

```c
static const struct netdev_ops_s g_<chip>_can_ops =
{
  .ifup     = <chip>_can_ifup,      /* 启动 CAN 控制器 */
  .ifdown   = <chip>_can_ifdown,    /* 关闭 CAN 控制器 */
  .transmit = <chip>_can_transmit,  /* 发送 CAN 帧 */
  .receive  = <chip>_can_receive,   /* 接收 CAN 帧 */
#ifdef CONFIG_NETDEV_IOCTL
  .ioctl    = <chip>_can_ioctl,     /* CAN 特定 ioctl */
#endif
};
```

| 回调 | 必需 | 说明 |
|------|------|------|
| `ifup` | Yes | 初始化 CAN 控制器、配置过滤器、使能中断、进入 OPERATIONAL 状态 |
| `ifdown` | Yes | 关闭中断、停止控制器、释放 pending TX pkt |
| `transmit` | Yes | 将 CAN 帧写入硬件 TX buffer，返回 OK 或 -EBUSY |
| `receive` | Yes | 从硬件 RX FIFO/buffer 读取帧，返回 netpkt 或 NULL |
| `ioctl` | Optional | CAN 状态查询/设置、bus-off 恢复、transceiver 控制 |
| `addmac`/`rmmac` | N/A | CAN 无 MAC 地址，不需要实现 |
| `reclaim` | N/A | CAN 帧短小，一般不需要 |

### 3.2 设备私有结构定义

```c
struct <chip>_can_priv_s
{
  struct netdev_lowerhalf_s   dev;           /* 必须第一个成员 */
  struct <chip>_can_config_s *config;        /* 板级配置指针 */

  /* TX buffer 追踪 */
  netpkt_t                  **tx_pkt_pending; /* 各 TX buffer 对应的 pending pkt */
  uint32_t                    txmb_sflags;    /* TX mailbox 状态位图：1=占用 */

  /* RX 状态追踪 */
  uint32_t                    rxmb0_sflags;   /* RX dedicated buffer NDAT1 */
  uint32_t                    rxmb1_sflags;   /* RX dedicated buffer NDAT2 */
  uint8_t                     rxfifo0_pending; /* RX FIFO0 待读帧数 */
  uint8_t                     rxfifo1_pending; /* RX FIFO1 待读帧数 */

  /* 错误状态 */
  uint8_t                     err_pending;    /* 错误标志位图 */
  uint8_t                     state;          /* CAN 控制器状态 */

#ifdef CONFIG_<CHIP>_CAN_ERROR_POLLING
  struct work_s               errwork;        /* 错误轮询 worker */
#endif
};
```

**关键设计点**：
- `dev` 必须是第一个成员，上层框架通过指针强转访问
- `tx_pkt_pending[]` 数组大小 = 硬件 TX buffer 数量，动态分配
- `txmb_sflags` 位图追踪哪些 TX buffer 正在使用
- `rxmb0_sflags` / `rxmb1_sflags` 在 ISR 中一次性快照硬件 NDAT 寄存器

### 3.3 板级配置结构

```c
struct <chip>_can_config_s
{
  uint8_t   intf;             /* CAN 接口号（用于 can%d 命名） */

  /* 中断资源 */
  int       tx_irq;           /* TX 完成中断号 */
  int       rx_irq;           /* RX 就绪中断号 */
  int       err_irq;          /* 错误中断号 */

  /* 波特率 */
  uint32_t  baudrate;         /* 标称波特率（如 500000） */
  uint32_t  fast_baudrate;    /* CAN FD 数据段波特率（如 2000000） */

  /* vendor-specific 硬件配置由厂商自行定义 */
};
```

---

## 四、核心 API

### 4.1 设备注册与注销

```c
/* 注册 CAN 网络设备 — 注意使用 NET_LL_CAN */
int netdev_lower_register(FAR struct netdev_lowerhalf_s *dev,
                          enum net_lltype_e lltype);  /* 传 NET_LL_CAN */
int netdev_lower_unregister(FAR struct netdev_lowerhalf_s *dev);
```

### 4.2 链路状态通知

```c
/* CAN bus-on / bus-off */
void netdev_lower_carrier_on(FAR struct netdev_lowerhalf_s *dev);
void netdev_lower_carrier_off(FAR struct netdev_lowerhalf_s *dev);
```

**CAN 特有用法**：
- `carrier_on`：CAN 控制器进入 OPERATIONAL 模式时调用
- `carrier_off`：检测到 bus-off 状态时调用
- bus-off 恢复后重新调用 `carrier_on`

### 4.3 收发完成通知

```c
/* ISR 中通知上层 */
void netdev_lower_rxready(FAR struct netdev_lowerhalf_s *dev);
void netdev_lower_txdone(FAR struct netdev_lowerhalf_s *dev);
```

**CAN 特有用法**：
- `rxready`：RX 中断中调用，通知上层来调用 `receive()` 回调取帧
- `txdone`：TX 完成中断中调用，通知上层 TX quota 已归还
- **重要**：TX 完成时同时调用 `rxready`，因为 `receive()` 回调中可能需要处理 TX confirm（详见第五节）

### 4.4 NetPKT Buffer 接口（CAN 常用子集）

```c
/* 分配 / 释放 */
FAR netpkt_t *netpkt_alloc(FAR struct netdev_lowerhalf_s *dev,
                           enum netpkt_type_e type);
void netpkt_free(FAR struct netdev_lowerhalf_s *dev,
                 FAR netpkt_t *pkt, enum netpkt_type_e type);

/* 获取 buffer 数据指针（CAN 帧不分片，直接使用） */
FAR uint8_t *netpkt_getdata(dev, pkt);

/* 设置/获取数据长度 */
unsigned int netpkt_setdatalen(dev, pkt, len);
unsigned int netpkt_getdatalen(dev, pkt);
```

**CAN 驱动不需要的 API**：
- `netpkt_copyin` / `netpkt_copyout` — CAN 帧不分片，用 `netpkt_getdata` 直接操作
- `netpkt_is_fragmented` / `iob_count` — CAN 帧始终在单个 IOB 中
- `up_addrenv_va_to_pa` / `up_clean_dcache` — CAN 不走 DMA scatter-gather

---

## 五、数据收发模式

### 5.1 发送路径（transmit 回调）

```c
static int <chip>_can_transmit(FAR struct netdev_lowerhalf_s *dev,
                                FAR netpkt_t *pkt)
{
  FAR struct <chip>_can_priv_s *priv = (FAR struct <chip>_can_priv_s *)dev;

#ifdef CONFIG_NET_CAN_CANFD
  FAR struct canfd_frame *frame =
      (FAR struct canfd_frame *)netpkt_getdata(dev, pkt);
#else
  FAR struct can_frame *frame =
      (FAR struct can_frame *)netpkt_getdata(dev, pkt);
#endif

  /* Step 1: 检查控制器状态 */
  if (priv->state != CAN_STATE_OPERATIONAL)
    return -EIO;

  /* Step 2: 禁用 TX 中断，防止并发修改 txmb_sflags */
  <chip>_can_txint(priv, false);

  /* Step 3: 寻找空闲 TX buffer 并填充硬件消息 */
  int txbuf_id = find_free_txbuffer(priv);
  if (txbuf_id < 0)
    {
      <chip>_can_txint(priv, true);
      return -EBUSY;  /* 所有 TX buffer 繁忙 */
    }

  /* Step 4: 将 CAN 帧写入硬件 TX buffer */
  write_frame_to_hw(priv, txbuf_id, frame);

  /* Step 5: 追踪 pkt 和 TX buffer 状态 */
  priv->txmb_sflags |= (1 << txbuf_id);
  priv->tx_pkt_pending[txbuf_id] = pkt;  /* ⚠️ 不在此释放 pkt */

  /* Step 6: 重新使能 TX 中断 */
  <chip>_can_txint(priv, true);
  return OK;
}
```

**关键规则**：
- **不在 transmit 中释放 pkt** — pkt 的生命周期由 TX 完成中断管理
- **TX buffer 满时返回 `-EBUSY`** — 上层会基于 quota 重试
- **必须追踪 `tx_pkt_pending`** — TX 完成中断时需要释放对应的 pkt

**反模式**：
```c
/* ❌ 在 transmit 中释放 pkt — 上层还需要引用 */
netpkt_free(dev, pkt, NETPKT_TX);

/* ❌ 忘记追踪 pkt — TX 完成时内存泄漏 */
write_frame_to_hw(priv, txbuf_id, frame);
/* 缺少 priv->tx_pkt_pending[txbuf_id] = pkt; */
```

### 5.2 接收路径（receive 回调）

CAN 的 `receive()` 回调同时处理 **RX 帧接收** 和 **TX confirm** 两个职责：

```c
static FAR netpkt_t *<chip>_can_receive(FAR struct netdev_lowerhalf_s *dev)
{
  FAR struct <chip>_can_priv_s *priv = (FAR struct <chip>_can_priv_s *)dev;
  netpkt_t *pkt = NULL;
  uint8_t msg_len = 0;

  /* Step 1: 分配 RX pkt */
  pkt = netpkt_alloc(dev, NETPKT_RX);
  if (pkt == NULL)
    {
      nwarn("Allocate RX pkt failed\n");
      goto txconfirm_out;  /* 分配失败时仍尝试 TX confirm */
    }

  /* Step 2: 设置帧长度（CAN 帧定长） */
#ifdef CONFIG_NET_CAN_CANFD
  netpkt_setdatalen(dev, pkt, sizeof(struct canfd_frame));
#else
  netpkt_setdatalen(dev, pkt, sizeof(struct can_frame));
#endif

  /* Step 3: 优先处理错误帧 */
#ifdef CONFIG_NET_CAN_ERRORS
  if (priv->err_pending != 0)
    {
      msg_len = <chip>_errhandle(priv, netpkt_getdata(dev, pkt));
      priv->err_pending = 0;
      goto receive_out;
    }
#endif

  /* Step 4: 从各 RX 源读取帧（优先级：dedicated buffer > FIFO0 > FIFO1） */
  /* 4a: Dedicated RX buffers */
  if (priv->rxmb0_sflags != 0)
    {
      uint8_t buf_id = ffs(priv->rxmb0_sflags) - 1;
      msg_len = read_message(priv, netpkt_getdata(dev, pkt),
                             RX_DEDICATED_BUFFER, buf_id);
      priv->rxmb0_sflags &= ~(1 << buf_id);
      goto receive_out;
    }

  /* 4b: RX FIFO 0 */
  if (priv->rxfifo0_pending > 0)
    {
      msg_len = read_message(priv, netpkt_getdata(dev, pkt),
                             RX_FIFO0, 0);
      priv->rxfifo0_pending--;
      goto receive_out;
    }

  /* 4c: RX FIFO 1 */
  if (priv->rxfifo1_pending > 0)
    {
      msg_len = read_message(priv, netpkt_getdata(dev, pkt),
                             RX_FIFO1, 0);
      priv->rxfifo1_pending--;
      goto receive_out;
    }

receive_out:
  if (msg_len <= 0)
    {
      netpkt_free(dev, pkt, NETPKT_RX);
      pkt = NULL;
    }
  else
    {
      return pkt;  /* ✅ 成功接收，返回 pkt */
    }

txconfirm_out:
  /* Step 5: 无 RX 帧时，检查是否有 TX confirm */
  if (priv->txmb_sflags != 0)
    {
      pkt = <chip>_tx_confirm(priv);
    }

  return pkt;  /* 返回 TX confirm pkt 或 NULL */
}
```

**关键设计模式 — receive 回调的双重职责**：

```
receive() 被调用
    │
    ├─→ 有 RX 帧？ ──Yes──→ 分配 pkt → 从 HW 读帧 → 返回 pkt
    │           │
    │           No
    │           │
    └─→ 有 TX confirm？ ──Yes──→ 从 tx_pkt_pending 取出 pkt → 归还 quota → 返回 pkt
                │
                No
                │
                └─→ 返回 NULL
```

这个设计是 CAN SocketCAN 驱动特有的模式。TX 完成中断同时调用 `rxready` 和 `txdone`，上层调度 `receive()` 回调时，如果没有 RX 帧，驱动可以返回 TX confirm pkt（包含已发送帧的 echo），供 loopback 等功能使用。

> **可选实现模式（Feishu 参考）**：也可采用“IRQ 先入队、receive 只出队”的模式。
>
> - RX 中断中：分配 `pkt` → 填充 CAN 帧数据 → 入 `rx_queue` → 调用 `netdev_lower_rxready`
> - `receive()` 回调中：仅从 `rx_queue` 出队并返回 `pkt`
>
> 该模式将硬件访问尽量前移到中断上下文，`receive()` 逻辑更简洁，适合统一 RX/TX confirm 队列化实现。

### 5.3 TX Confirm 处理

```c
static netpkt_t *<chip>_tx_confirm(struct <chip>_can_priv_s *priv)
{
  FAR struct netdev_lowerhalf_s *dev = (FAR struct netdev_lowerhalf_s *)priv;
  uint8_t tx_buffs = get_tx_buffer_count(priv);
  netpkt_t *pkt = NULL;

  /* 加锁保护 tx_pkt_pending 和 txmb_sflags */
  netdev_lock(&dev->netdev);

  for (uint8_t id = 0; id < tx_buffs; id++)
    {
      if ((priv->txmb_sflags & (1 << id)) != 0
          && hw_tx_complete(priv, id))
        {
          pkt = priv->tx_pkt_pending[id];
          priv->tx_pkt_pending[id] = NULL;
          priv->txmb_sflags &= ~(1 << id);

          /* 归还 TX quota */
          atomic_add(&dev->quota_ptr[NETPKT_TX], 1);

          netdev_unlock(&dev->netdev);
          return pkt;
        }
    }

  netdev_unlock(&dev->netdev);
  return pkt;
}
```

### 5.4 TX Buffer 刷新（flush）

在 bus-off 恢复或 ifdown 时，需要取消所有 pending TX buffer：

```c
static int <chip>_flush_txbuff(struct <chip>_can_priv_s *priv)
{
  uint8_t tx_buffs = get_tx_buffer_count(priv);

  do
    {
      <chip>_can_txint(priv, false);

      for (uint8_t id = 0; id < tx_buffs; id++)
        {
          if ((priv->txmb_sflags & (1 << id)) != 0)
            {
              /* 取消硬件发送 */
              hw_cancel_tx(priv, id);

              /* 释放 pending pkt */
              netpkt_free((struct netdev_lowerhalf_s *)priv,
                          priv->tx_pkt_pending[id], NETPKT_TX);
              priv->txmb_sflags &= ~(1 << id);
            }
        }

      <chip>_can_txint(priv, true);
      netdev_lower_txdone((struct netdev_lowerhalf_s *)priv);
    }
  while (priv->txmb_sflags != 0);

  return OK;
}
```

---

## 六、中断处理

### 6.1 TX 完成中断

```c
static int <chip>_tx_interrupt(int irq, void *context, void *arg)
{
  struct <chip>_can_priv_s *priv = arg;

  if (hw_tx_complete_flag(priv))
    {
      /* 同时通知 rxready 和 txdone：
       * rxready → 上层调用 receive() → 返回 TX confirm pkt
       * txdone  → 上层知道 TX quota 有空位了
       */
      netdev_lower_rxready((struct netdev_lowerhalf_s *)priv);
      netdev_lower_txdone((struct netdev_lowerhalf_s *)priv);

      hw_clear_tx_flag(priv);
    }

  return OK;
}
```

> **可选 TX confirm 模式（Feishu 参考）**：`tx_confirm` 也可不在 `receive()` 中轮询 `txmb_sflags`，而是在 TX 完成路径中直接把 confirm 对应的 `pkt` 放入 `rx_queue`，随后调用 `netdev_lower_rxready`。这样 `receive()` 统一做“队列出队”，TX/RX 复用同一投递通道。

### 6.2 RX 就绪中断

```c
static int <chip>_rx_interrupt(int irq, void *context, void *arg)
{
  struct <chip>_can_priv_s *priv = arg;

  /* 快照 RX 状态到 priv 成员，供 receive() 回调使用 */

  /* Dedicated RX buffers */
  if (hw_has_dedicated_rx(priv))
    {
      priv->rxmb0_sflags = hw_read_ndat1(priv);
      priv->rxmb1_sflags = hw_read_ndat2(priv);
    }

  /* RX FIFO */
  if (hw_fifo0_has_data(priv))
    priv->rxfifo0_pending = hw_fifo0_fill_level(priv);
  if (hw_fifo1_has_data(priv))
    priv->rxfifo1_pending = hw_fifo1_fill_level(priv);

  netdev_lower_rxready((struct netdev_lowerhalf_s *)priv);
  return OK;
}
```

### 6.3 错误中断

```c
static int <chip>_err_interrupt(int irq, void *context, void *arg)
{
  struct <chip>_can_priv_s *priv = arg;

  if (hw_busoff_detected(priv))
    {
      netdev_lower_carrier_off(&priv->dev);  /* 通知链路断开 */

#ifdef CONFIG_NET_CAN_ERRORS
      priv->err_pending |= ERR_BUSOFF_PENDING;
      netdev_lower_rxready((struct netdev_lowerhalf_s *)priv);
      /* receive() 回调中通过 errhandle 构造错误帧 */
#else
      <chip>_busoff_recovery(priv);  /* 直接恢复 */
#endif
      hw_clear_busoff_flag(priv);
    }

  return OK;
}
```

### 6.4 中断注册模式

```c
/* 方案 A: 直接 irq_attach（默认） */
irq_attach(config->tx_irq, <chip>_tx_interrupt, priv);
irq_attach(config->rx_irq, <chip>_rx_interrupt, priv);
irq_attach(config->err_irq, <chip>_err_interrupt, priv);

/* 方案 C: irq_attach_wqueue（线程化） */
irq_attach_wqueue(config->tx_irq, NULL, <chip>_tx_interrupt, priv,
                  CONFIG_<CHIP>_XXXCAN_ISR_WQUEUE_PRIORITY);
irq_attach_wqueue(config->rx_irq, NULL, <chip>_rx_interrupt, priv,
                  CONFIG_<CHIP>_XXXCAN_ISR_WQUEUE_PRIORITY);
```

---

## 七、CAN 控制器状态管理

CAN 控制器有三种状态，与 Ethernet 的简单 link up/down 不同：

```
                 ┌──────────────┐
                 │   SLEEP      │ ← 模块断电/时钟关闭
                 └──────┬───────┘
                        │ ifup / setmode(OPERATIONAL)
                        ▼
                 ┌──────────────┐
    bus-off ←──  │ OPERATIONAL  │ ←── bus-off recovery
    (carrier_off)└──────┬───────┘     (carrier_on)
                        │ setmode(STOPPED)
                        ▼
                 ┌──────────────┐
                 │  STOPPED     │ ← 初始化模式，可修改配置
                 └──────────────┘
```

### ifup 完整流程

```c
static int <chip>_can_ifup(FAR struct netdev_lowerhalf_s *dev)
{
  struct <chip>_can_priv_s *priv = (struct <chip>_can_priv_s *)dev;

  if (priv->state == CAN_STATE_OPERATIONAL)
    return OK;  /* 幂等 */

  /* Step 1: 使能 CAN 模块（多核共享时需要引用计数） */
  enable_can_module(priv);

  /* Step 2: 初始化 CAN 节点（波特率、采样点、帧模式） */
  init_can_node(priv);

  /* Step 3: 配置硬件过滤器 */
  setup_hw_filters(priv);

  /* Step 4: 配置并使能中断 */
  setup_interrupt_lines(priv);
  up_enable_irq(priv->config->tx_irq);
  up_enable_irq(priv->config->rx_irq);
  up_enable_irq(priv->config->err_irq);
  enable_hw_interrupts(priv);

  /* Step 5: 进入 OPERATIONAL 模式 */
  priv->state = CAN_STATE_OPERATIONAL;
  priv->txmb_sflags = 0;

  /* Step 6: 通知链路就绪 */
  netdev_lower_carrier_on(dev);

  return OK;
}
```

### ifdown 完整流程

```c
static int <chip>_can_ifdown(FAR struct netdev_lowerhalf_s *dev)
{
  struct <chip>_can_priv_s *priv = (struct <chip>_can_priv_s *)dev;

  /* Step 1: 通知链路断开 */
  netdev_lower_carrier_off(dev);

  /* Step 2: 停止 CAN 控制器（进入 STOPPED 模式） */
  setmode(priv, CAN_STATE_STOPPED);

  /* Step 3: 取消错误轮询 worker */
#ifdef CONFIG_<CHIP>_CAN_ERROR_POLLING
work_cancel(HPWORK, &priv->errwork);
#endif

  /* Step 4: 释放 pending TX pkt（防泄漏） */
  flush_txbuff(priv);

  /* Step 5: 减少模块引用计数，必要时禁用模块 */
  disable_can_module_if_last(priv);

  return OK;
}
```

---

## 八、错误处理

### 8.1 错误帧构造（`CONFIG_NET_CAN_ERRORS`）

需要 `select NET_CAN_HAVE_ERRORS` 并开启 `CONFIG_NET_CAN_ERRORS=y`。

CAN 上报 error message 流程与普通报文上报流程基本一样，使用 `struct can_frame` 来上报具体错误信息。

CAN 错误帧通过 `receive()` 回调返回给上层，帧格式符合 Linux SocketCAN 标准：

```c
static uint8_t <chip>_errhandle(struct <chip>_can_priv_s *priv, uint8_t *buf)
{
  struct can_frame *frame = (struct can_frame *)buf;
  uint16_t errbits = 0;
  uint8_t data[CAN_ERR_DLC];

  memset(data, 0, sizeof(data));

  /* Bus-off */
  if (hw_busoff_status(priv))
    errbits = CAN_ERR_BUSOFF;

  /* 协议错误 */
  switch (hw_last_error_code(priv))
    {
      case STUFF_ERROR:  data[2] |= CAN_ERR_PROT_STUFF; errbits |= CAN_ERR_PROT; break;
      case FORM_ERROR:   data[2] |= CAN_ERR_PROT_FORM;  errbits |= CAN_ERR_PROT; break;
      case ACK_ERROR:    errbits |= CAN_ERR_ACK; break;
      case BIT1_ERROR:   data[2] |= CAN_ERR_PROT_BIT1;  errbits |= CAN_ERR_PROT; break;
      case BIT0_ERROR:   data[2] |= CAN_ERR_PROT_BIT0;  errbits |= CAN_ERR_PROT; break;
      case CRC_ERROR:    data[3] |= CAN_ERR_PROT_LOC_CRC_SEQ; errbits |= CAN_ERR_PROT; break;
    }

  /* Error warning / Error passive */
  if (hw_tx_error_count(priv) >= 96)
    { data[1] |= CAN_ERR_CRTL_TX_WARNING; errbits |= CAN_ERR_CRTL; }
  if (hw_rx_error_count(priv) >= 96)
    { data[1] |= CAN_ERR_CRTL_RX_WARNING; errbits |= CAN_ERR_CRTL; }
  if (hw_is_error_passive(priv))
    { data[1] |= CAN_ERR_CRTL_TX_PASSIVE; errbits |= CAN_ERR_CRTL; }

  if (errbits != 0)
    {
      frame->can_id = errbits | CAN_ERR_FLAG;
      frame->can_dlc = CAN_ERR_DLC;
      memcpy(frame->data, data, CAN_ERR_DLC);
      NETDEV_ERRORS(&priv->dev.netdev);
      return CAN_ERR_DLC;
    }

  return 0;
}
```

### 8.2 Bus-Off 恢复

```c
static int <chip>_busoff_recovery(struct <chip>_can_priv_s *priv)
{
  /* Step 1: 刷新所有 pending TX buffer */
  flush_txbuff(priv);

  /* Step 2: 清除 TX 完成中断标志 */
  hw_clear_tx_complete_flag(priv);

  /* Step 3: 退出初始化模式，进入正常模式 */
  hw_exit_init_mode(priv);

  /* Step 4: 通知链路恢复 */
  netdev_lower_carrier_on(&priv->dev);

  return OK;
}
```

### 8.3 错误轮询模式

定期检查 CAN 控制器错误状态（适用于需要主动上报错误计数器变化的场景）：

```c
static void <chip>_errpolling(FAR void *arg)
{
  struct <chip>_can_priv_s *priv = arg;

  if (priv->state != CAN_STATE_OPERATIONAL)
    return;

  priv->err_pending |= ERR_POLLING_PENDING;
  netdev_lower_rxready((struct netdev_lowerhalf_s *)priv);

  work_queue(HPWORK, &priv->errwork, <chip>_errpolling,
             priv, MSEC2TICK(CONFIG_CAN_ERROR_POLLING_CYCLE));
}
```

---

## 九、设备注册

### 完整注册流程

```c
int <chip>_socket_init_can(struct net_driver_s **dev,
                            struct <chip>_can_config_s *config,
                            size_t num)
{
  struct <chip>_can_priv_s *priv;
  int ret;

  /* 可选：配置 CAN 模块时钟 */
  setup_can_clock();

  for (int i = 0; i < num; i++)
    {
      /* 如有多核归属策略，请在 vendor-specific 字段中自行扩展并过滤 */

      /* Step 1: 分配私有结构（kmm_zalloc 自动清零） */
      priv = kmm_zalloc(sizeof(struct <chip>_can_priv_s));
      if (priv == NULL)
        return -ENOMEM;

      /* Step 2: 分配 TX pending 追踪数组 */
      priv->tx_pkt_pending = kmm_zalloc(sizeof(netpkt_t *) * tx_buf_count);
      if (priv->tx_pkt_pending == NULL)
        { kmm_free(priv); return -ENOMEM; }

      /* Step 3: 填充下半区结构 */
      priv->config = &config[i];
      priv->dev.ops = &g_<chip>_can_ops;

      /* RX 模式选择 */
#ifdef CONFIG_<CHIP>_XXXCAN_ISR_WQUEUE
      priv->dev.rxtype = NETDEV_RX_DIRECT;
#else
      priv->dev.rxtype = NETDEV_RX_THREAD;
#endif

      /* TX/RX quota */
      priv->dev.quota[NETPKT_TX] = tx_buf_count;
      priv->dev.quota[NETPKT_RX] = 1;

      /* CAN loopback 默认开启 */
      IFF_SET_LOOPBACK(priv->dev.netdev.d_flags);

      /* 可选：配置 transceiver STB pin */
      setup_transceiver_pin(priv);

      /* Step 4: 注册网络设备 — NET_LL_CAN */
      snprintf(priv->dev.netdev.d_ifname, IFNAMSIZ, "can%d", config[i].intf);
      ret = netdev_lower_register(&priv->dev, NET_LL_CAN);
      if (ret < 0)
        { kmm_free(priv->tx_pkt_pending); kmm_free(priv); return ret; }

      /* Step 5: 注册中断（注册时先禁用） */
      up_disable_irq(config[i].tx_irq);
      up_disable_irq(config[i].rx_irq);
      up_disable_irq(config[i].err_irq);

      irq_attach(config[i].tx_irq, <chip>_tx_interrupt, priv);
      irq_attach(config[i].rx_irq, <chip>_rx_interrupt, priv);
      irq_attach(config[i].err_irq, <chip>_err_interrupt, priv);

      priv->state = CAN_STATE_SLEEP;
      dev[config[i].intf] = &priv->dev.netdev;
    }

  return OK;
}
```

**注册 checklist**：
- [ ] 使用 `NET_LL_CAN`（不是 `NET_LL_ETHERNET`）
- [ ] 接口名 `can%d`（不是 `eth%d`）
- [ ] `quota[NETPKT_TX]` = 硬件 TX buffer 数量
- [ ] `quota[NETPKT_RX]` = 1（CAN 逐帧处理）
- [ ] `IFF_SET_LOOPBACK` 开启 loopback（SocketCAN 默认行为）
- [ ] 中断先 `up_disable_irq` 再 `irq_attach`
- [ ] 初始状态为 `CAN_STATE_SLEEP`

---

## 十、高级特性

### 10.1 CAN FD 支持

**编译时选择帧类型**：
```c
#ifdef CONFIG_NET_CAN_CANFD
  FAR struct canfd_frame *frame = ...;
  frame->flags |= CANFD_FDF;  /* FD Format */
  frame->flags |= CANFD_BRS;  /* Bitrate Switch */
  frame->len = can_dlc2bytes(dlc);  /* 0-64 bytes */
#else
  FAR struct can_frame *frame = ...;
  frame->can_dlc = dlc;  /* 0-8 bytes */
#endif
```

### 10.2 CAN 硬件过滤器

在 ifup 中配置硬件过滤器，减少 CPU 中断负载：

```c
static void <chip>_setup_filters(struct <chip>_can_priv_s *priv)
{
  /* 1. 非匹配帧处理策略（通常 reject） */
  hw_set_nonmatching_filter(priv, REJECT);

  /* 2. 配置 RX FIFO 过滤器 */
  for (int i = 0; i < priv->config->rxfifo0_filter_cnt; i++)
    hw_set_filter(priv, FIFO0, &priv->config->rxfifo0_filter[i]);

  /* 3. 配置 Dedicated RX buffer 过滤器 */
  for (int i = 0; i < priv->config->rxbuf_filter_cnt; i++)
    hw_set_filter(priv, DEDICATED_BUF, &priv->config->rxbuf_filter[i]);
}
```

### 10.3 ioctl 命令

CAN SocketCAN 驱动支持的 ioctl 命令：

```kconfig
CONFIG_NETDEV_IOCTL=y
CONFIG_NETDEV_CAN_FILTER_IOCTL=y
CONFIG_NET_CANPROTO_OPTIONS=y
```

```c
static int <chip>_can_ioctl(FAR struct netdev_lowerhalf_s *dev, int cmd,
                             unsigned long arg)
{
  switch (cmd)
    {
      case SIOCGCANBITRATE:     /* 读取 CAN 控制器 bitrate */
        return <chip>_get_bitrate(priv, (FAR struct canioc_bittiming_s *)arg);

      case SIOCSCANBITRATE:     /* 设置 CAN 控制器 bitrate */
        return <chip>_set_bitrate(priv, (FAR const struct canioc_bittiming_s *)arg);

      case SIOCACANEXTFILTER:   /* 添加硬件扩展 ID filter */
        return <chip>_add_extfilter(priv, (FAR const struct canioc_extfilter_s *)arg);

      case SIOCDCANEXTFILTER:   /* 删除硬件扩展 ID filter */
        return <chip>_del_extfilter(priv, (FAR const struct canioc_extfilter_s *)arg);

      case SIOCACANSTDFILTER:   /* 添加硬件标准 ID filter */
        return <chip>_add_stdfilter(priv, (FAR const struct canioc_stdfilter_s *)arg);

      case SIOCDCANSTDFILTER:   /* 删除硬件标准 ID filter */
        return <chip>_del_stdfilter(priv, (FAR const struct canioc_stdfilter_s *)arg);

      case SIOCCANRESTART:      /* bus-off 恢复 */
        return <chip>_busoff_recovery(priv);

      case SIOCSCANSTATE:       /* 设置 CAN 控制器状态 */
        return <chip>_setmode(priv, state->state);

      case SIOCGCANSTATE:       /* 获取 CAN 控制器状态 */
        return <chip>_getmode(priv, &state->state);

      case SIOCCANOFLUSH:       /* 刷新所有 TX buffer */
        return <chip>_flush_txbuff(priv);

      case SIOCGCANTRSVSTATE:   /* 获取 CAN transceiver 状态 */
      case SIOCSCANTRSVSTATE:   /* 设置 CAN transceiver 状态 */
        return transceiver_control(priv, cmd, arg);
    }

  return -ENOTTY;
}
```

### 10.4 多核共享 CAN 模块

当多个核心共享同一个 CAN 模块时（如 AURIX 的 CAN0 有 4 个节点分配给不同核心），需要引用计数管理模块使能/禁用：

```c
/* 使能模块（ifup 中） */
irq_mask = spin_lock_irqsave(module_lock);
module_ref_count++;
if (module_ref_count == 1 && !is_module_enabled(can))
  enable_module(can);
spin_unlock_irqrestore(module_lock, irq_mask);

/* 禁用模块（ifdown 中） */
irq_mask = spin_lock_irqsave(module_lock);
module_ref_count--;
if (module_ref_count == 0 && is_module_enabled(can))
  disable_module(can);
spin_unlock_irqrestore(module_lock, irq_mask);
```

### 10.5 RX 时间戳

```c
/* 在 receive 回调中添加时间戳 */
#ifdef CONFIG_NET_TIMESTAMP
  if (get_mac_time(&pkt->io_time) != OK)
    nwarn("Get timestamp failed\n");
#endif

/* ifup 中声明支持时间戳 */
priv->dev.netdev.d_features |= NETDEV_RX_STAMP;
```

---

## 十一、Bring-Up 验证 Checklist

### 五、CAN 测试（参考）

建议先确认用户态测试工具配置：

```kconfig
CONFIG_CANUTILS_CANDUMP=y
CONFIG_CANUTILS_CANSEND=y
```

基础命令：

- 抓包：`candump can0`
- 发送：`cansend can0 123#DEADBEEF`
- 过滤器测试：`candump can1,123:C00007FF`
- 错误帧测试：`candump -e can1,#00000004`

### 编译阶段

- [ ] 编译通过，无 warning
- [ ] `nuttx/tools/checkpatch.sh -f <driver>.c` 代码风格通过
- [ ] `static_assert` 验证 IOB 大小足够

### 基础功能

- [ ] `ifup can0` 成功
- [ ] `ifconfig` 显示 can0 接口
- [ ] 使用 `cansend` 工具发送帧：`cansend can0 123#DEADBEEF`
- [ ] 使用 `candump` 工具接收帧：`candump can0`
- [ ] 对端设备发送帧，本端 `candump` 正确接收
- [ ] 本端发送帧，对端接收正确

### CAN FD（如果启用）

- [ ] 发送 CAN FD 帧（64 字节数据）
- [ ] 接收 CAN FD 帧
- [ ] `CANFD_BRS`（Bitrate Switch）正常工作

### 过滤器

- [ ] 设置 `CAN_RAW_FILTER` 后只收到匹配帧
- [ ] 默认 catch-all 过滤器接收所有帧

### 错误处理

- [ ] 断开 CAN bus，检测到 bus-off
- [ ] `SIOCCANRESTART` ioctl 恢复 bus-off
- [ ] `CONFIG_NET_CAN_ERRORS` 启用时，收到错误帧

### 状态管理

- [ ] `SIOCSCANSTATE` 切换 OPERATIONAL / STOPPED / SLEEP
- [ ] `SIOCGCANSTATE` 返回正确状态
- [ ] 多次 `ifdown` + `ifup` 循环无异常

### 压力测试

- [ ] 高速持续收发（500kbps / 2Mbps FD）无帧丢失
- [ ] 长时间运行无内存泄漏
- [ ] bus-off 恢复后继续正常收发
- [ ] TX buffer 满时返回 `-EBUSY`，恢复后正常发送

### 多核补充（如适用）

- [ ] 不同核心的 CAN 节点独立工作
- [ ] 共享模块的引用计数正确
- [ ] 一个核心 ifdown 不影响其他核心

---

## 十二、常见运行时问题排查

### 发送失败返回 -EBUSY

所有 TX buffer 都在使用中。原因可能是：
1. CAN bus 未连接或 bus-off → 检查 `ifconfig` 中 carrier 状态
2. 对端无 ACK → 单节点测试需开启 loopback 模式
3. TX 完成中断未正确触发 → 检查中断配置和 `netdev_lower_txdone` 调用

### candump 收不到帧

1. 检查 `ifup can0` 是否成功
2. 检查 carrier 状态 → `netdev_lower_carrier_on` 是否被调用
3. 检查 RX 中断是否触发 → 在 ISR 中加 `caninfo` 日志
4. 检查硬件过滤器 → 是否拒绝了所有帧
5. 检查 `receive()` 回调 → 是否正确读取硬件 FIFO/buffer

### 帧数据错误

1. 检查字节序 → CAN ID 处理是否正确考虑标准/扩展帧
2. 检查 DLC → `can_dlc2bytes()` / `can_bytes2dlc()` 转换
3. 检查数据拷贝 → 确认从正确的硬件地址读取

### 内存泄漏

1. TX pending pkt 是否在所有路径都被释放（正常 TX confirm + flush + ifdown）
2. RX alloc 失败时是否正确处理（不能 continue，必须 break）
3. `netpkt_free` 的 type 参数是否正确（TX pkt 用 `NETPKT_TX`，RX 用 `NETPKT_RX`）

### Bus-off 循环

CAN 控制器频繁进入 bus-off：
1. 检查波特率配置 → 两端必须一致
2. 检查总线终端电阻 → CAN bus 两端各 120Ω
3. 检查 transceiver → STB pin 是否正确拉低（Normal 模式）

---

## 十三、Cross References

### 本文档依赖

| 文档 | 用途 |
|------|------|
| `SKILL.md` | 主入口：Driver Type Dispatch Table、通用开发流程 |
| `eth_netdev_pattern.md` | Ethernet netdev 参考（对比 CAN netdev 差异） |
| `coding_rules.md` | 编码规范、内核 API、中断规则、work_queue 选择 |
| `board_registration.md` | 板级注册模式、defconfig 配置 |

### 关键头文件

| 头文件 | 内容 |
|--------|------|
| `include/nuttx/net/netdev_lowerhalf.h` | `netdev_ops_s`、`netdev_lowerhalf_s`、NetPKT API |
| `include/nuttx/can.h` | CAN 帧定义、CAN ID 掩码、错误帧定义 |
| `include/nuttx/net/can.h` | SocketCAN 网络接口定义 |

### 参考驱动

| 驱动 | 路径 | 说明 |
|------|------|------|
| STM32 FDCAN | `arch/arm/src/stm32/stm32_fdcan_sock.c` | STM32 FDCAN SocketCAN 参考 |
| STM32 bxCAN | `arch/arm/src/stm32/stm32_can_sock.c` | STM32 bxCAN SocketCAN 参考 |
| i.MX RT FlexCAN | `arch/arm/src/imxrt/imxrt_flexcan.c` | NXP FlexCAN SocketCAN 参考 |
| S32K3 FlexCAN | `arch/arm/src/s32k3xx/s32k3xx_flexcan.c` | NXP S32K3 SocketCAN 参考 |

### SocketCAN 协议栈源码

| 文件 | 用途 |
|------|------|
| `net/can/can_sockif.c` | Socket 接口（setup, bind, close, poll） |
| `net/can/can_sendmsg.c` | TX 路径 |
| `net/can/can_recvmsg.c` | RX 路径 |
| `net/can/can_input.c` | 帧分发到 socket |
| `net/can/can_conn.c` | 连接管理与过滤 |
| `net/can/can_setsockopt.c` | Socket 选项（过滤器配置等） |
