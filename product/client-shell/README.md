# Alcor 单窗口客户端

客户端外壳提供顶部 Alcor / DeepSeek Harness 切换。Alcor 作为可选功能插件，在 DSH 原生插件页安装、启用、关闭和卸载；进入完整界面仍使用顶部切换。管理包为 `@alcor/extension`，业务原生包仅加载在 Alcor Host；生态插件仍使用各自环境的原生安装与生命周期。

一个 BaseWindow 持有顶部和两套内容 WebContentsView；原生视图保留窗口归属，仅当前内容可见，切换保留另一套页面和后台。关闭插件时才移除视图并停止 Host。两个内容分别使用持久 Session、HOME、DSH_HOME、Profile、插件和数据根。每个 Session 的 `dsh-app://app` 请求固定转发给其所属 Host，路由不读取当前模式，避免切换时把旧请求送到另一边。认证 cookie 留在主进程，不暴露给渲染页面。

DSH Profile 的原生 bundle 选择是启停状态的唯一来源。客户端观察该文件，在未启用 Host HMR 时也执行 Alcor 的启停；不新增模型可调用的管理 Remote。关闭会切回 DSH、关闭 Alcor 视图和 Host，并结束其所属子进程，保留插件数据与业务库。切换界面不执行关闭。卸载后，安装记录阻止下次启动自动重装；再次安装由原生插件管理器负责，独立环境的保留数据可以继续使用。卸载管理包遵循 DSH 原有的停用／重启要求。

环境标注由客户端 preload 内置提供，实际边界由独立 Host 与 Session 承担。进入新版客户端时移除两边的独立 `@alcor/plugin-info` 管理包，旧共用 Profile 的兼容源码保留，不要求第三方声明专属范围。

Host 由已验证的官方 Desktop 包内原生入口启动，通过 IPC 管理正常退出。官方归档和签名保持原样。客户端本身是 Alcor 本地构建，使用自己的应用标识和本地签名，不冒充官方发行版。目前仅验证 macOS ARM64 / DSH 0.2.2-rc.1，公共分发和自动升级尚未建立。

构建：`npm run product:client:pack -- --config <本机配置.json> --out <新目录/Alcor.app>`。本机配置包含 `officialApp`、`environmentRoot`、`workbenchDsh`、`workbenchHome`、`workbenchWorkspace`、`workbenchBrowser` 和 `clientData` 的绝对路径，不包含凭据。已有陌生客户端状态目录会被拒绝。

单窗口验收为 `product/tests/client-shell-desktop-check.py`，只接受显式标记的临时环境；检查切换、草稿、业务 API 分隔、原样插件、启停、卸载／重装、独立后台停止、关闭后重启和业务数据恢复。实际原生菜单与窗口显示另用桌面检查确认。旧双窗口验收不能替代本客户端的证据。

内容沿用官方前端能力和原生目录／文件路径桥。官方专有的账户原生窗口、内置浏览器桥、快捷键编辑、自动更新与拖出文件等桌面桥尚未全部接入，不宣称与官方桌面外壳功能等价。模型、来源、业务及插件后台继续由各自原生 Host 负责。

Windows 接续开发的入口、约束和待办见 [Windows 开发交接](WINDOWS-HANDOFF.md)。当前源码已保存 macOS 实现，尚未完成 Windows 适配。
