# PA Amplifier — DSP PA Pattern

本文档覆盖有 DSP 固件加载的复杂 PA 芯片的通用驱动架构模式。
简单 PA（无 DSP）参见 [`pa_amplifier_pattern.md`](pa_amplifier_pattern.md)。
板级注册参见 [`pa_board_integration.md`](pa_board_integration.md)。

> **适用范围**：适用于带 HALO DSP 或类似 DSP 内核的 I2S 外置功放芯片。
> 本文档基于两个生产级 Cirrus Logic DSP PA 驱动总结，提取了通用架构模式。
> 文档中描述了两种实现风格（自实现固件解析 vs vendor SDK fw_img API），
> 新驱动应根据 vendor SDK 提供的能力选择合适的风格。

## 一、与简单 PA 的关键差异

| 维度 | 简单 PA | DSP PA |
|------|---------|--------|
| I2C 访问 | direct 模式（NuttX I2C API） | pa_bsp 模式（通过 `pa_bsp.h` 间接访问） |
| 寄存器位宽 | 16-bit | 32-bit |
| 寄存器地址宽度 | 8-bit | 32-bit |
| 初始化 | 写寄存器表 | 固件加载 + OTP unpack + errata patch |
| 场景切换 | 切换寄存器配置表 | 切换 DSP 系数文件（tune image） |
| 校准 | OTP 读写（硬件校准） | ReDC DSP 校准（软件校准，结果存 KVDB） |
| 电源管理 | 无 hibernate | 支持 hibernate/wake 低功耗模式 |
| 事件处理 | 无 | IRQ + event handler + error recovery |
| ops.shutdown | NULL | 实现（power_down + hibernate） |
| ops.pause/resume | NULL | 实现（power_down / wake + power_up） |
| 命令表 | uint8_t 数组 + write_reg_table | uint32_t 寄存器对数组 + write_array |

## 二、I2C 访问模式

DSP PA 使用 `pa_bsp` 模式，通过 `pa_bsp.h` 提供的全局函数间接访问 I2C，
不直接持有 `struct i2c_master_s *` 指针。

### API 签名

```c
/* pa_bsp.h 提供的全局函数 */
int pa_read_register(uint32_t frequency, uint8_t address,
                     uint32_t *regval, uint32_t regaddr, int i2c_port);
int pa_write_register(uint32_t frequency, uint8_t address,
                      uint32_t regaddr, uint32_t regval, int i2c_port);
int pa_read_block(uint32_t frequency, uint8_t address,
                  uint32_t regaddr, uint8_t *data, uint32_t len, int i2c_port);
int pa_write_block(uint32_t frequency, uint8_t address,
                   uint32_t waddr, uint8_t *data, uint32_t len, int i2c_port);
```

### 驱动内封装

> 驱动内部封装为接收 `priv` 的便捷函数，从 `priv->lower` 提取 I2C 参数：

```c
int {chip}_read_register(FAR struct {chip}_dev_s *priv,
                         uint32_t *regval, uint32_t regaddr)
{
  return pa_read_register(priv->lower->frequency, priv->lower->address,
                          regval, regaddr, priv->lower->i2c_port);
}

int {chip}_write_register(FAR struct {chip}_dev_s *priv,
                          uint32_t regaddr, uint32_t regval)
{
  return pa_write_register(priv->lower->frequency, priv->lower->address,
                           regaddr, regval, priv->lower->i2c_port);
}

int {chip}_read_block(FAR struct {chip}_dev_s *priv,
                      uint32_t regaddr, uint8_t *data, uint32_t len)
{
  return pa_read_block(priv->lower->frequency, priv->lower->address,
                       regaddr, data, len, priv->lower->i2c_port);
}

int {chip}_write_block(FAR struct {chip}_dev_s *priv,
                       uint32_t waddr, uint8_t *data, uint32_t len)
{
  return pa_write_block(priv->lower->frequency, priv->lower->address,
                        waddr, data, len, priv->lower->i2c_port);
}
```

### Read-Modify-Write

```c
uint32_t {chip}_update_reg(struct {chip}_dev_s *priv,
                           uint32_t addr, uint32_t mask, uint32_t val)
{
  uint32_t temp_val;
  {chip}_read_register(priv, &temp_val, addr);
  temp_val = (temp_val & ~mask) | val;
  return {chip}_write_register(priv, addr, temp_val);
}
```

### 寄存器对数组写入

