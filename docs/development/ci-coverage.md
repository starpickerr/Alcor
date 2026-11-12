# CI 与当前产品架构

当前交付是安装在官方 DSH 内的 `@alcor/*` 插件。入口与平台范围见 [产品说明](../../product/README.md)，实际调用链是插件 Host 服务、原生凭据、插件业务 Worker 与 MCP Remote。官方 DSH 保持原程序和更新机制。

`make audit` 与 `make check` 现在都完整构建当前客户端并执行 `product/tests/*.test.mjs`，不按变更文件缩减产品测试。GitHub 的 `Native Alcor product` 执行 macOS 完整产品测试、秘书与 Apple 打包、独立临时 Keychain 实测，以及 Windows/Linux 原生插件检查。连接器任务按 `product/source-releases.lock.json` 检出当前源码、核对现有摘要、冻结打包并实际检查协议、工具注解、技能参数、版本及秘密不落盘。

## 自动执行条件

| 范围 | 自动检查 | 手动入口 |
| --- | --- | --- |
| `packages/`、`product/`、`scripts/product/`、根 npm 清单、Makefile、工作流 | 对 main/develop 的 PR，以及 main/develop 推送执行 Native；包含整个产品测试集，不只新增用例 | Native workflow dispatch；`make audit` |
| 保留的 `product/client-shell/`、`@alcor/extension`、隔离环境管理脚本 | 仍在 Native 完整产品测试集内；保留生命周期、凭据传输、进程归属及数据保护断言 | Native workflow dispatch；原隔离方案另保留在 `archive/isolated-runtimes-20261112-02217cd4` |
| `agent/`、`core/`、`application/`、`desktop/` 等旧运行时代码、旧 tests/evals、旧依赖与检查脚本、Legacy 来源锁 | 同样的 PR/推送事件执行 Legacy maintenance；macOS 运行适用增量检查，依赖等公共变更仍选择完整旧 Python 套件；Linux 另测实际进程、授权和 HTTP/SSRF 边界 | Legacy workflow dispatch 执行 `make audit-legacy` 完整旧审计，包含全部 18 个旧 UI 脚本；旧 Agent evals 保留 |
| 旧 Windows Bash streaming | 原工作流继续在实现、对应测试或工作流变化的 PR 上执行 | Windows Bash workflow dispatch |

Legacy 全量与真实 Keychain 集成归 macOS；不能让 Ubuntu 调用 macOS Security.framework，也不能用虚假凭据后端制造成功。Linux 实际隔离和网络安全检查仍单独执行。两类来源锁独立：Legacy 的历史 MCP 提交不替代当前插件连接器发行锁。

学校来源仓库由用户改为公开，并已通过 GitHub 核验；两个来源任务均按固定提交读取公开仓库，不需要额外 Secret，也不持久化检出凭据。来源摘要和篡改拒绝校验继续执行。

## 原假设与当前覆盖

本次没有删除测试或增加 skip。已移除入口的 UI 预期按实际路由适配，安全语义移到真实存储验证；其余变化是检查的所属范围、平台与缺失的当前产品覆盖。维护旧代码仍触发相应检查。

| 原测试或失败组的假设 | 当前架构证据 | 保留或替代覆盖 |
| --- | --- | --- |
| 所有产品变更都应经过自研 Python Agent/Core 与旧 Electron 页面 | `package.json` 的默认测试指向 `product/tests/`；`build-client.mjs` 拒绝旧运行时输入；新增 `native-runtime-boundary.test.mjs` 实际打包所有现行 Host 入口并检查插件 Python 导入 | Native 完整测试与打包；旧全量套件仍用于旧运行时维护 |
| AgentHub 通过旧 Core execution lease 和 `Stella-Plugins` Worker 运行 | 当前 owner 为 `packages/alcor-agent-hub/`，官方 DSH 负责开发会话；共享入口不再依赖隔离导航 | `agent-hub.test.mjs`、`hub-independent.test.mjs`、`business-execution.test.mjs`；旧 gateway/reliability 测试与固定外仓保留在 Legacy |
| 任务、账单、记忆和知识都来自兄弟插件仓 | 现行包承接自己的业务 Worker；`packages/alcor-secretary/business/` 与 `alcor-organizer` 维护事务和数据契约 | business/domain/execution、bill/task/tree、恢复及 receipt/CAS 用例；旧外仓的八个原始摘要继续校验 |
| eLearning/邮件通过旧 Core 私有账户和固定旧 MCP 摘要交付 | 当前 `alcor-elearning`、`alcor-mail` 使用 DSH 凭据与 Remote，当前发行锁有独立版本、提交和摘要 | mail/elearning-credentials、source-remotes、briefing-source-selection；新增真实冻结协议任务；Legacy 来源篡改拒绝继续执行 |
| Ubuntu 应成功保存真实 macOS Keychain 凭据 | 当前可选 `@alcor/privacy` 明确仅支持 macOS；旧 MacOSKeychain 同样依赖 Security.framework | macOS 真实临时 Keychain 的轮换、重启、锁定、无文件/环境回退测试；Linux 保留实际 HTTP/SSRF 和进程安全检查 |
| 原生 Keychain 测试可在没有 Profile 服务的裸 Context 启动 | 更名后的 provider 在访问凭据前必须确认 Profile，拒绝未知身份或旧 provider 混用 | 主夹具和跨进程夹具声明当前 `@alcor/privacy`；生产拒绝保护不变。8 项真实 Keychain、4 项包内身份用例及 AgentHub 的 2 项身份迁移用例均保留实测，并纳入 native CI |
| 旧 Core RunPolicy/OS 隔离直接代表官方 DSH 插件权限 | 官方 Host 是同进程可信插件模型；旧 Core 的隔离不会自动保护新 Host | 当前 real tool gate、actor/capability classification、organizer 排队后撤销、owner peer、陈旧快照和不可逆写入验证；旧 Core 边界仍在 Legacy/Linux 检查，不声称两者等价 |
| 旧桌面路由和 18 项浏览器脚本可证明共享界面交付 | 当前顶层切换与原生服务由 secretary/plugin-info 及 DSH 插槽实现 | mode/sidebar/automation 等现行产品测试与官方客户端验收入口；18 个旧脚本全部保留，独立记录开始、结束、耗时和超时 |
| 能力目录仍展示旧连接访问设置，并调用 `/api/capabilities/access` | 当前 `SkillsPage`、API 类型与桌面路由已无该入口；旧 Core 限制事实仍保留 | UI 验证旧入口缺席且调用旧路由会失败；`test_feature_access_settings.py` 在真实存储核对初始拒绝、保存后的只读范围、版本递增及不改写为 `mcp:browser`，保留权限与主体边界 |
| 当前共享模式允许移除保留的独立环境测试 | 隔离实现仍有实际代码、已有环境和归档分支 | environments、extension-lifecycle、client-shell transport、child-processes 等仍在完整产品套件内 |

