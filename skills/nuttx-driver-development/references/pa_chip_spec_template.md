# PA Chip Specification Template

本模板定义了 PA 功放芯片规格文档的标准格式。
为每个新芯片创建 `references/chips/{chip}_spec.md`，按本模板填写所有已知信息。

> 格式约定：使用 YAML 风格的 key-value 对，嵌套在 markdown 代码块中，便于机器解析。
> 未知字段填 `unknown` 或 `N/A`。

---

## 1. Chip Identity

```yaml
chip_name: "{CHIP_MODEL}"           # 例: "FS1999F", "AW88266A"
vendor: "{VENDOR}"                  # 例: "Foursemi", "Awinic"
i2c_address: 0x{HH}                # 7-bit I2C 地址
i2c_frequency: {FREQ}              # I2C 时钟频率 (Hz)，例: 400000
chip_id_register: 0x{HH}           # Chip ID 寄存器地址
chip_id_expected: 0x{HH}           # 期望的 Chip ID 值（高字节匹配）
chip_id_match: "high_byte"         # 匹配方式: "exact" | "high_byte" | "mask"
register_data_width: 16            # 寄存器数据位宽: 16 | 32
has_dsp: false                     # 是否有 DSP
driver_version: "v1.0.0"           # 驱动版本号
```

## 2. I2C Protocol

```yaml
register_address_width: 8          # 寄存器地址宽度 (bits): 8 | 16
data_width: 16                     # 数据宽度 (bits): 16 | 32
byte_order: "big_endian"           # 字节序: "big_endian" | "little_endian"
retry_count: 2                     # I2C 失败重试次数
retry_delay_ms: 5                  # 重试间隔 (ms)
retry_max_inner: 5                 # 内部重试最大次数（reg_read/reg_write 层）
i2c_reset_on_error: true           # 错误时是否 I2C reset
i2c_reset_error_handling: |
  I2C_RESET 后检查返回值：如果 ret < 0 且 ret != -EIO，则提前 break 退出重试循环。
  只有 -EIO 或成功时才继续重试。
i2c_write_buffer_size: 512         # frsm_i2c_write 内部缓冲区大小（字节），需要容纳 REG_BULK 大 payload
i2c_write_value_param_type: "FAR uint8_t *"  # L0 层 value 参数类型（非 const）
i2c_write_len_param_type: "uint8_t"          # L0 层 len 参数类型
```

### Command Table Encoding

描述寄存器表的编码格式（用于 init_table / scene_table / spkon_table 等）。

```yaml
# 命令表中每条指令的格式
command_encoding:
  - type: "REG_SET"
    addr_range: "0x00-0xF8"
    format: "[addr(1B), data_hi(1B), data_lo(1B)]"
    description: "直接写寄存器"

  - type: "REG_BITS"
    addr_byte: 0xFE
    format: "[0xFE, reg(1B), mask_hi(1B), mask_lo(1B), val_hi(1B), val_lo(1B)]"
    description: "读-改-写位操作"

  - type: "REG_BULK"
    addr_byte: 0xFD
    format: "[0xFD, size(1B), data...]"
    description: "批量写"

  - type: "DELAY"
    addr_byte: 0xFF
    format: "[0xFF, delay_hi(1B), delay_lo(1B)]"
    description: "延时 (ms)"
```

## 3. Register Map

列出驱动使用的所有寄存器。

```yaml
registers:
  - addr: 0x{HH}
    name: "{REG_NAME}"
    default: 0x{HHHH}              # 默认值（如已知）
    access: "RW"                   # RW | RO | WO
    description: "{描述}"

  # 示例:
  # - addr: 0x03
  #   name: "DEVID"
  #   default: 0x0500
  #   access: "RO"
  #   description: "Device ID register"
```

## 4. Bit Field Definitions

对需要位操作的寄存器，定义各 bit field。

```yaml
bit_fields:
  - register: 0x{HH}
    name: "{REG_NAME}"
    fields:
      - name: "{FIELD_NAME}"
        bits: "[{MSB}:{LSB}]"     # 例: "[15:12]"
        mask: 0x{HHHH}
        shift: {N}
        values:                    # 可选：枚举值
          - value: 0x{H}
            meaning: "{描述}"

  # 示例:
  # - register: 0x17
  #   name: "I2SCTRL"
  #   fields:
  #     - name: "I2SSR"
  #       bits: "[15:12]"
  #       mask: 0xF000
  #       shift: 12
  #       values:
  #         - value: 0x1
  #           meaning: "8000 Hz"
```

## 5. PLL Configuration

