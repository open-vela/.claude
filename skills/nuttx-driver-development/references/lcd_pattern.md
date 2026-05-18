# LCD Driver Pattern — openvela LCD 显示驱动框架

本文档是 LCD 驱动子系统的完整参考，由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

> **适用范围**：适用于通过 SPI/I2C/并行接口连接的小型 LCD 面板驱动（无独立显存），如 ST7789、SSD1306、ILI9341 等。对于具有独立显存的显示控制器（GPU、LCDC），请使用 `fb_pattern.md`。

## Table of Contents

1. [一、框架概述](#一框架概述) — 上下半区模型、LCD 与 FB 的关系、两种设备接口
2. [二、开发准备：代码与配置](#二开发准备代码与配置) — 关键文件位置、Kconfig、驱动文件布局
3. [三、关键数据结构](#三关键数据结构) — `lcd_dev_s`、`lcd_planeinfo_s`、`lcddev_area_align_s`
4. [四、核心 API 与驱动操作集](#四核心-api-与驱动操作集) — 注册方式、lcd_dev_s 回调详解
5. [五、数据传输模式](#五数据传输模式) — putrun/getrun、putarea/getarea、redraw
6. [六、框架特性](#六框架特性) — lcd_framebuffer 桥接、lcd_dev 字符设备、对齐约束
7. [七、设备私有结构与注册](#七设备私有结构与注册) — 私有结构模板、注册函数、公共头文件
8. [八、完整 Bring-Up 流程](#八完整-bring-up-流程) — 从 Kconfig 到应用层的端到端流程
9. [九、测试方法](#九测试方法) — LCD 设备测试工具与方法
10. [十、Cross References](#十cross-references) — 关联文档索引

---

## 一、框架概述

NuttX LCD 驱动框架为通过 SPI/I2C/并行接口连接的小型显示面板提供统一接口。与 framebuffer 驱动不同，LCD 驱动没有独立显存，数据通过 `putrun`/`putarea` 回调逐行或逐区域传输到面板。

### LCD 驱动的两种设备接口

LCD 驱动实现 `lcd_dev_s` 接口后，有两种方式暴露给应用层：

| 接口 | 设备节点 | 机制 | 适用场景 |
|------|---------|------|---------|
| **lcd_dev** | `/dev/lcdN` | 直接字符设备，通过 ioctl 传输像素数据 | 需要精细控制像素传输的场景 |
| **lcd_framebuffer** | `/dev/fbN` | 桥接层分配 RAM 缓冲区，模拟 framebuffer 接口 | 需要与 LVGL/NX 等图形框架集成 |

### 上下半区驱动模型

```
Application
    │
    ├─── 路径 A: lcd_framebuffer 桥接 ──────────────────┐
    │    open("/dev/fbN")                                │
    │    mmap → 写入 RAM 缓冲区                          │
    │    ioctl(FBIO_UPDATE) → lcdfb_updatearea()         │
    │         │                                          │
    │         ▼                                          │
    │    lcd_framebuffer.c (RAM buffer + fb_vtable_s)    │
    │         │  putrun() / putarea()                    │
    │         ▼                                          │
    │                                                    │
    ├─── 路径 B: lcd_dev 直接访问 ──────────────────────┐│
    │    open("/dev/lcdN")                               ││
    │    ioctl(LCDDEVIO_PUTAREA, &area)                  ││
    │         │                                          ││
    │         ▼                                          ││
    │    lcd_dev.c (ioctl → planeinfo callbacks)         ││
    │         │  putrun() / putarea()                    ││
    │         ▼                                          ▼│
    │    ┌─────────────────────────────────────────────────┐
    │    │  lcd_dev_s — YOUR driver (lower-half)           │
    │    │  实现 lcd_planeinfo_s 的 putrun/putarea 回调    │
    │    └─────────────────────────────────────────────────┘
    │         │
    ▼         ▼
    Bus: SPI_TRANSFER() / I2C_TRANSFER() / Parallel I/O
```

**下半区 (YOUR driver) 职责**:
- 实现 `lcd_dev_s` 操作集（getvideoinfo、getplaneinfo、setpower 等）
- 实现 `lcd_planeinfo_s` 数据传输回调（putrun/getrun 和/或 putarea/getarea）
- 通过 SPI/I2C 总线与 LCD 控制器通信
- 管理 LCD 初始化序列、电源、对比度、背光


## 二、开发准备：代码与配置

### 关键文件位置

- **框架核心**:
  - `nuttx/drivers/lcd/lcd_dev.c`: LCD 字符设备上半区（`/dev/lcdN`）
  - `nuttx/drivers/lcd/lcd_framebuffer.c`: LCD→FB 桥接层（`/dev/fbN`）
- **头文件**:
  - `nuttx/include/nuttx/lcd/lcd.h`: LCD 接口定义（`lcd_dev_s`、`lcd_planeinfo_s`）
  - `nuttx/include/nuttx/lcd/lcd_dev.h`: LCD 字符设备 ioctl 命令定义
- **参考驱动**:
  - `nuttx/drivers/lcd/skeleton.c`: 官方 LCD 驱动骨架（最佳起点）
  - `nuttx/drivers/lcd/st7789.c`: ST7789 SPI LCD 驱动（生产级参考）
  - `nuttx/drivers/lcd/ssd1306_base.c`: SSD1306 OLED 驱动（I2C/SPI 双模式参考）
  - `nuttx/drivers/lcd/ili9341.c`: ILI9341 LCD 驱动（并行接口参考）

### 内核配置项 (Kconfig)

- `CONFIG_LCD`: 启用 LCD 驱动框架
- `CONFIG_LCD_DEV`: 启用 LCD 字符设备接口（`/dev/lcdN`）
- `CONFIG_LCD_FRAMEBUFFER`: 启用 LCD→FB 桥接（`/dev/fbN`）
- `CONFIG_LCD_MAXPOWER`: LCD 最大电源/背光级别（通常 1 或 255）
- `CONFIG_LCD_MAXCONTRAST`: LCD 最大对比度级别
- `CONFIG_LCD_DYN_ORIENTATION`: 启用运行时方向切换

### 驱动文件布局

```
nuttx/
├── drivers/lcd/
│   ├── mylcd.c               # 驱动实现 (lower-half)
│   ├── mylcd.h               # 驱动私有头文件（寄存器定义等）
│   ├── Make.defs             # Add CSRCS += mylcd.c
│   ├── CMakeLists.txt        # Add list(APPEND SRCS mylcd.c)
│   └── Kconfig               # Add CONFIG_LCD_MYLCD entry
├── include/nuttx/lcd/
│   └── mylcd.h               # 公共头文件：初始化函数原型
└── boards/<arch>/<chip>/<board>/src/
    └── <board>_lcd.c         # 板级初始化
```

### LCD 驱动信息解析指导（供 Agent Step A.3 使用）

LCD 面板驱动不使用 sensor 的"寄存器摘要表 + SPI/I2C 接口规格"模板。当 Agent 在 Step A.3 解析 LCD 面板驱动信息时，应提取以下内容：

#### 从 Datasheet 提取

| 提取项 | 对应驱动骨架位置 | 示例 |
|--------|-----------------|------|
| 分辨率（xres × yres） | `getvideoinfo` 回调中填充 `fb_videoinfo_s` | 240×240、320×240 |
| 像素格式 / BPP | `getplaneinfo` 回调中填充 `lcd_planeinfo_s.bpp` | RGB565(16bpp)、RGB888(24bpp) |
| 初始化命令序列 | `mylcd_send_init_sequence()` | Sleep Out(0x11) → Display ON(0x29) |
| 写入地址设置命令 | `mylcd_setwindow()` 或 `mylcd_setpage()` — putrun/putarea 中调用 | MIPI DCS: CASET(0x2A) + RASET(0x2B) + RAMWR(0x2C)；OLED: page/column 地址命令 |
| SPI 模式与最大频率 | `SPI_SETMODE()` + `SPI_SETFREQUENCY()`（仅 SPI 接口） | Mode 0、10MHz |
| D/C 引脚时序 | 命令/数据切换逻辑（仅 SPI 接口；I2C 通过控制字节区分） | 命令前拉低 D/C，数据前拉高 D/C |
| 复位时序 | `mylcd_hw_reset()` 或软件复位命令(0x01) | RST 拉低 10ms → 拉高 → 等待 120ms |
| 背光控制方式 | `setpower` 回调 | GPIO 开关 / PWM 调光 |
| 电源引脚（VDD、VDDI） | 初始化函数中的上电序列 | VDD → VDDI → RST → init cmds |
| 睡眠/唤醒命令 | `setpower(0)` / `setpower(>0)` | Sleep In(0x10) / Sleep Out(0x11) |
| 显示方向控制 | `MADCTL(0x36)` 命令参数 | 旋转 0°/90°/180°/270° |

#### 从参考驱动提取

| 提取项 | 用途 |
|--------|------|
| putrun/putarea 实现模式 | 窗口设置 + 数据传输的代码模式 |
| SPI 传输封装 | `SPI_SEND` vs `SPI_SNDBLOCK` vs DMA |
| D/C 引脚切换方式 | GPIO 直接操作 vs SPI cmddata 回调 |
| 工作缓冲区大小 | `g_runbuffer` 的分配策略 |
| 注册路径 | `board_lcd_getdev` + `lcddev_register` 或 `fb_register` |

#### 交叉验证规则

1. Datasheet 中的初始化命令序列 → 与参考驱动的 `init_sequence[]` 数组对比，标注差异命令
2. SPI 模式/频率 → 与参考驱动的 `SPI_SETMODE()` / `SPI_SETFREQUENCY()` 对比
3. 如果参考驱动是不同型号面板（如参考 ST7789 开发 ST7735），**标注命令码差异**（如 MADCTL 参数、COLMOD 参数、初始化序列差异）

## 三、关键数据结构

### lcd_planeinfo_s — 数据传输回调（核心）

```c
struct lcd_planeinfo_s
{
  /* 数据传输回调 — 至少实现 putrun + getrun */

  CODE int (*putrun)(FAR struct lcd_dev_s *dev, fb_coord_t row,
                     fb_coord_t col, FAR const uint8_t *buffer,
                     size_t npixels);

  CODE int (*putarea)(FAR struct lcd_dev_s *dev,
                      fb_coord_t row_start, fb_coord_t row_end,
                      fb_coord_t col_start, fb_coord_t col_end,
                      FAR const uint8_t *buffer, fb_coord_t stride);

  CODE int (*getrun)(FAR struct lcd_dev_s *dev, fb_coord_t row,
                     fb_coord_t col, FAR uint8_t *buffer,
                     size_t npixels);

  CODE int (*getarea)(FAR struct lcd_dev_s *dev,
                      fb_coord_t row_start, fb_coord_t row_end,
                      fb_coord_t col_start, fb_coord_t col_end,
                      FAR uint8_t *buffer, fb_coord_t stride);

  CODE int (*redraw)(FAR struct lcd_dev_s *dev);

  /* 工作缓冲区 — 至少 (bpp * xres / 8) 字节 */
  FAR uint8_t *buffer;

  uint8_t bpp;                  /* 每像素位数 */
  FAR struct lcd_dev_s *dev;    /* 反向指针（由 getplaneinfo 填充） */
};
```

| 回调 | 用途 | 必需? |
|------|------|-------|
| `putrun` | 写入一行部分像素数据 | **Yes** |
| `putarea` | 写入矩形区域像素数据（批量传输，性能更优） | Recommended |
| `getrun` | 读取一行部分像素数据 | **Yes** |
| `getarea` | 读取矩形区域像素数据 | Optional |
| `redraw` | 触发整屏刷新（e-ink 等慢速显示器专用） | Conditional |

> [!NOTE] putarea/getarea 为 NULL 时的自动降级
>
> `lcd_dev.c` 上半区在处理 `LCDDEVIO_PUTAREA`/`LCDDEVIO_GETAREA` 时，
> 如果 `putarea`/`getarea` 回调为 NULL，会自动用 `putrun`/`getrun` 逐行模拟。
> `lcd_framebuffer.c` 桥接层同样有此降级逻辑。
> 因此 `putrun`/`getrun` 是最低要求，`putarea` 是性能优化。

> [!IMPORTANT] putarea 的性能优势
>
> 大多数 SPI LCD 控制器支持设置窗口地址后连续写入像素数据。实现 `putarea` 可以：
> - 只发送一次窗口设置命令（而非每行一次）
> - 利用 SPI DMA 批量传输整个区域
> - 性能提升通常 2-5 倍
>
> 如果硬件支持，强烈建议实现 `putarea`。

### lcd_dev_s — LCD 设备操作集

```c
struct lcd_dev_s
{
  /* 配置查询 */
  CODE int (*getvideoinfo)(FAR struct lcd_dev_s *dev,
                           FAR struct fb_videoinfo_s *vinfo);
  CODE int (*getplaneinfo)(FAR struct lcd_dev_s *dev, unsigned int planeno,
                           FAR struct lcd_planeinfo_s *pinfo);

#ifdef CONFIG_FB_CMAP
  CODE int (*getcmap)(FAR struct lcd_dev_s *dev, FAR struct fb_cmap_s *cmap);
  CODE int (*putcmap)(FAR struct lcd_dev_s *dev, FAR const struct fb_cmap_s *cmap);
#endif

  /* 光标控制（需 CONFIG_FB_HWCURSOR） */
#ifdef CONFIG_FB_HWCURSOR
  CODE int (*getcursor)(FAR struct lcd_dev_s *dev,
                        FAR struct fb_cursorattrib_s *attrib);
  CODE int (*setcursor)(FAR struct lcd_dev_s *dev,
                        FAR struct fb_setcursor_s *settings);
#endif

  /* 电源与对比度 */
  CODE int (*getpower)(struct lcd_dev_s *dev);
  CODE int (*setpower)(struct lcd_dev_s *dev, int power);
  CODE int (*getcontrast)(struct lcd_dev_s *dev);
  CODE int (*setcontrast)(struct lcd_dev_s *dev, unsigned int contrast);

  /* 帧率控制 */
  CODE int (*setframerate)(struct lcd_dev_s *dev, int rate);
  CODE int (*getframerate)(struct lcd_dev_s *dev);

  /* 对齐约束查询 */
  CODE int (*getareaalign)(FAR struct lcd_dev_s *dev,
                           FAR struct lcddev_area_align_s *align);

  /* 自定义 ioctl */
  CODE int (*ioctl)(FAR struct lcd_dev_s *dev, int cmd, unsigned long arg);

  /* 设备打开/关闭 */
  CODE int (*open)(FAR struct lcd_dev_s *dev);
  CODE int (*close)(FAR struct lcd_dev_s *dev);
};
```

| Callback | Purpose | Required? |
|----------|---------|-----------|
| `getvideoinfo` | 返回显示配置（分辨率、像素格式） | **Yes** |
| `getplaneinfo` | 返回平面信息（数据传输回调、bpp、工作缓冲区） | **Yes** |
| `setpower` / `getpower` | 面板电源/背光控制（0=关，CONFIG_LCD_MAXPOWER=全亮） | **Yes** |
| `getcursor` / `setcursor` | 硬件光标控制（需 CONFIG_FB_HWCURSOR） | Conditional |
| `setcontrast` / `getcontrast` | 对比度控制 | Optional |
| `setframerate` / `getframerate` | 帧率控制 | Optional |
| `getareaalign` | 返回硬件对齐约束（行/列起始对齐、宽高对齐） | Optional |
| `ioctl` | 自定义 ioctl 透传 | Optional |
| `open` / `close` | 设备打开/关闭 | Optional |

### lcddev_area_align_s — 硬件对齐约束

```c
struct lcddev_area_align_s
{
  uint16_t row_start_align;   /* 起始行对齐 */
  uint16_t height_align;      /* 高度对齐 */
  uint16_t col_start_align;   /* 起始列对齐 */
  uint16_t width_align;       /* 宽度对齐 */
  uint16_t buf_align;         /* 缓冲区地址对齐 */
};
```

> [!NOTE] getareaalign 的默认行为
>
> 如果驱动未实现 `getareaalign` 回调（设为 NULL），`lcd_dev.c` 上半区返回默认值：
> 所有对齐均为 1（无对齐约束），`buf_align = sizeof(uintptr_t)`。
> 仅当硬件有特殊对齐要求时才需要实现此回调。

## 四、核心 API 与驱动操作集

### 注册方式

LCD 驱动不直接调用注册 API。而是通过板级代码提供 `board_lcd_getdev()` 函数返回 `lcd_dev_s` 指针，由上层框架完成注册：

```c
/* 板级代码实现 — 返回 LCD 设备实例 */
FAR struct lcd_dev_s *board_lcd_getdev(int lcddev);

/* 方式 1: lcd_dev 字符设备注册（由 lcd_dev.c 调用） */
int lcddev_register(int devno);  /* → /dev/lcdN */

/* 方式 2: lcd_framebuffer 桥接注册（由 lcd_framebuffer.c 调用） */
int fb_register(int display, int plane);  /* → /dev/fbN */
```

> [!NOTE] 注册流程
>
> 1. 板级代码调用 `board_lcd_initialize()` 初始化 LCD 硬件
> 2. 板级代码调用 `lcddev_register(0)` 或 `fb_register(0, 0)`
> 3. 框架内部调用 `board_lcd_getdev(0)` 获取 `lcd_dev_s` 指针
> 4. 框架创建设备节点 `/dev/lcd0` 或 `/dev/fb0`

## 五、数据传输模式

### putrun — 逐行写入（基础模式）

每次写入一行中的部分像素。所有 LCD 驱动必须实现此回调。

```c
static int mylcd_putrun(FAR struct lcd_dev_s *dev, fb_coord_t row,
                        fb_coord_t col, FAR const uint8_t *buffer,
                        size_t npixels)
{
  FAR struct mylcd_dev_s *priv = (FAR struct mylcd_dev_s *)dev;

  /* 1. 设置 LCD 窗口地址 */
  mylcd_setwindow(priv, col, col + npixels - 1, row, row);

  /* 2. 发送像素数据 */
  mylcd_senddata(priv, buffer, npixels * (priv->bpp >> 3));

  return OK;
}
```

### putarea — 矩形区域写入（推荐模式）

一次写入整个矩形区域，减少窗口设置命令次数，支持 DMA 批量传输。

```c
static int mylcd_putarea(FAR struct lcd_dev_s *dev,
                         fb_coord_t row_start, fb_coord_t row_end,
                         fb_coord_t col_start, fb_coord_t col_end,
                         FAR const uint8_t *buffer, fb_coord_t stride)
{
  FAR struct mylcd_dev_s *priv = (FAR struct mylcd_dev_s *)dev;
  size_t cols = col_end - col_start + 1;
  size_t pixel_size = priv->bpp >> 3;
  fb_coord_t row;

  /* 1. 设置 LCD 窗口地址（只需一次） */
  mylcd_setwindow(priv, col_start, col_end, row_start, row_end);

  /* 2. 逐行发送像素数据 */
  for (row = row_start; row <= row_end; row++)
    {
      mylcd_senddata(priv, buffer, cols * pixel_size);
      buffer += stride;
    }

  return OK;
}
```

### redraw — 整屏刷新（e-ink 专用）

e-ink 等慢速显示器在多次 putrun/putarea 后调用一次 redraw 触发实际刷新。

## 六、框架特性

### lcd_framebuffer 桥接

`CONFIG_LCD_FRAMEBUFFER` 启用后，`lcd_framebuffer.c` 在 RAM 中分配一个与屏幕等大的缓冲区（`stride * yres` 字节），将 `lcd_dev_s` 包装为 `fb_vtable_s`。应用通过 `/dev/fbN` 的 mmap 直接写入 RAM 缓冲区，通过 `FBIO_UPDATE` ioctl 触发桥接层将数据传输到 LCD。

**桥接层刷新策略**（源自 `lcd_framebuffer.c` `lcdfb_updateearea()`）：
- 如果驱动实现了 `putarea` → 调用一次 `putarea` 传输整个更新区域
- 如果驱动仅实现 `putrun` → 逐行调用 `putrun` 传输
- 传输完成后，如果驱动实现了 `redraw` → 调用 `redraw` 触发实际刷新

这使得 LVGL、NX 等图形框架可以透明地使用 LCD 驱动，无需关心底层是 FB 还是 LCD。

> [!IMPORTANT] lcd_framebuffer 的初始化流程
>
> `lcd_framebuffer.c` 实现了 `up_fbinitialize()` 和 `up_fbgetvplane()`，
> 因此通过 `fb_register(display, plane)` 注册。内部流程：
> 1. `up_fbinitialize()` → `board_lcd_initialize()` + `board_lcd_getdev()`
> 2. 分配 RAM 缓冲区（`kmm_zalloc(stride * yres)`）
> 3. 填充 `fb_vtable_s`（updatearea → lcdfb_updateearea）
> 4. 初始刷新整屏 + 设置背光至 75% (`setpower(3*MAXPOWER/4)`)
> 5. `up_fbgetvplane()` 返回 vtable → `fb_register_device()` 创建 `/dev/fbN`

### lcd_dev 字符设备

`CONFIG_LCD_DEV` 启用后，`lcd_dev.c` 将 `lcd_dev_s` 包装为字符设备 `/dev/lcdN`，通过 ioctl 命令传输像素数据：

| ioctl | 用途 |
|-------|------|
| `LCDDEVIO_PUTRUN` | 写入一行像素（参数: `const struct lcddev_run_s *`） |
| `LCDDEVIO_GETRUN` | 读取一行像素（参数: `struct lcddev_run_s *`） |
| `LCDDEVIO_PUTAREA` | 写入矩形区域（参数: `const struct lcddev_area_s *`） |
| `LCDDEVIO_GETAREA` | 读取矩形区域（参数: `struct lcddev_area_s *`） |
| `LCDDEVIO_SETPOWER` / `LCDDEVIO_GETPOWER` | 电源控制 |
| `LCDDEVIO_SETCONTRAST` / `LCDDEVIO_GETCONTRAST` | 对比度控制 |
| `LCDDEVIO_GETPLANEINFO` | 获取平面信息（`struct lcd_planeinfo_s *`） |
| `LCDDEVIO_GETVIDEOINFO` | 获取视频信息（`struct fb_videoinfo_s *`） |
| `LCDDEVIO_SETPLANENO` | 切换当前操作的颜色平面 |
| `LCDDEVIO_SETFRAMERATE` / `LCDDEVIO_GETFRAMERATE` | 帧率控制 |
| `LCDDEVIO_GETAREAALIGN` | 获取硬件对齐约束（`struct lcddev_area_align_s *`） |

**ioctl 数据结构**（定义在 `include/nuttx/lcd/lcd_dev.h`）：

```c
struct lcddev_run_s
{
  fb_coord_t row, col;       /* 起始行、列 */
  FAR uint8_t *data;         /* 像素数据缓冲区 */
  size_t npixels;            /* 像素数量 */
};

struct lcddev_area_s
{
  fb_coord_t row_start, row_end;   /* 起始行、结束行 */
  fb_coord_t col_start, col_end;   /* 起始列、结束列 */
  fb_coord_t stride;               /* 行步长（字节），0 则自动计算 */
  FAR uint8_t *data;               /* 像素数据缓冲区 */
};
```

> [!NOTE] stride 为 0 时的行为
>
> 当 `lcddev_area_s.stride` 为 0 时，`lcd_dev.c` 自动计算：
> `stride = (col_end - col_start + 1) * pixel_size`。
> 仅当数据缓冲区的行宽大于实际写入区域时才需要显式设置 stride。


## 七、设备私有结构与注册

### 设备私有结构

```c
struct mylcd_dev_s
{
  struct lcd_dev_s dev;             /* 必须是第一个成员 (可强转) */
  FAR struct spi_dev_s *spi;       /* SPI 总线接口 */
  uint8_t power;                   /* 当前电源/背光级别 */
  uint8_t contrast;                /* 当前对比度 */

  /* 硬件控制引脚 */
  int dc_pin;                      /* Data/Command 引脚 */
  int rst_pin;                     /* Reset 引脚 */
  int bl_pin;                      /* Backlight 引脚 */
};
```

### 静态数据定义

```c
/* 工作缓冲区 — 至少 (bpp * xres / 8) 字节 */
static uint16_t g_runbuffer[MYLCD_XRES];

/* 视频信息 */
static const struct fb_videoinfo_s g_videoinfo =
{
  .fmt     = FB_FMT_RGB16_565,
  .xres    = MYLCD_XRES,
  .yres    = MYLCD_YRES,
  .nplanes = 1,
};

/* 平面信息 — 包含数据传输回调 */
static const struct lcd_planeinfo_s g_planeinfo =
{
  .putrun  = mylcd_putrun,
  .putarea = mylcd_putarea,
  .getrun  = mylcd_getrun,
  .buffer  = (FAR uint8_t *)g_runbuffer,
  .bpp     = MYLCD_BPP,
};

/* LCD 设备实例 */
static struct mylcd_dev_s g_lcddev =
{
  .dev =
  {
    .getvideoinfo = mylcd_getvideoinfo,
    .getplaneinfo = mylcd_getplaneinfo,
    .getpower     = mylcd_getpower,
    .setpower     = mylcd_setpower,
    .getcontrast  = mylcd_getcontrast,
    .setcontrast  = mylcd_setcontrast,
  },
};
```

### 初始化函数 (Public API)

```c
FAR struct lcd_dev_s *mylcd_lcdinitialize(FAR struct spi_dev_s *spi)
{
  FAR struct mylcd_dev_s *priv = &g_lcddev;

  priv->spi = spi;

  /* 硬件复位 */
  mylcd_hw_reset(priv);

  /* 发送初始化命令序列 */
  mylcd_send_init_sequence(priv);

  /* 清屏 */
  mylcd_clear(priv, 0x0000);

  /* 返回 lcd_dev_s 指针 */
  return &priv->dev;
}
```

> [!NOTE] LCD 驱动通常使用静态分配
>
> 与 sensor/fb 驱动不同，大多数 in-tree LCD 驱动使用静态全局变量（`g_lcddev`）而非动态分配。
> 这是因为嵌入式系统通常只有一个 LCD 面板，静态分配节省堆内存。
> 如果需要支持多个同型号 LCD，可改为动态分配。

### 公共头文件

```c
#ifndef __INCLUDE_NUTTX_LCD_MYLCD_H
#define __INCLUDE_NUTTX_LCD_MYLCD_H

#include <nuttx/config.h>

#ifdef CONFIG_LCD_MYLCD

struct spi_dev_s;  /* Forward reference */

#ifdef __cplusplus
#define EXTERN extern "C"
extern "C"
{
#else
#define EXTERN extern
#endif

FAR struct lcd_dev_s *mylcd_lcdinitialize(FAR struct spi_dev_s *spi);

#undef EXTERN
#ifdef __cplusplus
}
#endif

#endif /* CONFIG_LCD_MYLCD */
#endif /* __INCLUDE_NUTTX_LCD_MYLCD_H */
```

### getplaneinfo 实现要点

```c
static int mylcd_getplaneinfo(FAR struct lcd_dev_s *dev,
                              unsigned int planeno,
                              FAR struct lcd_planeinfo_s *pinfo)
{
  DEBUGASSERT(dev && pinfo && planeno == 0);
  memcpy(pinfo, &g_planeinfo, sizeof(struct lcd_planeinfo_s));

  /* 关键：必须设置 dev 反向指针 */
  pinfo->dev = dev;
  return OK;
}
```

> [!IMPORTANT] pinfo->dev 必须设置
>
> `getplaneinfo` 回调中必须将 `pinfo->dev` 设置为当前 `lcd_dev_s` 指针。
> 上半区（lcd_dev.c 和 lcd_framebuffer.c）通过此指针回调 putrun/putarea。
> 遗漏此赋值会导致空指针崩溃。

### LCD 驱动功能 Checklist 模板（供 Agent Step B 使用）

当 Agent 生成 LCD 面板驱动的 `requirements.md` 时，使用以下功能 Checklist 替代 sensor 的默认模板。默认勾选规则基于 datasheet 支持情况：

```markdown
## 功能 Checklist

### 必需功能（不可取消）
- [x] getvideoinfo — 返回分辨率、像素格式、平面数
- [x] getplaneinfo — 返回数据传输回调、bpp、工作缓冲区（含 pinfo->dev 反向指针）
- [x] putrun — 逐行像素写入（最低要求）
- [x] getrun — 逐行像素读取（最低要求）
- [x] setpower / getpower — 面板电源/背光控制
- [x] 初始化命令序列 — 从 datasheet 提取完整 init sequence
- [x] 复位 — 硬件 RST 引脚时序或软件复位命令（0x01），取决于面板硬件
- [x] 写入地址设置 — 窗口地址（CASET/RASET/RAMWR，MIPI DCS 兼容面板）或页/列地址（SSD1306 等 OLED），取决于面板类型

### 推荐功能（默认勾选，研发可取消）
- [x] putarea — 矩形区域批量写入（性能优化，减少地址设置命令次数）
- [x] D/C 引脚管理 — 命令/数据模式切换（仅 SPI 接口面板；I2C 面板通过控制字节区分）
- [x] Sleep In / Sleep Out — 低功耗模式（如 datasheet 支持）
- [x] 显示方向控制 — MADCTL 或等效寄存器配置旋转/镜像

### 可选功能（默认不勾选，研发按需开启）
- [ ] getarea — 矩形区域批量读取
- [ ] setcontrast / getcontrast — 对比度控制
- [ ] setframerate / getframerate — 帧率控制
- [ ] getareaalign — 硬件对齐约束查询
- [ ] redraw — 整屏刷新触发（e-ink 等慢速显示器专用）
- [ ] open / close — 设备引用计数
- [ ] 自定义 ioctl — 厂商私有命令透传
- [ ] 部分刷新 — 仅更新脏区域（需面板支持 Partial Mode）
- [ ] 色彩反转 — Display Inversion ON/OFF
- [ ] Idle 模式 — 降低色深节省功耗（如 datasheet 支持）
- [ ] Gamma 校正 — 自定义 gamma 曲线
```

### LCD 驱动 requirements.md 模板（供 Agent Step B 使用）

LCD 面板驱动的 requirements.md 使用以下章节结构替代 sensor 的默认模板：

1. **面板概述**: 芯片型号（如 ST7789V）、分辨率、像素格式、接口类型（SPI/I2C/并行）
2. **接口规格**: SPI 模式/最大频率（或 I2C 地址/频率）、D/C 引脚（仅 SPI）、RST 引脚（如有）、背光引脚
3. **初始化命令序列**: 从 datasheet 提取的完整 init sequence（命令码 + 参数 + 延时），以表格或数组形式列出
4. **写入地址设置**: 窗口地址（CASET/RASET/RAMWR）或页/列地址命令格式、坐标计算方式
5. **数据传输模式**: putrun vs putarea 选择理由、SPI 传输方式（`SPI_SEND` / `SPI_SNDBLOCK` / DMA）
6. **设备接口路径**: lcd_dev（`/dev/lcdN`）vs lcd_framebuffer（`/dev/fbN`）选择理由
7. **电源管理**: 上电序列（VDD→VDDI→RST→init）、Sleep In/Out 命令、背光控制方式
8. **代码流程**: 初始化序列、putrun/putarea 流程、setpower 流程（伪代码或流程图）
9. **功能 Checklist**: 上述 Checklist 模板（研发在交互 2 中确认）
10. **参考驱动**: 骨架参考（如 st7789.c）和芯片参考的匹配结果及关键差异

## 八、完整 Bring-Up 流程

```
1. Kconfig: CONFIG_LCD=y, CONFIG_LCD_MYLCD=y in defconfig
       │  可选: CONFIG_LCD_DEV=y (→ /dev/lcdN)
       │  可选: CONFIG_LCD_FRAMEBUFFER=y (→ /dev/fbN)
       │
2. Make.defs / CMakeLists.txt: mylcd.c added to build
       │
3. Board init: board_lcd_initialize()
       │  spi = stm32_spibus_initialize(LCD_SPI_BUS)
       │  mylcd_lcdinitialize(spi)
       │
4. board_lcd_getdev(0):
       │  return &g_lcddev.dev  /* 返回 lcd_dev_s 指针 */
       │
5. 路径 A — lcd_dev:
       │  lcddev_register(0)
       │  → board_lcd_getdev(0) → register_driver("/dev/lcd0")
       │
   路径 B — lcd_framebuffer:
       │  fb_register(0, 0)
       │  → up_fbinitialize(0) → board_lcd_initialize() + board_lcd_getdev(0)
       │  → 分配 RAM 缓冲区 → fb_register_device("/dev/fb0")
       │
6. Application (lcd_dev):
       │  fd = open("/dev/lcd0", O_RDWR)
       │  struct lcddev_area_s area = { ... };
       │  ioctl(fd, LCDDEVIO_PUTAREA, &area)
       │
   Application (lcd_framebuffer):
       │  fd = open("/dev/fb0", O_RDWR)
       │  fbmem = mmap(...)
       │  /* 写入 fbmem */
       │  ioctl(fd, FBIO_UPDATE, &area)
```

## 九、测试方法

### lcd_dev 接口测试

```c
int fd = open("/dev/lcd0", O_RDWR);

/* 获取显示信息 */
struct fb_videoinfo_s vinfo;
ioctl(fd, LCDDEVIO_GETVIDEOINFO, &vinfo);

/* 设置电源（0=关，CONFIG_LCD_MAXPOWER=全亮） */
ioctl(fd, LCDDEVIO_SETPOWER, CONFIG_LCD_MAXPOWER);

/* 写入矩形区域 */
struct lcddev_area_s area;
area.row_start = 0;
area.row_end   = vinfo.yres - 1;
area.col_start = 0;
area.col_end   = vinfo.xres - 1;
area.stride    = 0;  /* 0 = 自动计算，等效于 vinfo.xres * pixel_size */
area.data      = pixel_buffer;
ioctl(fd, LCDDEVIO_PUTAREA, &area);

/* 写入单行像素 */
struct lcddev_run_s run;
run.row     = 0;
run.col     = 0;
run.data    = line_buffer;
run.npixels = vinfo.xres;
ioctl(fd, LCDDEVIO_PUTRUN, &run);

close(fd);
```

### lcd_framebuffer 接口测试

与 framebuffer 测试方法相同，参见 `fb_pattern.md` 的测试章节。

### SPI 通信验证

```bash
# 使用逻辑分析仪或示波器验证：
# 1. SPI 时钟频率是否正确
# 2. D/C 引脚在命令/数据切换时的时序
# 3. CS 引脚的片选行为
# 4. 初始化命令序列是否完整发送
```

## 十、Cross References

- 主 SKILL.md — 驱动通识知识（编码规范、内核 API、中断规则、同步原语等）
- `references/fb_pattern.md` — Framebuffer 驱动模式（独立显存的显示控制器）
- `references/bus_access.md` — I2C/SPI 总线访问 API 参考
- `references/coding_rules.md` — 编码规范、内核 API、中断规则
- `references/board_registration.md` — 板级驱动注册模式
- `references/nuttx_nav_search.md` — 驱动示例路径和 NuttX 树导航
