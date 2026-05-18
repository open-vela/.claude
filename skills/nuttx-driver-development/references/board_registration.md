# Board-Level Driver Registration

## Overview

In NuttX, device drivers are registered during board initialization. The board-specific code obtains bus instances (I2C, SPI) from the architecture layer and passes them to driver registration functions.

## Initialization Flow

> [!IMPORTANT] I2C/SPI device drivers MUST be initialized in `board_late_initialize()` or later.
> Earlier stages (`drivers_early_initialize`, `drivers_initialize`, `board_early_initialize`)
> do NOT have the scheduler ready — bus operations require blocking and will deadlock.

```
OS Boot
  │
  ▼
nx_start()
  ├── drivers_early_initialize()       [NO blocking, NO heap]
  ├── drivers_initialize()             [NO blocking]
  ├── board_early_initialize()         [NO blocking]
  └── board_late_initialize()          [OK: scheduler ready, blocking allowed]
      └── board_app_initialize()       [called if CONFIG_BOARDCTL]
          └── board_bringup()          [common pattern]
              ├── board_bmp280_initialize(0, 1)
              ├── board_bmi160_initialize(0, 1)
              └── ...
  ▼
Application starts
```

**Timing rules:**
- I2C/SPI sensor registration → `board_late_initialize()` or `board_app_initialize()`
- Operations depending on filesystem → delay to `board_app_finalinitialize()`
- Avoid long-running init in these functions — use `work_queue` for async init if needed
- Avoid large local variables in init functions — use `kmm_zalloc` instead

## Board Bringup Pattern

Most boards follow this pattern in their bringup file:

```c
/* boards/<arch>/<chip>/<board>/src/<board>_bringup.c */

#include <nuttx/config.h>

#ifdef CONFIG_SENSORS_BMP280
int board_bmp280_initialize(int devno, int busno);
#endif

int board_app_initialize(uintptr_t arg)
{
  int ret;

#ifdef CONFIG_SENSORS_BMP280
  ret = board_bmp280_initialize(0, 1);  /* devno=0, I2C bus=1 */
  if (ret < 0)
    {
      syslog(LOG_ERR, "ERROR: board_bmp280_initialize failed: %d\n", ret);
    }
#endif

  /* ... more driver registrations ... */

  return OK;
}
```

## I2C Sensor Registration Example

```c
/* boards/arm/stm32/common/src/stm32_bmp280.c */

#include <nuttx/config.h>
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/sensors/bmp280.h>
#include "stm32_i2c.h"

int board_bmp280_initialize(int devno, int busno)
{
  FAR struct i2c_master_s *i2c;
  int ret;

  /* Step 1: Get the I2C bus instance from architecture layer */

  i2c = stm32_i2cbus_initialize(busno);
  if (i2c == NULL)
    {
      snerr("ERROR: Failed to initialize I2C%d\n", busno);
      return -ENODEV;
    }

  /* Step 2: Register the sensor driver */

  ret = bmp280_register(devno, i2c);
  if (ret < 0)
    {
      snerr("ERROR: Failed to register BMP280 on I2C%d: %d\n", busno, ret);
    }

  return ret;
}
```

## SPI Sensor Registration Example

```c
/* boards/arm/stm32/<board>/src/<board>_max31855.c */

#include <nuttx/config.h>
#include <nuttx/spi/spi.h>
#include <nuttx/sensors/max31855.h>
#include "stm32_spi.h"

int board_max31855_initialize(int devno, int busno)
{
  FAR struct spi_dev_s *spi;
  int ret;

  /* Step 1: Get the SPI bus instance */

  spi = stm32_spibus_initialize(busno);
  if (spi == NULL)
    {
      snerr("ERROR: Failed to initialize SPI%d\n", busno);
      return -ENODEV;
    }

  /* Step 2: Register the sensor driver */

  char devpath[16];
  snprintf(devpath, sizeof(devpath), "/dev/temp%d", devno);

  ret = max31855_register(devpath, spi, devno);
  if (ret < 0)
    {
      snerr("ERROR: Failed to register MAX31855: %d\n", ret);
    }

  return ret;
}
```

## Architecture-Specific Bus Init Functions

Each architecture provides its own bus initialization. The naming convention:

### I2C

| Architecture | Function |
|-------------|----------|
| STM32 | `stm32_i2cbus_initialize(busno)` |
| STM32L4 | `stm32l4_i2cbus_initialize(busno)` |
| ESP32 | `esp32_i2cbus_initialize(busno)` |
| RP2040 | `rp2040_i2cbus_initialize(busno)` |
| IMXRT | `imxrt_i2cbus_initialize(busno)` |
| CXD56xx | `cxd56_i2cbus_initialize(busno)` |
| SAMV7 | `sam_i2cbus_initialize(busno)` |

### SPI

| Architecture | Function |
|-------------|----------|
| STM32 | `stm32_spibus_initialize(busno)` |
| ESP32 | `esp32_spibus_initialize(busno)` |
| RP2040 | `rp2040_spibus_initialize(busno)` |
| IMXRT | `imxrt_lpspi_initialize(busno)` |

## Enabling in defconfig

To enable a driver, add these to your board's `defconfig`:

```
# Enable the sensor subsystem
CONFIG_SENSORS=y

# Enable I2C bus support
CONFIG_I2C=y

# Enable the specific sensor driver
CONFIG_SENSORS_BMP280=y
CONFIG_BMP280_I2C_FREQUENCY=400000
CONFIG_BMP280_I2C_ADDR_76=y

# Enable board-level initialization
CONFIG_BOARDCTL=y
CONFIG_BOARD_LATE_INITIALIZE=y
```

## Adding Board Init to Build

### Make.defs (board level)

```makefile
ifeq ($(CONFIG_SENSORS_BMP280),y)
  CSRCS += stm32_bmp280.c
endif
```

### CMakeLists.txt (board level)

```cmake
if(CONFIG_SENSORS_BMP280)
  list(APPEND SRCS stm32_bmp280.c)
endif()
```

## Multiple Instances

To register multiple instances of the same sensor:

```c
/* Two BMP280 sensors on the same I2C bus */
bmp280_register(0, i2c);  /* /dev/uorb/sensor_baro0 */
bmp280_register(1, i2c);  /* /dev/uorb/sensor_baro1 */

/* Or on different buses */
i2c1 = stm32_i2cbus_initialize(1);
i2c2 = stm32_i2cbus_initialize(2);
bmp280_register(0, i2c1);
bmp280_register(1, i2c2);
```

## Checklist for Board Registration

- [ ] Board init function created (`board_<device>_initialize()`)
- [ ] Called from `board_app_initialize()` or `board_bringup()`
- [ ] Guarded by `#ifdef CONFIG_SENSORS_MYDEVICE`
- [ ] Source added to board's `Make.defs` and `CMakeLists.txt`
- [ ] `defconfig` updated with required CONFIG options
- [ ] I2C/SPI bus number matches hardware wiring
- [ ] Device address matches hardware configuration (pull-up/down on addr pins)

## Input Device Board Registration

Input devices (touch, keyboard, mouse) follow the same board registration pattern as sensors, but use a `board_s` callback structure for interrupt management instead of a simple bus+address pair.

### Touch Panel Board Registration Example

```c
/* boards/<arch>/<chip>/<board>/src/board_mytouch.c */

#include <nuttx/config.h>
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/input/mytouch.h>
#include "board_gpio.h"  /* Board-specific GPIO definitions */

/* Forward declarations */

static int  mytouch_irq_attach(FAR const struct mytouch_board_s *board,
                               xcpt_t isr, FAR void *arg);
static void mytouch_irq_enable(FAR const struct mytouch_board_s *board,
                               bool enable);
static void mytouch_nreset(FAR const struct mytouch_board_s *board,
                           bool nstate);
static void mytouch_power(FAR const struct mytouch_board_s *board,
                          bool enable);

/* Board callback instance — static, one per physical device */

static xcpt_t g_mytouch_isr;
static FAR void *g_mytouch_arg;

static const struct mytouch_board_s g_mytouch_board =
{
  .irq_attach   = mytouch_irq_attach,
  .irq_enable   = mytouch_irq_enable,
  .nreset       = mytouch_nreset,
  .power_enable = mytouch_power,
};

/* Interrupt management — wraps board-specific GPIO IRQ API */

static int mytouch_irq_attach(FAR const struct mytouch_board_s *board,
                              xcpt_t isr, FAR void *arg)
{
  g_mytouch_isr = isr;
  g_mytouch_arg = arg;

  /* Board-specific: configure GPIO as interrupt input */

  board_gpio_config(TOUCH_INT_PIN, GPIO_INPUT | GPIO_IRQ_FALLING);
  irq_attach(TOUCH_INT_IRQ, isr, arg);
  return OK;
}

static void mytouch_irq_enable(FAR const struct mytouch_board_s *board,
                               bool enable)
{
  if (enable)
    {
      up_enable_irq(TOUCH_INT_IRQ);
    }
  else
    {
      up_disable_irq(TOUCH_INT_IRQ);
    }
}

static void mytouch_nreset(FAR const struct mytouch_board_s *board,
                           bool nstate)
{
  board_gpio_write(TOUCH_RESET_PIN, nstate);
}

static void mytouch_power(FAR const struct mytouch_board_s *board,
                          bool enable)
{
  board_gpio_write(TOUCH_POWER_PIN, enable);
}

/* Board init entry point — called from board_bringup */

int board_mytouch_initialize(FAR const char *devpath, int busno)
{
  FAR struct i2c_master_s *i2c;

  i2c = board_i2cbus_initialize(busno);
  if (i2c == NULL)
    {
      ierr("ERROR: Failed to init I2C%d\n", busno);
      return -ENODEV;
    }

  return mytouch_register(devpath, i2c, 0x5A, &g_mytouch_board);
}
```

### Key Differences from Sensor Board Registration

| Aspect | Sensor | Input (Touch) |
|--------|--------|---------------|
| Registration call | `sensor_register(devno, i2c)` | `mytouch_register(devpath, i2c, addr, board)` |
| Board callbacks | None (bus only) | `board_s` with irq_attach/irq_enable + optional nreset/power |
| Interrupt setup | Done inside driver | Done in board callbacks (board owns GPIO resources) |
| Device path | Auto-generated (`/dev/uorb/...`) | Explicit (`/dev/input0`) |
