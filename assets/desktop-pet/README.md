# Alcor 小熊形象包

独立的 AuraBot pet 标准形象包，不是 Alcor 插件的运行依赖。形象沿用 Alcor 的大圆眼镜、圆耳朵和围巾，配色为奶油白身体、雾蓝围巾和金色星星。九组透明背景动画覆盖陪伴、说话、点击、思考、工作、结果、等待、成功和出错。

将 `alcor-bear-animation/`、`alcor-bear-config.json`、`alcor-bear-manifest.json` 放到 AuraBot 数据目录的 `modules-data/pet/packs/`，在上游桌宠中选择 `alcor-bear`。从「系统 → Aurabot」安装 pet 社区模块并启动桌宠；右键小熊选择「打开对话框」聊天。启用、关闭、聊天和物理效果均由上游模块管理。

源矢量为本仓库 `packages/alcor-secretary/src/assets/alcor-mascot.svg`；着色版本为本目录 `alcor-bear.svg`。没有复制上游默认鲸鱼素材。上游 pet 代码保持原样，来源为 `aureth-labs/dsh-aurabot-qq-mods`，模块版本 1.0.18，提交 `750f18a03b8904e9d836944ea0b6ad9419b0845c`，代码许可证 MIT。形象素材沿用本项目许可。

重建需要 Python（Pillow、Playwright Chromium）及支持 libvpx-vp9 的 FFmpeg：

```sh
python scripts/product/build-pet-pack.py --ffmpeg /path/to/ffmpeg
```

macOS 安装注意：上游 1.0.18 的 ZIP 直装器会跳过框架链接，可能在启动时缺少 Electron Framework。可校验官方 Electron 压缩包的 SHA-256，再用系统 `ditto -x -k` 解压到客户端 `node_modules/electron/dist/`，保留框架链接；无需改动模块或生成的客户端代码。

输出为 512×512、24 fps、2 秒循环的 VP9 WebM，含透明通道。生成器会检查透明度和动画变化。
