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

## 原假设与当前覆盖

本次没有删除测试、降低断言或增加 skip；变化是检查的所属范围、平台与缺失的当前产品覆盖。旧测试保留原语义，维护对应代码仍触发它们。

| 原测试或失败组的假设 | 当前架构证据 | 保留或替代覆盖 |
| --- | --- | --- |
| 所有产品变更都应经过自研 Python Agent/Core 与旧 Electron 页面 | `package.json` 的默认测试指向 `product/tests/`；`build-client.mjs` 拒绝旧运行时输入；新增 `native-runtime-boundary.test.mjs` 实际打包所有现行 Host 入口并检查插件 Python 导入 | Native 完整测试与打包；旧全量套件仍用于旧运行时维护 |
| AgentHub 通过旧 Core execution lease 和 `Stella-Plugins` Worker 运行 | 当前 owner 为 `packages/alcor-agent-hub/`，官方 DSH 负责开发会话；共享入口不再依赖隔离导航 | `agent-hub.test.mjs`、`hub-independent.test.mjs`、`business-execution.test.mjs`；旧 gateway/reliability 测试与固定外仓保留在 Legacy |
| 任务、账单、记忆和知识都来自兄弟插件仓 | 现行包承接自己的业务 Worker；`packages/alcor-secretary/business/` 与 `alcor-organizer` 维护事务和数据契约 | business/domain/execution、bill/task/tree、恢复及 receipt/CAS 用例；旧外仓的八个原始摘要继续校验 |
| eLearning/邮件通过旧 Core 私有账户和固定旧 MCP 摘要交付 | 当前 `alcor-elearning`、`alcor-mail` 使用 DSH 凭据与 Remote，当前发行锁有独立版本、提交和摘要 | mail/elearning-credentials、source-remotes、briefing-source-selection；新增真实冻结协议任务；Legacy 来源篡改拒绝继续执行 |
| Ubuntu 应成功保存真实 macOS Keychain 凭据 | 当前可选 `@alcor/privacy` 明确仅支持 macOS；旧 MacOSKeychain 同样依赖 Security.framework | macOS 真实临时 Keychain 的轮换、重启、锁定、无文件/环境回退测试；Linux 保留实际 HTTP/SSRF 和进程安全检查 |
| 旧 Core RunPolicy/OS 隔离直接代表官方 DSH 插件权限 | 官方 Host 是同进程可信插件模型；旧 Core 的隔离不会自动保护新 Host | 当前 real tool gate、actor/capability classification、organizer 排队后撤销、owner peer、陈旧快照和不可逆写入验证；旧 Core 边界仍在 Legacy/Linux 检查，不声称两者等价 |
| 旧桌面路由和 18 项浏览器脚本可证明共享界面交付 | 当前顶层切换与原生服务由 secretary/plugin-info 及 DSH 插槽实现 | mode/sidebar/automation 等现行产品测试与官方客户端验收入口；18 个旧脚本全部保留，独立记录开始、结束、耗时和超时 |
| 当前共享模式允许移除保留的独立环境测试 | 隔离实现仍有实际代码、已有环境和归档分支 | environments、extension-lifecycle、client-shell transport、child-processes 等仍在完整产品套件内 |

## 证据边界

CI 合成账号、临时数据和原生 Keychain 不证明个人 Apple、学校或 IMAP 账号可用。连接器协议任务不连接真实学校或邮箱。源码和打包检查不替代签名官方 Desktop 的 GUI、升级与个人数据迁移验收；没有改变相关宿主支持范围或官方程序。

完整运行结果与仍失败项记录在本任务交付记录中；路径分流或门禁名称本身不作为通过证据。
