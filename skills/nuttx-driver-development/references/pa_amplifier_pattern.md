# PA Amplifier Driver Pattern — I2S 智能功放驱动框架

本文档是 audio PA (Power Amplifier) 驱动子系统的架构规范与约束规则参考。
由主 SKILL.md 的 Driver Type Dispatch Table 自动路由加载。

> 本文档基于 AW88266A、CS35L42、CS35L41B、FS1818、FS1999 五个生产级功放驱动总结。
> 仅覆盖简单 PA（无 DSP 固件加载）。DSP PA 参见 [`pa_dsp_pattern.md`](pa_dsp_pattern.md)。
> 板级注册和 Kconfig 参见 [`pa_board_integration.md`](pa_board_integration.md)。
> 代码模板参见 [`pa_codegen_templates.md`](pa_codegen_templates.md)。

> **适用范围**：仅适用于 I2S 接口的外置智能功放芯片。内置 codec 功放走 audio lower-half 框架，不适用本文档。

## Phase 0: Chip Spec Input

在开始编写任何驱动代码之前，必须先准备目标芯片的规格文档，存放在 `references/chips/{chip}_spec.md`。
格式定义在 [`references/pa_chip_spec_template.md`](pa_chip_spec_template.md)。

如果 chip spec 缺少以下信息，AI 应询问用户：

| 决策 | 选项 | 影响 |
|------|------|------|
| 芯片型号 | 具体型号 | 决定寄存器位宽、DSP 支持、校准方式 |
| I2C 配置 | 地址/频率/端口 | 通信参数 |
| GPIO 控制 | VDD/Reset/IRQ 引脚 | 上电序列 |
| 场景模式 | MUSIC/VOICE/SCO 等 | ioctl 命令设计 |
| 是否支持校准 | Yes/No | 决定是否需要 OTP/KVDB 集成 |

## Phase 0.5: Chip Spec 优先级规则

> **关键原则：Chip Spec 只描述硬件事实，驱动设计决策由 Pattern 推导。**
>
> Chip spec（`references/chips/{chip}_spec.md`）只应包含从 datasheet 可提取的硬件信息：
> 寄存器、协议、时序、电气特性。不应包含 `stop_sequence`、`configure_flow`、
> `ops_callbacks` 等驱动软件设计决策。
>
> 驱动设计决策由本 pattern 文档根据 chip spec 中的硬件特性字段自动推导：

### 推导规则快速查表

> 以下汇总表将 chip spec 字段映射到驱动设计决策，供快速查找。
> 详细推导逻辑见下方各规则。

| chip spec 条件 | stop 行为 | configure 时机 | stub 层 | start 流程 |
|---------------|-----------|---------------|---------|-----------|
| `has_dsp=false`, `calibration=false`, `max_devices=1` | 空实现 (return OK) | 直接写寄存器 | 可简化 | PWD→PLL→AMP→unmute→volume |
| `has_dsp=false`, `calibration=false`, `max_devices>1` | 空实现 (return OK) | 只保存参数 | 完整 stub | stub_init→stub_spkon→volume |
| `has_dsp=true` 或 `calibration=true` | stub_spkoff + stub_deinit | 只保存参数 | 完整 stub | stub_init→stub_spkon→volume |

### 推导规则表

> **规则 1：stop 行为**
> ```
> 如果 has_dsp == false 且 calibration.supported == false：
>   → stop 空实现（return OK）
>   → 理由：无 DSP 状态需要清理，保持功放上电避免 pop 噪声
> 如果 has_dsp == true 或 calibration.supported == true：
>   → stop 执行 stub_spkoff + stub_deinit
>   → 理由：需要清理 DSP 保护状态或校准上下文
> ```
>
> **规则 2：configure 写寄存器时机**
> ```
> 如果 max_devices == 1 且 has_dsp == false：
>   → configure 中直接写寄存器（set_device + set_channel + set_rate + set_width + set_blck）
>   → 理由：单设备无时序协调需求，I2C 在 configure 时已就绪
> 如果 max_devices > 1：
>   → configure 只保存参数，延迟到 cold_start 统一写入
>   → 理由：多设备需要在 start 时协调时序
> ```
>
> **规则 3：是否需要 stub 层**
> ```
> 如果 max_devices > 1 或 has_dsp == true 或 calibration.supported == true：
>   → 需要完整 stub 层（detect_devices / stub_init / stub_spkon / stub_spkoff）
> 如果 max_devices == 1 且 has_dsp == false 且 calibration.supported == false：
>   → 可以简化，start 直接执行 PWD→PLL→AMP→unmute 序列
> ```
>
> **规则 4：start 流程**
> ```
> 简单 PA（max_devices==1, no DSP, no calibration）：
>   → run_pwd(false) → pll_check → run_amp(true) → run_mute(false) → set_volume(100)
> 复杂 PA（多设备/DSP/校准）：
>   → stub_init → stub_spkon → volume → usleep(10ms) → g_audio_status=1
> ```
>
> **规则 5：MULTI_SESSION（固定规则，不依赖芯片特性）**
> ```
> 所有 ops 回调（configure/start/stop/pause/resume/reserve/release）
> 必须支持 CONFIG_AUDIO_MULTI_SESSION 编译分支。
> 这是 NuttX audio 框架的硬性要求，与芯片无关。
> ```

