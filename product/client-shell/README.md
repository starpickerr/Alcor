# Alcor 单窗口客户端

客户端外壳提供顶部 Alcor / DeepSeek Harness 切换。Alcor 是产品模式，不注册为工作台侧栏入口或启动器插件。现有业务原生包仅加载在 Alcor Host；生态插件仍使用各自环境的原生安装与生命周期。

一个 BaseWindow 持有顶部和两套内容 WebContentsView；切换仅挂载当前内容，保留另一套 WebContents 和后台。两个内容分别使用持久 Session、HOME、DSH_HOME、Profile、插件和数据根。每个 Session 的 `dsh-app://app` 请求固定转发给其所属 Host，路由不读取当前模式，避免切换时把旧请求送到另一边。认证 cookie 留在主进程，不暴露给渲染页面。

Host 由已验证的官方 Desktop 包内原生入口启动，通过 IPC 管理正常退出。官方归档和签名保持原样。客户端本身是 Alcor 本地构建，使用自己的应用标识和本地签名，不冒充官方发行版。目前仅验证 macOS ARM64 / DSH 0.2.2-rc.1，公共分发和自动升级尚未建立。

构建：`npm run product:client:pack -- --config <本机配置.json> --out <新目录/Alcor.app>`。本机配置包含 `officialApp`、`environmentRoot`、`workbenchDsh`、`workbenchHome`、`workbenchWorkspace`、`workbenchBrowser` 和 `clientData` 的绝对路径，不包含凭据。已有陌生客户端状态目录会被拒绝。

单窗口验收为 `product/tests/client-shell-desktop-check.py`，只接受显式标记的临时环境；检查切换、草稿、业务 API 分隔、原样插件、状态和任务重启恢复。实际原生菜单与窗口显示另用桌面检查确认。旧双窗口验收不能替代本客户端的证据。

内容沿用官方前端能力和原生目录／文件路径桥。官方专有的账户原生窗口、内置浏览器桥、快捷键编辑、自动更新与拖出文件等桌面桥尚未全部接入，不宣称与官方桌面外壳功能等价。模型、来源、业务及插件后台继续由各自原生 Host 负责。

Windows 接续开发的入口、约束和待办见 [Windows 开发交接](WINDOWS-HANDOFF.md)。当前源码已保存 macOS 实现，尚未完成 Windows 适配。
