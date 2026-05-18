# NuttX Driver Review Checklist

6-dimension review. Mark each item PASS / WARN / FAIL.

## Dimension 1: Initialization

| Check Item | Level |
|------------|-------|
| Init in correct boot stage (`board_late_initialize` or later for I2C/SPI; `board_app_finalinitialize` for FS/KVDB deps) | FAIL |
| RPMsg drivers verify remote core readiness | WARN |
| No long delays in init path | WARN |
| No large local variables in init (use heap) | FAIL |
| Chip ID verified in `register()`, returns `-ENODEV` on mismatch | FAIL |
| Full resource cleanup on `register()` failure | FAIL |

## Dimension 2: Power Management

| Check Item | Level |
|------------|-------|
| `activate(false)` sets hardware to lowest power state | FAIL |
| `activate(false)` calls `work_cancel`/`work_cancel_sync` | FAIL |
| `activate(true)` restores normal operating mode | FAIL |
| No work_queue callback running after deactivate (`work_cancel_sync`) | FAIL |
| `close()` powers down hardware (chardev pattern) | WARN |
| Multiple power modes exposed via Kconfig/ioctl (if device supports) | INFO |

## Dimension 3: API Usage

| Check Item | Level |
|------------|-------|
| Kernel file API (`file_open` etc.), not user-space `open`/`read`/`write` | FAIL |
| `O_CLOEXEC` used when fd not needed by child tasks | WARN |
| `file_poll` + semaphore async pattern (not user-space `poll`) | WARN |
| `nxsem_wait_uninterruptible`, not `nxsem_wait` | FAIL |
| `up_udelay`/`up_mdelay`, not `usleep` | FAIL |
| Every I2C/SPI transfer return value checked | FAIL |
| `kmm_zalloc` preferred over `kmm_malloc` | WARN |
| No `kmm_malloc`/`kmm_free` in I/O path | FAIL |
| DMA buffers use `kmm_memalign` | WARN |

## Dimension 4: Critical Section & ISR

| Check Item | Level |
|------------|-------|
| ISR calls no blocking functions (`nxmutex_lock`, `sem_wait`, `kmm_malloc`, `I2C_TRANSFER`) | FAIL |
| ISR completes within 10us | WARN |
| `irq_attach()` before `up_enable_irq()` | FAIL |
| Critical section duration minimized, no blocking inside | WARN |
| Spinlock sections < 10us | WARN |
| Lock/unlock symmetry on all paths (including error paths) | FAIL |

## Dimension 5: Runtime Environment

| Check Item | Level |
|------------|-------|
| No global variables for device state | FAIL |
| No dedicated kthread for periodic work (exception: long blocking needed) | WARN |
| work_queue callback not too long (split if needed) | WARN |
| `struct work_s` embedded in private struct | WARN |
| Sensor data collection uses LPWORK (not HPWORK — bus I/O blocks) | FAIL |
| HPWORK callback < 1ms, no blocking, no bus I/O | WARN |
| No large local variables in driver functions | WARN |
| RPMsg messages use fixed-width types only (no `size_t`/`void *`) | FAIL |

## Dimension 6: Coding Style

| Check Item | Level |
|------------|-------|
| `nuttx/config.h` is first include | FAIL |
| Apache 2.0 / SPDX license header | FAIL |
| 2-space indent, no tabs | FAIL |
| Line width <= 78 chars | WARN |
| `snake_case` naming | FAIL |
| `_s`/`_e`/`_t` suffixes, `g_` prefix | WARN |
| `FAR` on pointer params | WARN |
| Header guard `__INCLUDE_PATH_H` | WARN |
| Private headers not in `include/nuttx/` | WARN |
| No unnecessary stack variable init | WARN |
| Blank line after variable declarations | WARN |
| Macro and function alignment | WARN |
| No magic numbers | WARN |
| No unused/debug code | WARN |
| Zero compiler warnings | FAIL |
| NuttX section order in file layout | WARN |
| NuttX function comment block format | WARN |

## Common Pitfalls

Beyond the 6-dimension checklist, watch for these frequently missed issues:

1. **Forgetting `work_cancel_sync` in deactivate** — Using `work_cancel` instead of `work_cancel_sync` can leave a worker callback still running after deactivate returns, causing use-after-free if the driver is unregistered immediately after.

2. **Mixing HPWORK and LPWORK incorrectly** — HPWORK is for short, non-blocking callbacks (< 1ms, no bus I/O). LPWORK allows blocking calls including I2C/SPI transfers. Sensor data reads involve bus operations that block, so they should use LPWORK. HPWORK is only for pure memory operations that never touch I2C/SPI.

3. **Stack overflow in deeply nested call chains** — NuttX tasks have small stacks (typically 2048-4096 bytes). Avoid large local arrays/structs in functions that may be called from deep call chains. Use heap allocation for buffers > 64 bytes.

4. **Missing error propagation in init chains** — When `register()` calls multiple init sub-functions, each failure must clean up all previously acquired resources (I2C bus, allocated memory, registered interrupts) in reverse order. A common bug is freeing memory but not detaching the interrupt handler.

## Report Template

```markdown
# Driver Review Report: <driver_name>

## Summary
- Pattern: uORB sensor / chardev / RPMsg
- Bus: I2C / SPI / both

## Results

| Dimension | PASS | WARN | FAIL |
|-----------|------|------|------|
| Initialization | X | X | X |
| Power Management | X | X | X |
| API Usage | X | X | X |
| Critical Section & ISR | X | X | X |
| Runtime Environment | X | X | X |
| Coding Style | X | X | X |

## FAIL Items (Must Fix)
1. [Dim] Item — line XXX. Fix: ...

## WARN Items (Recommended)
1. ...

## nxstyle Results
...
```

## Submission Readiness Checklist

Beyond code quality, verify before submission:
- commit message in English
- Matches design doc, no undefined code
- Kconfig/Make.defs/Makefile structure justified
- Complex logic commented, public functions documented
- Blocking/non-blocking and sync/async correctness
- Parameter sanity, buffer overflow, loop termination checks
- Tested on SIM or hardware, static analysis clean
- Resource symmetry: alloc/free, lock/unlock, irq, file open/close