> DSP PA 使用 `uint32_t` 寄存器对数组（`{addr, value, addr, value, ...}`），
> 不使用简单 PA 的 `uint8_t` 命令表编码。

```c
uint32_t {chip}_write_array(struct {chip}_dev_s *priv,
                            uint32_t *array, uint32_t array_len)
{
  for (uint32_t i = 0; i < array_len / 2; i++)
    {
      ret = {chip}_write_register(priv, array[2 * i], array[2 * i + 1]);
      if (ret) return ret;
    }
  return OK;
}
```


## 三、文件布局

```
vendor/xiaomi/vela/drivers/audio/
├── {chip}/
│   ├── {chip}.c              # 主驱动实现（ops 回调、电源管理、初始化）
│   ├── {chip}.h              # 寄存器定义 + 私有结构体 + 状态机常量
│   ├── {chip}_fw.c           # 固件加载逻辑（风格 A：自实现解析）
│   ├── {chip}_fw.h           # 固件加载函数原型
│   ├── {chip}_fw_img.c       # 主固件数据（const uint8_t 数组）
│   ├── {chip}_fw_img.h       # 主固件数据声明
│   ├── {chip}_tune_fw_img.c  # 默认 tune 系数数据
│   ├── {chip}_tune_fw_img.h  # tune 系数声明
│   ├── {chip}_cal_fw_img.c   # 校准固件数据（可选）
│   ├── {chip}_cal_fw_img.h   # 校准固件声明
│   └── {chip}_tune_*_fw_img.c # 场景特定 tune 系数（SCO/modem 等）
├── common/
│   ├── bsp_driver_if.h       # BSP 驱动接口定义
│   ├── fw_img.c              # fw_img 格式解析（Cirrus 通用）
│   ├── fw_img.h              # fw_img 数据结构和 API
│   ├── regmap.c              # 寄存器映射工具
│   └── regmap.h              # regmap 接口
├── pa_bsp.c                  # 公共 I2C/GPIO 访问层
└── pa_bsp.h                  # 公共 BSP 接口

# 公共头文件
nuttx/include/nuttx/audio/{chip}.h  # lower_s + initialize 原型
```

> **如果芯片 vendor SDK 提供独立的驱动状态结构**：
> ```
> {chip}/
> ├── bsp/                    # BSP 平台适配层
> ├── config/                 # 系统配置寄存器表（syscfg_regs）
> └── fw/                     # 固件相关头文件（symbol ID 定义等）
> ```

## 四、关键数据结构

### 4.1 设备私有结构

```c
struct {chip}_dev_s
{
  struct audio_lowerhalf_s dev;       /* 必须是第一个成员 */
  const FAR struct {chip}_lower_s *lower;

  uint16_t                samprate;
#ifndef CONFIG_AUDIO_EXCLUDE_VOLUME
#ifndef CONFIG_AUDIO_EXCLUDE_BALANCE
  uint16_t                balance;
#endif
  uint8_t                 volume;
#endif
  uint8_t                 nchannels;
  uint8_t                 bpsamp;
  uint32_t                bclk;

  /* DSP PA 特有字段 */
  bool                    initialize;              /* 初始化完成标志 */
  bool                    is_calibrate_value_loaded; /* 校准值已加载 */
  bool                    is_running;              /* 播放状态 */
  int                     state;                   /* 驱动状态机 */
  int                     mode;                    /* 运行模式 */
  uint32_t                asp_gain;                /* ASP 增益 */
  uint32_t                dsp_gain;                /* DSP 增益 */
  int                     scenario_mode;           /* 场景模式 */
  uint32_t                event_flags;             /* 事件标志 */
  uint8_t                 otp_contents[128];       /* OTP 缓存 */
  sem_t                   pendsem;                 /* 事件同步信号量 */
  struct work_s           work;                    /* 固件加载工作队列 */

  /* 如果 vendor SDK 提供独立驱动状态结构，可嵌入 */
  /* {vendor}_driver_t driver; */
};
```

### 4.2 驱动状态机

> DSP PA 有明确的状态机，状态转换必须严格遵守：

| 状态 | 值 | 含义 | 允许的转换 |
|------|---|------|-----------|
| UNCONFIGURED | 0 | 未初始化 | → CONFIGURED |
| CONFIGURED | 1 | 已配置，未上电 | → STANDBY |
| STANDBY | 2 | 非 DSP 模式待机 | → POWER_UP |
| POWER_UP | 3 | 已上电 | → POWER_DOWN, ERROR |
| ERROR | 4 | 错误状态 | → UNCONFIGURED (reset) |
| DSP_POWER_UP | 5 | DSP 模式已上电 | → DSP_STANDBY, ERROR |
| DSP_STANDBY | 6 | DSP 模式待机 | → POWER_UP |
| HIBERNATE | 7 | 低功耗休眠 | → WAKEUP |

