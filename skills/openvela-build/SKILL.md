---
name: openvela-build
description: "openvela 固件编译、配置和模拟器运行。Use when: 编译 openvela、build、make、构建、menuconfig、运行模拟器、emulator、编译报错修复。"
---

# openvela Build

编译 openvela 固件、修改内核配置、运行模拟器。

## 默认目标

默认编译目标为 goldfish-arm64-v8a-ap（64 位 ARM 模拟器）：

```
CONFIG_PATH=vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap/
OUTPUT_DIR=cmake_out/vela_goldfish-arm64-v8a-ap/
```

如果用户指定了其他板级配置路径，替换上述路径即可。

## 命令

### 编译

```bash
./build.sh <CONFIG_PATH> --cmake -j$(nproc)
```

示例：
```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap/ --cmake -j$(nproc)
```

**成功标志**: 输出 `#### build completed successfully`

**编译产物**: `<OUTPUT_DIR>/nuttx`（ELF）、`<OUTPUT_DIR>/vela_ap.bin`

### 修改内核配置（menuconfig）

```bash
./build.sh <CONFIG_PATH> --cmake menuconfig
```

menuconfig 是交互式 TUI 界面，**不能在 AI 终端中直接运行**。提示用户在自己的终端中执行。

修改配置后保存会自动更新 `.config`。如需持久化到 defconfig：
```bash
./build.sh <CONFIG_PATH> --cmake savedefconfig
```

### 运行模拟器

```bash
./emulator.sh <OUTPUT_DIR>
```

示例：
```bash
./emulator.sh cmake_out/vela_goldfish-arm64-v8a-ap/
```

**成功标志**: 出现 `goldfish-armv8a-ap>` NSH 提示符

### 清理编译

```bash
rm -rf <OUTPUT_DIR>
```

全量清理后重新编译：
```bash
rm -rf cmake_out/vela_goldfish-arm64-v8a-ap/ && ./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap/ --cmake -j$(nproc)
```

## 编译流程

1. 用户指定或确认目标配置路径
2. 执行 `./build.sh <path> --cmake -j$(nproc)`
3. 检查输出最后几行是否包含 `build completed successfully`
4. 如果编译失败，分析错误日志并尝试修复（最多 3 轮）

## 编译失败处理

| 错误类型 | 特征 | 处理 |
|----------|------|------|
| 代码错误 | `error:` + 本驱动文件名 | 自动修复代码，重新编译 |
| 配置缺失 | `undefined reference` + CONFIG 相关 | 提示用户 menuconfig 开启 |
| 工具链问题 | `command not found` | 提示用户安装工具链 |
| 缓存问题 | 奇怪的链接错误 | `rm -rf <OUTPUT_DIR>` 后重编 |

## 启用新驱动的 CONFIG

如果需要临时启用某个 CONFIG 进行编译验证：

```bash
# 方法 1: 直接追加到 defconfig（推荐）
echo "CONFIG_SENSORS_BME680=y" >> <CONFIG_PATH>/defconfig

# 方法 2: 用 sed 修改 .config（临时，不持久化）
sed -i 's/# CONFIG_SENSORS_BME680 is not set/CONFIG_SENSORS_BME680=y/' <OUTPUT_DIR>/.config
```

修改后重新编译即可。

## 常用配置路径

| 目标 | 配置路径 |
|------|----------|
| goldfish-arm64 (推荐) | `vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap/` |
| goldfish-arm32 | `vendor/openvela/boards/vela/configs/goldfish-armeabi-v7a-ap/` |
| 自定义板级 | `vendor/<vendor>/boards/<chip>/<board>/configs/<config>/` |