## 证据边界

CI 合成账号、临时数据和原生 Keychain 不证明个人 Apple、学校或 IMAP 账号可用。连接器协议任务不连接真实学校或邮箱。源码和打包检查不替代签名官方 Desktop 的 GUI、升级与个人数据迁移验收；没有改变相关宿主支持范围或官方程序。

完整运行结果与仍失败项记录在本任务交付记录中；路径分流或门禁名称本身不作为通过证据。

原生 Swift 构建和合约探针统一在临时目录内编译完整源码快照，避免同步目录在编译期间改变输入元数据；原有 Apple 行为断言保留。只修复编译输入，不请求个人 Apple 授权。

GNU Make 的完整审计/无 Conda 验证归入已有解释器工具检查，在 macOS 和 Linux 维护任务执行；不要求 Windows 插件用户额外安装 Unix Make。插件 Host/Python 运行时边界用例则在三个系统执行。连接器冻结只收集真实 lowlevel/stdio 服务器使用的 MCP 模块，不引入未使用开发 CLI 的可选依赖；所有来源和协议断言保留。

Legacy 扩展只读加入 `/usr/share/zoneinfo` 的系统实际解析路径。本机为 `/private/var/db/timezone/tz/2026f.1.0/zoneinfo`；不授权其父目录，不更改写入、网络或个人数据保护。GitHub macOS 的源代码解释器曾因原路径指向读取范围之外而集中失败。公共依赖准备复用生产下载器、固定 uv 与两个已核验来源的带摘要锁文件，先在临时状态准备，再供隔离测试重新校验复制；缺失缓存不再让 UI 测试提前退出。

## 本次验证记录（2026-11-12）

- 修复前手动旧审计：140 FAIL / 33 ERROR；不是现行插件运行结果。
- 本机旧套件诊断在原 900 秒预算外中断，保留 2464 PASS / 4 FAIL / 47 SKIP 的部分结果。两项缺本机旧桌面 TypeScript/Vite，另两项旧 MCP adoption 初始化被拒；不标完整通过。GitHub 维护任务按完整依赖与对应平台验证。
- 原生完整审计曾复现两项 Swift 输入变化失败；快照修复后 `make audit` 为 537 PASS / 0 FAIL / 1 既有 Windows 专属 SKIP，总计 538。Apple 原生打包通过，相关定向合约 17 项及 Worker/watcher 3 项通过。
- 真实 Keychain 原测试夹具缺少新版 Profile 服务，8 项在访问凭据前失败；夹具适配后 8 项、真实锁定/恢复，以及包内身份 4 项、AgentHub 身份迁移 2 项通过。生产安全检查没有放宽。
- 两个当前源码锁对应的冻结包构建和实际协议断言通过；测试含工具集合、写入注解、技能参数、禁止凭据写入和秘密不落盘。原始日志保留 SDK 退出时的 stderr，不将协议检查当作真实账号验收。
- GitHub `977e8975`：Native 的 macOS、Windows、Linux 三任务通过；冻结来源任务因误收集未使用 MCP CLI 缺少可选 typer 而失败。Legacy Linux 通过；macOS 为 3011 PASS / 143 FAIL / 53 既有 SKIP，另有两个 UI 失败，分别为已移除入口和缺少公共依赖缓存。Windows Bash 通过。失败原始日志保留，不将该提交标绿。
- 2026-11-13 定向回归：真实时区读取及扩展隔离 27 项通过；旧连接存储、解释器与 UI 进程管理 26 项通过；公共校验缓存准备入口通过；冻结模块收敛后两个当前包的完整协议断言通过。当前完整 `make audit` 为 536 PASS / 0 FAIL / 1 既有 Windows 专属 SKIP，总计 537；原 Make 断言迁入工具测试，不是删除覆盖。
- GitHub `821c66ab`：三平台产品任务再次通过，连接器构建通过；协议启动发现切换临时工作目录后的相对路径问题，已改为启动前解析绝对包路径，并以 CI 同样的相对目录参数完成本地全套协议复测。Legacy 的依赖准备与新增 Linux 时区/私有文件拒绝检查通过，macOS 完整旧审计仍在运行。
- GitHub 修复后最终提交的完整检查待运行；完成前不宣称 CI 全绿。此次未合并 main/develop，也未修改官方 DSH 或个人 Profile。