### 4.3 运行模式

| 模式 | 值 | 含义 | 固件 |
|------|---|------|------|
| ASP_MODE | 0 | 纯 ASP 直通（无 DSP） | 不加载固件 |
| DSP_TUNE_MODE | 1 | DSP 调音模式（正常播放） | 主固件 + tune 系数 |
| DSP_CAL_MODE | 2 | DSP 校准模式 | 主固件 + 校准固件 |

## 五、DSP 固件加载

### 5.1 固件格式

DSP PA 使用 Cirrus Logic 的 `fw_img` 格式（v1/v2），由 `common/fw_img.c` 解析。

> **fw_img 文件结构**：
> ```
> [Pre-header: magic_number_1 + format_rev]
> [Header: img_size + sym_table_size + alg_id_list_size + fw_id + fw_version + data_blocks + max_block_size]
> [Symbol Linking Table: {sym_id, sym_addr} × sym_table_size]
> [Algorithm ID List: alg_id × alg_id_list_size]
> [Data Blocks: {block_size, block_addr, data[]} × data_blocks]
> [Magic Number 2]
> [Checksum (Fletcher-32)]
> ```

### 5.2 固件数据编译

> 固件以 C 数组形式编译到驱动中（不从文件系统加载）：

```c
/* {chip}_fw_img.c */
const uint8_t g_{chip}_fw_img[] = { /* 二进制数据 */ };
const uint32_t g_{chip}_fw_img_len = sizeof(g_{chip}_fw_img);

/* {chip}_tune_fw_img.c — 默认 tune 系数 */
const uint8_t g_{chip}_tune_fw_img[] = { /* 二进制数据 */ };

/* {chip}_tune_sco_fw_img.c — SCO 场景 tune 系数 */
const uint8_t g_{chip}_tune_sco_fw_img[] = { /* 二进制数据 */ };

/* {chip}_cal_fw_img.c — 校准固件（可选） */
const uint8_t g_{chip}_cal_fw_img[] = { /* 二进制数据 */ };
```

### 5.3 加载流程

#### 风格 A：自实现固件解析

> 适用于 vendor SDK 不提供 fw_img 解析库的情况，驱动自行解析固件格式。

```
dsp_boot(mode):
  1. 如果 mode == ASP_MODE → 设置 state=STANDBY，跳过固件加载
  2. load_main_fw_process → 解析主固件 fw_img，写入 DSP RAM
  3. 根据 mode 选择系数：
     - DSP_TUNE_MODE → load_tune_process（按 scenario_mode 选择 tune 系数）
     - DSP_CAL_MODE → load_cal_process
  4. 如果 DSP_TUNE_MODE → load_calibration_value（从 KVDB 加载校准值）
  5. set_boot_configuration → 配置 DSP 启动参数
  6. state = DSP_STANDBY
```

> **load_fw_process 内部步骤**：
> 1. 计算 checksum
> 2. 读取 pre-header（验证 magic number）
> 3. 读取 header（获取 sym_table_size、max_block_size 等）
> 4. kmm_zalloc 分配 block_read 缓冲区（max_block_size 大小）
> 5. 读取 symbol linking table（如果有）
> 6. 读取 algorithm ID list（如果有）
> 7. 逐块写入固件数据到 DSP RAM（update_fw_data）
> 8. 验证 magic number 2
> 9. 验证 Fletcher-32 checksum
> 10. 释放临时内存

#### 风格 B：使用 fw_img API（推荐）

> 适用于 vendor SDK 提供 `fw_img.c` 解析库的情况。新驱动应优先使用此风格。

```
dut_boot(boot_state, fw_img, is_wmdr_only):
  1. 如果 !is_wmdr_only → {chip}_boot(NULL) 清除旧固件状态
  2. 释放上次 boot 的 malloc 内存
  3. memset(boot_state, 0)
  4. fw_img_read_header(boot_state) → 解析头部
  5. malloc sym_table + alg_id_list + block_data
  6. 循环 fw_img_process(boot_state)：
     - FW_IMG_STATUS_DATA_READY → write_block 写入设备
     - FW_IMG_STATUS_NODATA → 提供下一块数据
     - FW_IMG_STATUS_FAIL → 错误退出
  7. 如果 !is_wmdr_only → {chip}_boot(&boot_state->fw_info)
  8. 释放 block_data
```

