# 功能配置与访问限制

安全中心不提供独立资源授权页面、权限管理标签或全局默认权限编辑。新对话和定时任务固定默认只读，各自保存三档执行权限。

| 功能 | 管理位置 |
| --- | --- |
| 来源账号、连接、课程范围及文件目录 | 能力管理 → 实际插件／MCP 的配置；信息源卡片链接到此处 |
| 工具独立启停 | 能力管理 → 对应提供者的功能 |
| 任务执行模式 | 任务管理 → 当前任务的独立权限 |
| 历史非 MCP 用户级连接限制 | 能力管理 → 连接访问设置（仅展示已有记录） |
| 对话模式及该对话的历史连接限制 | 对话权限设置 |

安全中心保留安全活动、凭据状态、数据访问摘要和其他安全概览。旧 `?tab=permissions` 转到安全总览，后台任务链接直接进入任务管理。没有独立的数据源／任务／插件／MCP 资源授权表单。

## 既有授权继续执行

移除管理入口不复制、重签发或清空授权。现有 `resource_grants`、`resource_restrictions` 和历史 MCP scoped grants 使用原数据库；ID、主体、操作范围、用途、revision、父授权、有效期和撤销状态不变。移除的提供者可保留能力目录记录，其旧限制仍生效。

资源检查由既有 SecurityService、Gateway 与 Data Service 执行，独立于工具开关和执行模式。完全访问不会自动获得所有账号和资源；只读不能写入；过期、撤销或父授权失效仍拒绝。账号、密码及业务存储不迁移。

保留的对话和非 MCP 连接管理先校验所有者、主体与上下文，并沿用迁移核验及 revision 冲突保护。用户级非 MCP 连接限制保留原键，例如 `browser`，不会转换为 `mcp:browser`。

## API 清理

旧 `/api/security/resources`、`/api/security/connections`、`/api/security/mcp-access`、`/api/data/access`、`/api/tasks/access` 管理入口均已移除。`/api/capabilities/access` 仅接受已有非 MCP connection 限制，拒绝插件／MCP 独立授权；`/api/sessions/access` 保留对话自身的兼容限制管理。共用既有安全事实，不构成另一套权限判断。

验证及既有失败见[能力详情与来源入口验收](capability-details-sources-validation.md)。测试使用隔离数据，不操作真实账号或迁移真实授权库。