## 一、架构概述

PA 功放驱动作为 `audio_comp` 的 companion device，不直接处理 PCM 数据流，
而是控制外部功放芯片的电源、增益、场景切换和校准。

### 架构模型

```
ALSA-lib / upper-half
    │
    ▼  audio_register("pcm0p", comp_dev)
┌──────────────────────────────────────┐
│ audio_comp (组合设备)                 │
│ - 将 codec lower-half 和 PA lower-half│
│   组合为一个逻辑设备                  │
│ - start/stop/configure 同时下发到两者 │
└──────────────────────────────────────┘
    │                    │
    ▼                    ▼
┌──────────┐      ┌──────────────┐
│ Codec    │      │ PA Amplifier │ ← 你的驱动
│ lower-half│      │ lower-half   │
└──────────┘      └──────────────┘
    │                    │
    ▼                    ▼
  DMA/I2S             I2C Bus
```

### 文件布局

```
vendor/xiaomi/vela/drivers/audio/
├── {chip}/
│   ├── {chip}.c              # 主驱动实现
│   └── {chip}.h              # 寄存器定义 + 私有结构体
├── pa_bsp.c                  # 公共 I2C/GPIO/IRQ 访问层
└── pa_bsp.h                  # 公共 BSP 接口

# 公共头文件（NuttX 系统级，定义 lower_s + initialize 原型）
nuttx/include/nuttx/audio/{chip}.h
```

### 公共头文件 vs 私有头文件的职责划分

> **关键约束**：PA 驱动涉及两个头文件，职责严格分离，**不能重复定义类型**。

| 内容 | 公共头文件 `nuttx/audio/{chip}.h` | 私有头文件 `{chip}.h` |
|------|----------------------------------|---------------------|
| `{chip}_lower_s` | ✅ 定义 | ❌ 不定义，include 公共头文件 |
| `scene_table_s` | ✅ 定义 | ❌ 从公共头文件继承 |
| `{chip}_initialize` | ✅ 声明 | ❌ 不重复声明 |
| 寄存器地址宏 | ❌ | ✅ 定义 |
| `{chip}_dev_s` | ❌ | ✅ 定义 |
| packed 命令结构体 | ❌ | ✅ 定义 |
| 枚举类型 | ❌ | ✅ 定义 |
| `ARRAY_SIZE` 宏 | ❌ | ✅ 定义 |
| 校准编译宏 | ❌ | ✅ 定义 |
| `late_initialize` 声明 | ❌ | ✅ 声明 |

> **私有头文件必须 include 公共头文件**：
> ```c
> #include <nuttx/audio/{chip}.h>
> ```

> **scene_table_s.len 的类型**：公共头文件中 `len` 字段为 `uint32_t`，
> 驱动内部的 `write_reg_table` 中 index 变量也必须使用 `uint32_t`。

## 二、I2C 访问层规则

PA 驱动使用 direct 模式（直接 NuttX I2C API），三层架构：

### 三层 API 签名

| 层 | 函数 | 签名 | 说明 |
|---|------|------|------|
| L0 | `{chip}_i2c_write` | `(priv, regaddr, value_buf, len)` | 底层 I2C 传输 |
| L0 | `{chip}_i2c_read` | `(priv, regaddr, regval_buf, len)` | 底层 I2C 传输 |

> **L0 层 i2c_write 缓冲区**：内部使用 `uint8_t buf[512]` 大缓冲区，
> 因为 REG_BULK 命令的 payload 可能较大。不能缩小为 8 字节。
>
> **L0 层 i2c_write 参数类型**：`value_buf` 为 `FAR uint8_t *`（非 const），
> `len` 为 `uint8_t`。
>
> **L0 层 I2C_RESET 错误处理**：`I2C_RESET` 后检查返回值，
> 如果 `ret < 0 && ret != -EIO` 则提前 break 退出重试循环。
> 只有 `-EIO` 或成功时才继续重试。
| L1 | `{chip}_reg_write` | `(priv, regaddr, uint16_t value)` | 带内层重试，标量参数 |
| L1 | `{chip}_reg_read` | `(priv, regaddr, uint16_t *value)` | 带内层重试，标量参数 |
| L1 | `{chip}_reg_read_raw` | `(priv, regaddr, uint8_t *buf)` | 读原始字节（校准用） |
| L2 | `{chip}_reg_bits_write` | `(priv, regaddr, mask_hi, mask_lo, val_hi, val_lo)` | 6 个标量参数 |

