# 能力详情的工具开关与配置归属

本轮分支 `feat/capability-tool-switches`，基于 develop `192d4997` 创建；之前的未提交改动原样保留。本轮未提交、合并或推送。

## 实现

功能页沿用原工具名称、简介与排列，删除工具行箭头、展开详情和点击展开行为；右侧使用现有 Toggle。原技术信息区域继续保留，不新增授权模式。开关保存失败会显示错误，保存期间禁用重复操作；成功回写现有父详情数据，切换标签不会恢复旧状态。

插件及 MCP 共用 CapabilitySettings 的 `tool` 状态。MCP 使用原 McpTool 规范 ID（服务摘要和工具摘要），没有第二套状态表。`tool_enabled` 同时检查服务开关和工具开关，发现、缓存实例、Agent 动态同步、直接调用均受约束。服务停用/重新启用、连接代次替换不会重置工具意图。服务资源授权结束后、缓存检查/目标 dispatch 前再次检查开关；返回后继续检查原安全授权，停用期间不释放结果。

现有 `/api/tools` 增加可选提供者上下文，从真实能力目录验证工具归属再保存；不接受未注册工具。开关不授予调用权限，SecurityService、三档 Agent/任务权限、资源范围、撤销及执行环境检查继续使用原机制。

配置页的插件继续使用实际声明的 SettingsDescriptor；MCP 按已有 settingsUrl 找对应的现有配置组件。eLearning 和邮箱分别复用原账号/密码/连接测试/文件同步组件及 API，不复制凭据或配置。其他提供者使用自己已有的配置声明/入口；没有发布可编辑配置的提供者显示说明，不强加账号表单。配置页不展示工具开关。

能力详情的旧资源授权编辑入口与后台 provider 管理分支已移除；定时任务页的“数据与连接访问”及 `/api/tasks/access` 管理路由已移除。原授权事实继续保存在 ResourceGrantStore/既有 MCP scoped grants 中，既有连接限制、有效期、父子关系、撤销状态不变，不清空或放宽。数据源、对话和非 MCP 连接的既有管理不在本轮清理范围。任务三档执行权限保持。

## 范围与兼容决策

- 顶部卡片、概览、运行保持原布局和交互；父详情仅增加工具保存后的数据回写。
- 按最后明确的边界，本轮没有修改数据源或其他设置页的跳转；现有 origin/back 路径继续生效。数据源原配置暂保留，两个入口共用原状态。
- 旧授权仍是后台实际访问限制，不能迁移成工具开关，也没有将删除管理入口解释成删除授权事实。
- 未映射到实际 Core adapter 的插件工具声明仍按既有执行模型处理；不为不可调用的声明伪造另一套执行机制或可用状态。

## 验证

隔离用户数据运行，不连接真实账号。

- 相关后端回归：186 passed，含独立持久化、同名 MCP 隔离、Agent 发现/动态同步、缓存/直接执行、授权中停用、响应中停用、服务重连、原权限与授权数据保留。
- 浏览器：ui_capability_tool_switches、ui_capability_features、ui_capability_details、ui_tasks、ui_data_management、ui_feature_access_settings 通过。覆盖同页切标签、重开页面、真实 eLearning 工具定义、同一账号接口、原生配置草稿/冲突恢复、任务页授权移除、其他标签/导航和窄窗口。
- TypeScript typecheck、相关 ESLint/Ruff、git diff --check 通过。

审查：独立只读审查发现两项 Important（切标签旧状态、异步授权结束后的开关缺口），均补 RED→GREEN 回归并修复；无 Critical/Minor。完整日志保留在本地 `.superpowers/sdd/2026-10-25-capability-tool-switches/`。

## 全量回归

`pytest tests -q`：2437 passed / 33 failed / 22 skipped。与上一轮工作区全量结果比较，失败节点集合完全相同，无新增失败。全量收集开始后新增的服务重连保留单工具状态用例，由最后 186 项相关回归单独验证。

已有失败如下，完整异常原因见本地 full-final.log：

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
