# 对话与定时任务权限、工作空间验证

实现分支：`feat/task-independent-workspaces`，从 develop 的 `192d4997` 创建，保留此前未提交的资源授权整理及无关改动。本轮未提交、合并或推送。

## 实现与兼容处理

- 新对话、任务与无主体执行上下文固定默认只读；原全局修改入口停用。旧配置与模板记录只作为迁移证据保留，不删除用户原数据。
- 旧主体迁移先保存有效权限，新增主体使用固定默认；迁移失败保持限制，原生控制不允许绕过迁移校验。
- 新任务支持 independent 与 source。独立任务无需对话 ID，复用任务持久目录；关联任务参考近期消息及关联工作空间，独立保存权限。
- 旧 source/reuse/new 保持原目录与记录。新关联范围以 workspace_version=1 标记，避免静默移动旧文件。
- 每个新任务的每次执行使用独立内部 Agent 会话；结果复用 job_runs/result_cache，在任务详情运行记录查看，无独立的结果存储体系。
- 任务模式按开始时的快照执行，编辑下次生效；删除/撤销任务仍立即阻止后续操作。数据源、账号、MCP 及插件授权继续分别检查，不因完全访问自动获得资源。

## 验证证据

使用 `/opt/anaconda3/envs/Stella/bin/python scripts/run_isolated.py` 隔离用户数据，不访问真实账号。

- 核心相关回归：141 passed（策略、迁移、任务、原生控制、安全中心、功能授权）。
- 扩大回归：144 passed（MCP 数据源、资源撤销、原生设置、事务提醒、后台调度和权限回归）。旧测试现明确声明原生 UI 来源或显式主体权限，替代已移除的全局默认修改。
- 浏览器：ui_tasks.py 覆盖默认独立/只读、切换关联、无 session_id 提交、运行记录输出、任务独立权限编辑及关联事务导航。另运行 ui_security_modes.py、ui_run_policy.py。
- TypeScript typecheck、相关 ESLint/Ruff、git diff --check。

完整输出位于本地 `.superpowers/sdd/2026-10-24-task-permission-workspaces/`。

## 审查与决策

独立审查代理因额度限制未能执行，已补作者本地审查，不能视作独立审查通过。检查了 API/运行记录所有权、关联私有目录隔离、任务快照、资源授权及原生 UI 边界；审查发现任务快照可能掩盖删除撤销，已补 RED→GREEN 回归，撤销仍立即生效。

保留旧目录和迁移证据是兼容选择；用户需重新选择关联范围才能采用新的关联工作空间。任务运行记录只读取现有表与结果缓存，避免新增数据管理系统。以上选择的限制是不会自动移动旧任务文件。

## 全量回归结果

`pytest tests -q`：2430 passed / 33 failed / 22 skipped。与上一轮工作区记录的失败集合比较，33 项均在原有 34 项失败中，无新增失败节点。完整测试启动后补充的任务即时撤销回归由最终 141 项相关测试单独验证。

资源与执行边界补充回归：68 passed。三个浏览器回归、类型检查、相关 ESLint/Ruff 和 diff 检查均通过。安全中心浏览器测试曾受 Vite HMR 模块重复影响，重启本轮自有服务后通过。

仍失败的全量测试如下（主要为既有 Agent Hub、插件信任/数据绑定及 MCP 元数据问题）：

- `tests/test_agent_hub_reliability.py::test_cli_completion_does_not_leave_descendants`
- `tests/test_agent_hub_reliability.py::test_cli_submit_is_nonblocking_and_observation_retains_run`
- `tests/test_agent_hub_reliability.py::test_external_cli_inherits_parent_filesystem_ceiling`
- `tests/test_agent_hub_reliability.py::test_once_approved_public_selector_starts_real_provider[selector1]`
- `tests/test_agent_hub_reliability.py::test_once_approved_public_selector_starts_real_provider[selector2]`
- `tests/test_agent_hub_reliability.py::test_worker_crash_reaps_owned_cli_groups`
- `tests/test_apple_briefing_source.py::test_native_calendar_collection_uses_managed_owner_grants_and_honors_revocation[background]`
- `tests/test_apple_briefing_source.py::test_native_calendar_collection_uses_managed_owner_grants_and_honors_revocation[interactive]`
- `tests/test_automation_plugin_gateway.py::test_commit_automation_package_and_invoke_service`
- `tests/test_briefing_peer_authorization.py::test_background_obeys_mode_while_native_ui_does_not`
- `tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_native_create_repeated_create_and_linked_views[calendar]`
- `tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_native_create_repeated_create_and_linked_views[todo]`
- `tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_projection_and_sync[False]`
- `tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_projection_and_sync[True]`
- `tests/test_briefing_peer_authorization.py::test_disabled_owner_hides_enrichment_and_preserves_pending_projection`
- `tests/test_briefing_peer_authorization.py::test_native_reminder_configuration_reaches_task_owner`
- `tests/test_briefing_peer_authorization.py::test_native_worker_reads_need_no_plugin_grant`
- `tests/test_briefing_peer_authorization.py::test_nondefault_user_uses_only_its_own_owner_grants[alice]`
- `tests/test_briefing_peer_authorization.py::test_obsolete_plugin_grant_does_not_block_native_workflow`
- `tests/test_briefing_plugin_gateway.py::test_commit_briefing_package_and_invoke_service`
- `tests/test_briefing_plugin_single_writer.py::test_briefing_creates_and_recovers_todo_through_scoped_reverse_call`
- `tests/test_managed_mcp_metadata.py::test_managed_config_loads_description_from_the_server_package[False]`
- `tests/test_managed_mcp_metadata.py::test_managed_config_loads_description_from_the_server_package[True]`
- `tests/test_owned_resource_providers.py::test_owner_managed_methods_consumer_purpose_and_disable`
- `tests/test_package_install_bills.py::test_commit_bills_package_and_ping_service`
- `tests/test_package_install_tasks.py::test_commit_tasks_package_and_invoke_service`
- `tests/test_plugin_invoke_contract.py::test_read_only_agent_can_query_but_cannot_write`
- `tests/test_plugin_managed_authorization.py::test_background_purpose_cannot_be_downgraded_by_a_callback`
- `tests/test_plugin_managed_authorization.py::test_managed_host_accepts_approved_scope_and_job_cannot_use_interactive_grant`
- `tests/test_plugin_managed_authorization.py::test_managed_host_ignores_legacy_full_grants_and_refuses_bypass`
- `tests/test_plugin_managed_authorization.py::test_metadata_cannot_refresh_a_delegated_write_deadline`
- `tests/test_plugin_management.py::test_managed_install_does_not_issue_full_grant_when_worker_starts`
- `tests/test_plugin_migration_runtime.py::test_real_bills_migration_creates_and_verifies_schema_without_publishing`