> **关键**：`reg_write`/`reg_read` 使用标量参数，不使用 `struct reg_set*`。
> `reg_bits_write` 使用 6 个标量参数，不使用 `struct reg_bits*`。

### I2C 层级选择核心原则

| 场景 | 应使用层级 | 原因 |
|------|-----------|------|
| 命令表批量执行（REG_SET） | **L0** | 外层已有重试，不需要内层重试 |
| 命令表中 REG_BITS | **L2** | 需要 read-modify-write |
| 独立寄存器操作 | **L1** | 需要内层重试保证可靠性 |
| OTP 操作 | **L0** | OTP 有自己的重试/等待逻辑 |
| 批量备份/恢复 | **L0** | 与命令表写入保持一致 |

> **核心原则**：命令表执行、OTP 操作、批量备份/恢复走 L0；独立寄存器操作走 L1；read-modify-write 走 L2。

> **Read-Modify-Write 优化**：`reg_bits_write` 内部应检查 `if (new_val == old_val) return 0;`，
> 跳过无变化的写入以减少 I2C 流量。

> **`reg_bits_write` 内部读/写使用 L0**：避免双重重试。

## 三、关键数据结构

### 3.1 板级配置结构 (公共头文件)

```c
struct {chip}_scene_table_s
{
  uint32_t len;                /* 表长度（字节数），必须是 uint32_t */
  const uint8_t *scene_data;   /* 命令表数据指针 */
};

struct {chip}_lower_s
{
  int  i2c_port;       /* I2C 端口号 */
  int  i2c_addr;       /* I2C 地址 */
  int  i2c_freq;       /* I2C 频率 */
  int  reset_gpio;     /* Reset GPIO (-1 if unused) */
  int  vdd_gpio;       /* VDD enable GPIO (-1 if unused) */
  /* ... 芯片特有字段（fw_tcoef/fw_ratio 等） */
  /* ... 场景表（init_table/music_table 等） */
  /* ... 回调函数（reset/power_en 等） */
};
```

### 3.2 设备私有结构

```c
struct {chip}_dev_s
{
  struct audio_lowerhalf_s dev;       /* 必须是第一个成员 */
  const FAR struct {chip}_lower_s *lower;
  FAR struct i2c_master_s *i2c;       /* direct 模式必需 */

  uint16_t samprate;
#ifndef CONFIG_AUDIO_EXCLUDE_VOLUME
#ifndef CONFIG_AUDIO_EXCLUDE_BALANCE
  uint16_t balance;
#endif
  uint8_t  volume;
#endif
  uint8_t  nchannels;
  uint8_t  bpsamp;
  uint32_t bclk;
  bool     paused;
  bool     mute;

  /* configure 中保存的原始参数（用于 cold_start） */
  uint8_t  channel_num;              /* 1CH 强制转 2CH 后的值 */
  uint8_t  bits;
  uint16_t sample_rate;
};
```

### 3.3 多设备架构

即使 `DEV_MAX=1`，代码结构**必须保持多设备遍历模式**：

```c
static struct frsm_dev frsm_device[FRSM_DEV_MAX];
static uint8_t frsm_ndev;

for (idx = 0; idx < frsm_ndev; idx++) {
    dev = frsm_device + idx;
    if (dev->skip_set) continue;
    /* ... */
}
```

Per-device 结构包含：`addr`, `id`, `is_on`, `skip_set`, `bypass_dsp`, `devid`,
`chn_mask`, `cur_scene`, `spkre`, `spkre_min`, `spkre_max`, `err_code`。

## 四、audio_ops_s 回调规范

### 4.1 ops 函数指针表

> **必须为 NULL 的回调**：shutdown, pause, resume, reserve, release,
> allocbuffer, freebuffer, enqueuebuffer, cancelbuffer, read, write。
>
> **start/stop 为公开函数（非 static）**，可能被板级代码直接调用。
>
> **所有 ops 回调必须支持 `CONFIG_AUDIO_MULTI_SESSION` 编译分支**。
> NuttX audio 框架在开启 `CONFIG_AUDIO_MULTI_SESSION` 时会改变 ops 函数指针签名
> （增加 `session` 参数），不加分支会导致编译错误。
> 受影响的回调：configure, start, stop, pause, resume, reserve, release。
>
> **ops 表条件编译分支**：
> - `.stop` 受 `CONFIG_AUDIO_EXCLUDE_STOP` 保护
> - `.pause`/`.resume` 受 `CONFIG_AUDIO_EXCLUDE_PAUSE_RESUME` 保护
> 注意：不是 `CONFIG_AUDIO_EXCLUDE_VOLUME`。

