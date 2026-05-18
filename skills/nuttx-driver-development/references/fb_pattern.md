# Framebuffer Driver Pattern — openvela 帧缓冲驱动框架

本文档是 framebuffer (fb) 驱动子系统的完整参考，由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

> **适用范围**：适用于具有独立显存（VRAM）的显示控制器驱动，如 SoC 内置 LCDC 控制器（AM335x LCDC、STM32 LTDC 等）。
>
> 对于通过 SPI/I2C 连接的小型 LCD 面板（无独立显存），请使用 `lcd_pattern.md`。

## Table of Contents

1. [一、框架概述](#一框架概述) — 上下半区模型、FB 与 LCD 的区别、设备节点
2. [二、开发准备：代码与配置](#二开发准备代码与配置) — 关键文件位置、Kconfig、驱动文件布局
3. [三、关键数据结构](#三关键数据结构) — `fb_vtable_s`、`fb_videoinfo_s`、`fb_planeinfo_s`、overlay
4. [四、核心 API 与驱动操作集](#四核心-api-与驱动操作集) — `fb_register_device`、vtable 回调详解
5. [五、显示更新模式](#五显示更新模式) — Pan Display、Update Area
6. [六、框架特性](#六框架特性) — 多缓冲、VSync 同步与屏幕类型、Overlay
7. [七、Command 屏运行时基础设施](#七command-屏运行时基础设施) — LP 状态机、命令队列、TE 引用计数、ISR 锁
8. [八、设备私有结构与注册](#八设备私有结构与注册) — 私有结构模板、注册函数、公共头文件
9. [九、完整 Bring-Up 流程](#九完整-bring-up-流程) — 从 Kconfig 到应用层的端到端流程
10. [十、测试方法](#十测试方法) — fb 设备测试工具与方法
11. [十一、Cross References](#十一cross-references) — 关联文档索引

---

## 一、框架概述

NuttX framebuffer 驱动框架为具有独立显存的显示控制器提供统一的 POSIX 文件接口。上半区 (`drivers/video/fb.c`) 实现了完整的字符设备操作（open/close/read/write/seek/ioctl/mmap/poll），下半区驱动只需填充 `fb_vtable_s` 回调表。

### FB 与 LCD 驱动的区别

| 特性 | Framebuffer (fb) | LCD (lcd) |
|------|-----------------|-----------|
| 显存 | 驱动分配独立 VRAM，应用直接 mmap 访问 | 无独立显存，通过 putrun/putarea 逐行/区域传输 |
| 设备节点 | `/dev/fbN` | `/dev/lcdN`（lcd_dev）或 `/dev/fbN`（lcd_framebuffer 桥接） |
| 典型硬件 | SoC LCDC 控制器、独立显示芯片 | SPI/I2C 小型 LCD 面板 |
| 数据传输 | 应用直接写 VRAM + ioctl 通知刷新 | 上半区通过回调逐行/区域推送像素 |
| 多缓冲 | 原生支持（pan display） | 需通过 lcd_framebuffer 桥接层模拟 |

### 上下半区驱动模型

```
Application (open/read/write/ioctl/mmap/poll)
    │
    ▼
VFS: /dev/fbN
    │
    ▼
Upper-half: fb.c ← 通用层，处理 VFS 操作、panbuf 队列、poll 通知
    │  getvideoinfo / getplaneinfo / pandisplay / setpower / ...
    ▼
Lower-half: YOUR driver ← 填充 fb_vtable_s 回调
    │
    ▼
Hardware: LCDC / Display Controller
```

**上半区 (fb.c) 职责**:
- 注册字符设备 `/dev/fbN`
- 实现 `file_operations`：open、close、read、write、seek、ioctl、mmap、poll
- 管理 panbuf 环形队列（渲染与送显解耦）
- poll 事件通知（POLLOUT = buffer 可写，POLLPRI = VSync 到来）
- 处理所有标准 ioctl 命令（FBIOGET_VIDEOINFO、FBIOGET_PLANEINFO 等）

**下半区 (YOUR driver) 职责**:
- 分配显存（framebuffer memory）
- 填充 `fb_vtable_s` 回调表
- 实现硬件初始化、电源管理、帧率控制
- TE/VSync 中断处理
- 可选：实现 overlay、硬件光标、色彩映射等高级功能

## 二、开发准备：代码与配置

### 硬件上下文清单（FB/LCDC 驱动专属）

FB/LCDC 驱动深度依赖芯片平台 HAL，缺少硬件上下文时只能生成 TODO 骨架。开发前需向用户收集：

| 优先级 | 信息 | 缺失时的影响 |
|--------|------|-------------|
| **P0 必需** | LCDC HAL 头文件（`lcdc_init/start/stop/set_buffer` 等函数签名） | pandisplay/updatearea 留 TODO。**这是最关键的信息源**，从 API 签名即可推断初始化序列和中断链路 |
| **P0 必需** | TE/VSync + Framedone 中断注册方式 | 中断处理只能写骨架 |
| **P0 必需** | 屏幕类型（Command 屏 / Video 屏） | 决定中断模型和 panbuf 消费策略（见第六章），选错会导致画面撕裂或黑屏 |
| **P1 推荐** | 同平台参考驱动 | 有则最佳；无则可从 HAL 头文件 + Datasheet + Linux/Zephyr 同芯片驱动推断 |
| **P1 推荐** | LCD 面板设备接口（`lcd_dev_s`）、DMA/Cache API、PM 接口 | setpower/updatearea/PM 无法实现 |
| **P1 推荐** | 显存分配方式（静态地址 / linker section / 动态） | 可能用错分配方式 |
| **P2 可选** | 旋转/压缩硬件、双屏/多 Layer 配置 | 高级特性无法实现 |

**无同平台参考驱动时的降级路径**：HAL 头文件（推断 API 调用序列）→ 芯片 Datasheet/Programming Guide（初始化流程图 + 中断源描述）→ Linux/Zephyr 同芯片 DRM 驱动（`probe()` = 初始化序列，`irq_handler` = 中断链路）→ 厂商 SDK 示例代码。

> [!TIP] 用户提供硬件信息的最简方式：将厂商 HAL 头文件和同平台参考驱动通过 `#File` 拖入对话，或告知文件路径让 AI 自行读取。

### 关键文件位置

- **框架核心**:
  - `nuttx/drivers/video/fb.c`: framebuffer 上半区实现
  - `nuttx/drivers/lcd/lcd_framebuffer.c`: LCD→FB 桥接层（将 lcd_dev_s 包装为 fb_vtable_s）
- **头文件**:
  - `nuttx/include/nuttx/video/fb.h`: framebuffer 接口定义（fb_vtable_s、数据结构、ioctl 命令）
- **应用示例**:
  - `apps/examples/fb/fb_main.c`: FB 测试应用（双缓冲 pan display + poll(POLLOUT) VSync + overlay 查询）

### 内核配置项 (Kconfig)

- `CONFIG_VIDEO_FB`: 启用 framebuffer 字符设备驱动框架
- `CONFIG_FB_UPDATE`: 启用 `FBIO_UPDATE` ioctl（通知显示控制器刷新指定区域）
- `CONFIG_FB_SYNC`: 启用 `FBIO_WAITFORVSYNC` ioctl（VSync 同步）
- `CONFIG_FB_OVERLAY`: 启用硬件 overlay 支持
- `CONFIG_FB_CMAP`: 启用 RGB 色彩映射
- `CONFIG_FB_HWCURSOR`: 启用硬件光标

### 驱动文件布局

驱动实现文件的位置由用户指定（可以是 `nuttx/drivers/video/`、`nuttx/arch/<arch>/src/<chip>/`、`vendor/` 或其他项目目录）。以下为典型结构示例：

```
<用户指定的驱动目录>/
├── mycontroller_fb.c         # 驱动实现 (lower-half)
├── mycontroller_fb.h         # 驱动私有头文件（寄存器定义等，可选）
├── Make.defs                 # Add CSRCS += mycontroller_fb.c
├── CMakeLists.txt            # Add list(APPEND SRCS mycontroller_fb.c)
└── Kconfig                   # Add CONFIG_VIDEO_MYCONTROLLER entry

nuttx/include/nuttx/video/
└── mycontroller_fb.h         # 公共头文件：注册函数原型

boards/<arch>/<chip>/<board>/src/
└── <board>_fb.c              # 板级初始化
```

> [!NOTE] 驱动文件放置位置
>
> Agent 在 Step A 交互阶段会询问用户驱动文件的目标目录。常见选择：
> - `nuttx/drivers/video/` — NuttX 上游通用驱动
> - `nuttx/arch/<arch>/src/<chip>/` — 平台相关的 LCDC 驱动
> - `vendor/<vendor>/drivers/` — 厂商私有驱动
>
> 无论放在哪里，公共头文件通常仍放在 `nuttx/include/nuttx/video/`。

### FB 驱动信息解析指导（供 Agent Step A.3 使用）

FB/LCDC 驱动不使用 sensor 的"寄存器摘要表 + SPI/I2C 接口规格"模板。当 Agent 在 Step A.3 解析 FB/LCDC 驱动信息时，应提取以下内容：

#### 从 HAL 头文件提取

| 提取项 | 对应 FB 骨架位置 | 示例 |
|--------|-----------------|------|
| LCDC 初始化函数（`lcdc_init/open/start`） | `up_fbinitialize()` 或注册函数中的硬件初始化 | `hal_lcdc_init()` |
| 显示基地址设置函数（`lcdc_set_buffer/set_addr`） | `pandisplay` 回调中切换显示地址 | `hal_lcdc_set_bufaddr()` |
| 传输触发函数（`lcdc_flush/send/start_transfer`） | `updatearea` 回调或 TE 中断处理中启动传输 | `hal_lcdc_send()` |
| TE/VSync 中断回调注册（`lcdc_set_te_cb/irq_register`） | 中断处理链路 | `hal_lcdc_set_callback()` |
| Framedone 回调注册 | Command 屏的帧完成通知 | `hal_lcdc_set_framedone_cb()` |
| 电源控制函数（`lcdc_power/backlight`） | `setpower` / `getpower` 回调 | `hal_lcdc_set_power()` |
| 反初始化函数（`lcdc_deinit/close/stop`） | `up_fbuninitialize()` 或错误清理路径 | `hal_lcdc_deinit()` |

#### 从参考驱动 / Datasheet 提取

| 提取项 | 用途 |
|--------|------|
| 屏幕类型（Command / Video） | 决定中断模型和 panbuf 消费策略 |
| 分辨率、像素格式、BPP | 填充 `fb_videoinfo_s` 和 `fb_planeinfo_s` |
| 显存分配方式 | 选择 kmm_zalloc / 静态数组 / 硬件固定地址 / linker section |
| 多缓冲数量 | 计算 `yres_virtual = yres * N` |
| DMA/Cache 要求 | 显存对齐、Cache flush 时机 |
| PM 状态机 | setpower 的电源级别定义 |

#### 交叉验证规则

1. HAL 头文件中的初始化函数参数 → 与参考驱动的 `up_fbinitialize()` 调用序列对比
2. 中断回调签名 → 与参考驱动的 TE/framedone 处理函数对比
3. 如果参考驱动是不同芯片平台，**标注 HAL API 名称差异**（如 BES 用 `hal_lcdc_*`，STM32 用 `stm32_ltdc_*`）

## 三、关键数据结构

### fb_videoinfo_s — 显示控制器信息

```c
struct fb_videoinfo_s
{
  uint8_t    fmt;           /* 下半区填充, 必需: 像素格式，如 FB_FMT_RGB16_565 */
  fb_coord_t xres;          /* 下半区填充, 必需: 水平分辨率（像素） */
  fb_coord_t yres;          /* 下半区填充, 必需: 垂直分辨率（像素） */
  uint8_t    nplanes;       /* 下半区填充, 必需: 颜色平面数，通常为 1 */
#ifdef CONFIG_FB_OVERLAY
  uint8_t    noverlays;     /* 下半区填充, 可选: overlay 数量 */
#endif
#ifdef CONFIG_FB_MODULEINFO
  uint8_t    moduleinfo[128]; /* 下半区填充, 可选: 厂商自定义模块信息 */
#endif
};
```

### fb_planeinfo_s — 颜色平面信息

```c
struct fb_planeinfo_s
{
  FAR void  *fbmem;         /* 下半区填充, 必需: 帧缓冲内存起始地址 */
  size_t     fblen;         /* 下半区填充, 必需: 帧缓冲内存总大小（字节） */
  fb_coord_t stride;        /* 下半区填充, 必需: 每行字节数 */
  uint8_t    display;       /* 下半区填充, 必需: 显示编号 */
  uint8_t    bpp;           /* 下半区填充, 必需: 每像素位数 */
  uint32_t   xres_virtual;  /* 下半区填充, 可选: 虚拟水平分辨率（多缓冲时使用） */
  uint32_t   yres_virtual;  /* 下半区填充, 可选: 虚拟垂直分辨率（多缓冲时 = yres * nbuffers） */
  uint32_t   xoffset;       /* 应用/上半区传入, 可选: 当前显示偏移 X */
  uint32_t   yoffset;       /* 应用/上半区传入, 可选: 当前显示偏移 Y（pan display 切换缓冲区） */
};
```

### fb_vtable_s — 驱动操作集（核心）

完整定义见 `nuttx/include/nuttx/video/fb.h`。回调按功能分组：

```c
struct fb_vtable_s
{
  /* 必需回调 */
  int (*getvideoinfo)(FAR struct fb_vtable_s *vtable,
                      FAR struct fb_videoinfo_s *vinfo);
  int (*getplaneinfo)(FAR struct fb_vtable_s *vtable, int planeno,
                      FAR struct fb_planeinfo_s *pinfo);

  /* 可选：设备打开/关闭 */
  int (*open)(FAR struct fb_vtable_s *vtable);
  int (*close)(FAR struct fb_vtable_s *vtable);

  /* 条件编译回调（按 CONFIG_FB_* 启用） */
  /* CONFIG_FB_CMAP:    getcmap / putcmap */
  /* CONFIG_FB_HWCURSOR: getcursor / setcursor */
  /* CONFIG_FB_UPDATE:  updatearea */
  /* CONFIG_FB_SYNC:    waitforvsync */
  /* CONFIG_FB_OVERLAY: getoverlayinfo / settransp / setchromakey /
   *   setcolor / setblank / setarea / setdestarea / panoverlay
   *   CONFIG_FB_OVERLAY_BLIT: blit / blend */

  /* Pan display（多缓冲切换） */
  int (*pandisplay)(FAR struct fb_vtable_s *vtable,
                    FAR struct fb_planeinfo_s *pinfo);

  /* 帧率控制 */
  int (*setframerate)(FAR struct fb_vtable_s *vtable, int rate);
  int (*getframerate)(FAR struct fb_vtable_s *vtable);

  /* 电源管理 */
  int (*getpower)(FAR struct fb_vtable_s *vtable);
  int (*setpower)(FAR struct fb_vtable_s *vtable, int power);

  /* 自定义 ioctl */
  int (*ioctl)(FAR struct fb_vtable_s *vtable, int cmd, unsigned long arg);

  /* 上半区私有数据 — 由 fb_register_device() 自动设置，驱动不得修改 */
  FAR void *priv;
};
```

## 四、核心 API 与驱动操作集

### 注册 API

两种方式最终都是调 `fb_register_device(display, plane, vtable)` 注册 `/dev/fbN`，本质一样，但**二选一，不可混用**。`fb_register` 多一层间接，通过 `up_fbinitialize` + `up_fbgetvplane` 这套固定签名的全局接口获取 vtable。直接调 `fb_register_device` 更灵活——注册函数签名自己定，想传寄存器基地址、中断号、还是别的什么参数都行，不受 `up_fb*` 接口约束。

```c
int fb_register_device(int display, int plane, FAR struct fb_vtable_s *vtable);

/* fb_register 是 inline 封装，内部调用 up_fb* 后转到 fb_register_device */
static inline int fb_register(int display, int plane)
{
  FAR struct fb_vtable_s *vtable;
  int ret;

  ret = up_fbinitialize(display);
  if (ret < 0)
    {
      return ret;
    }

  vtable = up_fbgetvplane(display, plane);
  if (vtable == NULL)
    {
      return -EINVAL;
    }

  return fb_register_device(display, plane, vtable);
}
```

走 `fb_register` 路径时，需要在 fb驱动内实现：

```c
int up_fbinitialize(int display);                                /* 初始化硬件 */
FAR struct fb_vtable_s *up_fbgetvplane(int display, int vplane); /* 返回 vtable */
void up_fbuninitialize(int display);                             /* 反初始化 */
```

### 上半区辅助 API（供下半区在中断处理中调用）

```c
/* VSync 通知 — VSync中断中 通知上半区 VSync 到来，唤醒 poll(POLLPRI) 等待者进行动画、视频等同步处理 */
void fb_notify_vsync(FAR struct fb_vtable_s *vtable);

/* Pan buffer 管理 — 下半区在 TE/framedone 中断中使用，实现帧队列消费 */
int fb_peek_paninfo(FAR struct fb_vtable_s *vtable, FAR union fb_paninfo_u *info, int overlay);
int fb_remove_paninfo(FAR struct fb_vtable_s *vtable, int overlay);
int fb_paninfo_count(FAR struct fb_vtable_s *vtable, int overlay);
```

> [!IMPORTANT] panbuf 队列机制与屏幕类型
>
> `fb.c` 上半区维护一个 panbuf 环形队列，将渲染和送显解耦：
> - **渲染器**通过 `FBIOPAN_DISPLAY` ioctl 将新帧入队（`fb_add_paninfo`）
> - **驱动**在中断中通过 `fb_peek_paninfo` / `fb_remove_paninfo` 消费队列
> - `fb_remove_paninfo` 释放队列空位后触发 `POLLOUT`，通知渲染器可以提交新帧
> - `fb_notify_vsync` 触发 `POLLPRI`，通知应用 VSync 到来（用于动画/视频同步）
>
> 根据屏幕类型，驱动适配方式不同（详见第六章）：
> - **Command 屏**：TE 中断取帧发送 → framedone 中断调用 `fb_remove_paninfo` 释放
> - **Video 屏**：TE 中断检查队列，有新帧则 `fb_remove_paninfo` 丢弃旧帧 + `fb_peek_paninfo` 取新帧

> [!WARNING] vtable->priv 由上半区管理
>
> `fb_register_device()` 内部会设置 `vtable->priv = fb`（指向上半区私有结构 `fb_chardev_s`）。
> 下半区驱动**不得**自行设置 `vtable->priv`，否则会破坏上半区的 pan buffer 管理。
> 下半区应将自己的私有数据嵌入到包含 `fb_vtable_s` 的外层结构中，通过强转访问。

### 回调函数详解

| Callback | Purpose | Required? |
|----------|---------|-----------|
| `getvideoinfo` | 返回显示控制器配置（分辨率、像素格式、平面数） | **Yes** |
| `getplaneinfo` | 返回颜色平面信息（显存地址、大小、stride、bpp） | **Yes** |
| `open` / `close` | 设备打开/关闭，用于引用计数和硬件初始化/反初始化 | Optional |
| `pandisplay` | 切换显示缓冲区（多缓冲），通过 yoffset 指定当前显示的缓冲区 | Recommended |
| `setpower` / `getpower` | 面板电源控制（0=全关，正整数=开启级别） | Recommended |
| `setframerate` / `getframerate` | 帧率控制（0=禁用刷新） | Optional |
| `updatearea` | 通知硬件刷新指定矩形区域（需 CONFIG_FB_UPDATE） | Conditional |
| `waitforvsync` | 阻塞等待 VSync 信号（需 CONFIG_FB_SYNC） | Conditional |
| `ioctl` | 自定义 ioctl 命令透传 | Optional |

> [!IMPORTANT] pandisplay 回调的调用时机
>
> `pandisplay` 在 `FBIOPAN_DISPLAY` ioctl 中**同步调用**，不是由 VSync 触发。
> 上半区流程：`FBIOPAN_DISPLAY` → `fb_add_paninfo()` 入队 → `pandisplay()` 回调。
> 驱动可在 `pandisplay` 中记录待切换的 buffer 地址，在下一次 TE/VSync 中断中实际切换。

### FB 驱动功能 Checklist 模板（供 Agent Step B 使用）

当 Agent 生成 FB/LCDC 驱动的 `requirements.md` 时，使用以下功能 Checklist 替代 sensor 的默认模板。默认勾选规则基于硬件支持情况：

```markdown
## 功能 Checklist

### 必需功能（不可取消）
- [x] getvideoinfo — 返回分辨率、像素格式、平面数
- [x] getplaneinfo — 返回显存地址、大小、stride、bpp
- [x] 显存分配 — 根据硬件选择分配方式（动态/静态/硬件固定）
- [x] fb_register_device 或 up_fbinitialize 注册

### 推荐功能（默认勾选，研发可取消）
- [x] pandisplay — 多缓冲切换（如硬件支持多缓冲）
- [x] setpower / getpower — 面板电源控制
- [x] TE/VSync 中断处理 — 如硬件有 TE/VSync 中断引脚
- [x] Framedone 中断处理 — 如 Command 屏
- [x] fb_notify_vsync — TE/VSync 中断中通知上半区进行动画、视频等同步处理

### 可选功能（默认不勾选，研发按需开启）
- [ ] updatearea — 部分区域刷新（需 CONFIG_FB_UPDATE）
- [ ] waitforvsync — 阻塞等待 VSync（需 CONFIG_FB_SYNC）
- [ ] setframerate / getframerate — 帧率控制
- [ ] overlay 支持 — 硬件多层混合（需 CONFIG_FB_OVERLAY）
- [ ] open / close — 设备引用计数
- [ ] 色彩映射 — getcmap / putcmap（需 CONFIG_FB_CMAP）
- [ ] 硬件光标 — getcursor / setcursor（需 CONFIG_FB_HWCURSOR）
- [ ] 自定义 ioctl — 厂商私有命令透传
- [ ] 软件 VSync — work_queue 定时模拟（无硬件中断时）

### Command 屏推荐功能（仅 Command 屏默认勾选，研发可取消）
- [x] LP 管理 — 空闲时 LCDC sleep，有帧时 wakeup
- [x] 面板命令队列 — framedone 帧间隙发送面板命令
- [x] TE 引用计数 — 多用户共享 TE 中断
- [x] ISR/线程互斥锁 — 帧传输与命令发送互斥
- [x] 面板控制权 — hold/release 独占总线
```

### FB 驱动 requirements.md 模板（供 Agent Step B 使用）

FB/LCDC 驱动的 requirements.md 使用以下章节结构替代 sensor 的默认模板：

1. **显示控制器概述**: 芯片型号、LCDC 类型（DSI/DPI/SPI/RGB）、支持的接口
2. **HAL API 清单**: 从 Step A.3 提取的 HAL 函数映射表（初始化、传输、中断、电源）
3. **屏幕参数**: 分辨率、像素格式、BPP、屏幕类型（Command/Video）、刷新率
4. **显存方案**: 分配方式、多缓冲数量、DMA/Cache 要求
5. **中断模型**: TE/VSync/Framedone 中断链路、屏幕类型对应的 panbuf 消费策略
6. **电源管理**: 面板电源状态定义、背光控制、PM 集成
7. **运行时模块 HAL 映射**（仅 Command 屏）: 第七章 6 个模块的平台 HAL API 映射（LP sleep/wakeup、GPIO TE 配置、DSI 命令发送、ISR 锁原语等）
8. **代码流程**: 初始化序列、pandisplay 流程、中断处理流程（伪代码或流程图）
9. **功能 Checklist**: 上述 Checklist 模板（研发在交互 2 中确认）
10. **参考驱动**: 骨架参考和芯片参考的匹配结果及关键差异


## 五、显示更新模式

### 模式 1：Pan Display 多缓冲（推荐）

应用写入后备缓冲区，通过 `FBIOPAN_DISPLAY` 将新帧入队到 panbuf，驱动在中断中消费队列实现无撕裂刷新。

```
渲染器写入 buffer[1]
    │
    ▼
ioctl(FBIOPAN_DISPLAY, &pinfo)  ← pinfo.yoffset = yres * 1
    │
    ├─→ fb_add_paninfo() 入队到 panbuf
    └─→ pandisplay() 回调（同步，驱动可记录待切换地址）

                    ···（等待硬件中断）···

TE / VSync 中断到来
    │
    ▼
驱动: fb_peek_paninfo() 取出帧地址 → 写入 LCD 控制器
    │
    ▼
Framedone 中断（Command 屏）或下一次 TE 中断（Video 屏）
    │
    ▼
驱动: fb_remove_paninfo() → 触发 POLLOUT → 渲染器可提交新帧
```

### 模式 2：Update Area（适用于部分刷新）

应用修改 framebuffer 后，通过 `FBIO_UPDATE` 通知硬件刷新指定区域。需要 `CONFIG_FB_UPDATE`。

```c
struct fb_area_s area = { .x = 0, .y = 0, .w = 320, .h = 240 };
ioctl(fd, FBIO_UPDATE, &area);
```

## 六、框架特性

### 多缓冲 (Multi-Buffering)

通过 `yres_virtual = yres * N` 分配 N 个缓冲区，应用通过 `FBIOPAN_DISPLAY` 切换。

**关键机制**（源自 `fb.c` `fb_register_device()`）：
- 上半区通过 `fbcount = pinfo.yres_virtual / vinfo.yres` 计算缓冲区数量
- panbuf 环形队列大小 = `fbcount * sizeof(union fb_paninfo_u)`

```c
/* 驱动初始化时分配多缓冲 — fbcount 由上半区自动计算 */

fb->planeinfo.xres_virtual = fb->videoinfo.xres;
fb->planeinfo.yres_virtual = fb->videoinfo.yres * NUM_BUFFERS;
fb->planeinfo.fblen        = fb->planeinfo.stride *
                              fb->planeinfo.yres_virtual;
fb->planeinfo.fbmem        = kmm_zalloc(fb->planeinfo.fblen);
```

> [!NOTE] yres_virtual 为 0 时的行为
>
> 如果驱动不设置 `yres_virtual`（保持 0），上半区将 `fbcount` 视为 1，
> pan buffer 只能容纳一帧，`FBIOPAN_DISPLAY` 仍可工作但无多缓冲效果。

### VSync 同步与屏幕类型

VSync（垂直同步）的核心目的是**防止画面撕裂**：确保渲染器写 buffer 和 LCD 读 buffer 在时间和空间上不重叠。通过 panbuf 队列机制，渲染和送显解耦——渲染器负责入队，驱动在中断中消费队列。

#### Command 屏 vs Video 屏

| 特性 | Command 屏 | Video 屏 |
|------|-----------|---------|
| 屏幕 RAM | 内置一帧缓存 | 无，依赖 LCD 控制器持续刷新 |
| 传输时机 | 按需：有新帧才传输 | 持续：每个 VSync 周期都需要帧数据 |
| 中断模型 | TE 中断（取帧发送）+ Framedone 中断（释放帧） | TE 中断（检查队列、切换帧） |
| 功耗 | 低（静态画面不传输） | 高（持续刷新） |
| 典型场景 | 穿戴设备（电池供电） | 成本敏感产品 |
| 运行时基础设施 | 需要 LP 管理 + 命令队列 + TE 引用计数 + ISR 锁（见第七章） | 不需要 |

#### Command 屏适配

```c
/* TE 中断：屏幕即将刷新，取出新帧地址启动传输 */
static void lcdc_te_irq(int irq, void *context, void *arg)
{
  struct lcdcdev_s *priv = arg;
  union fb_paninfo_u info;

  if (fb_peek_paninfo(&priv->vtable, &info, FB_NO_OVERLAY) == OK)
    {
      uintptr_t buf = (uintptr_t)priv->pinfo.fbmem +
                       priv->pinfo.stride * info.planeinfo.yoffset;
      lcdc_set_bufaddr(buf);
    }
}

/* Framedone 中断：传输完成，释放帧（触发 POLLOUT 通知渲染器） */
static void lcdc_framedone_irq(int irq, void *context, void *arg)
{
  struct lcdcdev_s *priv = arg;
  fb_remove_paninfo(&priv->vtable, FB_NO_OVERLAY);
}
```

#### Video 屏适配

```c
/* TE 中断：每个 VSync 周期检查队列，有新帧则切换，无则维持旧帧 */
static void lcdc_te_irq(int irq, void *context, void *arg)
{
  struct lcdcdev_s *priv = arg;
  union fb_paninfo_u info;
  int count;

  count = fb_paninfo_count(&priv->vtable, FB_NO_OVERLAY);
  if (count > 0)
    {
      /* 有多帧时丢弃旧帧，保持最新 */

      if (count > 1)
        {
          fb_remove_paninfo(&priv->vtable, FB_NO_OVERLAY);
        }

      if (fb_peek_paninfo(&priv->vtable, &info, FB_NO_OVERLAY) == OK)
        {
          uintptr_t buf = (uintptr_t)priv->pinfo.fbmem +
                           priv->pinfo.stride * info.planeinfo.yoffset;
          lcdc_set_bufaddr(buf);
        }
    }

  /* 无新帧时 LCD 控制器继续显示旧帧 */
}
```

#### 软件 VSync（无硬件 TE/VSync 中断时的替代方案）

无硬件中断的设备可通过 `work_queue(HPWORK, ...)` 定时模拟（周期 = `MSEC2TICK(1000/fps)`），采用 Video 屏模式消费 panbuf 队列。在 worker 中调用 `fb_peek_paninfo` / `fb_remove_paninfo` + 硬件 flush。

### Overlay 硬件混合

需要 `CONFIG_FB_OVERLAY`。支持多个 overlay 层的透明度、chromakey、区域设置、blit/blend 操作。

## 七、Command 屏运行时基础设施

> Video 屏驱动不需要本章内容 — LCDC 持续刷新，无 LP/命令队列需求。

Command 屏按需传输，空闲时 LCDC 停止。驱动必须自包含实现以下 6 个模块才能正常运行：

| 模块 | 职责 | 触发场景 |
|------|------|---------|
| LP 状态机 | 空闲时 `hal_lcdc_sleep()`，有帧时 `hal_lcdc_wakeup()` | framedone 队列空 → 进入；pandisplay → 退出 |
| 面板命令队列 | 缓存面板命令，framedone 帧间隙统一发送 | 亮度调节、AOD 切换、ESD 恢复 |
| TE 引用计数 | 位图管理 GPIO TE 中断，最后一个用户释放时关闭 | 帧传输 + 命令发送共享 TE |
| ISR/线程锁 | ISR 安全的 trywait 互斥（基于 sem_t + int_lock） | 帧传输（ISR）与命令发送（线程）并发 |
| 同步通知 | LP 退出后立即触发首帧传输，不等 TE | LP wakeup 完成回调 |
| 面板控制权 | 独占总线：停帧 → 清命令队列 → 获取 TE | ESD 恢复、面板重初始化 |

### 模块依赖

```
pandisplay / framedone / TE 中断
    ├→ LP 状态机 ──→ 同步通知
    ├→ 命令队列 ──→ ISR/线程锁 + TE 引用计数
    └→ 面板控制权 ──→ TE 引用计数
```

### 关键约束

1. LP enter/exit 必须在 `enter_critical_section()` 保护下执行
2. ISR 锁中禁止 `nxsem_wait()`，只能用 trywait 语义
3. 命令队列 flush 前必须先 LP exit（LCDC 必须醒着才能发 DSI 命令）
4. 这些模块的具体 HAL API 映射由平台 requirements 文档提供

---

## 八、设备私有结构与注册

### 设备私有结构

```c
struct mycontroller_fb_s
{
  struct fb_vtable_s vtable;          /* 必须首成员，可强转为 fb_vtable_s */
  struct fb_planeinfo_s planeinfo;    /* 平面信息（含显存指针） */
  struct fb_videoinfo_s videoinfo;    /* 视频信息 */
  FAR void *base;                     /* 硬件寄存器基地址 */
  int irq;                            /* TE/VSync 中断号 */
  spinlock_t lock;                    /* 自旋锁 */
};
```

### 注册函数要点

注册函数的关键步骤（完整示例见 `am335x_lcdc.c` 或 `sim_framebuffer.c`）：

1. `kmm_zalloc(sizeof(*fb))` — 分配私有结构
2. 填充 `videoinfo`（xres/yres/fmt/nplanes）和 `planeinfo`（fbmem/fblen/stride/bpp/yres_virtual）
3. 分配显存 — 方式因硬件而异：
   - `kmm_zalloc` / `memalign` — 堆上动态分配
   - 静态数组 `__attribute__((section(...)))` — linker section
   - 硬件固定 VRAM 地址 — 直接赋值
4. 填充 `vtable` 回调（getvideoinfo/getplaneinfo/pandisplay/setpower/getpower + 可选回调）
5. 可选：`irq_attach()` + `up_enable_irq()` — 注册 VSync 中断（先 attach 再 enable）
6. `fb_register_device(display, 0, &fb->vtable)` — 注册 `/dev/fbN`
7. 错误路径：goto-based cleanup 释放所有已分配资源

> [!WARNING] vtable->priv 由上半区管理，驱动不得设置。私有数据通过外层结构强转访问。

### 两种注册路径（二选一，不可混用）

> [!WARNING] 两种路径互斥
>
> 选择 `fb_register_device()` 就不要实现 `up_fb*`；选择 `fb_register()` 就不要调 `fb_register_device()`。混用会导致双重注册。

- **路径 A：`fb_register_device()` 直接注册**（推荐新驱动使用）
  - 驱动自己定义注册函数（签名自由），内部完成硬件初始化 + 填充 vtable + 调用 `fb_register_device(display, 0, &vtable)`
  - 板级代码直接调用驱动的注册函数
  - **不需要**实现 `up_fbinitialize` / `up_fbgetvplane` / `up_fbuninitialize`

- **路径 B：`fb_register()` → `up_fb*` 全局接口**
  - 驱动实现 `up_fbinitialize(display)` 做硬件初始化，`up_fbgetvplane(display, vplane)` 返回 vtable 指针
  - 板级代码调用 `fb_register(display, plane)`，内部自动调 `up_fbinitialize` → `up_fbgetvplane` → `fb_register_device`
  - **不需要**在驱动内部调用 `fb_register_device()`

### 公共头文件

放在 `nuttx/include/nuttx/video/` 或 `vendor/.../include/`，只声明注册函数原型。格式参考 `nuttx/include/nuttx/video/fb.h` 中已有的驱动头文件。

## 九、完整 Bring-Up 流程

两种注册路径与第八章一一对应。

### 路径 A：`fb_register_device()` 直接注册

```
1. Kconfig: CONFIG_VIDEO_MYCONTROLLER=y
2. Make.defs / CMakeLists.txt: 添加 mycontroller_fb.c
3. Board bringup 函数:
       mycontroller_fb_register(0, BASE_ADDR, IRQ)
4. mycontroller_fb_register():
       kmm_zalloc(fb)
       硬件初始化 → 填充 videoinfo / planeinfo → 分配显存 → 填充 vtable
       可选: irq_attach + up_enable_irq（TE/VSync 中断）
       fb_register_device(display, 0, &fb->vtable) → 创建 /dev/fb0
5. Application:
       open → ioctl(FBIOGET_PLANEINFO) → mmap → 写 fbmem
       → ioctl(FBIOPAN_DISPLAY) → poll(POLLPRI)
```

### 路径 B：`fb_register()` → `up_fb*`（NuttX 上游主流）

```
1. Kconfig + Make.defs / CMakeLists.txt: 同路径 A
2. 驱动实现 up_fbinitialize() / up_fbgetvplane() / up_fbuninitialize()
3. Board bringup 函数:
       fb_register(0, 0)
         → up_fbinitialize(0)    ← 硬件初始化 + 显存分配 + 填充 vtable
         → up_fbgetvplane(0, 0)  ← 返回 vtable 指针
         → fb_register_device()  → 创建 /dev/fb0
4. Application: 同路径 A
```

> [!NOTE] NuttX 上游板级代码几乎全部使用路径 B。路径 A 适合需要自定义注册函数签名的场景。两者最终都调 `fb_register_device()`，应用层行为一致。

## 十、测试方法

### NuttX fb 示例应用 (apps/examples/fb)

NuttX 自带 `fb` 示例应用，启用 `CONFIG_EXAMPLES_FB=y` 后可直接使用：

```bash
# 运行 fb 测试（默认 /dev/fb0，绘制彩色矩形）
fb

# 指定设备
fb /dev/fb1
```

该示例覆盖了 FB 应用流程：获取 videoinfo/planeinfo → mmap → 双缓冲 → 绘制 → pan display → poll。

### 应用层标准流程

```c
/* 1. 打开设备 + 获取信息 */
int fd = open("/dev/fb0", O_RDWR);
ioctl(fd, FBIOGET_VIDEOINFO, &vinfo);
ioctl(fd, FBIOGET_PLANEINFO, &pinfo);

/* 2. mmap 获取显存（KERNEL build 中必须用 mmap） */
FAR void *fbmem = mmap(NULL, pinfo.fblen, PROT_READ | PROT_WRITE,
                       MAP_SHARED | MAP_FILE, fd, 0);

/* 3. 双缓冲检测：pinfo.yres_virtual == vinfo.yres * 2 */

/* 4. 绘制 → poll(POLLOUT) 检查空位 → FBIOPAN_DISPLAY 切换 */
pinfo.yoffset = 0;
ioctl(fd, FBIOPAN_DISPLAY, &pinfo);

/* 5. 清理 */
munmap(fbmem, pinfo.fblen);
close(fd);
```

> [!IMPORTANT] 应用层关键注意事项
>
> - **必须用 mmap 获取显存地址**：在 KERNEL build 中，`pinfo.fbmem` 是物理地址，应用无法直接访问
> - **poll 事件语义**：`POLLOUT` = panbuf 有空位可提交新帧；`POLLPRI` = VSync 到来

### Overlay 测试（需 CONFIG_FB_OVERLAY）

通过 `FBIO_SELECT_OVERLAY` 选择 overlay 层，`FBIOGET_OVERLAYINFO` 获取信息，`FBIO_SELECT_OVERLAY(FB_NO_OVERLAY)` 恢复默认层。

## 十一、Cross References

- 主 SKILL.md — 驱动通识知识（编码规范、内核 API、中断规则、同步原语等）
- `references/lcd_pattern.md` — LCD 驱动模式（SPI/I2C 小型面板，无独立显存）
- `references/coding_rules.md` — 编码规范、内核 API、中断规则
- `references/board_registration.md` — 板级驱动注册模式
- `references/nuttx_nav_search.md` — 驱动示例路径和 NuttX 树导航