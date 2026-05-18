# NuttX Driver Coding Rules & Kernel API Reference

Cross-subsystem rules that affect code correctness. These cannot be caught by checkpatch.

## Coding Style

Code formatting (indentation, braces, line length, etc.) is enforced by `nuttx/tools/checkpatch.sh`. **Always run it after generating code:**

```bash
nuttx/tools/checkpatch.sh -f <your_file.c>
```

Full style reference: `nuttx/CONTRIBUTING.md`

### Key Rules Beyond Formatting

- **No global variables**: Use dynamically allocated private structs (`kmm_zalloc`) to support multiple device instances. Never store device state in file-scope static variables.
  - **例外**：单实例驱动的 IRQ handler 需要在中断上下文访问实例，允许使用 `static FAR struct xxx_dev_s *g_xxx_priv` 全局指针，但必须在 `initialize` 中赋值且加注释说明原因（如 `/* 全局指针，供 IRQ handler 在中断上下文中访问实例 */`）。
- **Always use `kmm_zalloc`** (not `kmm_malloc`) for private struct allocation — avoids uninitialized field bugs.
- **FAR pointer**: Use `FAR` macro for all pointer parameters crossing module boundaries.
- **Error returns**: Use negated errno values (`-EINVAL`, `-ENOMEM`, `-ENODEV`, etc.).
- **Debug macros**: Use `snerr()`, `snwarn()`, `sninfo()` for sensor drivers.
- **Guard macros**: `#if defined(CONFIG_I2C) && defined(CONFIG_SENSORS_XXX)` wrapping entire `.c` file.
- **Global/static variable naming**: Use `g_` prefix (e.g., `g_sensor_ops`, `g_mydevice_fops`).
- **Struct type naming**: `struct xxx_dev_s` (suffix `_s`), enum: `enum xxx_e` (suffix `_e`).

## Kernel API Usage Rules

| Scenario | Correct | Wrong (will cause bugs) |
|----------|---------|------------------------|
| Microsecond delay | `up_udelay()` / `up_mdelay()` | `usleep()` (triggers scheduler, unusable in interrupt/early-init context) |
| Semaphore wait | `nxsem_wait_uninterruptible()` | `nxsem_wait()` (can be interrupted by signals) |
| File ops in kernel | `file_open()` / `file_read()` / `file_write()` | `open()` / `read()` / `write()` (user-space API, wrong in kernel context) |
| DMA buffer alloc | `kmm_memalign()` | `kmm_malloc()` (no alignment guarantee) |
| Periodic work | `work_queue(LPWORK, ...)` | `kthread_create()` (prefer work_queue; use LPWORK for bus I/O, HPWORK only for < 1ms non-blocking callbacks) |
| Interrupt setup | `irq_attach()` first, then `up_enable_irq()` | Enabling before attaching (may fire with no handler) |
| Avoid I/O path malloc | Pre-allocate buffers at init time | `kmm_malloc` in read/write path (fragmentation, latency) |

## Initialization Timing Constraints

I2C/SPI device drivers **must** be initialized in `board_late_initialize()` or later (scheduler is ready, blocking is allowed). Initializing in `board_early_initialize()` or `drivers_initialize()` will deadlock because bus operations require blocking.

```
nx_start()
├── board_early_initialize()         ← Idle task. NO blocking, NO heap, NO bus ops
├── board_late_initialize()          ← AppBringup task. Scheduler ready, blocking OK
│   └── Most I2C/SPI drivers go here
├── board_app_initialize()           ← NSH task. Filesystem NOT yet mounted
├── rc.sysinit                       ← Mounts filesystem, starts KVDB and core services
├── board_app_finalinitialize()      ← NSH task. Filesystem available, KVDB ready
│   └── Drivers needing FS/KVDB/remote nodes go here
└── rcS                              ← Starts user applications
```

Placement rules:
- **I2C/SPI drivers** (no FS dependency) → `board_late_initialize()`
- **Drivers that access files** → `board_app_finalinitialize()` (filesystem mounted after `rc.sysinit`)
- **Drivers that use KVDB** → `board_app_finalinitialize()` (KVDB started by `rc.sysinit`)
- **Drivers accessing remote nodes (RPMsg)** → must ensure remote core and rpmsg dev are ready first
- **Avoid long delays in init** — use `work_queue` deferred init to avoid blocking system boot
- **Avoid large local variables in init** — AppBringup stack is limited, use `kmm_zalloc` instead

