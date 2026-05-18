# LIN SocketCAN Network Driver Pattern — LIN 驱动网络子系统适配框架

## 1. 框架概述

在 NuttX 中，LIN (Local Interconnect Network) 协议栈通过复用 SocketCAN 框架进行实现。这是一种非常有效的设计，使得上层应用程序可以使用标准的 `socket(PF_CAN, SOCK_RAW, CAN_RAW)` 接口，无缝地操作底层 LIN 总线。

LIN 驱动注册为 `NET_LL_CAN` 链路层类型。虽然底层物理总线和协议（单线、主从、基于调度的帧）与 CAN 有显著差异，但它们共享相同的核心数据结构 `struct can_frame`（载荷最大 8 字节）。网络接口命名约定为 `lin%d`，以便与标准的 `can%d` 接口区分。

### LIN 与 CAN 特性对比表

| 特性 | CAN (Controller Area Network) | LIN (Local Interconnect Network) | 适配框架差异点 |
| :--- | :--- | :--- | :--- |
| 通信模式 | 多主、全双工 (CSMA/CD+AMP) | 单主、多从、半双工 | LIN 必须严格区分 Master 与 Slave 模式的代码路径 |
| 传输速率 | 高达 1 Mbps (Classic CAN) | 最高 20 kbps | LIN 传输更慢，极易导致 TX 队列满，需注意队列深度 |
| 物理连线 | 双绞线 (CAN_H, CAN_L) | 单线 (12V) 加地线 | 物理层收发器控制（如唤醒、休眠）有所不同 |
| 标识符 | 11-bit 或 29-bit | 6-bit (PID, 0-63) | LIN 仅使用 `can_id` 的低 6 位作为 PID |
| 载荷长度 | 0-8 字节 (FD 支持 64) | 0-8 字节 | LIN 驱动中必须拒绝所有 CAN FD 帧 (`canfd_frame`) |
| MAC 仲裁 | 硬件仲裁 | Master 节点调度表 | 发送逻辑不同（Master 触发报头，Slave 被动应答） |

## 2. 开发准备

在进行 LIN 驱动开发之前，需确保内核 Kconfig 与网络缓冲区已正确配置，同时文件组织符合内核规约。

### Kconfig 依赖项

驱动需依赖以下内核配置：
*   `CONFIG_NET_CAN`: 开启 SocketCAN 协议栈支持。
*   `CONFIG_NETDEVICES`: 启用网络设备注册机制。
*   `CONFIG_NETDEV_IOCTL`: 允许用户态通过 `ioctl()` 配置底层网络接口。
*   `CONFIG_NETDEV_CAN_STATE_IOCTL`: 启用 CAN/LIN 特定的状态控制 (休眠、唤醒、错误状态读取)。

### IOB 缓冲区约束 (IOB2 Config)

NuttX 网络栈使用 IOB (I/O Buffer) 机制。LIN 帧通过 `struct can_frame`（16 字节）传输，同时驱动需兼容 `struct canfd_frame`（72 字节）以支持协议栈的统一帧格式处理（尽管 LIN 实际载荷不超过 8 字节）。
为了优化内存利用率，避免使用大尺寸的 `IOB_BUFSIZE1` 造成浪费，我们通常利用次级 IOB：
*   `CONFIG_IOB_BUFSIZE2`: 配置为 96 字节。
此大小可同时容纳 `struct can_frame`（16 字节）和 `struct canfd_frame`（72 字节）加上 `CONFIG_NET_LL_GUARDSIZE` 开销。

```c
static_assert(CONFIG_IOB_BUFSIZE2 >= sizeof(struct canfd_frame) +
              CONFIG_NET_LL_GUARDSIZE, "IOB2 buffer too small for canfd_frame");
```

### 文件组织规范

LIN 驱动应放置于特定芯片架构的网卡驱动目录中：
*   驱动源码：`arch/<arch>/src/<chip>/<chip>_lin.c`
*   头文件：`arch/<arch>/src/<chip>/<chip>_lin.h`

## 3. 关键数据结构

LIN 驱动的内部管理需要一个严谨的私有结构体。以下是必须包含的标准 Pattern。

### Ringbuffer (RX 接收环形队列)

