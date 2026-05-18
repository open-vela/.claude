# I2C and SPI Bus Access Patterns

## I2C Bus Access

> [!IMPORTANT] I2C and SPI bus operations are **blocking** (they acquire internal locks and wait for hardware).
> They MUST NOT be called from interrupt context. Use `work_queue` to defer bus access to thread context.

### Core Interface

NuttX I2C uses a message-based transfer model. The key structures:

```c
/* From include/nuttx/i2c/i2c_master.h */

struct i2c_master_s
{
  FAR const struct i2c_ops_s *ops;  /* I2C vtable */
};

struct i2c_msg_s
{
  uint32_t frequency;         /* I2C clock frequency */
  uint16_t addr;              /* 7-bit or 10-bit slave address */
  uint16_t flags;             /* I2C_M_READ, I2C_M_TEN, I2C_M_NOSTOP, I2C_M_NOSTART */
  FAR uint8_t *buffer;        /* Data buffer */
  ssize_t length;             /* Buffer length in bytes */
};
```

### Transfer Macro

```c
#define I2C_TRANSFER(dev, msgs, count)  ((dev)->ops->transfer(dev, msgs, count))
```

### Common I2C Patterns

#### Read a single register (8-bit address, 8-bit value)

```c
static int mydev_getreg8(FAR struct mydev_s *priv, uint8_t regaddr,
                          FAR uint8_t *regval)
{
  struct i2c_msg_s msg[2];
  int ret;

  *regval = 0;

  /* Write: register address */
  msg[0].frequency = priv->freq;
  msg[0].addr      = priv->addr;
  msg[0].flags     = 0;             /* Write */
  msg[0].buffer    = &regaddr;
  msg[0].length    = 1;

  /* Read: register value */
  msg[1].frequency = priv->freq;
  msg[1].addr      = priv->addr;
  msg[1].flags     = I2C_M_READ;
  msg[1].buffer    = regval;
  msg[1].length    = 1;

  ret = I2C_TRANSFER(priv->i2c, msg, 2);
  if (ret < 0)
    {
      snerr("ERROR: I2C_TRANSFER failed: %d\n", ret);
    }

  return ret;
}
```

#### Read multiple registers (burst read)

```c
static int mydev_getregs(FAR struct mydev_s *priv, uint8_t regaddr,
                          FAR uint8_t *rxbuf, uint8_t length)
{
  struct i2c_msg_s msg[2];

  msg[0].frequency = priv->freq;
  msg[0].addr      = priv->addr;
  msg[0].flags     = 0;
  msg[0].buffer    = &regaddr;
  msg[0].length    = 1;

  msg[1].frequency = priv->freq;
  msg[1].addr      = priv->addr;
  msg[1].flags     = I2C_M_READ;
  msg[1].buffer    = rxbuf;
  msg[1].length    = length;

  return I2C_TRANSFER(priv->i2c, msg, 2);
}
```

#### Write a single register

```c
static int mydev_putreg8(FAR struct mydev_s *priv, uint8_t regaddr,
                          uint8_t regval)
{
  struct i2c_msg_s msg;
  uint8_t txbuf[2];

  txbuf[0] = regaddr;
  txbuf[1] = regval;

  msg.frequency = priv->freq;
  msg.addr      = priv->addr;
  msg.flags     = 0;
  msg.buffer    = txbuf;
  msg.length    = 2;

  return I2C_TRANSFER(priv->i2c, &msg, 1);
}
```

#### Write multiple bytes

> [!WARNING] Stack overflow risk with large writes
>
> The VLA `txbuf[length + 1]` is allocated on the stack. For large burst writes
> (e.g., firmware upload > 256 bytes), use `kmm_zalloc` instead to avoid stack overflow.
> Work queue threads typically have 2-4KB stacks.

```c
static int mydev_putregs(FAR struct mydev_s *priv, uint8_t regaddr,
                          FAR const uint8_t *data, uint8_t length)
{
  struct i2c_msg_s msg;
  uint8_t txbuf[length + 1];

  txbuf[0] = regaddr;
  memcpy(&txbuf[1], data, length);

  msg.frequency = priv->freq;
  msg.addr      = priv->addr;
  msg.flags     = 0;
  msg.buffer    = txbuf;
  msg.length    = length + 1;

  return I2C_TRANSFER(priv->i2c, &msg, 1);
}
```