## Work Queue Selection: HPWORK vs LPWORK

| Queue | Priority | Use For | Constraint |
|-------|----------|---------|------------|
| `HPWORK` | Highest | Extremely short non-blocking callbacks (< 1ms, no bus I/O) | Callback must complete in **< 1ms**, NO blocking, NO I2C/SPI |
| `LPWORK` | Low | Sensor data collection, I2C/SPI transfers, background tasks | Can block for bus operations, tolerates millisecond-level latency |

Rules:
- Sensor periodic data collection → `LPWORK` (I2C/SPI transfers block, incompatible with HPWORK)
- ISR bottom-half with bus access → `LPWORK` (bus operations require blocking)
- HPWORK only for pure memory operations that never touch I2C/SPI bus
- Use static `struct work_s` (embedded in private struct), never dynamically allocate work items
- `work_queue()` and `work_cancel()` are safe to call from interrupt context
- `work_cancel_sync()` blocks until a running work item completes — use when you need guaranteed cancellation (e.g., in `activate(false)` before powering down hardware)

## Interrupt-Driven Driver Pattern

For devices with hardware interrupt (data-ready pin), use the ISR → work_queue → push pattern:

```c
/* ISR: minimal — clear interrupt, submit bottom-half */

static int mydevice_isr(int irq, FAR void *context, FAR void *arg)
{
  FAR struct mydevice_dev_s *priv = arg;

  /* Submit bottom-half to LPWORK (safe in ISR context) */

  work_queue(LPWORK, &priv->work, mydevice_worker, priv, 0);
  return OK;
}

/* Bottom-half: runs in thread context, can block */

static void mydevice_worker(FAR void *arg)
{
  FAR struct mydevice_dev_s *priv = arg;

  /* Read device data via I2C/SPI (blocking OK here) */

  mydevice_read_data(priv, &data);

  /* Push to upper-half or process data */
  /* ... subsystem-specific push logic ... */
}
```

Interrupt initialization (in register function):
```c
/* Order matters: attach first, then enable */

irq_attach(priv->irq, mydevice_isr, priv);
up_enable_irq(priv->irq);
```

## Interrupt Context Rules

What is **forbidden** in ISR:

| Forbidden | Reason |
|-----------|--------|
| `nxmutex_lock()`, `sem_wait()`, `sleep()` | Blocking — will ASSERT |
| `kmm_malloc()`, `kmm_free()` | Internally acquires mutex |
| `I2C_TRANSFER()`, `SPI_LOCK()` | Blocking bus operations |
| Long computation (> 10μs) | Starves other interrupts and tasks |

What is **allowed** in ISR:

| Allowed | Use Case |
|---------|----------|
| `work_queue()`, `work_cancel()` | Submit bottom-half work |
| `sem_post()` | Wake a waiting task |
| `spin_lock_irqsave()` / `spin_unlock_irqrestore()` | Protect shared data with thread context |
| Atomic operations (`atomic_set`, `atomic_read`, etc.) | Simple flag/counter updates |

## Synchronization Primitives for Drivers

Choose based on context and critical section duration:

| Scenario | Use | API |
|----------|-----|-----|
| ISR ↔ thread shared data | Spinlock + IRQ save | `spin_lock_irqsave()` / `spin_unlock_irqrestore()` |
| Thread ↔ thread shared data | Mutex | `nxmutex_lock()` / `nxmutex_unlock()` |
| Simple counter/flag (any context) | Atomic ops | `atomic_set()` / `atomic_read()` / `atomic_add()` |
| Task synchronization (wait/notify) | Semaphore | `nxsem_wait_uninterruptible()` / `nxsem_post()` |

Key rules:
- `nxmutex` is the **preferred** mutex for kernel/driver code (not `pthread_mutex`)
- `nxmutex` automatically enables priority inheritance — prevents priority inversion
- Spinlock critical sections must be **< 10μs** — never call blocking APIs while holding a spinlock
- For semaphore as notification (init value=0, different tasks do wait/post), disable priority protocol: `sem_setprotocol(&sem, SEM_PRIO_NONE)`