因为 LIN 中断优先级较高且不能执行耗时操作，驱动必须使用轻量级的环形缓冲区在中断和工作队列之间传递接收到的帧。

```c
#define POOL_SIZE 3

struct frame_ringbuffer_s
{
  uint16_t         head;
  uint16_t         tail;
  struct can_frame buffer[POOL_SIZE];
};

static inline void ringbuffer_init(struct frame_ringbuffer_s *rb)
{
  rb->head = 0;
  rb->tail = 0;
}

static inline bool ringbuffer_is_empty(struct frame_ringbuffer_s *rb)
{
  return rb->head == rb->tail;
}

static inline bool ringbuffer_is_full(struct frame_ringbuffer_s *rb)
{
  return ((rb->tail + 1) % POOL_SIZE) == rb->head;
}

static inline struct can_frame *ringbuffer_get_write_ptr(struct frame_ringbuffer_s *rb)
{
  if (ringbuffer_is_full(rb))
    {
      return NULL;
    }

  return &rb->buffer[rb->tail];
}

static inline void ringbuffer_commit_write(struct frame_ringbuffer_s *rb)
{
  rb->tail = (rb->tail + 1) % POOL_SIZE;
}

static inline struct can_frame *ringbuffer_get_read_ptr(struct frame_ringbuffer_s *rb)
{
  if (ringbuffer_is_empty(rb))
    {
      return NULL;
    }

  return &rb->buffer[rb->head];
}

static inline void ringbuffer_commit_read(struct frame_ringbuffer_s *rb)
{
  rb->head = (rb->head + 1) % POOL_SIZE;
}
```

### 私有上下文结构体 (Private Struct)

注意 `frame_cache[0]` 的灵活数组设计。Master 节点不需要此缓存，而 Slave 节点需要在初始化时分配额外的内存 (`64 * sizeof(struct can_frame)`) 来存储所有的被动响应数据。

```c
struct <chip>_lin_priv_s
{
  /* MUST be first to allow casting between netdev_lowerhalf_s and priv */
  struct netdev_lowerhalf_s        dev;

  /* Immutable board configuration */
  const struct <chip>_lin_config_s *config;

  /* RX frame ring buffer */
  struct frame_ringbuffer_s        rx_frames;

  /* Master TX pending packet */
  netpkt_t                        *tx_pkt_pending;

  /* Interface status */
  bool                             bifup;

  /* Synchronization */
  spinlock_t                       lock;

  /* Controller State Machine */
  uint8_t                          state;
  uint8_t                          event;

  /* Work queue for deferred processing */
  struct work_s                    delaywork;

  /* Error reporting flag */
  bool                             reported_error;

  /* Slave mode flexible response cache (PID 0-63).
   * Not allocated for Master instances.
   */
  struct can_frame                 frame_cache[0];
};
```

### 板级配置结构体 (Config Struct)

此结构体由各单板初始化代码（board config）通过常量传入。

```c
struct <chip>_lin_config_s
{
  uint8_t   intf;      /* Interface index (e.g., 0 for lin0) */
  uint16_t  rx_irq;    /* Receive IRQ number */
  uint16_t  tx_irq;    /* Transmit IRQ number */
  uint16_t  ex_irq;    /* Error/Exception IRQ number */
  bool      master;    /* True if Master node, False if Slave node */
  /* vendor-specific configurations by integrator can follow */
};
```

## 4. 核心 API

### 网络设备操作集 (netdev_ops_s)

网络子系统通过 `netdev_ops_s` 注册的回调函数与 LIN 底层交互。

```c
static const struct netdev_ops_s g_<chip>_lin_ops =
{
  .ifup     = <chip>_lin_ifup,       /* Turn on the interface */
  .ifdown   = <chip>_lin_ifdown,     /* Turn off the interface */
  .transmit = <chip>_lin_transmit,   /* Transmit a packet */
  .receive  = <chip>_lin_receive,    /* Receive a packet */
#ifdef CONFIG_NETDEV_IOCTL
  .ioctl    = <chip>_lin_ioctl,      /* IO control operations */
#endif
};
```

### 关键网络栈函数说明