> **成功标准**：`{chip}_boot` 返回 OK 且 `driver.state == DSP_STANDBY`。
> 如果任一步骤返回 FAIL，应释放已分配内存并返回错误。

> **选择依据**：如果 `common/fw_img.c` 可用，使用风格 B；否则使用风格 A 自行实现解析。

### 5.4 场景切换（Tune Change）

> DSP PA 通过切换 DSP 系数文件实现场景切换，不是简单的寄存器配置表。

```
tune_change_params:
  1. 检查 scenario_mode 是否变化，未变则跳过
  2. start_tuning_switch → 暂停 DSP + 准备切换
  3. set_boot_configuration → 加载新 tune 系数
  4. finish_tuning_switch → 恢复 DSP 运行
```

> **场景到 tune 系数的映射**（在 chip spec 中定义）：
> | 场景 | tune 数据变量 |
> |------|-------------|
> | SPEAKER (默认) | `g_{chip}_tune_fw_img` |
> | SCO | `g_{chip}_tune_sco_fw_img` |
> | MODEM | `g_{chip}_tune_modem_fw_img` |
> | 其他自定义场景 | `g_{chip}_tune_{scene}_fw_img` |


## 六、电源管理

### 6.1 电源状态流转

```
                    ┌─────────────┐
                    │ UNCONFIGURED│
                    └──────┬──────┘
                           │ reset + check_id + otp_unpack + errata
                    ┌──────▼──────┐
                    │ CONFIGURED  │
                    └──────┬──────┘
                           │ dsp_boot
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼──────┐  ┌─▼──────────┐ │
       │  STANDBY    │  │DSP_STANDBY │ │
       │ (ASP mode)  │  │(DSP mode)  │ │
       └──────┬──────┘  └─────┬──────┘ │
              │               │         │
              │  power_up     │ power_up│
              ▼               ▼         │
       ┌─────────────────────────┐      │
       │      POWER_UP           │      │
       │  (GLOBAL_EN=1, 播放中)  │      │
       └──────┬──────────────────┘      │
              │                         │
              │ power_down              │
              ▼                         │
       ┌─────────────┐                  │
       │  hibernate  │◄─────────────────┘
       └─────────────┘
              │ wake
              ▼
       (回到 STANDBY/DSP_STANDBY)
```

### 6.2 power_up 流程

```
power_up:
  1. 如果 DSP 模式（state != STANDBY）：
     a. 设置 MEM_RDY
     b. 使能 HALO DSP 时钟（DSP1_CCM_CORE_EN）
  2. 设置 BLOCK_ENABLES
  3. 设置 GLOBAL_EN = 1
  4. 轮询等待 MSM_PUP_DONE_EINT1（超时 20 次，每次 1ms）
  5. 清除 MSM_PUP_DONE IRQ 标志
  6. 如果非 DSP 模式 → 返回
  7. 如果有有效校准数据 → 写入 ReDC 值 + AUDIO_REINIT
  8. 发送 MBOX 命令 AUDIO_PLAY
  9. 如果有校准数据 → 等待 50ms → 验证校准值已生效
  10. 检查 DSP PM_CUR_STATE == ACTIVE
```

### 6.3 power_down 流程

```
power_down:
  1. 发送 MBOX 命令 AUDIO_PAUSE
  2. 轮询等待 MSM_PDN_DONE_EINT1（超时 20 次，每次 1ms）
  3. 清除 MSM_PDN_DONE IRQ 标志
  4. 设置 GLOBAL_EN = 0
  5. 轮询等待 MSM_PDN_DONE_EINT1 再次确认
  6. 清除 IRQ 标志
```

### 6.4 hibernate / wake

> DSP PA 支持 hibernate 低功耗模式，在 stop/shutdown 时进入，resume/start 时退出。

```
hibernate:
  1. 禁用 HALO DSP 时钟
  2. 发送 MBOX 命令 HIBERNATE
  3. 等待确认
  4. 配置 wake 源（GPIO/timer）

wake:
  1. 触发 wake 信号
  2. 等待芯片就绪
  3. 重新使能 DSP 时钟
  4. 验证芯片状态
```

