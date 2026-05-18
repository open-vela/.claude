# PA Amplifier — Code Generation Templates

本文档包含 PA 功放驱动的 C 代码模板，供 AI 生成代码时参考。
所有模板中 `{chip}` 替换为实际芯片名，`{CHIP}` 替换为大写形式。

> 架构规范和约束规则参见 [`pa_amplifier_pattern.md`](pa_amplifier_pattern.md)。

## I2C L0 — 直接传输

```c
static int {chip}_i2c_write(FAR struct {chip}_dev_s *priv, uint8_t regaddr,
                            FAR uint8_t *value, uint8_t len)
{
  struct i2c_config_s config;
  uint8_t buf[512];
  int ret;

  config.address   = priv->lower->address;
  config.frequency = priv->lower->frequency;
  config.addrlen   = 7;

  buf[0] = regaddr;
  memcpy(buf + 1, value, len);

  for (int i = 0; i < {CHIP}_I2C_RETRY; i++) {
      ret = i2c_write(priv->i2c, &config, buf, len + 1);
      if (ret >= 0) break;
#ifdef CONFIG_I2C_RESET
      ret = I2C_RESET(priv->i2c);
      if ((ret < 0) && (ret != -EIO)) break;
#endif
      usleep(2 * 1000);
  }
  return ret;
}

static int {chip}_i2c_read(FAR struct {chip}_dev_s *priv, uint8_t regaddr,
                           FAR uint8_t *regval, uint8_t len)
{
  struct i2c_config_s config;
  int ret;

  config.address   = priv->lower->address;
  config.frequency = priv->lower->frequency;
  config.addrlen   = 7;

  for (int i = 0; i < {CHIP}_I2C_RETRY; i++) {
      ret = i2c_writeread(priv->i2c, &config, &regaddr, 1, regval, len);
      if (ret >= 0) break;
#ifdef CONFIG_I2C_RESET
      ret = I2C_RESET(priv->i2c);
      if ((ret < 0) && (ret != -EIO)) break;
#endif
      usleep(2 * 1000);
  }
  return ret;
}
```

## I2C L1 — 带内层重试

```c
static int {chip}_reg_write(FAR struct {chip}_dev_s *priv, uint8_t regaddr,
                            uint16_t value)
{
  uint8_t buf[2];
  int ret;
  WORD2BUF(value, buf);
  for (int i = 0; i < {CHIP}_I2C_RETRY_MAX; i++) {
      ret = {chip}_i2c_write(priv, regaddr, buf, 2);
      if (ret >= 0) return ret;
      usleep({CHIP}_I2C_DELAY_MS * 1000);
  }
  fsm_error("reg_write 0x%02x failed: %d", regaddr, ret);
  return ret;
}

static int {chip}_reg_read(FAR struct {chip}_dev_s *priv, uint8_t regaddr,
                           FAR uint16_t *value)
{
  uint8_t buf[2];
  int ret;
  for (int i = 0; i < {CHIP}_I2C_RETRY_MAX; i++) {
      ret = {chip}_i2c_read(priv, regaddr, buf, 2);
      if (ret >= 0) { BUF2WORD(buf, *value); return ret; }
      usleep({CHIP}_I2C_DELAY_MS * 1000);
  }
  fsm_error("reg_read 0x%02x failed: %d", regaddr, ret);
  return ret;
}

static int {chip}_reg_read_raw(FAR struct {chip}_dev_s *priv,
                               uint8_t regaddr, FAR uint8_t *buf)
{
  int ret;
  for (int i = 0; i < {CHIP}_I2C_RETRY_MAX; i++) {
      ret = {chip}_i2c_read(priv, regaddr, buf, 2);
      if (ret >= 0) return ret;
      usleep({CHIP}_I2C_DELAY_MS * 1000);
  }
  fsm_error("reg_read_raw 0x%02x failed: %d", regaddr, ret);
  return ret;
}
```

## I2C L2 — Read-Modify-Write

```c
static int {chip}_reg_bits_write(FAR struct {chip}_dev_s *priv,
                                 uint8_t regaddr,
                                 uint8_t mask_hi, uint8_t mask_lo,
                                 uint8_t val_hi, uint8_t val_lo)
{
  uint8_t buf[2];
  uint8_t new_hi, new_lo;
  int ret;

  ret = {chip}_i2c_read(priv, regaddr, buf, 2);
  if (ret < 0) return ret;

  new_hi = (buf[0] & ~mask_hi) | val_hi;
  new_lo = (buf[1] & ~mask_lo) | val_lo;
  if (new_hi == buf[0] && new_lo == buf[1]) return OK;

  buf[0] = new_hi;
  buf[1] = new_lo;
  return {chip}_i2c_write(priv, regaddr, buf, 2);
}
```

## detect_devices

```c
static int frsm_detect_devices(FAR struct {chip}_dev_s *priv)
{
  uint8_t buf[2];
  int ret;
  uint8_t idx;

  frsm_ndev = 0;
  for (idx = 0; idx < FRSM_DEV_MAX; idx++) {
      struct frsm_dev *dev = frsm_device + idx;
      memset(dev, 0, sizeof(struct frsm_dev));
      dev->addr      = frsm_addr[idx];
      dev->id        = idx;
      dev->chn_mask  = (1 << idx);
      dev->spkre     = spkre_dft[idx];
      dev->spkre_min = spkre_range[idx * 2];
      dev->spkre_max = spkre_range[idx * 2 + 1];

      uint8_t saved_addr = priv->lower->address;
      ((FAR struct {chip}_lower_s *)priv->lower)->address = dev->addr;
      ret = frsm_i2c_read(priv, FRSM_REG_DEVID, buf, 2);
      ((FAR struct {chip}_lower_s *)priv->lower)->address = saved_addr;

      if (ret < 0 || buf[0] != FRSM_CHIP_DEVID) {
          dev->skip_set = 1;
          continue;
      }
      dev->devid = (buf[0] << 8) | buf[1];
      frsm_ndev++;
  }
  return (frsm_ndev > 0) ? OK : -ENODEV;
}
```