```yaml
pll_registers:
  - name: "PLLCTRL1"
    addr: 0x{HH}
  - name: "PLLCTRL2"
    addr: 0x{HH}
  - name: "PLLCTRL3"
    addr: 0x{HH}

# BCLK → PLL 寄存器值映射表
pll_divider_table:
  - bclk: {BCLK_HZ}
    pll1: 0x{HHHH}
    pll2: 0x{HHHH}
    pll3: 0x{HHHH}
```

## 6. Sample Rate Table

```yaml
sample_rate_control:
  register: 0x{HH}
  field_mask: 0x{HHHH}
  field_shift: {N}

sample_rate_table:
  - rate: {RATE_HZ}
    value: 0x{HH}
```

## 7. Power Sequences

用命令表编码格式描述各电源序列。

```yaml
speaker_on_sequence:
  description: "{描述}"
  commands:
    - type: "REG_SET"
      addr: 0x{HH}
      value: 0x{HHHH}
    - type: "DELAY"
      ms: {N}

speaker_off_sequence:
  description: "{描述}"
  commands:
    - type: "REG_SET"
      addr: 0x{HH}
      value: 0x{HHHH}
    - type: "DELAY"
      ms: {N}

power_up_sequence:
  description: "芯片上电序列"
  steps:
    - action: "VDD enable"
      delay_ms: {N}
    - action: "Reset release"
      delay_ms: {N}
    - action: "Chip ID verify"
    - action: "Write init table"
    - action: "Set default scene"
    - action: "Speaker off (standby)"

power_down_sequence:
  description: "芯片下电序列"
  steps:
    - action: "Speaker off"
    - action: "Deinit"
```

## 8. Default Register Config (Init Table)

```yaml
# init_table 由板级 lower_s 提供，通过 fs1999_scene_table_s 结构传入。
# 这里记录已知的默认配置（如果从代码/datasheet 可提取）。
init_table_source: "board_lower"   # "hardcoded" | "board_lower" | "firmware"
init_table_format: "command_table" # 使用上述 command_encoding 格式

# 如果是硬编码的 init 表，列出内容:
# init_registers:
#   - addr: 0x{HH}
#     value: 0x{HHHH}
```

## 9. Scene Configurations

```yaml
scenes:
  - id: 1
    name: "music"
    ioctl_string: "set_scenario=music"
    ioctl_enum: 0xFF01
    table_source: "board_lower"    # "hardcoded" | "board_lower"
    description: "{描述}"

  - id: 2
    name: "voice"
    ioctl_string: "set_scenario=sco"
    ioctl_enum: 0xFF02
    table_source: "board_lower"
    description: "{描述}"

  # 场景切换附加操作（如 bypass DSP）
  # ⚠️ 场景表数据（init_table/music_table 等）由板级 lower_s 提供，
  # 本节只记录场景 ID / ioctl 字符串映射关系。
  # bypass 的状态跟踪、进入/退出流程等驱动设计决策由 pa_amplifier_pattern.md 推导。
scene_extras:
  bypass_dsp:
    enabled: false                 # 是否支持 DSP bypass 模式
    key_register: 0x{HH}          # 解锁寄存器（硬件事实，来自 datasheet）
    key_value: 0x{HHHH}           # 解锁值（硬件事实）
    lock_value: 0x{HHHH}          # 锁定值（硬件事实）
    mode_table: []                 # bypass 需要修改的寄存器列表（硬件事实，来自 datasheet）
    backup_table: []               # bypass 前需要备份的寄存器列表（硬件事实，与 mode_table 对应）
```

## 10. Calibration

