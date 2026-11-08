<p align="center"><img src="../../packages/alcor-secretary/src/assets/alcor-avatar.svg" alt="Alcor" width="112" /></p>

# Alcor

[English](../../README.md) · [中文](README.md) · [繁體中文](README-Hant.md) · [日本語](../ja/README.md)

Alcor 是运行在 DeepSeek Harness（DSH）中的私人助理。通过原生插件，你可以管理事务和日程、记录账单、生成简报和设置自动化。Alcor 和 DSH 共用同一个桌面窗口、模型、会话和工作区。

在 Alcor 页面上，将鼠标移到小熊上或点击它即可对话。小熊跟随当前工作区，切换页面可以继续聊。拖动小熊可以调整位置，在 **设置 → Alcor** 中可以调整悬停行为、显示小熊或恢复默认位置。

## 桌面安装

当前桌面目标为 Apple Silicon Mac。请使用[兼容版本清单](../../product/desktop-release.lock.json)中列出的官方 DSH 客户端。

1. 在已初始化的 DSH Profile 中打开插件管理器，从 `.tgz` 文件安装 `@alcor/secretary`。
2. 按需安装下表中的可选插件。
3. 退出并重新打开 DSH，在窗口顶部的模式菜单中选择 **Alcor**。

旧 Stella Profile 需要先完成迁移，再安装更名后的插件。安装和打包步骤见[原生产品指南](../../product/README.md)。

| 插件 | 功能 |
| --- | --- |
| `@alcor/secretary` | 管理事务和子事务、查看日程、记录账单、生成简报及安排定时更新。 |
| `@alcor/apple-sync` | 选择来源并授权后，与 Apple 日历和提醒事项同步日程和事务。 |
| `@alcor/elearning` | 连接复旦 eLearning 账号，读取课程、作业和公告。 |
| `@alcor/mail` | 连接 IMAP 邮箱，只读查询邮件并提供简报来源。 |
| `@alcor/agent-hub` | 在 DSH 工作台中跟踪开发任务和 Agent 执行记录。 |

## 本地开发

需要 Node.js 24.9 或更高版本、Python 3.9 或更高版本。macOS 辅助程序的构建还需要 Xcode Command Line Tools。在仓库根目录运行以下命令。

```sh
npm ci --ignore-scripts
npm run product:build
npm test
npm run dev
```

开发预览使用独立的 DSH Profile。运行 `npm run product:pack` 可在 `.local/artifacts/` 生成可安装的秘书插件包。构建学校和邮件插件还需要相邻的 MCP 仓库与固定版本的构建依赖，详见 [MCP 接入指南](../../product/mcp-connections.md)。

原生插件源码位于 `packages/`，构建脚本位于 `scripts/product/`，集成测试位于 `product/tests/`。早期独立 Python/Electron 应用的说明见[旧版指南](guide.md)。

## 项目来源与许可

Alcor 基于 zhayujie 及贡献者开发的 [CowAgent](https://github.com/zhayujie/CowAgent)（原 `chatgpt-on-wechat`）独立开发，复用上游的 Agent、模型、工具、技能、记忆和消息通道基础，并增加桌面工作流，以及事务、日程、账单、简报和安全管理等共享业务服务。

来源与版权说明见[第三方声明](../../THIRD_PARTY_NOTICES.md)，许可条款见 [MIT 许可证](../../LICENSE)。

## 作者的话

这是我第一次独立完成的开源项目，感谢 Codex 和 Cursor 在开发过程中提供的帮助。项目仍有需要完善的地方，欢迎通过[问题反馈](https://github.com/starpickerr/Alcor/issues)提交建议和缺陷报告。