## stub_init

```c
static int frsm_stub_init(FAR struct {chip}_dev_s *priv)
{
  struct frsm_dev *dev;
  uint8_t idx;

  frsm_delay_ms(10);
  int ndev = frsm_detect_devices(priv);
  if (ndev <= 0) return -ENODEV;

  is_spkon = 0;
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set) continue;
      frsm_write_reg_table(priv, &priv->lower->init_table);
      frsm_set_scene(priv, dev, AUDIO_SENCE_TYPE_MUSIC);
      frsm_spkoff(priv, dev);
  }
  return OK;
}
```

## stub_spkon

```c
static int frsm_stub_spkon(FAR struct {chip}_dev_s *priv,
                            FAR struct spkr_hw_params *params)
{
  struct frsm_dev *dev;
  uint8_t idx;
  int ret;

  if (params == NULL) return -EINVAL;
  if (is_spkon) { fsm_alert("Skip to set spkon!"); return OK; }
  is_spkon = 1;

  /* Pass 1: set_scene + set_hw_params */
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set) continue;
      ret = frsm_set_scene(priv, dev, params->scene);
      ret |= frsm_set_hw_params(priv, dev, params);
      dev->err_code = ret;
  }

  /* Pass 2: bypass_dsp + spkon */
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set || dev->err_code) continue;
      dev->bypass_dsp = params->rsvd[0];  /* 在第二遍赋值 */
      frsm_bypass_dsp_scene(priv, dev);
      frsm_spkon(priv, dev);
  }

  frsm_delay_ms(10);
  return OK;
}
```

## stub_spkoff

```c
static int frsm_stub_spkoff(FAR struct {chip}_dev_s *priv)
{
  struct frsm_dev *dev;
  uint8_t idx;

  if (!is_spkon) { fsm_alert("Skip to set spkoff!"); return OK; }
  is_spkon = 0;

  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      dev->skip_set   = 0;
      dev->err_code   = 0;
      dev->bypass_dsp = 0;  /* 必须在 bypass_dsp_scene 之前 */
      frsm_spkoff(priv, dev);
      frsm_bypass_dsp_scene(priv, dev);
  }

  fsm_alert("spkoff");
  frsm_delay_ms(40);
  return OK;
}
```

## stub_volume

```c
static int frsm_stub_volume(FAR struct {chip}_dev_s *priv,
                             uint8_t chn_mask, int volume)
{
  struct frsm_dev *dev;
  uint16_t vol = volume & 0xFFFF;
  uint8_t idx;

  fsm_alert("set volume.%02x:%d", chn_mask, volume);
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      frsm_set_volume(priv, vol);
  }
  return OK;
}
```

## stub_calibrate

```c
static int frsm_stub_calibrate(FAR struct {chip}_dev_s *priv,
                                FAR struct calib_result *result)
{
  struct frsm_dev *dev;
  uint8_t idx;

  if (result == NULL) return -EINVAL;
  if (!frsm_check_spkon()) return -EBUSY;
  frsm_delay_ms(20);

  /* Enter calibration mode */
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set) continue;
      frsm_calibrate(priv, dev, 1);
  }

  frsm_delay_ms(3000);
  nxmutex_lock(&g_calib_lock);

  /* Loop 1: reg_dump */
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set) continue;
      frsm_reg_dump(priv, 0xCF);
  }

  /* Loop 2: collect results */
  result->ndev = 0;
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set) continue;
      frsm_get_calib_result(priv, dev, result);
  }

  /* Loop 3: exit calibration */
  for (idx = 0; idx < frsm_ndev; idx++) {
      dev = frsm_device + idx;
      if (dev->skip_set) continue;
      frsm_calibrate(priv, dev, 0);
  }

  nxmutex_unlock(&g_calib_lock);
  return OK;
}
```

## check_spkon

```c
static int frsm_check_spkon(void)
{
  int retry = 0;
  do {
      if (is_spkon) return 1;
      frsm_delay_ms(FRSM_CHECK_DELAY_MS);
  } while (retry++ < FRSM_CHECK_TIME_MAX);
  return 0;
}
```

## set_scene

```c
static int frsm_set_scene(FAR struct {chip}_dev_s *priv,
                           struct frsm_dev *dev, uint16_t scene)
{
  struct {chip}_scene_table_s tbl;
  int ret;

  scene &= 0x00FF;  /* 必须先提取低字节 */
  if (dev->cur_scene == scene) return OK;

  switch (scene) {
      case 1: /* music */
        tbl.len = priv->lower->music_table.len;
        tbl.scene_data = priv->lower->music_table.scene_data;
        break;
      case 2: /* voice */
        tbl.len = priv->lower->voice_table.len;
        tbl.scene_data = priv->lower->voice_table.scene_data;
        break;
      case 3: /* voip */
        tbl.len = priv->lower->voip_table.len;
        tbl.scene_data = priv->lower->voip_table.scene_data;
        break;
      default:
        return -EINVAL;
  }

  ret = frsm_write_reg_table(priv, &tbl);
  if (ret >= 0) dev->cur_scene = scene;
  return ret;
}
```