### I2C Message Flags

| Flag | Value | Description |
|------|-------|-------------|
| `I2C_M_READ` | 0x0001 | Read transfer (slave → master) |
| `I2C_M_TEN` | 0x0002 | 10-bit address mode |
| `I2C_M_NOSTOP` | 0x0040 | Don't send STOP after this message |
| `I2C_M_NOSTART` | 0x0080 | Don't send START before this message |

### I2C Speed Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `I2C_SPEED_STANDARD` | 100000 | Standard mode (100 kHz) |
| `I2C_SPEED_FAST` | 400000 | Fast mode (400 kHz) |
| `I2C_SPEED_FAST_PLUS` | 1000000 | Fast+ mode (1 MHz) |
| `I2C_SPEED_HIGH` | 3400000 | High-speed mode (3.4 MHz) |

### I2C Helper Functions

NuttX provides convenience wrappers in `drivers/i2c/`:

```c
/* Simple read: write regaddr then read data */
int i2c_writeread(FAR struct i2c_master_s *dev,
                  FAR const struct i2c_config_s *config,
                  FAR const uint8_t *wbuffer, int wbuflen,
                  FAR uint8_t *rbuffer, int rbuflen);

/* Simple write */
int i2c_write(FAR struct i2c_master_s *dev,
              FAR const struct i2c_config_s *config,
              FAR const uint8_t *buffer, int buflen);

/* Simple read */
int i2c_read(FAR struct i2c_master_s *dev,
             FAR const struct i2c_config_s *config,
             FAR uint8_t *buffer, int buflen);
```

### Mutex-Protected I2C with Retry and Reset

For production drivers that share an I2C bus with other devices, wrap all bus access with mutex protection, retry logic, and optional bus reset. This pattern is used by touch, sensor, and NFC drivers.

```c
/* Private struct must include a mutex for bus serialization */

struct mydev_s
{
  FAR struct i2c_master_s *i2c;
  uint8_t                  addr;
  uint32_t                 freq;
  mutex_t                  lock;   /* Protects I2C bus access */
  /* ... other fields ... */
};

/* Write-then-read with mutex + retry + I2C_RESET */

static int mydev_i2c_write_read(FAR struct mydev_s *priv,
                                FAR uint8_t *cmd, uint16_t cmdlen,
                                FAR uint8_t *buf, uint16_t buflen)
{
  struct i2c_msg_s msg[2];
  int retries;
  int ret;

  msg[0].frequency = priv->freq;
  msg[0].addr      = priv->addr;
  msg[0].flags     = 0;
  msg[0].buffer    = cmd;
  msg[0].length    = cmdlen;

  msg[1].frequency = priv->freq;
  msg[1].addr      = priv->addr;
  msg[1].flags     = I2C_M_READ;
  msg[1].buffer    = buf;
  msg[1].length    = buflen;

  nxmutex_lock(&priv->lock);
  for (retries = 0; retries < 5; retries++)
    {
      ret = I2C_TRANSFER(priv->i2c, msg, 2);
      if (ret >= 0)
        {
          break;
        }

      usleep(1000);
#ifdef CONFIG_I2C_RESET
      I2C_RESET(priv->i2c);
#endif
    }

  nxmutex_unlock(&priv->lock);
  return (retries >= 5) ? -EIO : OK;
}

/* Write-only with mutex + retry */

static int mydev_i2c_write(FAR struct mydev_s *priv,
                           FAR uint8_t *buf, uint16_t len)
{
  struct i2c_msg_s msg;
  int retries;
  int ret;

  msg.frequency = priv->freq;
  msg.addr      = priv->addr;
  msg.flags     = 0;
  msg.buffer    = buf;
  msg.length    = len;

  nxmutex_lock(&priv->lock);
  for (retries = 0; retries < 5; retries++)
    {
      ret = I2C_TRANSFER(priv->i2c, &msg, 1);
      if (ret >= 0)
        {
          break;
        }

      usleep(1000);
#ifdef CONFIG_I2C_RESET
      I2C_RESET(priv->i2c);
#endif
    }

  nxmutex_unlock(&priv->lock);
  return (retries >= 5) ? -EIO : OK;
}
```

