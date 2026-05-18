# PA Amplifier — Board Integration

本文档覆盖 PA 功放驱动的 Kconfig 配置和板级注册。
驱动架构参见 [`pa_amplifier_pattern.md`](pa_amplifier_pattern.md)。

## Kconfig 配置

```kconfig
# 板级配置（vendor 层）
config BES_AUDIO_{CHIP}
	bool "Audio {chip} of bes platform"
	default n
	select AUDIO_{CHIP}
	select AUDIO_COMP
	select PLAYBACK_USE_I2S

# 芯片驱动配置
config AUDIO_{CHIP}
	bool "Audio {CHIP} chip"
	default n

config AUDIO_{CHIP}_DEBUG
	bool "Audio {CHIP} chip debug"
	depends on AUDIO_{CHIP}
	default n

# 公共配置
config AUDIO_PA_SWITCH_GPIO
	int "Set PA GPIO"
	default 22
```

## 板级注册（audio_comp 集成）

```c
#ifdef CONFIG_BES_AUDIO_{CHIP}
static struct {chip}_lower_s g_{chip}_lower =
{
  .i2c_port   = 0,
  .i2c_addr   = 0x34,
  .i2c_freq   = 400000,
  .reset_gpio = CONFIG_AUDIO_PA_RESET_GPIO,
  .vdd_gpio   = CONFIG_AUDIO_PA_VDD_GPIO,
  .irq_gpio   = -1,
};

static void board_{chip}_initialize(void)
{
  FAR struct audio_lowerhalf_s *pa_dev;
  pa_dev = {chip}_initialize(&g_{chip}_lower);
  if (pa_dev == NULL) return;

  /* 注册为 audio_comp companion device */
  FAR struct audio_lowerhalf_s *comp_dev;
  comp_dev = audio_comp_initialize("pcm0p", codec_dev, pa_dev);
}
#endif
```

## 场景表配置

板级代码通过 `{chip}_lower_s` 的场景表字段提供寄存器配置：

```c
static const uint8_t music_scene_data[] = {
  /* REG_SET / REG_BITS / DELAY 命令序列 */
};

static struct {chip}_lower_s g_{chip}_lower = {
  /* ... */
  .init_table  = { sizeof(init_data),  init_data },
  .music_table = { sizeof(music_data), music_data },
  .voice_table = { sizeof(voice_data), voice_data },
  .voip_table  = { sizeof(voip_data),  voip_data },
};
```