```yaml
calibration:
  supported: false                 # 是否支持校准
  type: "OTP"                      # "OTP" | "KVDB" | "ReDC" | "none"
  compile_guard: "{CHIP}_CAL"      # 校准代码的编译宏保护

  # 默认扬声器阻抗参数
  spkre_default: {VALUE}           # 默认阻抗值 (Q10 格式)
  spkre_min: {VALUE}               # 最小合法阻抗
  spkre_max: {VALUE}               # 最大合法阻抗

  # OTP 读取
  otp_key_register: 0x{HH}        # OTP 解锁寄存器
  otp_key_value: 0x{HHHH}         # OTP 解锁值
  otp_lock_value: 0x{HHHH}        # OTP 锁定值
  otp_data_register: 0x{HH}       # OTP 数据寄存器（存储 spkre）
  otp_count_register: 0x{HH}      # OTP 写入计数寄存器
  otp_max_count: {N}               # OTP 最大写入次数

  # OTP 读取流程 (get_otp_spkre)
  # 注意：全部使用 L0 层（i2c_write/i2c_read），不使用 L1 层
  otp_read_empty_behavior: "writeback_register"  # OTP 为空时的行为：
    # "writeback_register" — 将默认 spkre 写回 otp_data_register（如 F1h），确保后续读取正确
    # "memory_only" — 只设置内存变量，不写寄存器

  # OTP 等待就绪
  otp_ready_register: 0x{HH}      # OTP 状态寄存器（检查 busy 位）
  otp_ready_bit_mask: 0x{HHHH}    # busy 位掩码（该位为 0 表示就绪）
  otp_ready_initial_delay_ms: 5    # 首次检查前的初始延时 (ms)
  otp_ready_poll_delay_ms: 2       # 轮询间隔 (ms)
  otp_ready_max_retries: 20        # 最大轮询次数

  # OTP skip 时的行为
  otp_skip_writeback: true         # skip 写入时是否仍将新 spkre 写回 otp_data_register
  otp_skip_writeback_description: |
    当 OTP 更新条件不满足（偏差 <= 5%）时，虽然跳过 OTP 写入，
    但仍将新的 spkre 值写入 otp_data_register（如 F1h），
    使当前会话使用最新校准值。

  # 扬声器保护参数计算
  # ⚠️ 计算流程和函数调用顺序由 pa_amplifier_pattern.md 定义，
  # 这里只记录保护相关的寄存器地址和公式（硬件事实）。
  protection_registers:
    - addr: 0x{HH}
      formula: "{计算公式}"
  fw_ratio_format: "Q18.10"        # fw_ratio 的定点格式（datasheet 定义）
  fw_tcoef_format: "Q23.20"        # fw_tcoef 的定点格式（datasheet 定义）

  # 校准硬件时序（来自 datasheet）
  # ⚠️ 只记录芯片要求的硬件时序和等待时间，
  # 软件层面的状态跟踪、mutex、结果收集等由 pa_amplifier_pattern.md 定义。
  calibration_hw_timing:
    stabilization_delay_ms: {N}    # 进入校准模式后等待稳定的时间（datasheet 要求）
    otp_write_delay_ms: {N}        # OTP 写入后等待时间（datasheet 要求）

  # 校准模式寄存器表（硬件事实，来自 datasheet）
  calib_mode_table: []             # 进入校准模式需要修改的寄存器（reg_bits 格式）
  calib_backup_table: []           # 校准前需要备份的寄存器（reg_set 格式，与 calib_mode_table 对应）
```

## 11. DSP Firmware

```yaml
dsp:
  has_dsp: false
  # 以下仅在 has_dsp: true 时填写
  firmware_format: "N/A"           # "WMFW" | "binary" | "N/A"
  firmware_file: "N/A"             # 固件文件名
  boot_sequence: []                # DSP 启动序列
  coefficient_files: []            # 系数文件列表
```

## 12. GPIO Requirements

```yaml
gpio:
  vdd_enable:
    required: true
    pin_name: "vp_pin"             # lower_s 中的字段名
    active_level: "high"
    description: "VDD 电源使能"

  reset:
    required: true
    pin_name: "reset_pin"
    active_level: "high"           # high = reset active
    description: "芯片复位"

  interrupt:
    required: false
    pin_name: "int_pin"
    trigger: "N/A"                 # "rising" | "falling" | "low" | "N/A"
    description: "中断引脚"

  # 其他 GPIO（如有）
  additional_pins:
    - name: "va_pin"
      description: "VA 电源"
    - name: "wakeup_pin"
      description: "唤醒引脚"

  # GPIO 辅助函数
  gpio_helper_functions:
    vdd_enable_function:
      name: "{chip}_1v8_enable"    # VDD 电源使能独立函数名
      description: "独立的 VDD 电源使能函数（与 reset 分开）"
    reset_function:
      name: "{chip}_pin_reset"     # 复位独立函数名
      description: "独立的芯片复位函数"
```

## 13. Audio Capabilities

```yaml
audio_capabilities:
  supported_sample_rates:
    - 8000
    - 16000
    - 48000

  supported_bit_widths:
    - 16
    - 24
    - 32

  supported_channels:
    - 1
    - 2

  max_devices: 1                   # 最大设备数（多芯片并联）
  output_type: "speaker"           # "speaker" | "receiver" | "headphone"
  audio_format: "PCM"
  i2s_interface: true

  # getcaps 报告的能力
  getcaps_types:
    - "AUDIO_TYPE_OUTPUT"
    - "AUDIO_TYPE_FEATURE"
    - "AUDIO_TYPE_PROCESSING"     # 如果支持处理单元
  getcaps_features:
    - "AUDIO_FU_VOLUME"
    - "AUDIO_FU_BASS"             # 如果支持低音控制
    - "AUDIO_FU_TREBLE"           # 如果支持高音控制
    - "AUDIO_FU_BALANCE"          # 如果支持平衡控制
  getcaps_processing:
    - "AUDIO_PU_STEREO_EXTENDER"  # 如果支持立体声扩展
  getcaps_processing_details:
    AUDIO_PU_STEREO_EXTENDER:
      capabilities: "AUDIO_STEXT_ENABLE | AUDIO_STEXT_WIDTH"
  getcaps_output_channels: 2       # OUTPUT 类型报告的通道数（PA 通常为 2 = stereo）
  getcaps_pcm_subtypes:            # AUDIO_FMT_PCM 子类型
    - "AUDIO_SUBFMT_PCM_S16_LE"

  # getcaps 精确实现细节和 configure 字段映射是 NuttX 框架协议，
  # 不是芯片硬件信息，定义在 pa_amplifier_pattern.md 的 Section 4.2 和 4.3 中。
  # chip spec 只需声明上方的芯片能力字段（getcaps_types/features/processing 等）。

  # 音量控制
  volume_register: 0x{HH}
  volume_format: "raw_16bit"       # "raw_16bit" | "dB_scale" | "percentage"
```