### 4.2 getcaps

- `ac_channels` 在 QUERY 和 OUTPUT 中都报告 `2`（stereo）
- QUERY 中处理 `AUDIO_FMT_MIDI`（返回 SUBFMT_END）和 `AUDIO_FMT_PCM`（返回 SUBFMT_PCM_S16_LE）
- FEATURE 中报告 VOLUME/BASS/TREBLE/BALANCE
- PROCESSING 中处理 `AUDIO_PU_STEREO_EXTENDER`
- 可选采样率用 CONFIG 宏保护（如 44K）

#### getcaps 精确实现细节（NuttX 框架协议）

> 以下是 NuttX audio upper-half 框架要求的精确 bitmap 值和分支逻辑。
> datasheet 中不会有这些信息，但生成驱动代码时必须精确实现。
> 如果不确定，使用以下默认值（从已有生产级驱动总结）。
> 芯片支持的采样率/位宽/通道数从 chip spec 的 `audio_capabilities` 字段获取。

| ac_type | ac_format / sub | 设置字段 | 值 |
|---------|----------------|----------|-----|
| AUDIO_TYPE_QUERY | AUDIO_TYPE_QUERY | `ac_controls.b[0]` | `AUDIO_TYPE_OUTPUT \| AUDIO_TYPE_FEATURE \| AUDIO_TYPE_PROCESSING` |
| | | `ac_format.hw` | `(1 << (AUDIO_FMT_PCM - 1))` |
| AUDIO_TYPE_QUERY | AUDIO_FMT_MIDI | `ac_controls.b[0]` | `AUDIO_SUBFMT_END` |
| AUDIO_TYPE_QUERY | AUDIO_FMT_PCM | `ac_controls.b[0]` | `AUDIO_SUBFMT_PCM_S16_LE` |
| | | `ac_controls.b[1]` | `AUDIO_SUBFMT_END` |
| AUDIO_TYPE_QUERY | default | `ac_controls.b[0]` | `AUDIO_SUBFMT_END` |
| AUDIO_TYPE_OUTPUT | AUDIO_TYPE_QUERY | `ac_controls.hw[0]` | `AUDIO_SAMP_RATE_8K \| AUDIO_SAMP_RATE_16K \| AUDIO_SAMP_RATE_48K` |
| | | | 44K 受 `CONFIG_AUDIO_{CHIP}_SUPPORT_44K` 条件编译保护 |
| AUDIO_TYPE_FEATURE | AUDIO_FU_UNDEF | `ac_controls.b[0]` | `AUDIO_FU_VOLUME \| AUDIO_FU_BASS \| AUDIO_FU_TREBLE` |
| | | `ac_controls.b[1]` | `AUDIO_FU_BALANCE >> 8` |
| AUDIO_TYPE_PROCESSING | AUDIO_PU_UNDEF | `ac_controls.b[0]` | `AUDIO_PU_STEREO_EXTENDER` |
| AUDIO_TYPE_PROCESSING | AUDIO_PU_STEREO_EXTENDER | `ac_controls.b[0]` | `AUDIO_STEXT_ENABLE \| AUDIO_STEXT_WIDTH` |

### 4.3 configure

> **必须支持 `CONFIG_AUDIO_MULTI_SESSION` 编译分支**。
>
> **configure 写寄存器时机由推导规则 2 决定**（参见 Phase 0.5）：
> - **简单单设备 PA**（`max_devices == 1`，无 DSP）：configure 中直接写寄存器
>   （set_device + set_channel + set_rate + set_width + set_blck）。
> - **多设备 PA**（`max_devices > 1`）：configure 中只保存参数到
>   `priv->sample_rate`/`channel_num`/`bits`，不立即写寄存器，延迟到 cold_start。
>
> 通道数修正：1CH 强制转 2CH（仅多设备 PA 需要，简单 PA 由 set_channel 内部处理）。
> FEATURE 分支有 audinfo 日志。

#### configure 字段映射（NuttX audio_caps_s union）

> NuttX `audio_caps_s` 是一个 union 结构，不同 `ac_type` 下字段含义不同。
> 以下映射关系是框架约定，必须精确遵守。

| ac_type | 字段 | 来源 | 说明 |
|---------|------|------|------|
| AUDIO_TYPE_OUTPUT | sample_rate | `caps->ac_controls.hw[0]` | 采样率 |
| | bits | `caps->ac_controls.b[2]` | 位宽 |
| | channels | `caps->ac_channels` | 通道数 |
| AUDIO_TYPE_FEATURE | subtype | `caps->ac_format.hw` | FEATURE 分支的 switch 判断字段 |
| | volume | `caps->ac_controls.hw[0]` | 音量值 |
| | tone | `caps->ac_controls.b[0]` | 低音/高音值 |

