# USB DCD 驱动适配常见陷阱与经验

在 vendor HAL 层之上适配 NuttX USB Device Controller 驱动时的通用经验。
源自 BES USB V2 重构实战，但规则适用于所有基于 vendor HAL 的 USB DCD 适配。

## 适用范围

### 通用 HAL 适配规则（适用于所有 vendor HAL 之上的 USB DCD 适配）

- §1 USB RESET 后重新初始化硬件 — 所有 USB 控制器 reset 后都可能丢失寄存器状态
- §2 USB 速度必须从硬件读取 — NuttX composite 框架依赖 `usbdev_s.speed` 选择描述符，与具体 HAL 无关
- §3 追踪完整的 USB 初始化调用链 — 嵌入式平台的 VBUS/PMU/BSP 调用链复杂度是通用问题
- §4 HAL 回调缺少上下文时用编译时 wrapper — 适用于任何回调签名不传 EP 编号的 HAL
- §5 Vendor SDK 外部构建的增量编译不可信 — 适用于所有 CMake ExternalProject 集成的 vendor SDK

### BES USB V2 特定经验（仅在使用 BES HAL 时直接适用，其他 HAL 可参考思路）

- §1 中提到的 DIEPMSK/DOEPMSK 寄存器名称是 DWC2/BES 特定的，其他控制器 IP 的等效寄存器名不同
- §4 中 `callbacks.epn_send_compl[ep-1]` 的回调数组索引方式是 BES HAL 特有的 API 设计
- §6 EP_SUBMIT 后的完成时序 — `hal_send_epn` 的 "EP busy" 行为和 `send_compl` 回调清除机制是 BES HAL 特定的；其他 HAL 的 DMA 完成通知机制可能不同，但"complete 时机与旧驱动保持一致"的原则是通用的

## 1. USB RESET 后必须重新初始化硬件

USB 控制器在收到 bus reset 后，部分寄存器会被硬件复位（如中断掩码、FIFO 配置）。驱动的 reset 处理函数中必须重新配置这些寄存器，即使看起来与初始化阶段的配置重复。

**典型现象**：EP 配置成功，OUT 方向正常，但 IN 方向 DMA 提交后永远不完成（无传输完成中断）。

**检查清单**：
- IN/OUT EP 中断掩码（DIEPMSK/DOEPMSK 或等效寄存器）是否在 reset 后被重新设置
- DMA 全局使能位是否仍然有效
- EP0 配置是否被恢复

**规则**：`usbreset` 处理中的硬件重初始化不可省略，即使与 `usbinitialize` 中的逻辑重复。

## 2. USB 速度必须从硬件读取，不能假设

USB 控制器的实际枚举速度由主机和设备协商决定，可能与驱动初始化时设置的目标速度不同。NuttX composite 框架根据 `usbdev_s.speed` 选择描述符（HS maxpacket=512 vs FS maxpacket=64）。

**典型现象**：
- 硬编码 Full Speed 但硬件枚举为 High Speed → 主机发 512 字节包，设备按 64 字节接收，数据截断
- 硬编码 High Speed 但硬件回退到 Full Speed → 描述符中 maxpacket=512 但实际只能传 64 字节

**规则**：在 EP0 SETUP 处理或枚举完成回调中，从硬件状态寄存器（如 DSTS.ENUMSPD）读取实际速度并更新 `priv->usbdev.speed`。如果 HAL 不提供速度查询 API，允许在适配层做最小的寄存器读取。

## 3. 追踪完整的 USB 初始化调用链

嵌入式平台的 USB 初始化往往不是简单的 `arm_usbinitialize` → 硬件初始化。实际调用链可能涉及：

```
BSP 入口 (nuttx_main / board_late_initialize)
  → VBUS/充电器检测回调注册
    → PMU 中断触发
      → USB 控制器初始化
        → composite 设备组装
          → class driver 绑定
```

**典型现象**：驱动编译通过但 USB composite 不枚举（设备节点不存在）。

**检查清单**：
- VBUS 检测回调是否在新配置下被注册（检查条件编译宏）
- 启动时 VBUS 已插入的场景是否有手动触发逻辑
- `board_composite_connect` 是否在正确的时机被调用

**规则**：重构 USB 驱动时，必须追踪从 BSP 入口到 HAL 回调注册的完整调用链，不能只看驱动文件本身。

## 4. HAL 回调缺少上下文时用编译时 wrapper

部分 vendor HAL 的 EP 完成回调签名不传入 EP 编号，而是通过回调数组索引隐含 EP 信息（`callbacks.epn_send_compl[ep-1]`）。

**错误方案**：所有 EP 注册同一个回调，运行时通过 data 指针反查 EP → 不可靠（DMA 地址可能与 buffer 地址不同）。

**正确方案**：用宏生成 per-EP thin wrapper，每个 wrapper 传入编译时确定的 EP 编号：

```c
#define DEFINE_EP_WRAPPER(n) \
  static bool ep##n##_compl(...) { return common_handler(n, ...); }
DEFINE_EP_WRAPPER(1)
DEFINE_EP_WRAPPER(2)
```

**规则**：当回调缺少上下文参数时，优先用编译时确定的 wrapper 而非运行时反查。

## 5. Vendor SDK 外部构建的增量编译不可信

当 vendor SDK 作为 NuttX 的外部构建步骤（CMake ExternalProject）时，NuttX 的 `.config` 变更不会自动触发 vendor SDK 重编。

**典型现象**：切换 Kconfig 配置后编译通过，但烧录后设备行为与预期不符（实际跑的是旧代码）。

**规则**：
- 修改 vendor SDK 中的文件后，`touch` 该文件强制重编
- 关键配置切换后，删除 `out/` 目录全量重编
- 不要依赖增量编译来验证 Kconfig 配置切换

## 6. EP_SUBMIT 后的完成时序

`bes_epin_request`（或等效的 IN 传输启动函数）在调用 HAL 的 send 函数后，当前请求的 `xfrd` 可能已经等于 `len`（数据 <= maxpacket 时一次发完）。此时可以立即 complete 请求（通知上层"数据已提交给硬件"），这与旧驱动行为一致。

**但要注意**：如果上层在 complete 回调中立即提交新请求到同一个 EP，而 HAL 层的 DMA 还没完成上一次传输，新的 `hal_send_epn` 可能因为"EP busy"返回错误。这在高频小包传输场景下容易触发。

**规则**：
- complete 时机与旧驱动保持一致（不要提前也不要延后）
- 如果 HAL 的 send 函数有"EP busy"检查，确保 send_compl 回调正确清除 busy 状态后再处理队列中的下一个请求