Key points:
- `nxmutex_lock` serializes bus access across threads (worker, PM, ioctl)
- Retry count 3-5 is typical for embedded I2C (bus glitches, NAK recovery)
- `I2C_RESET` is guarded by `CONFIG_I2C_RESET` — not all platforms support it
- `usleep(1000)` between retries gives the slave device time to recover

---

## SPI Bus Access

### Core Interface

```c
/* From include/nuttx/spi/spi.h */

struct spi_dev_s
{
  FAR const struct spi_ops_s *ops;
};
```

### SPI Access Macros

| Macro | Description |
|-------|-------------|
| `SPI_LOCK(dev, true/false)` | Lock/unlock the SPI bus for exclusive access |
| `SPI_SELECT(dev, devid, true/false)` | Assert/deassert chip select |
| `SPI_SETFREQUENCY(dev, freq)` | Set SPI clock frequency |
| `SPI_SETMODE(dev, mode)` | Set SPI mode (SPIDEV_MODE0..3) |
| `SPI_SETBITS(dev, nbits)` | Set word size (typically 8) |
| `SPI_HWFEATURES(dev, features)` | Set hardware features |
| `SPI_SEND(dev, word)` | Send one word, return received word |
| `SPI_SNDBLOCK(dev, buf, nwords)` | Send block of data |
| `SPI_RECVBLOCK(dev, buf, nwords)` | Receive block of data |
| `SPI_EXCHANGE(dev, txbuf, rxbuf, nwords)` | Full-duplex exchange |

### Common SPI Pattern

```c
/* Lock, configure, select, transfer, deselect, unlock */

static int mydev_read_reg(FAR struct mydev_s *priv, uint8_t reg,
                           FAR uint8_t *buf, size_t len)
{
  uint8_t cmd = reg | 0x80;  /* Set read bit (device-specific) */

  SPI_LOCK(priv->spi, true);
  SPI_SETFREQUENCY(priv->spi, priv->freq);
  SPI_SETMODE(priv->spi, SPIDEV_MODE0);
  SPI_SETBITS(priv->spi, 8);

  SPI_SELECT(priv->spi, SPIDEV_ACCELEROMETER(priv->devid), true);
  SPI_SEND(priv->spi, cmd);
  SPI_RECVBLOCK(priv->spi, buf, len);
  SPI_SELECT(priv->spi, SPIDEV_ACCELEROMETER(priv->devid), false);

  SPI_LOCK(priv->spi, false);
  return OK;
}

static int mydev_write_reg(FAR struct mydev_s *priv, uint8_t reg,
                            uint8_t val)
{
  SPI_LOCK(priv->spi, true);
  SPI_SETFREQUENCY(priv->spi, priv->freq);
  SPI_SETMODE(priv->spi, SPIDEV_MODE0);
  SPI_SETBITS(priv->spi, 8);

  SPI_SELECT(priv->spi, SPIDEV_ACCELEROMETER(priv->devid), true);
  SPI_SEND(priv->spi, reg);       /* Register address (write bit = 0) */
  SPI_SEND(priv->spi, val);       /* Register value */
  SPI_SELECT(priv->spi, SPIDEV_ACCELEROMETER(priv->devid), false);

  SPI_LOCK(priv->spi, false);
  return OK;
}
```

### 16-bit Frame Protocol SPI Pattern

Some devices (e.g., Analog Devices ADIS series) use a 16-bit SPI frame protocol where each transaction is a single 16-bit word. The upper byte is the command (register address + R/W bit), and the lower byte is the data.