> **FEATURE 分支条件编译**：
> - `AUDIO_FU_VOLUME` → audinfo（受 `CONFIG_AUDIO_EXCLUDE_VOLUME` 保护）
> - `AUDIO_FU_BASS` → audinfo（受 `CONFIG_AUDIO_EXCLUDE_TONE` 保护）
> - `AUDIO_FU_TREBLE` → audinfo（受 `CONFIG_AUDIO_EXCLUDE_TONE` 保护）

### 4.4 ioctl

> `AUDIOIOC_SETPARAMTER` 的 `arg` 是 `char*` 字符串指针，使用 `strcmp` 精确匹配。
> `get_pa_chipId` 和 `get_pa_status` 通过函数**返回值**传递结果。
> 场景切换只设置全局变量 `s_audioSenceType`，在下次 `cold_start` 时生效。

## 五、命令表解析 (write_reg_table)

### 命令编码格式

| 类型 | 首字节 | 格式 | 步进 |
|------|--------|------|------|
| REG_SET | 0x00-0xF8 | `[addr, data_hi, data_lo]` | 3 |
| REG_BITS | 0xFE | `[0xFE, reg, mask_hi, mask_lo, val_hi, val_lo]` | 6 |
| REG_BULK | 0xFD | `[0xFD, size, data...]` | 2+size |
| DELAY | 0xFF | `[0xFF, delay_hi, delay_lo]` | 3 |

### 关键约束

> **空表处理**：当 `table == NULL` 或 `table->len == 0` 或 `table->scene_data == NULL` 时，
> **必须返回 `OK`**（静默跳过），**不能返回 `-EINVAL`**。

> **REG_SET 使用 L0 层**：`frsm_i2c_write`，不使用 `frsm_reg_write`。

> **DELAY 分支直接用 `usleep(delay_ms * 1000)`**，不用 `frsm_delay_ms()`。

> **尾部校验**：解析完成后应验证 `index == table->len`，不匹配则输出警告日志。

## 六、上电与初始化

### initialize 函数

```c
/* direct 模式 — 接收 I2C master */
FAR struct audio_lowerhalf_s *
{chip}_initialize(FAR struct i2c_master_s *i2c,
                  FAR struct {chip}_lower_s *lower);
```

- initialize 中只做最小初始化：复位 + 检测 chip ID（通过 `detect_devices`）
- 完整寄存器配置在 `cold_start`（ops.start）中执行
- **必须使用 `detect_devices` 做 chip ID 验证**，同时完成设备数组初始化
- `late_initialize` 直接遍历已有 `frsm_device[]`，不重新 `detect_devices`

### 平台封装函数

```c
FAR struct audio_lowerhalf_s *
{chip}_initialize(FAR struct {chip}_lower_s *lower)
{
  struct i2c_adapter *adap = i2c_get_adapter(lower->i2c_port);
  up_mdelay(20);
  return {chip}_initialize(adap->master, lower);
}
```

### GPIO 辅助函数

> GPIO 操作定义为独立 static 函数，使用芯片系列名（不带型号后缀）：
> ```c
> static void {chip_family}_pin_reset(int16_t gpio_index, bool status)
> {
>   pa_set_regulator_pinen(gpio_index, status);
> }
> ```

## 七、cold_start / stop 流程

### cold_start（ops.start 实际实现）

```
1. frsm_stub_init (重新初始化: detect_devices + init_table + set_scene + spkoff)
2. 计算 BCLK = bits * channel_num * sample_rate
3. 设置 bypass 标志 (params.rsvd[0]) 和默认音量
4. frsm_stub_spkon (场景/PLL/保护/bypass/spkon)
5. 全局音量覆盖 (g_volume != 0 时)
6. frsm_stub_volume (chn_mask=0xFF)
7. usleep(10ms) — 在 volume 之后
8. g_audio_status = 1
```

> **错误处理策略**：cold_start 不检查 `stub_init` 和 `stub_spkon` 的返回值，
> 采用 fire-and-forget 模式，总是继续执行到最后设置 `g_audio_status = 1`。
> 在 DEBUG 模式下应记录返回值供诊断：
> ```c
> ret = frsm_stub_init(priv);
> fsm_debug("stub_init ret=%d", ret);
> /* ... */
> ret = frsm_stub_spkon(priv, &params);
> fsm_debug("stub_spkon ret=%d", ret);
> ```

> **bypass 标志传递**：通过 `spkr_hw_params.rsvd[0]` 传递给 `stub_spkon`。

### stop

