# Runtime 安全与插件实例边界

2026-11-04，基于 Stella-Core `develop@a729286c969c5ad299abd256f85f6596f63043b1` 的源码对照与实现。统一执行与授权契约见[执行服务](execution-services.md)。参考目录只作研究，不参与构建或运行。

## 参考版本与来源

研究前对两仓执行 `git fetch origin --prune`，核对远端地址、分支、提交记录及 `HEAD...origin/<branch>`：左右差异均为 `0 0`，无须快进或覆盖文件。

| 官方仓库 | 分支与完整提交 | 当次工作区状态 |
| --- | --- | --- |
| [OpenAI Codex](https://github.com/openai/codex) | `main@ac558e2a3264e1025fd8025fdd71d03dfbe8c86e`，2026-11-03 16:09:51 UTC | 干净 |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | `master@8bd073392eef6f54e921a093b95076248eb08cbb`，2026-11-03 15:27:29 +08:00 | 保留未跟踪的 `.agents/skills/agent-experience/SKILL 2.md` |

以下源码路径相对于对应参考仓库，上述提交固定了研究对象，不能将以后的上游行为归于此版本。

## Codex 对照与安全适配

| 实际源码 | 核实的机制 | Stella 的整合决定 |
| --- | --- | --- |
| `codex-rs/core/src/tools/orchestrator.rs`：`ToolOrchestrator::run` | 先判定禁止、免审批或需审批，再选择执行沙箱；受限执行失败后的提权须重新决策，网络批准属于当前执行 | 保留 SecurityService → SecurityGateway / RunPolicy / Resource Grants → 执行器的入口；不创建第二份授权，不因 EPERM、网络或沙箱失败自动无沙箱重试 |
| `codex-rs/protocol/src/protocol.rs`：`AskForApproval` / `SandboxPolicy`；`codex-rs/core/src/sandboxing/mod.rs` | 审批策略、文件系统沙箱、网络策略分层，平台执行器落实权限 | 三档 RunPolicy 限制操作；资源访问和 OS 隔离独立复核。保留 Seatbelt / bubblewrap，隔离无法启动时拒绝执行；Full Access 仍保护凭据、托管数据及其他私有工作区 |
| `codex-rs/protocol/src/shell_environment.rs`：`populate_env`、Core 列表、`create_env_from_vars` | Core 白名单构造环境，大小写无关；Windows 缺 PATHEXT 时补默认值；启动身份变量不能经 override 恢复 | 将 Core 列表、筛选与 PATHEXT 行为适配到 `core/security/process_environment.py`，供 Shell 与 Agent Hub 委托进程共用。只有固定继承集合，没有任意宿主环境或用户 override 入口，启动身份变量自然不在集合内 |
| `codex-rs/core/src/exec_env.rs` | 构造环境与可信会话标识注入分开 | Bash 继续由 Core 注入工作区、运行及来源诊断标识；这些标识不授予权限。`.env` 声明的变量名及原凭据名称排除继续有效 |
| `codex-rs/sandboxing/src/seatbelt_network_policy.sbpl` | 网络规则由受控代理与平台需求生成，文件权限不等于网络通行 | 保留 Shell 所有模式禁网络、macOS 禁 mach-lookup 的边界；插件/受管 MCP 沿用 RPC 服务及网络授权。未移植 Codex 网络代理或新增直连旁路 |

上游默认行为需要区分：此版本 `ShellEnvironmentPolicy::default` 实际为 `inherit=All`、`ignore_default_excludes=true`。Stella 明确选择其 **Core 策略**作为后端到执行子进程的固定继承规则，并非声称 Codex 默认只继承白名单。

Bash 不再复制整个 `os.environ`：即使变量名不含 key/token，例如 `DATABASE_URL`、`OPENAI_WORKLOAD_IDENTITY_CONTEXT`，也不能继承；`BASH_ENV`、`PYTHONPATH`、`NODE_OPTIONS`、动态库注入变量同样不在集合。Shell 沙箱继续重写临时目录，HOME 沿用平台继承值，文件可见性由 OS 沙箱控制；插件及委托进程继续使用私有 HOME/TMP。现有 Plugin Host 有不同用途的固定环境列表，继续保留；MCP 经授权的专用凭据注入保持原契约，不能将 Shell 规则直接套在集成凭据上。

依赖后端任意自定义环境变量的 Shell 脚本需调整。需要业务凭据的能力应通过现有授权服务调用，不从后端环境隐式取得。白名单构造只是环境边界，不能替代文件、网络、进程或资源授权。

## Harness 对照与插件适配

| 实际源码 | 核实的机制 | Stella 的整合决定 |
| --- | --- | --- |
| `vendor/cordis/src/registry.ts`：`Registry.plugin/delete`、`Inject.resolve` | 可复用插件定义与每次加载的实例分离；活动上下文才能注册，实例拥有其效果；依赖声明规范化 | 保留 manifest 发现、Python PluginSession 与受控 RPC；在现有 PluginRegistry 明确用户、插件、代次所有权，不引入同进程代码注入 |
| `vendor/cordis/src/fiber.ts`：`_refresh/_setEpoch/_reload` | revision 和依赖实现身份组成 epoch；旧异步加载完成或失败不能覆盖新目标 | 沿用 generation，增加一次性待发布代次；只有最新预留代次能发布。过期、未预留、重复或已撤销发布均拒绝 |
| `vendor/cordis/src/fiber.ts`：`_unload`；`vendor/cordis/src/utils.ts`：`DisposableList` | 卸载撤销实例效果；依赖消费者退出后再释放提供方效果，清理由实例拥有 | 保留有界 drain、进程树终止与 finally 清理；撤销同时取消待发布代次，旧实例清理不能删除新实例。贡献快照写入和读取时深拷贝，外部修改不会改写登记状态 |
| `vendor/loader/src/index.ts`；`packages/boot/app-boot/src/profile-plugins.ts` | 模块加载树与安装清单分工；发现元数据不要求执行业务插件 | 沿用 manifest、distribution、PluginManager.inventory 的声明式发现及坏包可见状态，不在 Core 中执行插件完成发现 |
| `packages/boot/plugin-manager/src/operations.ts`；`apps/cli/src/plugin.ts` | 包安装、版本兼容、锁与运行激活分层 | 沿用 PackageStore / EnvironmentStore 的摘要、依赖锁、独立解释器、原子选择、候选预检和恢复；不添加 pnpm、Node 或另一套依赖管理器 |
| `vendor/cordis/src/utils.ts` 的 traceable context | 使用服务时保留消费者上下文 | 沿用 core_invoke 的父调用绑定、消费者、截止时间与公开服务白名单；代次复核显式使用 session 用户，插件声明或注册成功不授予权限 |

Registry 的键由全局 `plugin_id` 改为 `(user_id, plugin_id)`，服务名称也按用户划分。相同用户的不同插件仍不能占有同一个服务契约。Host、Core callback、Model/Data 授权回调、启停和管理列表均传入可信绑定用户。未显式传用户的内部兼容调用使用 `local-default`，不会列出其他用户。

预留新代次不会立即撤销已发布旧实例：候选未完成期间旧实例仍可用；新代次发布后旧实例调用不再匹配。撤销某代次只清理它自己的待发布记录和贡献；用户停用取消该用户该插件的所有发布及待发布代次。代次是进程内生命周期标识，不新增数据库迁移，也不等同授权版本。

插件间交互仍经 Core 公开服务 RPC：宿主恢复身份、用途、调用链、幂等键和期限，并按实时授权复核；插件不能自行指定用户、数据路径、凭据或提权。业务接口及存储格式保持原契约。

## 模块与代码复用范围

- 新增 `core/security/process_environment.py`，由 Bash 与 delegated_runtime 使用；仅用标准库。
- 修改现有 `core/plugins/registry/__init__.py`；更新 `host.py`、`core_invoke.py`、`lifecycle.py`、`runtime_hub.py` 和 `core/security/service.py` 的直接调用。
- Codex 直接适配只限上述环境模块。保留 Apache-2.0 文件声明、修改说明、`licenses/codex/LICENSE` 与完整上游 `NOTICE`，在 `THIRD_PARTY_NOTICES.md` 登记。桌面后端打包包含许可证与声明。
- Harness/Cordis 仅作设计参考，没有复制 TypeScript 源码或引入依赖。Harness 根许可证为 MIT（DeepSeek 2026）；重点参考的 vendored Cordis 另有 MIT 许可证（Shigma 2021-present）。
- 没有新增 ACL、通用 DI 框架、热加载协议或全库迁移。Core 管理授权与隔离，插件系统管理包、实例和贡献。

## 验证与限制

定向用例覆盖用户隔离、同用户服务冲突、过期/重复/撤销前后发布、嵌套快照修改、真实多用户 worker、管理停用、回调身份、在途撤销、环境筛选和真实 Shell/委托子进程；复用现有 OS 沙箱、依赖环境、生命周期恢复与 Bash 回归。本次 26 个测试文件的定向回归为 275 PASS、18 SKIP：15 项冻结运行器测试因缺少 PyInstaller 跳过，3 项为 Windows 专用测试；Python Ruff、diff 检查、spec 语法与许可证打包输入检查通过。具体命令见工作区本次任务交付记录；未构建完整桌面产物。

实际进程验证在 macOS Seatbelt 执行。Windows 环境规则通过平台模拟验证；Linux bubblewrap 与 Windows 实际执行未在本机验证，不能据此声明跨平台执行验收完成。Stella 当前未提供 Windows OS 沙箱，隔离能力不足时仍拒绝受管执行。

没有连接真实账号、模型或 MCP 服务，也没有移植 Codex 权限提升执行器、网络代理和全部平台沙箱。现有 Shell 禁网络策略继续生效；本次不声称实现 Codex 全部权限配置，不涉及模型对齐或内容审核。