```c
/* 16-bit frame protocol: each SPI transaction is one 16-bit word.
 * Read requires two transactions (pipeline protocol):
 *   TX1: [addr|0x00] → RX1: (previous result)
 *   TX2: [0x00|0x00] → RX2: (data for addr)
 *
 * Write splits 16-bit register value into two 8-bit writes:
 *   TX1: [addr_low  | value_low ]
 *   TX2: [addr_high | value_high]
 */

static uint16_t mydev_getreg16(FAR struct mydev_s *priv, uint8_t regaddr)
{
  uint16_t cmd;
  uint16_t result;

  SPI_LOCK(priv->spi, true);
  mydev_configspi(priv->spi);  /* SPI_SETBITS(spi, 16) */

  SPI_SELECT(priv->spi, SPIDEV_IMU(0), true);
  cmd = ((uint16_t)regaddr) << 8;  /* Read: R/W bit = 0 */
  SPI_SEND(priv->spi, cmd);
  SPI_SELECT(priv->spi, SPIDEV_IMU(0), false);

  up_udelay(priv->stall_us);  /* Inter-frame stall time (datasheet) */

  SPI_SELECT(priv->spi, SPIDEV_IMU(0), true);
  result = SPI_SEND(priv->spi, 0x0000);  /* Clock out data */
  SPI_SELECT(priv->spi, SPIDEV_IMU(0), false);

  SPI_LOCK(priv->spi, false);
  return result;
}

static void mydev_putreg16(FAR struct mydev_s *priv, uint8_t regaddr,
                            uint16_t value)
{
  uint16_t cmd;

  SPI_LOCK(priv->spi, true);
  mydev_configspi(priv->spi);

  /* Write low byte */

  SPI_SELECT(priv->spi, SPIDEV_IMU(0), true);
  cmd = (0x80 | regaddr) << 8 | (value & 0xff);
  SPI_SEND(priv->spi, cmd);
  SPI_SELECT(priv->spi, SPIDEV_IMU(0), false);

  up_udelay(priv->stall_us);

  /* Write high byte */

  SPI_SELECT(priv->spi, SPIDEV_IMU(0), true);
  cmd = (0x80 | (regaddr + 1)) << 8 | ((value >> 8) & 0xff);
  SPI_SEND(priv->spi, cmd);
  SPI_SELECT(priv->spi, SPIDEV_IMU(0), false);

  SPI_LOCK(priv->spi, false);
}
```

### SPI Device ID Macros

Used with `SPI_SELECT()` to identify the device type for chip-select routing:

| Macro | Usage |
|-------|-------|
| `SPIDEV_TEMPERATURE(devid)` | Temperature sensors |
| `SPIDEV_ACCELEROMETER(devid)` | Accelerometers |
| `SPIDEV_BAROMETER(devid)` | Barometers |
| `SPIDEV_FLASH(devid)` | Flash memory |
| `SPIDEV_DISPLAY(devid)` | Display controllers |
| `SPIDEV_WIRELESS(devid)` | Wireless modules |
| `SPIDEV_ETHERNET(devid)` | Ethernet controllers |

### SPI Modes

| Mode | CPOL | CPHA | Description |
|------|------|------|-------------|
| `SPIDEV_MODE0` | 0 | 0 | Clock idle low, sample on rising edge |
| `SPIDEV_MODE1` | 0 | 1 | Clock idle low, sample on falling edge |
| `SPIDEV_MODE2` | 1 | 0 | Clock idle high, sample on falling edge |
| `SPIDEV_MODE3` | 1 | 1 | Clock idle high, sample on rising edge |

---

## Getting Bus Instances

Bus instances are obtained from architecture-specific initialization functions, typically called in board-level code:

```c
/* I2C */
FAR struct i2c_master_s *i2c = stm32_i2cbus_initialize(busno);
FAR struct i2c_master_s *i2c = esp32_i2cbus_initialize(busno);
FAR struct i2c_master_s *i2c = rp2040_i2cbus_initialize(busno);

/* SPI */
FAR struct spi_dev_s *spi = stm32_spibus_initialize(busno);
FAR struct spi_dev_s *spi = esp32_spibus_initialize(busno);
```

The function name pattern is `<arch>_i2cbus_initialize(busno)` or `<arch>_spibus_initialize(busno)`.