*   `netdev_lower_register(dev, NET_LL_CAN)`: 在初始化时调用，指定此设备为类 CAN 的链路层。
*   `netdev_carrier_on(dev)` / `netdev_carrier_off(dev)`: 在 `ifup` / `ifdown` 时调用，通知协议栈载波状态。
*   `netdev_rxready(dev)`: 在底层接收到新帧并存入 Ringbuffer 后，通过工作队列调用，触发协议栈拉取数据。
*   `netdev_txdone(dev)`: 在 TX 完成（主节点）或出现错误时调用，释放设备的 TX 锁，允许协议栈下发下一帧。
*   `netpkt_alloc`, `netpkt_free`, `netpkt_getdata`, `netpkt_setdatalen`: 用于从协议栈内存池分配和管理数据包。

## 5. 数据收发模式

LIN 通信是半双工、单插槽（Single Slot）通信。驱动必须处理 Master 和 Slave 截然不同的通信行为。

### Master 节点发送逻辑 (Master Transmit)

Master 控制总线调度。它发送帧头（Header），然后自己发送响应数据，或者等待 Slave 提供响应数据。

步骤说明：
1. 检查 `txready` (判断 `state == CAN_STATE_OPERATIONAL`)。
2. 过滤非标准 `can_frame`（LIN 不支持 FD，因此 `pktlen != CAN_MTU` 时需丢弃）。
3. 提取 PID (`frame->can_id & 0x3F`)。
4. 计算数据长度 `data_length` (根据标准，PID 最高位可能包含校验类型，DLC 在 `frame->can_dlc` 中)。
5. 将网络包指针保存在 `tx_pkt_pending`，以防函数提前退出导致内存泄漏。
6. 将状态机切换为 `BUSY`。
7. 判断 `frame->can_id` 是否包含 `LIN_RTR_FLAG`：
   * 如果包含，配置硬件发送 Header 并配置接收模式（等待 Slave 响应）。
   * 如果不包含，配置硬件发送 Header 及 Data。
8. 启动硬件发送序列。

```c
static int <chip>_lin_transmit_master(struct <chip>_lin_priv_s *priv, struct can_frame *frame)
{
  /* Setup hardware for TX... */
  /* If frame->can_id & LIN_RTR_FLAG, setup RX mode after Header */

  /* Trigger hardware transmission */
  return OK;
}
```

### Slave 节点发送逻辑 (Slave Transmit)

Slave 节点被动响应。应用层通过 socket 发送数据包到 Slave 时，底层**不应立即在物理总线上发送数据**，而是将数据更新到本地的 `frame_cache` 中。
当 Master 节点发出对应 PID 的帧头时，Slave 硬件自动从缓存中提取并发送应答。

步骤说明：
1. 从 `can_id` 提取 PID (`0` ~ `63`)。
2. 更新对应索引的 `frame_cache[pid]`。
3. **重要**：因为数据只是放入缓存，无需等待物理层发送完毕，应立即释放 `netpkt` 并返回。

```c
static int <chip>_lin_transmit_slave(struct <chip>_lin_priv_s *priv, struct can_frame *frame)
{
  uint8_t pid = frame->can_id & LIN_ID_MASK;

  /* Update slave response cache for this PID */
  DEBUGASSERT(pid < LIN_SLAVE_CACHE_NUM);
  memcpy(&priv->frame_cache[pid], frame, sizeof(struct can_frame));

  /* Return OK immediately, upper layer (or transmit wrapper) will free the packet */
  return OK;
}
```

### 接收逻辑 (Receive - 从 Ringbuffer 出队)

当协议栈收到 `netdev_rxready` 通知后，会调用 `receive` 回调。我们从此前提到的 `rx_frames` 环形队列中安全出队。

```c
static netpkt_t *<chip>_lin_receive(struct netdev_lowerhalf_s *dev)
{
  struct <chip>_lin_priv_s *priv = (struct <chip>_lin_priv_s *)dev;
  struct can_frame *frame;
  netpkt_t *pkt = NULL;
  irqstate_t flags;

  flags = spin_lock_irqsave(&priv->lock);
  frame = ringbuffer_get_read_ptr(&priv->rx_frames);
  if (frame != NULL)
    {
      /* Allocate packet from NuttX network pool */
      pkt = netpkt_alloc(dev, NETPKT_RX);
      if (pkt != NULL)
        {
          /* Copy data and set length */
          memcpy(netpkt_getdata(dev, pkt), frame, CAN_MTU);
          netpkt_setdatalen(dev, pkt, CAN_MTU);

          /* Safely advance the read pointer */
          ringbuffer_commit_read(&priv->rx_frames);
        }
    }
  spin_unlock_irqrestore(&priv->lock, flags);

  return pkt;
}
```