> **hibernate 实现方式**（两种风格，根据芯片能力选择）：
>
> **风格 A：MBOX 命令方式**：
> hibernate/wake 使用 MBOX 命令交互，需要验证命令执行结果。
>
> **风格 B：Write Sequencer (WSEQ) 方式**：
> hibernate 通过 Write Sequencer 实现，需要先将 power sequence 写入 DSP，
> 芯片在 hibernate 期间自动执行 WSEQ 恢复。详见 Section 十二。
>
> **选择依据**：如果芯片支持 WSEQ，优先使用风格 B（更可靠）；否则使用风格 A。

## 七、audio_ops_s 回调规范

### 7.1 ops 函数指针表

> **与简单 PA 的关键差异**：DSP PA 实现 shutdown、pause、resume。

```c
static const struct audio_ops_s g_audioops =
{
  .getcaps   = {chip}_getcaps,
  .configure = {chip}_configure,
  .shutdown  = {chip}_shutdown,     /* 实现：power_down + hibernate */
  .start     = {chip}_start,
#ifndef CONFIG_AUDIO_EXCLUDE_STOP
  .stop      = {chip}_stop,         /* 实现：mute + power_down + hibernate */
#endif
  .pause     = {chip}_pause,        /* 实现：mute + power_down */
  .resume    = {chip}_resume,       /* 实现：wake + output_config + power_up */
  .ioctl     = {chip}_ioctl,
};
```

> **所有 ops 回调必须支持 `CONFIG_AUDIO_MULTI_SESSION` 编译分支**（与简单 PA 相同）。

### 7.2 start

> DSP PA 的 start 比简单 PA 简单 — 因为固件已在 `late_initialize` 中加载完成。

```
start:
  1. 检查 initialize 标志（未初始化返回 -EBUSY）
  2. wake（如果在 hibernate 状态）
  3. power_up
  4. unmute
  5. is_running = true
```

### 7.3 stop

```
stop:
  1. 检查 is_running（已停止则直接返回）
  2. 检查 event_flags（有事件则等待 pendsem）
  3. mute
  4. 如果 DSP_CAL_MODE → 先执行校准采集
  5. power_down
  6. 如果非 ASP_MODE → hibernate
  7. is_running = false
```

### 7.4 shutdown

```
shutdown:
  1. 检查 event_flags（有事件则等待 pendsem）
  2. mute
  3. power_down
  4. 如果非 ASP_MODE → hibernate
  5. is_running = false
```

> **stop vs shutdown**：stop 在 DSP_CAL_MODE 下会先采集校准结果，shutdown 不会。
> 两者都执行 power_down + hibernate。

### 7.5 pause / resume

```
pause:
  1. 检查 is_running
  2. 检查 event_flags
  3. mute
  4. power_down
  5. 如果非 ASP_MODE → hibernate
  6. is_running = false

resume:
  1. 检查 initialize
  2. 如果非 ASP_MODE → wake
  3. output_configuration（重新配置采样率/位宽/通道/增益）
  4. power_up
  5. unmute
  6. is_running = true
```

### 7.6 configure

> DSP PA 的 configure 只保存参数，不立即写寄存器。
> 实际配置在 `output_configuration`（resume 时调用）中执行。

```
configure(AUDIO_TYPE_OUTPUT):
  priv->samprate = caps->ac_controls.hw[0]
  priv->bpsamp   = caps->ac_controls.b[2]
  priv->nchannels = caps->ac_channels
```

### 7.7 ioctl

> DSP PA 的 ioctl 使用与简单 PA 相同的字符串匹配模式，
> 但增加了 DSP 特有的命令：

| ioctl key | 功能 | DSP 特有 |
|-----------|------|---------|
| `set_scenario` | 场景切换 | 触发 tune_change_params |
| `set_mode` | 切换 ASP/DSP/CAL 模式 | ✅ |
| `set_asp_gain` / `get_asp_gain` | ASP 增益控制 | |
| `set_dsp_gain` / `get_dsp_gain` | DSP 增益控制 | ✅ |
| `get_caliberate_value` | 获取校准值 | ✅ |
| `get_pa_chip_id` | 获取芯片 ID | |
| `set_bypass` | DSP bypass 模式 | ✅ (部分芯片) |
| `start_player` / `stop_player` | 手动播放控制 | ✅ (部分芯片) |

## 八、初始化流程

### 8.1 initialize（同步，最小初始化）

```
{chip}_initialize:
  1. kmm_zalloc 分配 dev_s
  2. 保存 lower 指针
  3. 设置默认参数（asp_gain, dsp_gain, scenario_mode）
  4. 初始化 pendsem
  5. 注册 audio_ops
  6. 返回 audio_lowerhalf_s 指针
```