## 14. NuttX Framework Hints

> 以下字段帮助 pattern 文档的推导规则做出正确决策。
> 大部分可以从 Section 1-12 的硬件信息自动推导，只有少数需要手动确认。
> 从 datasheet 生成 spec 时，只需填写 `max_devices` 和确认 `has_dsp`/`calibration.supported`。

```yaml
framework_hints:
  # 以下三个字段是推导规则的核心输入（参见 pa_amplifier_pattern.md Phase 0.5）
  # 它们决定了 stop 行为、configure 时机、是否需要 stub 层等驱动设计决策。
  # 通常可以从 Section 1 和 Section 10 直接获取，这里列出是为了显式确认。

  max_devices: 1                   # 最大设备数（单芯片=1，左右声道双芯片=2）
  has_dsp: false                   # 是否有 DSP（从 Section 1 chip_identity 获取）
  has_calibration: false           # 是否支持校准（从 Section 10 calibration.supported 获取）

  # 编译宏（可从芯片名自动生成，通常不需要手动填写）
  config_guard: "CONFIG_AUDIO_{CHIP}"
  debug_guard: "CONFIG_AUDIO_{CHIP}_DEBUG"
```

---

## 使用说明

1. 复制本模板到 `references/chips/{chip}_spec.md`
2. 从 datasheet / vendor SDK / 已有驱动代码中提取数据填入
3. 未知字段保留占位符或标注 `unknown`
4. **Section 14（NuttX Framework Integration）的字段大部分有合理默认值**，
   从 datasheet 生成时直接使用默认值即可，只需根据芯片特性微调以下字段：
   - `calibration_guard` — 校准宏名称（注意大小写必须与芯片惯例一致）
   - `bypass_key_unlock_scope` — 根据芯片是否需要 key unlock 来操作 bypass 寄存器
   - `otp_read_empty_behavior` — 根据芯片 OTP 行为选择
5. 驱动生成工具将解析此文件自动生成驱动骨架代码

### 信息分层

| 层级 | 来源 | 模板 Section | 说明 |
|------|------|-------------|------|
| 芯片硬件 | Datasheet | 1-12 | 寄存器、时序、PLL 表、校准参数 |
| 音频能力 | Datasheet + NuttX 约定 | 13 | getcaps bitmap、采样率、位宽 |
| 推导提示 | 从 Section 1/10 确认 | 14 | max_devices/has_dsp/has_calibration，供 pattern 推导规则使用 |

> **关键原则**：
> - 芯片硬件信息（Section 1-12）必须从 datasheet 精确提取
> - 驱动设计决策（stop 行为、configure 时机、stub 层结构等）**不在 chip spec 中定义**，
>   由 `pa_amplifier_pattern.md` 的 Phase 0.5 推导规则根据硬件特性自动决定
> - Section 14 只提供推导规则需要的少量确认字段

### 公共头文件约束

> **关键**：每个 PA 芯片在 NuttX 系统中有一个公共头文件 `nuttx/include/nuttx/audio/{chip}.h`，
> 其中定义了 `{chip}_lower_s`（板级配置）和 `{chip}_scene_table_s`（场景表）结构体，
> 以及 `{chip}_initialize` 函数原型。
>
> 芯片 spec 中 `board_lower_fields` 的字段类型和顺序**必须与公共头文件一致**。
> 生成驱动代码时，私有头文件 `{chip}.h` 不能重复定义这些结构体，
> 而是通过 `#include <nuttx/audio/{chip}.h>` 引用。
>
> 特别注意 `scene_table_s.len` 的类型：公共头文件中通常为 `uint32_t`，
> 驱动内部使用时 index 变量也必须匹配为 `uint32_t`。

### 数据来源优先级

1. 芯片 Datasheet（最权威）
2. Vendor SDK 参考代码
3. 已有生产级驱动代码（逆向提取）
4. 硬件工程师提供的配置表
