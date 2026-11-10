# 数据库与权限边界

`common/runtime_identity.py` 维护运行身份，`common/state_dir.py` 根据身份解析各类状态路径。当前业务库包括用户级 `todos/todos.db`、`calendar/calendar.db`、`bills/bills.db`、`briefing/briefing.db`；会话和调度还有各自存储。测试可通过显式服务路径和临时 `HOME`/`STELLA_DATA_DIR` 隔离。不要用真实用户目录运行审计或 Evals。

| 模块 | 表/模型入口 | 迁移和恢复关注点 |
| --- | --- | --- |
| 事务 | 外仓插件 `stella.tasks`（`backend/domain/schema.sql`）中的 `todos`、依赖、提醒和墓碑表 | 旧日程事务迁移、幂等应用、用户编辑保护 |
| 日程 | `stella.calendar` 插件拥有事件、分类和交接表；Core 仅拥有 Apple 映射库 | 简报来源变更与已有事件冲突 |
| 账单 | 外仓插件 `stella.bills`（`backend/domain/schema.sql`、`store.py`） | 账本、计划付款、期间报表一致性 |
| 简报 | 外仓 `stella.briefing` 插件的领域库与 worker，Core gateway 负责适配 | 候选快照、版本、来源游标、行动回执与 outbox |
| 会话 | `agent/memory/conversation_store.py` | 旧库迁移、运行准入与消息顺序 |

权限模式与平台边界已有 [详细说明](../security-permission-modes.md)。运行时的可信判定点是 `agent/runtime/tool_invocation.py` 和 `agent/permission/gateway.py`；`Application.command` 对定时业务写入再次校验。Web/API 端还需验证认证，不能把前端隐藏按钮当作授权。涉及外部资源的工具要检查路径、URL、重定向与凭证泄露。权限和事务的失败恢复必须有隔离测试。

普通本地 MCP 的历史私有目录保留既有内部安全复制实现；对应安全管理页面及 API 已移除，不再提供用户可编辑的迁移表单。`core/mcp/storage.py` 将确认绑定当前用户、启动实现、配置、开关版本及目录/文件快照；客户端仅传来源 ID，不能指定任意路径。复制拒绝符号链接、硬链接和特殊文件，原目录保留，当前目标通过系统原子排他重命名发布，不覆盖已有数据。确认记录在发布前保存 staging inode，发布后中断仍可恢复；目标或历史根目录被替换后需重新确认。邮箱/eLearning 的固定可信连接器继续使用各自的账号迁移入口。

Email保留独立MCP协议、原账号/游标格式及唯一托管连接；旧邮箱路由跳转到同一能力配置表单。Stella以`--hosted-connector`启动受管Server，Worker不访问账号库、原配置或密码，也不直接联网。每次请求只可回调当前Core已准入操作；方法、参数与资源范围由Core持有，Worker不能改成另一接收方或通用文件/网络调用。原email.db继续保存账号及Keychain引用，SQLite事务提交后才清理旧凭据。原设置支持状态刷新、修复、删除及测试连接，已保存密码不回填；凭据目录直接汇总声明字段状态，不启动Worker或解密。文件工具与Shell的凭据路径清单继续保护Email目录及SQLite附属文件。

邮箱可信连接器明确限定为Core的 `core/services/email_connector.py`、`EmailCredentialStore.connect` 与 `core/security/imap_transport.py`。前者只加载Core固定摘要所覆盖的hosts、imap、schema、service四个邮箱模块及schema.sql，执行与摘要同次读取的字节快照；不导入Server入口或任意扩展代码。认证仅提交到账号绑定的IMAPS主机/端口，复用地址校验、TLS、连接lease和现有资源授权；账号/凭据/授权变更会使相关在途连接失效。旧环境账号仅由local-default采用，删除事实保持，原环境引用丢失不会从进程缓存恢复。当前macOS测试证据及整体验收状态见[阶段记录](../security-managed-runtime-progress.md)。

Core 服务协调入口新增 `core/workspace/service.py`、`core/execution/context.py` 与 `core/security/service.py`。运行上下文不可变；独立 RunPolicy 保存在既有授权库中，调用和返回仍复查当前 revision、有效策略及 Grant。通用插件私有数据使用用户级 `plugins/<plugin-id>`，第一方数据句柄保持旧路径；文件/shell 的保护清单覆盖通用私有目录及原账单、事务、日程、Agent Hub、简报与自动化数据库及 SQLite 附属文件。详细协作、兼容及平台边界见[工作区与执行服务](execution-services.md)。

模型凭据在模型设置 `/models?credential=<name>` 内编辑，经 `/api/models` 的 credential action 更新。`agent/permission/center.py::set_secret` 更新原配置字段、`custom_providers[].api_key` 或原搜索命名空间，不创建第二份凭据库；配置文件原子替换并使用 `0600`。自定义厂商凭据的稳定键为 `CUSTOM:<id>`，展示名称可改，清除凭据保留厂商、地址、模型与用途绑定。保存后重建模型客户端并刷新现有进程环境，撤销同时清除兼容环境值。状态接口不返回密钥正文，修改记录 L3 审计。旧受认证模型配置 API 继续兼容；Desktop、精简版模型设置及首次设置共用模型凭据编辑器；没有安全中心跳转。

对话/任务权限统一为 RunPolicy + Capability Authorization + Resource Grants。项目不再持有执行授权，默认模板只影响新建主体。统一存储、迁移与真实插件隔离的接口及限制见[权限与执行边界](../security-permission-modes.md)。