> **不在 initialize 中加载固件**。固件加载在 `late_initialize` 中异步执行。

### 8.2 late_initialize（异步，固件加载）

```
late_initialize:
  1. power_enable（GPIO 上电）
  2. reset（硬件复位 + 等待 OTP boot）
  3. check_id（验证 DEVID/REVID）
  4. OTP unpack（读取 + 应用 trim 值）
  5. 写 errata patch
  6. boot_initialize（DSP 固件加载 + 配置）
  7. 设置中断（可选）
```

> **boot_initialize 内部**：
> ```
> 1. dsp_boot(mode) → 加载固件
> 2. set_mixer(mode) → 配置 DSP 输出路由
> 3. set_fadein → 使能淡入
> 4. mute(true)
> 5. 如果非 ASP_MODE：power_up → power_down → hibernate
> 6. initialize = true
> ```

### 8.3 OTP Unpack（部分芯片需要）

> 部分 DSP PA 芯片有硬件 OTP，需要在初始化时读取并应用 trim 值。
> 是否需要 OTP unpack 取决于芯片 datasheet 和 vendor SDK。

```
otp_unpack:
  1. read_block 读取 OTP 内容到 otp_contents[]
  2. 遍历 otp_map[]（packed entry 表，芯片特定）
  3. 对每个 entry：从 OTP 位流提取值 → 应用到对应寄存器
```

> 如果 vendor SDK 内部处理了 OTP unpack，驱动不需要自行实现。


## 九、DSP 校准（ReDC）

### 9.1 与简单 PA 校准的差异

| 维度 | 简单 PA (OTP) | DSP PA (ReDC) |
|------|--------------|---------------|
| 校准方式 | 硬件 OTP 读写 | DSP 算法计算 |
| 存储位置 | 芯片 OTP 寄存器 | KVDB（软件存储） |
| 校准触发 | ioctl 命令 | 切换到 DSP_CAL_MODE + stop |
| 校准时机 | 任意时刻 | 必须在播放状态下（DSP 需要音频信号） |
| 结果获取 | 读 OTP 寄存器 | 读 DSP FW control 符号 |

### 9.2 简单校准流程

> 适用于 DSP 自动完成校准计算的芯片。

```
calibrate:
  1. 读取 DSP 处理状态（is_dsp_processing）
  2. 如果 DSP 正在处理 → 读取校准结果
  3. 保存校准值到 KVDB
```

```
load_calibration_value:
  1. 从 KVDB 读取校准值
  2. 如果有效 → 通过 symbol table 写入 DSP
  3. is_calibrate_value_loaded = true
```

### 9.3 完整校准流程

> 适用于需要驱动主动控制校准过程的芯片（如需要设置环境温度、控制保护算法开关等）。

```
calibrate(ambient_temp, expected_redc):
  1. 切换到 DSP 模式
  2. 发送 MBOX AUDIO_PAUSE
  3. 禁用保护算法
  4. 设置环境温度
  5. 保存并清零 pilot tone threshold
  6. 设置 FIRST_RUN = 1
  7. 使能校准
  8. 使能保护算法
  9. 发送 MBOX AUDIO_REINIT + AUDIO_PLAY
  10. 轮询等待阻抗测量完成（超时 30 次，每次 100ms）
  11. 禁用校准和保护
  12. 恢复 pilot tone threshold
  13. 应用最新校准值
  14. 重新使能保护
  15. 读取测量阻抗 → cal_data.r
  16. 读取校准 checksum → 验证 checksum == r + status
  17. cal_data.is_valid = true
  18. 切换回正常模式
```

### 9.4 DSP FW Control 访问

> DSP PA 通过 symbol ID 访问 DSP 固件内部变量，不直接使用寄存器地址。

```c
/* 写 DSP 控制变量 */
uint32_t {chip}_write_fw_control(struct {chip}_dev_s *priv,
                                 fw_img_info_t *f,
                                 uint32_t symbol_id, uint32_t val)
{
  uint32_t addr = fw_img_find_symbol(f, symbol_id);
  if (!addr) return STATUS_FAIL;
  return {chip}_write_register(priv, addr, val);
}

/* 读 DSP 控制变量 */
uint32_t {chip}_read_fw_control(struct {chip}_dev_s *priv,
                                fw_img_info_t *f,
                                uint32_t symbol_id, uint32_t *val)
{
  uint32_t addr = fw_img_find_symbol(f, symbol_id);
  if (!addr) return STATUS_FAIL;
  return {chip}_read_register(priv, val, addr);
}
```