## RPMsg Cross-Core Driver Rules

When implementing RPMsg-based cross-core drivers (`*_rpmsg.c` / `*_rpmsg_server.c`):

- **Message structs must use fixed-width types only**: `uint8_t`, `int16_t`, `uint32_t`, `int64_t`, etc. Never use `size_t`, `ssize_t`, `time_t`, `void *`, `uintptr_t` (size varies across 32-bit and 64-bit cores).
- **Message structs must be packed**: Use `begin_packed_struct` / `end_packed_struct` macros to eliminate alignment differences between compilers.
- **Cross-bitwidth pointer fields**: Use `uint64_t` for cookie/context fields that may hold pointers (64-bit core ↔ 32-bit core).
- **RX callback must be short**: All endpoints on the same remote core share one RX thread. Blocking in callback stalls all message processing. Offload heavy work to `work_queue`.

```c
begin_packed_struct struct mydevice_rpmsg_header_s
{
  uint32_t command;
  int32_t  result;
  uint64_t cookie;   /* Client context — uint64_t for cross-bitwidth */
} end_packed_struct;
```

## Error Cleanup Pattern

For simple registration (1-2 resources), inline cleanup is fine. For complex registration involving multiple resources (mutex, semaphore, sensor_register, work_queue), use goto-based cleanup:

```c
ret = mydevice_initialize(priv);
if (ret < 0)
  {
    goto err_init;
  }

ret = sensor_register(&priv->sensor_lower, devno);
if (ret < 0)
  {
    goto err_register;
  }

return OK;

err_register:
  mydevice_uninitialize(priv);  /* 清理 initialize 中分配的资源（中断、工作队列等） */
err_init:
  kmm_free(priv);
  return ret;
```

## NuttX Compilation Pitfalls

Common traps that cause hard-to-diagnose compile errors across all driver subsystems.

### `container_of` with struct type argument

NuttX's `container_of` macro may fail when the type argument is written as `struct xxx` (depends on NuttX version and GCC `typeof` behavior). When the lower-half structure is the **first member** of the private struct, use a direct cast instead:

```c
/* ✅ Reliable — works when lower is the first member */

FAR struct mydevice_dev_s *priv = (FAR struct mydevice_dev_s *)lower;

/* ⚠️ May fail on some NuttX versions */

FAR struct mydevice_dev_s *priv = container_of(lower, struct mydevice_dev_s, lower);
```

**For new drivers**: Place the framework lower-half struct (`touch_lowerhalf_s`, `sensor_lowerhalf_s`, etc.) as the **first member** of the private struct, then use direct cast. This avoids the `container_of` portability issue entirely.

**For existing drivers**: If `container_of` compiles and runs correctly, do NOT refactor the struct layout. Reordering struct members changes the ABI and requires full regression testing. Only fix if there is an actual compile error caused by `container_of`.

### Preprocessor macro name collisions

NuttX headers define macros that silently replace identifiers in your code. If a struct field name matches a kernel macro, the preprocessor expands it before the compiler sees it, causing cryptic "no such member" errors.

Known collisions:

| Macro | Defined in | Expands to |
|-------|-----------|------------|
| `pm_register` | `<nuttx/power/pm.h>` | `pm_domain_register` |
| `pm_unregister` | `<nuttx/power/pm.h>` | `pm_domain_unregister` |
| `close` | `<unistd.h>` | `nx_close` (in kernel build) |
| `open` | `<fcntl.h>` | `nx_open` (in kernel build) |
| `read` / `write` | `<unistd.h>` | `nx_read` / `nx_write` (in kernel build) |

Prevention:
- **Never use bare kernel API names as struct field names**. Add a prefix or remove underscores: `pmregister` instead of `pm_register`.
- When you get "no such member" errors on a field that clearly exists, check if the field name is a NuttX macro by running: `grep -r '#define <field_name>' nuttx/include/`

### ISR callback signature

All hardware interrupt handlers must use the standard `xcpt_t` signature:

```c
/* ✅ Standard NuttX ISR signature */

static int mydevice_isr(int irq, FAR void *context, FAR void *arg);
```

Do **not** use `ioe_callback_t` (IOExpander) or other framework-specific callback types for hardware GPIO interrupts — those are for IOExpander subsystem internal use only.