### 发送确认 (TX Confirm - Master 模式)

当 Master 完成一次带有数据的 Header+Data 传输时，应该生成一个发送确认（Transmission Confirmation, TCF）反馈给 Socket 层（以便清除本地的发送队列阻塞）。

步骤：
1. 构造一个本地回环帧：包含原先的 PID，并加上 `LIN_TCF_FLAG`。DLC = 0。
2. 写入 `rx_frames` 环形队列。
3. 调度工作队列调用 `netdev_rxready` 和 `netdev_txdone`。
4. 释放 `priv->tx_pkt_pending`。

## 6. 中断处理

中断处理程序运行在异常上下文中，因此绝对不能进行内存分配、休眠或调用上层网络协议栈 API。必须采用 `irq_attach_wqueue` 工作队列推迟处理机制。

### 中断处理类型划分

*   **RX Handler**:
    *   **Master_RX**: Master 发送 Header 请求（RTR），Slave 回复数据后，Master 硬件触发 RX 中断。
    *   **Slave_RX**: Master 发出 Header 和 Data，Slave 接收完整帧后触发。
    *   将收到的数据写入 `rx_frames`，通过 `work_queue` 通知上层。

*   **TX Handler**:
    *   **Master_TX_Done**: Master 成功发送了完整的 Header+Data。此时触发 TX Confirm 逻辑。
    *   **Slave_TX_Done**: Slave 响应了 Master 的 Header。通常可配置中断，但不强求将其上传至网络栈。

*   **Error Handler**:
    *   读取硬件错误状态寄存器（如校验和错误、位错误、帧错误）。
    *   构造标准的 LIN 错误帧（详见第 8 节）。
    *   压入 Ringbuffer 以通知应用层诊断进程。

```c
/* Example of interrupt work queue processing */
static void <chip>_lin_interrupt_work(void *arg)
{
  struct <chip>_lin_priv_s *priv = (struct <chip>_lin_priv_s *)arg;

  /* Take lock */
  /* If RX ringbuffer is not empty -> netdev_rxready(&priv->dev) */
  /* If TX just completed -> netpkt_free(priv->dev, priv->tx_pkt_pending); netdev_txdone(&priv->dev) */
}
```

## 7. LIN 控制器状态管理

网络协议要求对物理控制器的状态进行精确管理。NuttX 为 CAN/LIN 定义了一组状态机，必须在驱动内维护 `priv->state` 变量。

### ASCII 状态机转换图

```text
                 ┌──────────────┐
                 │   STOPPED    │ ← ifdown / 模块未初始化
                 └──────┬───────┘
                        │ ifup
                        ▼
                 ┌──────────────┐
                 │    SLEEP     │ ← idle timer / sleep frame
                 └──────┬───────┘
                        │ wakeup / ioctl
                        ▼
                 ┌──────────────┐
     error ←──   │ OPERATIONAL  │ ←── TX/RX done
                 └──────┬───────┘
                        │ transmit()
                        ▼
                 ┌──────────────┐
                 │    BUSY      │ ← master TX in progress
                 └──────┬───────┘
                        │ ioctl(sleep) from OPERATIONAL
                        ▼
                 ┌──────────────┐
                 │  SPENDING    │ ← sleep frame sending (master)
                 └──────────────┘
```

*   **STOPPED**: 模块未初始化或已被 `ifdown` 关闭。需要 `ifup` 才能进入工作状态。
*   **SLEEP**: 总线处于低功耗模式。收发器通常进入待机。LIN `ifup` 后初始状态为 SLEEP（与 CAN 不同）。
*   **OPERATIONAL**: 正常工作状态。准备好接收或发送。
*   **BUSY**: （仅 Master）已经下发硬件发送指令，正在等待中断返回。
*   **SPENDING**: （仅 Master）正在发送专用的 Sleep 帧（PID 0x3C），等待发送完成后进入 SLEEP。

## 8. 错误处理