> **symbol ID 定义**在芯片特定的头文件中（如 `{chip}_sym.h`），
> 由 Cirrus 工具链从固件镜像自动生成。

## 十、MBOX 命令交互

> DSP PA 通过 Virtual MBOX 寄存器与 HALO DSP 通信。

### 常用 MBOX 命令

| 命令 | 寄存器 | 值 | 用途 |
|------|--------|---|------|
| AUDIO_PLAY | DSP_VIRTUAL1_MBOX_1 | 芯片特定 | 开始播放 |
| AUDIO_PAUSE | DSP_VIRTUAL1_MBOX_1 | 芯片特定 | 暂停播放 |
| AUDIO_REINIT | DSP_VIRTUAL1_MBOX_1 | 芯片特定 | 重新初始化（校准值更新后） |
| HIBERNATE | DSP_VIRTUAL1_MBOX_1 | 芯片特定 | 进入休眠 |

> **MBOX 命令确认方式**（两种风格，根据芯片选择）：
>
> **风格 A：Acked MBOX（需要等待 DSP 确认）**：
> ```
> send_acked_mbox_cmd(cmd):
>   1. 清除 HALO DSP Virtual MBOX 1 IRQ 标志
>   2. 写入命令到 MBOX 寄存器
>   3. 轮询等待 IRQ 标志置位（超时机制）
>   4. 读取 MBOX 状态寄存器
>   5. 验证状态正确（is_mbox_status_correct）
> ```
>
> **风格 B：Direct MBOX（直接写入，通过 FW control 验证）**：
> 直接写 MBOX 寄存器，不需要 acked 确认。
> 通过读取 `PM_CUR_STATE` FW control 验证 DSP 状态。
>
> **选择依据**：如果芯片 datasheet 要求 MBOX 命令确认，使用风格 A；
> 如果 vendor SDK 提供状态查询接口，使用风格 B。

## 十一、事件处理与错误恢复

### 11.1 事件类型

| 事件 | 含义 | 恢复方式 |
|------|------|---------|
| AMP_SHORT | 功放短路 | reset + 重新初始化 |
| OVERTEMP | 过温保护 | reset + 重新初始化 |
| BOOST_INDUCTOR_SHORT | 升压电感短路 | reset + 重新初始化 |
| BOOST_UNDERVOLTAGE | 升压欠压 | reset + 重新初始化 |
| BOOST_OVERVOLTAGE | 升压过压 | reset + 重新初始化 |
| STATE_ERROR | 状态机错误 | reset + 重新初始化 |

### 11.2 事件处理流程

```
event_handler:
  1. 读取 IRQ 状态寄存器
  2. 映射 IRQ 到 event_id
  3. 设置 event_flags
  4. 如果有事件 → state = ERROR

event_process:
  1. 如果 state == ERROR：
     a. 遍历事件描述表，输出错误日志
     b. reset + boot_initialize 恢复
     c. 如果 is_running → post pendsem 通知 stop/pause
     d. 否则清除 event_flags
```

### 11.3 中断处理

> DSP PA 使用 GPIO 中断 + work queue 模式：

```c
/* IRQ handler（ISR 上下文） */
static int {chip}_interrupt_handler(FAR struct ioexpander_dev_s *dev,
                                    ioe_pinset_t pinset, FAR void *arg)
{
  work_queue(HPWORK, &priv->work, {chip}_event_worker, priv, 0);
  return OK;
}

/* Work queue handler（线程上下文） */
static void {chip}_event_worker(FAR void *arg)
{
  {chip}_event_process(priv);
}
```

## 十二、Write Sequencer（WSEQ，可选）

> 部分 DSP PA 芯片支持 Write Sequencer (WSEQ)，用于 hibernate 期间自动执行寄存器恢复。
> 如果芯片支持 WSEQ，应优先使用此机制实现 hibernate/wake。

### WSEQ 操作类型

| 操作 | 编码 | 说明 |
|------|------|------|
| WRITE_REG_FULL | 0x00 | 完整 32-bit 地址 + 32-bit 值 |
| WRITE_REG_ADDR8 | 0x01 | 8-bit 地址偏移 + 32-bit 值 |
| WRITE_REG_L16 | 0x02 | 24-bit 地址 + 低 16-bit 值 |
| WRITE_REG_H16 | 0x03 | 24-bit 地址 + 高 16-bit 值 |
| END | 0xFF | 序列结束标记 |

