# Alcor 当前产品开发入口

项目与仓库现名为 **Alcor**。原生包源码位于 `packages/alcor-*`。本机旧 Profile 已在官方 DSH 0.2.2-rc.1 完成迁移，新旧包不得同时启用。现有 Preset、工具 ID、数据目录及凭据键是兼容契约，不能批量替换；已安装旧版本仍可能显示 Stella。当前目录是外层 Alcor 多仓工作区内的 Alcor 产品仓。

Current delivery (2026-11-12): install Alcor as a standard plugin inside the unchanged official DSH Desktop. Switch Alcor/DSH in one window using public main/sidebar/shell.overlay slots and a reserved top-left header matching the independent Alcor client, sharing Host, Profile, environment and configuration. Alcor automation opens the same native schedules page as DSH. Preserve the official signature and update path; verify compatibility per host version.

默认开发目标是重构后的 **DSH 原生 Alcor**，本目录已从原临时工作树迁入新版源码。先遵循工作区 [AGENTS.md](../AGENTS.md) 及其权威规则。

The isolated Host/Profile client remains an optional design, preserved on origin/archive/isolated-runtimes-20261112-02217cd4 at 02217cd4. Do not delete existing environments or migrate personal databases, credentials or external channels automatically.

product/client-shell and @alcor/extension remain compatibility code for the optional isolated design; the current shared implementation installs @alcor/secretary in the official Profile and does not replace official client UI modules.

- 产品：根 `package.json`、`packages/`、`product/`、`scripts/product/`。
- Apple：`packages/alcor-apple-sync/`（原生 `@alcor/apple-sync`）；事务与日程 UI：`packages/alcor-secretary/src/`；共享业务：`packages/alcor-organizer/`。
- 学校／邮件连接：`packages/alcor-elearning/`、`packages/alcor-mail/`；各自封装独立 MCP 与 DSH 凭据，不将管理接口开放给模型。构建 `npm run product:sources:pack`，只读协议与来源版本见 `product/mcp-connections.md` 及 `product/source-releases.lock.json`。新增 Remote 必须通过真实 DSH Gateway 挂载检查，避免方法名与宿主保留接口重名。
- 依赖安装 `make setup`，默认开发 `npm run dev` / `make dev`，默认检查 `make check`，默认测试 `npm test` / `make test`。Apple 构建 `npm run product:apple:pack`；安装、版本及限制见 [product/README.md](product/README.md)。
- `desktop/`、`agent/`、`application/`、`channel/`、`core/` 与外仓旧 `dsh-agent-hub/plugins/` 属于保留的旧运行路径；仅在用户明确要求旧版维护或读取迁移来源时使用。不要把旧版同名组件当作新版入口。
- 修改前核对 Git 分支、原生包清单、实际 UI 注册和已安装包路径。若目标文件仅被旧版引用，重新定位新版 owner；不要通过旧 Python / React 测试证明新版完成。
- 客户端构建会拒绝从旧 `desktop/`、`agent/`、`application/`、`channel/`、`core/` 引入代码；需要复用时将所需实现迁入原生包。新版 CI 是 `.github/workflows/native-product.yml`；保留的旧版 CI 不能代替它。旧环境安装／检查显式使用 `make setup-legacy` / `make check-legacy`；其余旧维护目标不作为新版验收。
- 最终验收以已校验的官方运行时、本地打包客户端和原生 Profile 为对象：同窗顶部切换、重启恢复、插件作用域及数据保留分别留证。网页、双窗口、合成账号和真实个人安装不能相互替代；官方专有桌面桥未适配时明确记录，不宣称等价支持。
- DSH 更新兼容是交付底线：插件不得安装或覆盖宿主的 `@deepseek-ai/*` 运行组件；SDK 使用宿主提供的可选 peer。扩展支持版本前，验证官方版本身份、旧 Profile 升级、配置与业务数据保留及插件不兼容时原生 DSH 仍可启动。不能用放宽 `engines.dsh` 或关闭兼容检查代替验收；入口为 `product/tests/host-compatibility.test.mjs` 与 `product/tests/upgrade-compatibility-check.py`。
- 验收区分源码、打包产物、已安装 DSH Profile 与真实 Apple 账号。使用隔离 Profile 和合成 EventKit；默认不运行个人账号同步。

迁移前临时工作树已清理，后续产品修改使用当前实际目录。中间产物和备份按工作区清理工作流核对依赖后处理。

设计变更先评估用户任务、页面必要性、能力归属及兼容影响，再实施已授权方案；不能仅按界面要求直接堆功能。

UI 文案保持精简：页面标题下不加介绍性副标题，删除重复的操作说明；权限／数据发送告知、破坏性操作确认、错误与必要状态保留在对应操作处。

Alcor 业务能力以对话工具为主：用户通过自然语言指令调用账单等工具完成操作，页面用于查看和管理结果。不得把已授权的对话操作强制转为页面确认流程；保留宿主现有权限与必要确认。