> **stop 的行为由推导规则 1 决定**（参见 Phase 0.5）：
> - 简单 PA（无 DSP，无校准）：stop 直接 `return OK`。
>   PA 的真正关闭由 `shutdown` 回调负责。stop 只表示"数据流暂停"，
>   功放保持上电状态以避免重启时的 pop 噪声。
> - 复杂 PA（有 DSP 或校准）：执行完整流程：
>
> ```
> 1. frsm_stub_spkoff
> 2. frsm_stub_deinit
> 3. g_audio_status = 0
> ```

## 八、Speaker On/Off 约束

### spkon/spkoff 命令表

> **spkon/spkoff 命令表必须定义为文件级 `static const uint8_t[]` 数组**，
> 不要定义为函数内局部变量。

> **spkon 函数只执行 spkon_table 命令序列**，不重复写 init_table 或 lock_table。
> init_table 在 `stub_init` 中已经写过。
> spkon 成功后设置 `dev->is_on = 1`；spkoff 成功后设置 `dev->is_on = 0`。

### hw_params 调用顺序

> `set_samplerate` → `set_pll` → `set_spk_prot`。
> 采样率必须先于 PLL 配置。

### set_pll 不检查单步返回值

> 三个 PLL 寄存器写入不逐步检查返回值，全部写完后统一返回。

## 九、Stub 层规范

### 防重入保护

> `stub_spkon` 和 `stub_spkoff` 必须检查 `is_spkon` 全局状态：
> - `stub_spkon` 开头：`if (is_spkon) { fsm_alert("Skip..."); return OK; }`
> - `stub_spkoff` 开头：`if (!is_spkon) { fsm_alert("Skip..."); return OK; }`
> 防重入检查在 stub 层做，不在底层 `spkon`/`spkoff` 中做。

### is_spkon 设置时机

> **`stub_spkon`**：防重入检查通过后，**立即设置 `is_spkon = 1`**（在两遍循环之前），
> 采用乐观策略防止并发重入。不要等到循环完成后才设置。
>
> **`stub_spkoff`**：防重入检查通过后，**立即设置 `is_spkon = 0`**（在循环之前），
> 然后再执行 spkoff 操作。

### stub_spkon 两遍循环

> 不能合并为一个循环。
> - 第一遍：`set_scene` + `set_hw_params`，记录 `dev->err_code`
> - 第二遍：`dev->bypass_dsp = params->rsvd[0]`（在第二遍赋值），`bypass_dsp_scene` + `spkon`
> - 第二遍跳过 `skip_set || err_code` 的设备
> - 循环后 `frsm_delay_ms(10)` 确保 spkon 生效

### stub_spkoff 操作顺序

> 先重置 `skip_set=0, err_code=0, bypass_dsp=0`，再调用 `spkoff` + `bypass_dsp_scene`。
> `bypass_dsp=0` 必须在 `bypass_dsp_scene` 之前设置。
> 循环后先 `fsm_alert("spkoff")`，再 `frsm_delay_ms(40)`。
> 注意：日志在 delay 之前输出。

### stub_deinit

> 无参数。遍历时跳过已 `skip_set=1` 的设备，只标记未跳过的。
> 遍历后输出 `fsm_alert("deinit")`。

### stub_volume

> `stub_volume` 签名：`frsm_stub_volume(priv, chn_mask, volume)`，接收 3 个参数。
> `chn_mask` 类型 `uint8_t`，用于多设备时按通道掩码过滤（`#if FRSM_DEV_MAX > 1`）。
> `volume` 参数类型是 `int`，内部用 `& 0xFFFF` 截断。
> `set_volume` 不接收 `dev` 参数（音量是全局的）。
> stub_volume 内部遍历设备数组，对每个匹配 chn_mask 的设备调用 `set_volume`。
> cold_start 中调用时 chn_mask 传 `0xFF`（所有通道）。

### detect_devices

> 自包含函数，每次 cold_start 都会被调用，重新初始化整个设备数组。
> 不要将设备数组初始化拆分到 `initialize` 中。
> 检测时临时切换 I2C 地址（saved_addr 模式）。

### stub_init

> 调用 `detect_devices` 后设 `is_spkon = 0`。
> 用 `frsm_write_reg_table(priv, &priv->lower->init_table)` 写初始化表。
> `set_scene` 传入 `AUDIO_SENCE_TYPE_MUSIC` 常量（不是 `s_audioSenceType` 全局变量），
> 确保每次 cold_start 重新初始化时从 music 场景开始。
> init_table 和 set_scene 在设备遍历循环内执行（每个设备各写一次）。

## 十、场景切换

### set_scene 场景 ID 提取

> **关键**：`set_scene` 内部**必须先** `scene &= 0x00FF` 提取低字节，
> 然后 switch 使用 `case 1`/`case 2`/`case 3`（不使用枚举值 `AUDIO_SENCE_TYPE_MUSIC` 等）。
> `dev->cur_scene` 存储提取后的低字节值。
> 相同场景不重复切换。

