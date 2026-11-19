# Alcor 当前产品开发入口

本目录是 **Alcor** 产品仓。原生包源码位于 `packages/alcor-*`，验证过的 DSH 客户端见[版本清单](product/desktop-release.lock.json)。现有 Preset、工具 ID、数据目录及凭据键属于运行兼容契约。

Alcor 通过官方 DSH Desktop 的原生插件机制安装，使用 `main`、`sidebar` 和 `shell.overlay` 公开插槽提供同窗顶部切换。Alcor 和 DSH 共用 Host、Profile 与配置。Alcor 自动化沿用官方调度服务，并提供插件自己的管理界面。

默认开发目标是重构后的 **DSH 原生 Alcor**，本目录已从原临时工作树迁入新版源码。先遵循工作区 [AGENTS.md](../AGENTS.md) 及其权威规则。

开始新开发任务前，依[工作区 Git 规范](../.agents/rules/git.md#分支与历史)刷新远端分支信息，核对其他开发任务的本地和远端分支是否已合入 `develop`。发现未合并分支时先请用户决定先合并还是直接开发。检查完成后，可从当前本地 `develop` 创建新分支，也可将已合入 `develop` 的开发分支快进到当前基线后复用。

- 产品：根 `package.json`、`packages/`、`product/`、`scripts/product/`。
- Apple：`../dsh-apple-sync/`（原生 `@alcor/apple-sync`）；事务与日程 UI：`packages/alcor-secretary/src/`；共享业务：`packages/alcor-organizer/`。
- 学校与邮件插件源码分别位于 `../dsh-fudan-elearning/plugin/` 和 `../dsh-email/plugin/`。账号管理由各包的本机设置提供，模型工具沿用 DSH 权限。可分别运行 `npm pack`，或从本仓运行 `npm run product:sources:pack`。协议与版本见 `product/mcp-connections.md` 和 `product/source-releases.lock.json`。新增 Remote 必须通过真实 DSH Gateway 挂载检查，避免与宿主保留接口重名。
- 依赖安装 `make setup`，默认开发 `npm run dev` / `make dev`，默认检查 `make check`，默认测试 `npm test` / `make test`。Apple 构建 `npm run product:apple:pack`；安装、版本及限制见 [product/README.md](product/README.md)。
- `desktop/`、`agent/`、`application/`、`channel/`、`core/` 属于保留的旧运行路径；仅在用户明确要求旧版维护或读取迁移来源时使用。不要把旧版同名组件当作新版入口。
- 修改前核对 Git 分支、原生包清单、实际 UI 注册和已安装包路径。若目标文件仅被旧版引用，重新定位新版 owner；不要通过旧 Python / React 测试证明新版完成。
- 客户端构建会拒绝从旧 `desktop/`、`agent/`、`application/`、`channel/`、`core/` 引入代码；需要复用时将所需实现迁入原生包。新版 CI 是 `.github/workflows/native-product.yml`；保留的旧版 CI 不能代替它。旧环境安装／检查显式使用 `make setup-legacy` / `make check-legacy`；其余旧维护目标不作为新版验收。
- 在已验证的官方 DSH Desktop 安装本地打包的原生插件，检查同窗顶部切换、重启恢复、插件作用域及数据保留，按实际验收环境记录结果。
- 沿用官方客户端、运行组件和更新器。宿主接口变更时适配插件，并核验配置、数据及插件生命周期。
- DSH 更新兼容是交付底线：插件不得安装或覆盖宿主的 `@deepseek-ai/*` 运行组件；SDK 使用宿主提供的可选 peer。扩展支持版本前，验证官方版本身份、旧 Profile 升级、配置与业务数据保留及插件不兼容时原生 DSH 仍可启动。不能用放宽 `engines.dsh` 或关闭兼容检查代替验收；入口为 `product/tests/host-compatibility.test.mjs` 与 `product/tests/upgrade-compatibility-check.py`。
- 验收区分源码、打包产物、已安装 DSH Profile 与真实 Apple 账号。使用隔离 Profile 和合成 EventKit；默认不运行个人账号同步。

按[工作区清理工作流](../.agents/workflows/cleanup.md)核对安装依赖并清理中间产物。

设计变更先评估用户任务、页面必要性、能力归属及兼容影响，再实施已授权方案；不能仅按界面要求直接堆功能。

UI 文案保持精简：页面标题下不加介绍性副标题，删除重复的操作说明；权限／数据发送告知、破坏性操作确认、错误与必要状态保留在对应操作处。

Alcor 业务能力以对话工具为主：用户通过自然语言指令调用账单等工具完成操作，页面用于查看和管理结果。不得把已授权的对话操作强制转为页面确认流程；保留宿主现有权限与必要确认。