标准的 SocketCAN API 对错误处理有严格规约。错误不仅要在底层被拦截，还需封装为特殊标志的 `can_frame` 上报。

### 错误帧构造规范

当硬件抛出异常时，构造如下帧：
```c
struct can_frame *frame = ringbuffer_get_write_ptr(&priv->rx_frames);
if (frame != NULL)
  {
    /* LIN_ERR_FLAG makes this an error frame */
    frame->can_id = pid | LIN_ERR_FLAG;
    frame->can_dlc = CAN_ERR_DLC; /* usually 8 */

    /* Clear payload */
    memset(frame->data, 0, CAN_ERR_DLC);

    frame->data[0] = error_class;      /* One of LIN_ERR_TX/RX/BUS/CTRL */
    frame->data[class_byte] = reason;  /* Specific bitmask from below */

    ringbuffer_commit_write(&priv->rx_frames);
  }
```

### 完整的 LIN 错误代码定义 (源自 nuttx/lin.h)

以下代码表在分析和编写底层硬件到 Socket 映射时必须严格遵循：

```c
/* Error Class (data[0]) */
#define LIN_ERR_TX      0x01    /* Error during transmission */
#define LIN_ERR_RX      0x02    /* Error during reception */
#define LIN_ERR_BUS     0x04    /* Bus error (e.g. physical layer) */
#define LIN_ERR_CTRL    0x08    /* Controller hardware error */

/* Error Reason Bits (data[1..4] depending on Class) */
/* TX Error Reasons */
#define LIN_ERR_TX_BIT_ERROR     0x01  /* Readback bit does not match TX */
#define LIN_ERR_TX_TIMEOUT       0x02  /* Transmission took too long */
#define LIN_ERR_TX_NO_RESP       0x04  /* Slave did not respond to Master RTR */

/* RX Error Reasons */
#define LIN_ERR_RX_CHECKSUM      0x01  /* Classic or Enhanced Checksum mismatch */
#define LIN_ERR_RX_FRAMING       0x02  /* Stop bit violation */
#define LIN_ERR_RX_SYNC          0x04  /* Sync field out of tolerance */
#define LIN_ERR_RX_PARITY        0x08  /* PID parity error */
#define LIN_ERR_RX_TIMEOUT       0x10  /* Frame inter-byte timeout */

/* Bus Error Reasons */
#define LIN_ERR_BUS_SHORT_GND    0x01  /* Bus stuck dominant */
#define LIN_ERR_BUS_SHORT_BAT    0x02  /* Bus stuck recessive */
```

### LIN 状态事件帧 (Event Frames)

通过 `LIN_EVT_FLAG` (0x10000000) 发送控制器的事件。当 `can_id` 带有此标志时，`data[0]` 用于表征特定的控制面事件：

```c
/* Events (data[0] when LIN_EVT_FLAG is set in can_id) */
#define LIN_EVT_WAKEUP          0x01  /* Master successfully sent a wakeup */
#define LIN_EVT_WAKEUP_PASSIVE  0x02  /* Slave received a wakeup pulse */
#define LIN_EVT_SLEEP           0x03  /* Node entered sleep state */
#define LIN_EVT_SLEEP_PASSIVE   0x04  /* Slave received Sleep command (0x3C) */
#define LIN_EVT_SLEEP_IDLE      0x05  /* Node slept due to idle timeout */
```

## 9. 设备注册

设备初始化期间（通常在板级 `board_initialize` 阶段调用），必须完成完整的申请、配置和注册。