### WSEQ 工作流

```
1. wseq_table_update → 更新/添加 WSEQ 表项
2. wseq_write_to_dsp → 将 WSEQ 表写入 DSP 符号地址
3. hibernate 时芯片自动执行 WSEQ 恢复寄存器
4. wseq_read_from_dsp → wake 后从 DSP 读回 WSEQ 表（同步状态）
```

## 十三、Review Checklist（DSP PA 额外项）

> 以下检查项是 DSP PA 特有的，与 `pa_amplifier_pattern.md` 的通用 checklist 互补。

| 维度 | 检查项 | 级别 |
|------|--------|------|
| **I2C** | 使用 pa_bsp 模式（不直接持有 i2c_master_s） | FAIL |
| | 寄存器位宽 32-bit，地址宽度 32-bit | FAIL |
| **固件** | 固件数据以 const uint8_t 数组编译 | FAIL |
| | load_fw_process 验证 checksum | FAIL |
| | 临时内存（block_read/sym_table）正确释放 | FAIL |
| | dsp_boot ASP_MODE 跳过固件加载 | FAIL |
| **状态机** | state 转换符合状态图 | FAIL |
| | initialize 标志在 boot_initialize 完成后设置 | FAIL |
| | is_running 在 start/stop/pause/shutdown 中正确维护 | FAIL |
| **电源** | power_up 轮询 MSM_PUP_DONE 有超时 | FAIL |
| | power_down 轮询 MSM_PDN_DONE 有超时 | FAIL |
| | 非 ASP_MODE 下 stop/shutdown 执行 hibernate | FAIL |
| **校准** | 校准值存储到 KVDB（不是 OTP） | FAIL |
| | load_calibration_value 在 dsp_boot 中调用 | FAIL |
| | power_up 时写入校准值 + AUDIO_REINIT | WARN |
| **ops** | shutdown 实现 power_down + hibernate | FAIL |
| | pause 实现 mute + power_down | FAIL |
| | resume 实现 wake + output_config + power_up | FAIL |
| | start 检查 initialize 标志 | FAIL |
| **事件** | event_handler 读取 IRQ 状态 | WARN |
| | 错误恢复执行 reset + boot_initialize | WARN |
| | pendsem 用于 stop/pause 等待事件处理完成 | WARN |
| **WSEQ** | 支持 WSEQ 的芯片: wseq_write_to_dsp 在 hibernate 前执行 | WARN |

## Cross References

- [`pa_amplifier_pattern.md`](pa_amplifier_pattern.md) — 简单 PA 架构规范（通用基础）
- [`pa_board_integration.md`](pa_board_integration.md) — Kconfig + 板级注册
- [`pa_chip_spec_template.md`](pa_chip_spec_template.md) — 芯片规格文档模板
- [`pa_codegen_templates.md`](pa_codegen_templates.md) — 简单 PA 代码模板

### 已有公共库（直接复用，无需创建）

> **前置检查**：开始开发 DSP PA 驱动前，AI 必须先判断以下公共库是否**适用**于目标芯片，再检查是否存在。
>
> **适用性判断**：
> - `fw_img` 库仅适用于使用 Cirrus fw_img 固件格式的芯片。其他厂商的 DSP PA 需要自行实现固件解析（风格 A），或集成该厂商提供的 SDK。
> - `pa_bsp` 适用于 32-bit 寄存器地址 + 32-bit 数据的芯片。如果新芯片使用不同的寄存器位宽，应改用 direct 模式或自行封装 I2C 访问层。
> - `bsp_driver_if` 仅适用于集成 Cirrus vendor SDK 的驱动。其他厂商的驱动不需要此文件。
>
> 如果公共库适用但项目中缺失，按 fallback 策略处理：

| 文件 | 路径 | 说明 | 缺失时 fallback |
|------|------|------|----------------|
| `fw_img.h` / `fw_img.c` | `common/` | Cirrus fw_img 格式解析 API，风格 B 固件加载依赖此库 | 降级为风格 A（自实现解析），或从 vendor SDK 获取此库并集成 |
| `bsp_driver_if.h` | `common/` | BSP 驱动接口定义 | 从 vendor SDK 获取，或根据芯片 datasheet 自行定义接口 |
| `pa_bsp.h` / `pa_bsp.c` | `audio/` | 公共 I2C/GPIO 访问层，所有 PA 驱动共用 | 改用 direct 模式（直接使用 NuttX I2C API，参见简单 PA 的 I2C 访问层） |