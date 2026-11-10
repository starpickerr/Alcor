# 权限重构验收记录

适用范围：Core `172819f6` 与 Plugins `c3667a3` 之后的 permission-unification 分支。实现说明见[权限与执行边界](security-permission-modes.md)。独立审查、修复与限制见[最终审查记录](permission-unification-review.md)。

## 强制验收证据

| # | 行为 | 可执行证据（Core tests/） |
| --- | --- | --- |
| 1 | Read Only 拒绝直接文件写入 | test_capability_authorization.py::test_business_write_does_not_grant_direct_file_write |
| 2 | Read Only + 事务 write 可创建真实事务 | test_capability_authorization.py::test_read_only_with_tasks_write_creates_real_todo |
| 3 | 事务 read 不能写，Full Access 不替代能力 | test_capability_authorization.py::test_tasks_read_cannot_create_even_with_full_filesystem_access |
| 4 | Full Access 不授予邮件账号或通用 MCP 资源 | test_capability_authorization.py::test_full_access_does_not_authorize_email_mcp；test_resource_grants.py |
| 5 | 同项目对话各自保存策略 | test_run_policy.py::test_project_conversations_have_independent_policies；ui_run_policy.py |
| 6 | 私有空间隔离、任务不借用项目根 | test_process_sandbox.py；test_run_policy.py::test_task_workspace_cannot_be_replaced_by_source_project |
| 7 | 任务独立策略，模型不能持久提权 | test_task_run_policy.py，包含真实 automation Worker 路径 |
| 8 | 插件/MCP 禁用前后复查、终止执行 | test_capability_enablement.py；test_extension_sandbox.py |
| 9 | 未知 MCP 交互审批及后台预授权 | test_capability_authorization.py::test_unknown_mcp_requires_exact_approval_and_background_preauthorization；test_resource_grants.py 的真实无映射 MCP 调用 |
| 10 | 子 Agent / Agent Hub 受父上限约束 | test_subagent.py；test_task_run_policy.py；test_agent_hub_plugin_gateway.py |
| 11 | 句柄/参数不能冒充用户或资源 | test_mcp_bound_resources.py；test_extension_sandbox.py::test_host_data_handle_cannot_replace_worker_user_or_namespace |
| 12 | 插件/MCP 实际 OS 隔离、外部 CLI 沙箱 | test_extension_sandbox.py；test_plugin_runner_isolation.py；test_agent_hub_plugin_gateway.py |
| 13 | 审批绑定操作、身份、资源和策略版本 | test_security_approval_resume.py；test_capability_authorization.py::test_approved_business_operation_reaches_service_once_without_approving_other_resources |
| 14 | 撤销/过期及父授权立即约束后续调用 | test_resource_grants.py；test_plugin_scoped_authorization.py；test_extension_sandbox.py |
| 15 | 后台缺权限明确失败，不弹窗或自动恢复 | test_task_run_policy.py::test_background_missing_sensitive_preauthorization_fails_without_pending；test_scheduler_run_now.py；ui_tasks.py |
| 16 | 历史 deny/revocation、幂等、故障恢复 | test_permission_migration.py；test_run_policy.py；test_permission_control.py |
| 17 | 事务、日程、简报、账单、自动化、Agent Hub | test_todos_plugin_gateway.py、test_calendar_plugin_gateway.py、test_briefing_peer_authorization.py、test_bills_web.py、test_automation_plugin_gateway.py、test_agent_hub_plugin_gateway.py；Plugins 四领域测试 |
| 18 | 单元、集成、安全、UI 与构建 | 最终结果见下表 |

## 检查结果

所有 Python 检查通过 `scripts/run_isolated.py` 使用临时 HOME/STELLA_DATA_DIR；Plugins 各领域分进程，避免同名 domain 模块冲突。

| 检查 | 结果 |
| --- | --- |
| Task 4 资源/OS/插件/MCP 阶段门禁 | 172 passed |
| Task 5 迁移/API/任务门禁 | 140 passed |
| 真实资源/生命周期补充回归 | 62 passed |
| Plugins 全部现有领域套件 | Bills 17、Briefing 23、Calendar 15、Tasks 46 passed（合计 101） |
| Node 业务/UI 工具函数 | 24 passed（包含 runtimeLog 4 项） |
| 完整桌面 build（含 renderer/main/typecheck） | PASS，保留现有 chunk 大小、vendor CSS 路径与 PostCSS 警告 |
| Ruff / ESLint / UI 静态检查 | PASS（Core/Plugins Ruff、完整 ESLint、scripts/ui_check.py） |
| Playwright | scripts/ui_check.py 全部 16 套 PASS，ui_run_policy.py 再次 PASS；日志专项 ui_logs.py PASS；包含嵌入式聊天、版本冲突、独立主体、资源撤销/恢复、受信任运行选择、凭据入口、桌面/窄屏/英文深色 |
| Core 全量（最终实现 f788aaa6） | 2290 passed / 22 skipped / 5 warnings，197.03 秒；最终代码完整回归通过 |
| 独立安全差异审查 | 唯一独立审查确认 8 Important、1 Minor；8 项均有 RED→GREEN 修复；最终验证补修聊天空值崩溃，保留 1 Minor，见审查记录 |

跳过项已逐项复核：17 项缺少 PyInstaller 的冻结产物检查、3 项 Windows 集成检查、1 项需显式启用真实外部 Agent 的矩阵、1 项本地开发 Skill 脚本检查。本次 renderer/main 构建通过；没有构建冻结 Python 后端/安装包，不把这些跳过项记为通过。

合并 develop 的日志更新后，保留完整日志工作台及日志采集；隔离 Worker 的日志桥导入和 MCP 后代停止问题已修复。最终静态检查整理动态路由导入，没有删减日志功能。开发 Agent 规范检查独立显示 SKIP（产品仓未包含本地规范），不计作通过。

## 数据与平台边界

开发过程中没有读取真实用户账号、凭据或业务库，没有对真实数据运行迁移。迁移只在合成数据库中验证；应用产品入口负责用户本地升级。macOS 实际探针覆盖工作区外读写、网络、无关进程信号和实际 Plugin/MCP Worker。Linux/Windows 需在对应平台继续验证，不能以本机单元测试代替。

恢复只撤回未被修改的迁移 RunPolicy 快照，资源授权和撤销记录保留在统一存储。历史连接级全自有资源范围明确展示，可撤销为指定资源；新 Full Access 主体不获得此范围。课程授权使用账号加课程 ID，连接公共元数据与具体课程授权分别校验。

## Git 与交付

Core 与 Plugins 分别在 permission-unification 分支提交。Core 实现与审查修复截至 `f788aaa6`，Plugins 为 `6fd762c`；最终门禁通过。授权交付目标为两仓本地 develop，不推送、不修改 main。Core 先合入 develop `717966ef` 的 logging-workbench 更新再验证；已有 logging-workbench 分支和任务外未跟踪副本保留。