```c
int <chip>_lin_initialize(const struct <chip>_lin_config_s *config)
{
  struct <chip>_lin_priv_s *priv;
  size_t alloc_size;

  /* 1. Calculate size with flexible array for Slaves */
  alloc_size = sizeof(struct <chip>_lin_priv_s);
  if (!config->master)
    {
      alloc_size += 64 * sizeof(struct can_frame); /* Frame cache for PID 0-63 */
    }

  /* 2. Allocate and zero memory */
  priv = kmm_zalloc(alloc_size);
  if (priv == NULL)
    {
      return -ENOMEM;
    }

  /* 3. Initialize fields */
  priv->config = config;
  priv->dev.ops = &g_<chip>_lin_ops;

  /* VERY IMPORTANT: Use direct RX to process synchronously in work queue */
  priv->dev.rxtype = NETDEV_RX_DIRECT;

  /* Quota configuration (typical for half-duplex LIN) */
  priv->dev.quota[NETPKT_TX] = 1;
  priv->dev.quota[NETPKT_RX] = 1;

  /* 4. Format network interface name (lin0, lin1, etc.) */
  snprintf(priv->dev.name, sizeof(priv->dev.name), "lin%d", config->intf);

  /* 5. Initialize locks and queues */
  spin_lock_init(&priv->lock);
  ringbuffer_init(&priv->rx_frames);

  /* 6. Register with network stack */
  int ret = netdev_lower_register(&priv->dev, NET_LL_CAN);
  if (ret < 0)
    {
      kmm_free(priv);
      return ret;
    }

  /* 7. Attach hardware IRQs */

  /* 方案 A: 直接 irq_attach */
  /* irq_attach(config->rx_irq, <chip>_lin_rx_interrupt, priv); */
  /* irq_attach(config->tx_irq, <chip>_lin_tx_interrupt, priv); */
  /* irq_attach(config->ex_irq, <chip>_lin_ex_interrupt, priv); */

  /* 方案 B: irq_attach_wqueue（线程化中断，推荐用于 LIN） */
  irq_attach_wqueue(config->rx_irq, NULL, <chip>_lin_rx_interrupt, priv,
                    CONFIG_<CHIP>_LIN_ISR_WQUEUE_PRIORITY);
  irq_attach_wqueue(config->tx_irq, NULL, <chip>_lin_tx_interrupt, priv,
                    CONFIG_<CHIP>_LIN_ISR_WQUEUE_PRIORITY);
  irq_attach_wqueue(config->ex_irq, NULL, <chip>_lin_ex_interrupt, priv,
                    CONFIG_<CHIP>_LIN_ISR_WQUEUE_PRIORITY);

  return OK;
}
```

## 10. 高级特性

### Master / Slave 架构细节

*   **Slave Cache Flexible Array**: 在 Slave 模式下，驱动启动时一次性分配好 64 个槽位的 `frame_cache`。当通过应用层写入数据时，标志通常包含 `LIN_CACHE_RESPONSE`。底层将其直接 `memcpy` 进此表并丢弃 `netpkt`。
*   **RTR Flag (Remote Transmission Request)**: 主节点下发请求时，`can_id` 带有 `LIN_RTR_FLAG` (0x40000000)。此时硬件应当只发送 Sync Break + Sync Byte + PID 字段，然后将 RX 端子打开，等待从节点或本机的从节点缓存进行响应。
*   **Single Response (一次性响应)**: 针对标志 `LIN_SINGLE_RESPONSE`，一旦该缓冲数据被消耗（硬件 TX 完成），应通过置空使其失效，直到下层应用重新补充。

### 休眠与唤醒机制 (Sleep / Wakeup)

LIN 标准具备完整的睡眠控制。

*   **进入休眠 (Go To Sleep)**: 主节点通过发送 PID `0x3C` 的特定格式帧使总线休眠。从节点通过识别到该命令进入休眠，并抛出 `LIN_EVT_SLEEP_PASSIVE` 事件。
*   **唤醒信号 (Wakeup Signal)**: 任意节点（不论主从）可通过拉低总线 250 微秒（显性电平脉冲）唤醒网络。
*   **空闲休眠 (Idle Timeout)**: 配合 NuttX 的低功耗工作队列 `LPWORK`，设置定时器，如果总线上无活动超过特定时间（通常为 4s 到 10s），触发底层进入休眠并抛出 `LIN_EVT_SLEEP_IDLE`。

### IOCTL 命令拦截

在 `receive` 轮询之外，应用层通过 ioctl 控制驱动行为。
*   `SIOCGCANSTATE`: 返回当前的控制器状态 (STOPPED/SLEEP/OPERATIONAL/BUSY/SPENDING)。
*   `SIOCSCANSTATE`: 传入 `CAN_STATE_SLEEP` 触发睡眠流程；传入 `CAN_STATE_OPERATIONAL` 触发唤醒序列。

### 物理层校验模式 (Checksum)

