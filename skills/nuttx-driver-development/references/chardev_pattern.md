# Standalone Character Device Driver Pattern

Use this pattern when your device doesn't fit an existing NuttX subsystem (sensor, serial, timer, etc.). You implement `struct file_operations` directly and register with `register_driver()`.

## Table of Contents

1. [Architecture](#architecture) — VFS → file_operations → Bus 调用链
2. [Key Data Structures](#key-data-structures) — `file_operations`、访问 private data、并发保护
3. [Minimal Example](#minimal-example-spi-temperature-sensor-max31855-style) — SPI 温度传感器简化示例
4. [Registration API](#registration-api) — `register_driver` / `unregister_driver`
5. [When to Use This Pattern vs Sensor uORB](#when-to-use-this-pattern-vs-sensor-uorb) — 选型对照表
6. [Production-Quality Skeleton](#production-quality-skeleton-based-on-nuttx-in-tree-pattern) — 带 mutex、refcount、unlink 的生产级骨架

---

## Architecture

```
Application (open/read/write/ioctl/close)
    │
    ▼
VFS: /dev/mydevN
    │
    ▼
file_operations (your driver callbacks)
    │
    ▼
Bus: I2C_TRANSFER() / SPI_*() / GPIO / etc.
```

## Key Data Structures

### file_operations

```c
/* From include/nuttx/fs/fs.h */
struct file_operations
{
  CODE int     (*open)(FAR struct file *filep);
  CODE int     (*close)(FAR struct file *filep);
  CODE ssize_t (*read)(FAR struct file *filep, FAR char *buffer, size_t buflen);
  CODE ssize_t (*write)(FAR struct file *filep, FAR const char *buffer, size_t buflen);
  CODE off_t   (*seek)(FAR struct file *filep, off_t offset, int whence);
  CODE int     (*ioctl)(FAR struct file *filep, int cmd, unsigned long arg);
  CODE int     (*mmap)(FAR struct file *filep, FAR struct mm_map_entry_s *map);
  CODE int     (*truncate)(FAR struct file *filep, off_t length);
  CODE int     (*poll)(FAR struct file *filep, FAR struct pollfd *fds, bool setup);
  CODE ssize_t (*readv)(FAR struct file *filep, FAR struct uio *uio);
  CODE ssize_t (*writev)(FAR struct file *filep, FAR struct uio *uio);
#ifndef CONFIG_DISABLE_PSEUDOFS_OPERATIONS
  CODE int     (*unlink)(FAR struct inode *inode);
#endif
};
```

### Accessing private data

```c
/* In read/write/ioctl callbacks: */
FAR struct inode *inode = filep->f_inode;
FAR struct mydevice_dev_s *priv = inode->i_private;
```

> [!IMPORTANT] Concurrent access protection
>
> Multiple threads may open and access the same device simultaneously.
> If your driver has mutable state (cached readings, power state, etc.),
> protect shared data with `nxmutex_lock()` / `nxmutex_unlock()` in your
> read/write/ioctl callbacks. Add a `mutex_t lock;` field to the private struct.

## Minimal Example: SPI Temperature Sensor (MAX31855-style)

> [!WARNING] This is a simplified example for learning purposes
>
> This example omits mutex protection, open/close reference counting, and unlink support
> for brevity. For production drivers, use the **Production-Quality Skeleton** section below
> which includes all of these.

```c
#include <nuttx/config.h>
#include <stdlib.h>
#include <errno.h>
#include <debug.h>
#include <nuttx/kmalloc.h>
#include <nuttx/fs/fs.h>
#include <nuttx/spi/spi.h>
#include <nuttx/sensors/mydevice.h>

#if defined(CONFIG_SPI) && defined(CONFIG_SENSORS_MYDEVICE)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct mydevice_dev_s
{
  FAR struct spi_dev_s *spi;   /* SPI bus handle */
  uint16_t devid;              /* Device ID for chip select */
  int16_t last_temp;           /* Cached last reading */
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static ssize_t mydevice_read(FAR struct file *filep, FAR char *buffer,
                              size_t buflen);
static ssize_t mydevice_write(FAR struct file *filep,
                               FAR const char *buffer, size_t buflen);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct file_operations g_mydevice_fops =
{
  NULL,             /* open  - no special init needed */
  NULL,             /* close - no special cleanup needed */
  mydevice_read,    /* read  */
  mydevice_write,   /* write */
  NULL,             /* seek  */
  NULL,             /* ioctl */
  NULL,             /* truncate */
  NULL              /* poll  */
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static void mydevice_lock(FAR struct spi_dev_s *spi)
{
  SPI_LOCK(spi, true);
  SPI_SETMODE(spi, SPIDEV_MODE0);
  SPI_SETBITS(spi, 8);
  SPI_HWFEATURES(spi, 0);
  SPI_SETFREQUENCY(spi, 400000);
}

static void mydevice_unlock(FAR struct spi_dev_s *spi)
{
  SPI_LOCK(spi, false);
}

static ssize_t mydevice_read(FAR struct file *filep, FAR char *buffer,
                              size_t buflen)
{
  FAR struct inode *inode = filep->f_inode;
  FAR struct mydevice_dev_s *priv = inode->i_private;
  FAR int16_t *temp = (FAR int16_t *)buffer;
  int32_t rawdata;

  if (!buffer)
    {
      return -EINVAL;
    }

  if (buflen < sizeof(int16_t))
    {
      return -EINVAL;
    }

  /* Read from SPI */

  mydevice_lock(priv->spi);
  SPI_SELECT(priv->spi, SPIDEV_TEMPERATURE(priv->devid), true);
  SPI_RECVBLOCK(priv->spi, &rawdata, 4);
  SPI_SELECT(priv->spi, SPIDEV_TEMPERATURE(priv->devid), false);
  mydevice_unlock(priv->spi);

  /* Convert raw data to temperature */

  *temp = (int16_t)(rawdata >> 18);
  priv->last_temp = *temp;

  return sizeof(int16_t);
}

static ssize_t mydevice_write(FAR struct file *filep,
                               FAR const char *buffer, size_t buflen)
{
  return -ENOSYS;  /* Write not supported */
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int mydevice_register(FAR const char *devpath, FAR struct spi_dev_s *spi,
                       uint16_t devid)
{
  FAR struct mydevice_dev_s *priv;
  int ret;

  DEBUGASSERT(spi != NULL);

  priv = kmm_zalloc(sizeof(struct mydevice_dev_s));
  if (priv == NULL)
    {
      snerr("ERROR: Failed to allocate instance\n");
      return -ENOMEM;
    }

  priv->spi   = spi;
  priv->devid = devid;

  /* Register the character driver */

  ret = register_driver(devpath, &g_mydevice_fops, 0666, priv);
  if (ret < 0)
    {
      snerr("ERROR: Failed to register driver: %d\n", ret);
      kmm_free(priv);
    }

  return ret;
}

#endif /* CONFIG_SPI && CONFIG_SENSORS_MYDEVICE */
```

## Registration API

```c
/* Register a character device driver */
int register_driver(FAR const char *path,
                    FAR const struct file_operations *fops,
                    mode_t mode,
                    FAR void *priv);

/* Unregister a character device driver */
int unregister_driver(FAR const char *path);
```

Parameters:
- `path`: Device path (e.g., `/dev/temp0`)
- `fops`: Pointer to your file_operations struct
- `mode`: File permissions (typically `0666`)
- `priv`: Private data pointer, accessible via `filep->f_inode->i_private`

## When to Use This Pattern vs Sensor uORB

| Use Character Device | Use Sensor uORB |
|---------------------|-----------------|
| Device doesn't fit sensor model | Any sensor device |
| Custom read/write semantics | Standard sensor data types |
| Need direct ioctl control | Want poll/batch/circular buffer for free |
| Legacy driver compatibility | New sensor driver development |
| Non-sensor peripherals (LED, motor, etc.) | Accelerometer, gyro, baro, temp, etc. |

## Production-Quality Skeleton (Based on NuttX In-Tree Pattern)

The simple example above omits open/close reference counting, mutex protection, and unlink support. The following skeleton (abstracted from `nuttx/drivers/i2c/i2c_driver.c`) shows the production-quality pattern used by real NuttX chardev drivers.

Key additions over the simple example:
- `nxmutex` protecting all shared state
- Reference counting in open/close
- Unlink support (deferred free when refs > 0)
- Proper cleanup on registration failure

```c
#include <nuttx/config.h>

#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <debug.h>

#include <nuttx/kmalloc.h>
#include <nuttx/fs/fs.h>
#include <nuttx/mutex.h>
#include <nuttx/i2c/i2c_master.h>

#if defined(CONFIG_I2C) && defined(CONFIG_MYDEVICE)

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define DEVNAME_FMT    "/dev/mydev%d"
#define DEVNAME_FMTLEN (12 + 3 + 1)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct mydevice_dev_s
{
  FAR struct i2c_master_s *i2c;  /* I2C bus handle */
  mutex_t lock;                  /* Mutual exclusion */
  int16_t crefs;                 /* Number of open references */
  bool unlinked;                 /* True if driver has been unlinked */
  uint8_t addr;                  /* I2C device address */
  int freq;                      /* I2C bus frequency */
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int     mydevice_open(FAR struct file *filep);
static int     mydevice_close(FAR struct file *filep);
static ssize_t mydevice_read(FAR struct file *filep, FAR char *buffer,
                 size_t buflen);
static ssize_t mydevice_write(FAR struct file *filep,
                 FAR const char *buffer, size_t buflen);
static int     mydevice_ioctl(FAR struct file *filep, int cmd,
                 unsigned long arg);
static int     mydevice_unlink(FAR struct inode *inode);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct file_operations g_mydevice_fops =
{
  mydevice_open,     /* open */
  mydevice_close,    /* close */
  mydevice_read,     /* read */
  mydevice_write,    /* write */
  NULL,              /* seek */
  mydevice_ioctl,    /* ioctl */
  NULL,              /* mmap */
  NULL,              /* truncate */
  NULL,              /* poll */
  NULL,              /* readv */
  NULL,              /* writev */
  mydevice_unlink    /* unlink */
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int mydevice_open(FAR struct file *filep)
{
  FAR struct mydevice_dev_s *priv = filep->f_inode->i_private;
  int ret = OK;

  DEBUGASSERT(priv != NULL);

  nxmutex_lock(&priv->lock);

  /* First open: initialize hardware if needed */

  if (priv->crefs == 0)
    {
      /* TODO: Hardware power-on / init sequence */
    }

  priv->crefs++;
  DEBUGASSERT(priv->crefs > 0);

  nxmutex_unlock(&priv->lock);
  return ret;
}

static int mydevice_close(FAR struct file *filep)
{
  FAR struct mydevice_dev_s *priv = filep->f_inode->i_private;
  int ret = OK;

  DEBUGASSERT(priv != NULL);

  nxmutex_lock(&priv->lock);

  DEBUGASSERT(priv->crefs > 0);
  priv->crefs--;

  /* Last close: power down hardware */

  if (priv->crefs == 0)
    {
      /* TODO: Hardware power-off sequence */

      /* If unlinked, free resources now */

      if (priv->unlinked)
        {
          nxmutex_destroy(&priv->lock);
          kmm_free(priv);
          filep->f_inode->i_private = NULL;
          return OK;
        }
    }

  nxmutex_unlock(&priv->lock);
  return ret;
}

static ssize_t mydevice_read(FAR struct file *filep, FAR char *buffer,
                             size_t buflen)
{
  FAR struct mydevice_dev_s *priv = filep->f_inode->i_private;
  int ret;

  if (buffer == NULL || buflen == 0)
    {
      return -EINVAL;
    }

  nxmutex_lock(&priv->lock);

  /* TODO: Read data from device via I2C */

  ret = -ENOSYS;

  nxmutex_unlock(&priv->lock);
  return ret;
}

static ssize_t mydevice_write(FAR struct file *filep,
                              FAR const char *buffer, size_t buflen)
{
  return -ENOSYS;  /* Write not supported */
}

static int mydevice_ioctl(FAR struct file *filep, int cmd,
                          unsigned long arg)
{
  FAR struct mydevice_dev_s *priv = filep->f_inode->i_private;
  int ret;

  DEBUGASSERT(priv != NULL);

  nxmutex_lock(&priv->lock);

  switch (cmd)
    {
      /* TODO: Add device-specific ioctl commands */

      default:
        ret = -ENOTTY;
        break;
    }

  nxmutex_unlock(&priv->lock);
  return ret;
}

static int mydevice_unlink(FAR struct inode *inode)
{
  FAR struct mydevice_dev_s *priv = inode->i_private;

  DEBUGASSERT(priv != NULL);

  nxmutex_lock(&priv->lock);

  if (priv->crefs <= 0)
    {
      nxmutex_destroy(&priv->lock);
      kmm_free(priv);
      inode->i_private = NULL;
      return OK;
    }

  priv->unlinked = true;
  nxmutex_unlock(&priv->lock);
  return OK;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int mydevice_register(int devno, FAR struct i2c_master_s *i2c)
{
  FAR struct mydevice_dev_s *priv;
  char devname[DEVNAME_FMTLEN];
  int ret;

  DEBUGASSERT(i2c != NULL);

  priv = kmm_zalloc(sizeof(struct mydevice_dev_s));
  if (priv == NULL)
    {
      return -ENOMEM;
    }

  priv->i2c  = i2c;
  priv->addr = 0x50;  /* TODO: Device-specific I2C address */
  priv->freq = 400000;
  nxmutex_init(&priv->lock);

  snprintf(devname, sizeof(devname), DEVNAME_FMT, devno);
  ret = register_driver(devname, &g_mydevice_fops, 0666, priv);
  if (ret < 0)
    {
      nxmutex_destroy(&priv->lock);
      kmm_free(priv);
    }

  return ret;
}

#endif /* CONFIG_I2C && CONFIG_MYDEVICE */
```

> [!NOTE] Reference source
>
> This skeleton is abstracted from `nuttx/drivers/i2c/i2c_driver.c`. When implementing
> a new chardev driver, copy this skeleton and fill in the `TODO` sections with
> device-specific logic. The mutex, refcount, and unlink patterns should be kept as-is.
