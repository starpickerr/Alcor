# Windows 开发交接

本分支 `feat/isolated-dsh-runtimes-20261111` 从 `develop` 的 `8a15a84c` 建立，保存已完成本地安装验收的 macOS 单窗口客户端。尚未实现 Windows 启动、打包或实机验收，不应把现有 Mac 安装包复制到 Windows 运行。

## 已确认的产品约束

- 顶部切换 Alcor／DSH，在同一个原生窗口直接切换，保留另一边的页面和草稿。
- Alcor 是产品模式，不注册为 DSH 的侧栏插件。两个独立原生 Host、Profile、插件安装与数据目录，浏览器 Session 和请求转发也固定分开。
- 第三方插件沿用标准 DSH 接口，无需增加 Alcor／DSH 专属字段。由客户端决定安装和运行在哪个环境。
- AuraBot 和桌宠归 Alcor；切换到 DSH 不改变 QQ 的归属，也不把 Alcor 的事务、简报、账单或工具交给工作台。
- 原官方 DSH 安装保持原样，校验其版本、归档与签名后使用其 Host。自有客户端独立命名、签名，不冒充官方发行版。
- 环境隔离约束正常插件的作用域，不是阻止恶意本机插件访问所有文件的 OS 沙箱。

## 当前入口

| 文件 | 职责／Windows 适配点 |
| --- | --- |
| `product/client-shell/main.mjs` | 单窗口、顶部切换、独立浏览器 Session、生命周期和目录选择；检查 Windows 标题栏及窗口按钮布局 |
| `product/client-shell/host.mjs` | 官方 Host 的 IPC 启停；目前使用 Mac 的 Resources 布局，需核对 Windows 官方包和进程退出行为 |
| `product/client-shell/transport.mjs` | 每个视图固定路由到所属 Host，认证 cookie 留在主进程；保持跨环境拒绝行为 |
| `scripts/product/environments.mjs` | 独立数据目录、环境变量、插件 CLI、运行实例锁；当前官方包校验仅接受 macOS，进程识别也使用 `ps` |
| `scripts/product/build-client-shell.mjs` | 当前仅实现 Mac `.app` 打包及本地签名；新增 Windows 打包，不放宽 Mac 校验来绕过平台差异 |
| `product/desktop-release.lock.json` | 已验证官方运行时版本与摘要；需补充实际检查过的 Windows 版本和架构 |
| `scripts/product/native-ui-layout.mjs` | 维护第一方原生 UI 顶部空槽适配，与生成物及来源摘要配套 |

`packages/alcor-secretary` 等包仍是内部能力模块。`packages/alcor-launcher`、`product:launcher:pack` 和双窗口验收保留作早期方案回归，不能用作当前产品入口。当前打包入口是 `product:client:pack`，不是 `product:desktop` 或旧共用 Profile 预览。

## Windows 实施顺序

1. 核对 Windows 版本、CPU 架构和官方 DSH 安装包。检查真实安装路径、二进制身份、Host／CLI 入口和运行时目录，建立 Windows 校验记录。
2. 适配程序定位、环境变量、路径、插件 CLI 和进程管理；保持每个环境的独立实例锁和数据保护，退出时不得遗留重复 QQ 回复进程。
3. 构建自有 Windows 客户端，调整窗口顶栏布局。Apple 同步当前为 macOS 专用，不能在 Windows 默认安装；邮件与学校连接的封装产物也需核对目标平台。
4. 在全新、明确标记可丢弃的测试目录验收，再处理真实用户升级。不要复制本机 Mac Profile、浏览器状态、模型凭据或 QQ 数据作默认启动配置。
5. 验证安装、顶部双向切换、两边插件清单、跨边业务 API 拒绝、草稿、独立存储、正常退出及重启恢复，再验证 AuraBot 和桌宠。真实消息发送使用明确授权的测试账号和对象。

代码要求 Node >=24.9。当前自动化桌面脚本也含 Mac 路径和信号操作，需要随 Windows 入口适配；不承诺直接在 Windows 执行现有 Mac 构建和桌面脚本即可通过。

## 已有证据与边界

2026-11-11：Mac 产品测试 513 项通过；后续原生布局和模式聚焦检查 31 项通过；最终 Host URL／传输边界 2 项通过。`product/tests/client-shell-desktop-check.py` 的最终打包验收通过，覆盖原样生态测试插件、单窗口切换、独立接口与存储、草稿及任务／模式重启恢复。个人 Mac 的原生菜单切换、重启及 AuraBot／桌宠在线状态已另行确认；未发送真实 QQ 测试消息。

桌面合成验收只接受带 `.client-shell-test` 所有权标记的测试目录，配置中的所有测试数据路径必须位于该目录内；不能指向个人数据根。其测试插件准备工具复用 `product/tests/isolated-environments-check.py`，无需启用旧 launcher。

官方原生账户窗口、内置浏览器桥、快捷键编辑、自动更新和拖出文件等专用桌面能力尚未全部接入，见本目录 README。公共发行和自动升级尚未建立。Windows 适配后还需在 Mac 上检查共享代码是否回归。

本提交只交接源码、必要的受维护资源和测试，不包含个人配置、凭据、业务数据库、冷备份或本地安装包。个人数据迁移与源码同步是两件独立的工作。