### 函数签名中的 per-device 参数

> 以下函数**必须**接收 `struct frsm_dev *dev` 参数：
> `set_scene`, `spkon`, `spkoff`, `set_spk_prot`, `bypass_dsp_scene`, `calibrate`, `get_calib_result`
>
> 以下函数**不接收** dev 参数：
> `stub_deinit(void)`, `check_spkon(void)`, `set_volume(priv, vol)`

## 十一、Bypass DSP

### 进入方式

> 必须逐条使用 `reg_bits_write` 做 read-modify-write，
> **不能**将 bypass_mode_table 当作普通命令表用 `write_reg_table` 执行。

### 地址匹配校验

> 进入 bypass 时，必须校验 `backup_table[i].addr == mode_table[i].reg`，
> 不匹配则报错返回。

### 状态跟踪

> 使用 `static bool bypass_dsp_backup`（推荐为函数内 static 局部变量）：
> - 进入 bypass 前检查：`if (!bypass_dsp_backup && !dev->bypass_dsp) return 0;`
> - 进入后设 `true`，退出后设 `false`

### 退出方式

> 逐条用 `frsm_i2c_write`（L0）将 backup 表写回。

### Key Register Unlock/Lock 范围

> **无论进入还是退出 bypass**，都必须先 unlock key register (0xCA91)，
> 操作完成后 lock (0x0000)。退出 bypass 恢复 backup 时也需要在 key unlock 状态下操作。
> unlock/lock 操作在 `bypass_dsp_scene` 函数的最外层（进入和退出分支共享）。

## 十二、校准（OTP 芯片）

### 校准模式进入

> calib_mode_table **不能**当作普通命令表执行。必须逐条 read-modify-write：
> 1. 先用 L0 层 `frsm_i2c_read` 读取 backup_table 对应寄存器的当前值（备份）
> 2. 验证 calib_mode_table[i].reg == backup_table[i].addr
> 3. 手动计算 `new = (old & ~mask) | val`，用 L0 层 `frsm_i2c_write` 写入
> **不使用 L2 层 `reg_bits_write`**，因为 L2 有 skip-if-unchanged 优化，
> 可能跳过校准模式需要的写入。

### 校准模式退出

> 将 backup_table 当作 REG_SET 命令表用 `write_reg_table` 写回。

### set_calib_mode 签名

> 推荐只接收 `priv` 参数（如果 DEV_MAX=1）。
> 只负责"进入校准模式"操作。

### 状态跟踪

> `static bool is_calib_backup`（推荐为 `calibrate` 函数内 static 局部变量）：
> - `calibrate(enable=0)` 开头：`if (!is_calib_backup) return OK;`
> - `calibrate(enable=1)` 成功后设 `true`
> - `calibrate(enable=0)` 恢复后设 `false`

### calibrate 中的 spkoff 时机

> 检查**全局 `is_spkon`**（不是 per-device `dev->is_on`）。
> 无论 enable 还是 !enable，如果当前 `is_spkon`，必须先 `spkoff`，
> 操作完成后再 `set_spk_prot` + `spkon` 恢复。

### 扬声器保护参数计算 (set_spk_prot)

> 内部**必须先调用** `get_otp_spkre` 读取/更新 spkre 值。
> 必须使用 **float 运算**计算保护参数（不能用整数近似）。

### stub_calibrate 线程安全

> mutex 区间内**必须分三个独立循环**（不能合并）：
> 1. reg_dump（调试）
> 2. 收集校准结果（`result->ndev = 0` 必须在此循环前设置）
> 3. 退出校准模式

### 校准结果失败处理

> 如果 `calc_spkre` 或 `save_spkre` 失败，将 `dev->spkre` 设为错误码（负数），
> 仍然填充 `result->info` 并递增 `ndev`。

### OTP check_otp_condition 设计

> - 范围检查在 check_otp_condition 内部执行
> - 错误码：范围失败 `-100`，OTP 写入次数用尽 `-101`
> - skip 时在内部写回 OTP 数据寄存器（确保当前会话使用最新值）

## 十三、全局状态变量

```c
static uint32_t g_volume = 0;                          /* 外部音量覆盖 */
static bool g_bypass = false;                          /* DSP bypass 开关 */
static int s_audioSenceType = AUDIO_SENCE_TYPE_MUSIC;  /* 当前场景 */
static int g_audio_status = 0;                         /* PA 状态 0=off 1=on */
static uint8_t is_spkon = 0;                           /* 全局 spkon 标志 */
```

## 十四、日志宏规范

> `fsm_debug` **必须**受 `CONFIG_AUDIO_{CHIP}_DEBUG` 条件编译控制。
> `fsm_info`/`fsm_error`/`fsm_alert` 不受 DEBUG 控制，始终输出。
> 日志宏定义在**私有头文件**中。