硬件通常具有两种校验寄存器配置：
*   **Classic Checksum**: 仅计算 Data 域。常用于诊断帧 (PID 0x3C, 0x3D) 及其它 LIN 1.x 节点。
*   **Enhanced Checksum**: 计算 PID + Data 域。LIN 2.x 标准。通常由底层驱动通过解析 `frame->can_id` 中的特定标志位，或维护一张静态路由表，在写入 TX FIFO 前配置硬件寄存器。

## 11. Bring-Up 验证 Checklist

在将新的 `<chip>_lin` 驱动推送到主线前，确保已验证以下核心场景：

1.  [ ] **Master 帧发送验证**: 发送标准数据帧，通过外部抓包仪确认 Sync, PID, Data, Enhanced Checksum 正确。
2.  [ ] **Master 请求验证 (RTR)**: 发送带 `LIN_RTR_FLAG` 的帧，验证从节点响应后，底层是否触发 `netdev_rxready`，应用层是否读取到完整数据。
3.  [ ] **Slave Cache 行为**: 向 Slave 注入数据后使用抓包仪发送 Header，验证 Slave 的硬件是否能够自主回复。
4.  [ ] **Sleep / Wakeup**: 触发睡眠命令后，验证 Master 节点 `priv->state` 经过 `SPENDING` → `SLEEP` 迁移，验证总线电平。测试唤醒脉冲是否能够恢复网络回到 `OPERATIONAL`。
5.  [ ] **Error Frames**: 短接 LIN 总线与 GND 触发总线错误，验证 Socket 客户端是否通过读取到 `LIN_ERR_FLAG` 帧获知错误。
6.  [ ] **Idle Timeout**: 无通信维持 10 秒，验证节点是否自动 fallback 进低功耗模式。

## 12. 常见运行时问题排查

开发和维护 LIN 驱动时常遇到以下问题，此列表提供故障分析指南：

*   **TX 状态卡死 (TX BUSY Forever)**
    *   *现象*: 应用程序调用 `write()` 阻塞，无法发出新的数据。
    *   *根因*: `netdev_txdone` 没有被调用。通常是由于 TX 完成中断丢失，或者在 Master 发送带数据帧时，缺少构建带有 `LIN_TCF_FLAG` 的回环帧逻辑导致的协议栈锁死。

*   **Slave 节点无法响应**
    *   *现象*: 抓包仪显示 Master Header 正常，但总线上无应答。
    *   *根因*: Slave 节点的 `frame_cache` 为空（应用层未及时填充），或者硬件过滤寄存器被错误配置阻挡了对应的 PID。

*   **Sleep 机制失效**
    *   *现象*: IOCTL 发送休眠命令，但设备迅速返回 OPERATIONAL。
    *   *根因*: 处于嘈杂总线上，频繁产生边沿跳变触发了唤醒。需确保在发送 0x3C 休眠帧期间，屏蔽外部触发的非预期 RX 中断。

*   **Ringbuffer 溢出警告 (Ringbuffer Full)**
    *   *现象*: 高负载下丢帧。
    *   *根因*: LIN 虽然速率低，但如果中断中调用 `work_queue` 排队效率跟不上，或上层 `recv()` 不及时，就会塞满 `POOL_SIZE` 为 3 的环形队列。建议根据可用内存适度增大 `POOL_SIZE`。

*   **唤醒失败 (Wakeup Failure)**
    *   *现象*: 外部设备拉低总线，但 MCU 无反应。
    *   *根因*: 休眠模式下未正确配置 RX 引脚为上升/下降沿唤醒中断源，导致物理脉冲无法触发 MCU 退出深度休眠。

## 13. Cross References

开发过程中，建议交叉参阅以下系统文档：

*   `can_netdev_pattern.md`: CAN/CAN FD 底层接口与 SocketCAN 框架通用说明，LIN 的很多基础概念衍生于此。
*   `coding_rules.md`: NuttX POSIX 规范和代码风格检查列表（必须过 `checkpatch.sh`）。
*   `board_registration.md`: 如何在 vendor/boards 目录下通过 `nuttx_netdev_register` 正确绑定驱动。
*   `nuttx/lin.h`: 内核态 LIN `can_id` 标志定义（包含 RTR, TCF, 错误类型，事件类型等标准宏）。
*   `nuttx/can.h`: `struct can_frame` 基础定义与网络接口结构。