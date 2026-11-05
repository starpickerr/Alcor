# Stella 当前产品开发入口

最终交付目标是：用户下载官方 **DSH 桌面客户端**，通过原生插件管理器安装 Stella，在同一个客户端进入现有 Stella 模式。网页仅用于开发预览；需要额外客户端补丁的结果不等同于“原版 DSH 只装插件即可完成”。

默认开发目标是重构后的 **DSH 原生 Stella**，本目录已从原临时工作树迁入新版源码。先遵循工作区 [AGENTS.md](../AGENTS.md) 及其权威规则。

- 产品：根 `package.json`、`packages/`、`product/`、`scripts/product/`。
- Apple：`packages/stella-apple-sync/`（原生 `@stella/apple-sync`）；事务与日程 UI：`packages/stella-secretary/src/`；共享业务：`packages/stella-organizer/`。
- 学校／邮件连接：`packages/stella-elearning/`、`packages/stella-mail/`；各自封装独立 MCP 与 DSH 凭据，不将管理接口开放给模型。构建 `npm run product:sources:pack`，只读协议与来源版本见 `product/mcp-connections.md` 及 `product/source-releases.lock.json`。新增 Remote 必须通过真实 DSH Gateway 挂载检查，避免方法名与宿主保留接口重名。
- 依赖安装 `make setup`，默认开发 `npm run dev` / `make dev`，默认检查 `make check`，默认测试 `npm test` / `make test`。Apple 构建 `npm run product:apple:pack`；安装、版本及限制见 [product/README.md](product/README.md)。
- `desktop/`、`agent/`、`application/`、`channel/`、`core/` 与外仓旧 `Stella-Plugins/plugins/` 属于保留的旧运行路径；仅在用户明确要求旧版维护或读取迁移来源时使用。不要把旧版同名组件当作新版入口。
- 修改前核对 Git 分支、原生包清单、实际 UI 注册和已安装包路径。若目标文件仅被旧版引用，重新定位新版 owner；不要通过旧 Python / React 测试证明新版完成。
- 客户端构建会拒绝从旧 `desktop/`、`agent/`、`application/`、`channel/`、`core/` 引入代码；需要复用时将所需实现迁入原生包。新版 CI 是 `.github/workflows/native-product.yml`；保留的旧版 CI 不能代替它。旧环境安装／检查显式使用 `make setup-legacy` / `make check-legacy`；其余旧维护目标不作为新版验收。
- 最终验收必须从未修改的官方桌面发布安装包开始：原生安装 Stella → 进入完整模式 → 重启恢复 → 停用／卸载恢复原生 DSH，并保留宿主既有配置和数据。网页、从源码启动的 Electron 和兼容补丁各自单独记录，不能相互替代。
- DSH 更新兼容是交付底线：插件不得安装或覆盖宿主的 `@deepseek-ai/*` 运行组件；SDK 使用宿主提供的可选 peer。扩展支持版本前，验证官方版本身份、旧 Profile 升级、配置与业务数据保留及插件不兼容时原生 DSH 仍可启动。不能用放宽 `engines.dsh` 或关闭兼容检查代替验收；入口为 `product/tests/host-compatibility.test.mjs` 与 `product/tests/upgrade-compatibility-check.py`。
- 验收区分源码、打包产物、已安装 DSH Profile 与真实 Apple 账号。使用隔离 Profile 和合成 EventKit；默认不运行个人账号同步。

原 `/private/tmp/stella-dsh-workspace/Stella-Core` 保留作迁移前副本，后续产品修改使用当前目录，避免两处并行开发。迁移并不表示全部旧业务数据已导入或旧运行时可以删除。

设计变更先评估用户任务、页面必要性、能力归属及兼容影响，再实施已授权方案；不能仅按界面要求直接堆功能。

UI 文案保持精简：页面标题下不加介绍性副标题，删除重复的操作说明；权限／数据发送告知、破坏性操作确认、错误与必要状态保留在对应操作处。

Stella 业务能力以对话工具为主：用户通过自然语言指令调用账单等工具完成操作，页面用于查看和管理结果。不得把已授权的对话操作强制转为页面确认流程；保留宿主现有权限与必要确认。