## 十五、必需的 include 文件

> .c 文件**必须** include：
> - `<debug.h>` — 提供 `audinfo`/`auderr` 等 NuttX 调试宏
> - `<syslog.h>` — 提供 `syslog()`、`LOG_INFO`/`LOG_ERR`/`LOG_ALERT` 等常量（日志宏依赖）
> - `"nuttx/mi2c/i2c_core.h"` — 提供 `struct i2c_adapter` 和 `i2c_get_adapter`
> - `<nuttx/audio/audio_comp.h>` — 提供 `audio_comp` 组合设备注册接口
> - `"pa_bsp.h"` — 提供 `pa_set_regulator_pinen`

## 十六、辅助函数

> - `frsm_delay_ms(uint32_t ms)` — 封装 `usleep(ms * 1000)`
> - `{chip}_get_current_time_ms(void)` — 时间戳工具（用于日志）
> - `late_initialize` — 必须实现，从 OTP 加载 spkre


## 十七、Review Checklist

> 标注：无标注 = 通用；`[OTP]` = OTP 校准芯片；`[BYPASS]` = 有 DSP bypass

| 维度 | 检查项 | 级别 |
|------|--------|------|
| **头文件** | 私有头文件 include 公共头文件，不重复定义 lower_s/scene_table_s | FAIL |
| | 私有头文件声明 late_initialize | FAIL |
| | `fsm_debug` 受 DEBUG 宏控制 | FAIL |
| **初始化** | Chip ID 验证，不匹配返回 NULL | FAIL |
| | detect_devices 内部完成设备数组初始化 + frsm_ndev 计数 | FAIL |
| | stub_init 调用 detect_devices 后设 `is_spkon = 0` | FAIL |
| | 定义独立 GPIO 辅助函数 | WARN |
| **I2C** | reg_write/reg_read 使用标量参数签名 | FAIL |
| | reg_bits_write 使用 6 个标量参数 | FAIL |
| | write_reg_table 中 REG_SET 使用 L0 | FAIL |
| | write_reg_table 空表返回 OK | FAIL |
| | write_reg_table 尾部校验 `index == table->len` | WARN |
| **ops** | start/stop/pause/resume/reserve/release 支持 MULTI_SESSION 分支 | FAIL |
| | configure 支持 MULTI_SESSION 分支 | FAIL |
| | configure 写寄存器时机符合推导规则 2（基于 max_devices/has_dsp） | FAIL |
| | stop 行为符合推导规则 1（基于 has_dsp/calibration） | FAIL |
| **场景** | set_scene 先 `scene &= 0x00FF` 再 switch `case 1/2/3` | FAIL |
| **stub** | stub_spkon/spkoff 防重入检查 | FAIL |
| | stub_spkon 分两遍循环 | FAIL |
| | stub_spkon 第二遍赋值 `dev->bypass_dsp = params->rsvd[0]` | FAIL |
| | stub_spkoff 先重置状态再调用 spkoff + bypass_dsp_scene | FAIL |
| | stub_spkoff 循环后 `frsm_delay_ms(40)` | FAIL |
| **spkon** | spkon 只执行 spkon_table，不写 init_table/lock_table | FAIL |
| | spkon/spkoff 命令表为文件级 static const 数组 | WARN |
| | set_pll 不逐步检查返回值 | WARN |
| **`[BYPASS]`** | bypass 进入用 reg_bits_write 逐条操作 | FAIL |
| | bypass 进入时校验地址匹配 | FAIL |
| | bypass_dsp_backup 状态跟踪正确 | FAIL |
| **`[OTP]`** | calibrate 检查全局 `is_spkon` | FAIL |
| | stub_calibrate mutex 区间内分三个独立循环 | FAIL |
| | get_calib_result 失败时仍填充 result | FAIL |
| | set_spk_prot 内部先调用 get_otp_spkre | FAIL |
| | set_spk_prot 使用 float 运算 | FAIL |
| **其他** | 实现 late_initialize | FAIL |
| | .c include `"nuttx/mi2c/i2c_core.h"` 和 `"pa_bsp.h"` | FAIL |
| | `CONFIG_AUDIO_{CHIP}` 宏保护整个文件 | FAIL |

## Cross References

- [`pa_codegen_templates.md`](pa_codegen_templates.md) — C 代码模板
- [`pa_dsp_pattern.md`](pa_dsp_pattern.md) — DSP PA 特有内容（CS35L42/CS35L41B）
- [`pa_board_integration.md`](pa_board_integration.md) — Kconfig + 板级注册
- [`pa_chip_spec_template.md`](pa_chip_spec_template.md) — 芯片规格文档模板
- `references/chips/{chip}_spec.md` — 各芯片的规格文档
