# 功能内访问设置：验证记录

> 此文保留当时实施与验证记录；现行标签、来源入口和管理接口见[统一能力详情验收](capability-details-sources-validation.md)。

分支：`refactor/remove-resource-authorization`；基线：`192d4997`。本轮未提交或合并；复核针对工作区差异，后续实现变化需要重验。

## 结果

| 检查 | 结果 |
| --- | --- |
| 权限、迁移、数据、MCP、能力相关后端用例 | 155 passed |
| 浏览器 | 7 套 PASS：功能内访问设置、安全中心、RunPolicy、能力详情、任务、能力功能、数据管理 |
| TypeScript、相关 ESLint / Ruff、git diff --check | PASS |
| 完整项目入口 `pytest tests -q` | 2421 passed / 34 failed / 22 skipped / 5 warnings |
| 独立基线工作树，同一隔离环境 | 2390 passed / 36 failed / 40 skipped / 5 warnings；当前 34 项失败均在基线失败集合中 |

浏览器覆盖首次账号配置、数据源／任务／插件／MCP 请求绑定、历史非 MCP 连接限制、对话独立限制、撤销、版本冲突、导航、窄屏与暗色。后端覆盖不同用户／主体／资源、过期与父授权撤销、未知和已移除提供者的保留授权、旧课程、旧连接键及原地读取不改变授权记录。Security Service 的真实允许与拒绝路径均有验证。

独立复核发现 4 项 Important：首次账号入口、旧课程撤销、已移除 MCP 的撤销入口、非 MCP 连接限制接管；均已修复并补充回归测试。没有延后处理的 Minor。

## 完整套件的既有失败

这些失败主要涉及 CLI／插件进程、宿主存储绑定和托管 MCP 包元数据。逐项与独立基线对照，没有本轮新增失败；不将完整套件描述为通过，也不为本任务修改无关运行机制。

```text
FAILED tests/test_agent_hub_reliability.py::test_cli_submit_is_nonblocking_and_observation_retains_run
FAILED tests/test_agent_hub_reliability.py::test_external_cli_inherits_parent_filesystem_ceiling
FAILED tests/test_agent_hub_reliability.py::test_cli_completion_does_not_leave_descendants
FAILED tests/test_agent_hub_reliability.py::test_worker_crash_reaps_owned_cli_groups
FAILED tests/test_agent_hub_reliability.py::test_once_approved_public_selector_starts_real_provider[selector0]
FAILED tests/test_agent_hub_reliability.py::test_once_approved_public_selector_starts_real_provider[selector1]
FAILED tests/test_agent_hub_reliability.py::test_once_approved_public_selector_starts_real_provider[selector2]
FAILED tests/test_apple_briefing_source.py::test_native_calendar_collection_uses_managed_owner_grants_and_honors_revocation[interactive]
FAILED tests/test_apple_briefing_source.py::test_native_calendar_collection_uses_managed_owner_grants_and_honors_revocation[background]
FAILED tests/test_automation_plugin_gateway.py::test_commit_automation_package_and_invoke_service
FAILED tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_native_create_repeated_create_and_linked_views[todo]
FAILED tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_native_create_repeated_create_and_linked_views[calendar]
FAILED tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_projection_and_sync[False]
FAILED tests/test_briefing_peer_authorization.py::test_default_owner_grants_allow_projection_and_sync[True]
FAILED tests/test_briefing_peer_authorization.py::test_obsolete_plugin_grant_does_not_block_native_workflow
FAILED tests/test_briefing_peer_authorization.py::test_disabled_owner_hides_enrichment_and_preserves_pending_projection
FAILED tests/test_briefing_peer_authorization.py::test_nondefault_user_uses_only_its_own_owner_grants[alice]
FAILED tests/test_briefing_peer_authorization.py::test_native_worker_reads_need_no_plugin_grant
FAILED tests/test_briefing_peer_authorization.py::test_native_reminder_configuration_reaches_task_owner
FAILED tests/test_briefing_peer_authorization.py::test_background_obeys_mode_while_native_ui_does_not
FAILED tests/test_briefing_plugin_gateway.py::test_commit_briefing_package_and_invoke_service
FAILED tests/test_briefing_plugin_single_writer.py::test_briefing_creates_and_recovers_todo_through_scoped_reverse_call
FAILED tests/test_managed_mcp_metadata.py::test_managed_config_loads_description_from_the_server_package[False]
FAILED tests/test_managed_mcp_metadata.py::test_managed_config_loads_description_from_the_server_package[True]
FAILED tests/test_owned_resource_providers.py::test_owner_managed_methods_consumer_purpose_and_disable
FAILED tests/test_package_install_bills.py::test_commit_bills_package_and_ping_service
FAILED tests/test_package_install_tasks.py::test_commit_tasks_package_and_invoke_service
FAILED tests/test_plugin_invoke_contract.py::test_read_only_agent_can_query_but_cannot_write
FAILED tests/test_plugin_managed_authorization.py::test_managed_host_ignores_legacy_full_grants_and_refuses_bypass
FAILED tests/test_plugin_managed_authorization.py::test_managed_host_accepts_approved_scope_and_job_cannot_use_interactive_grant
FAILED tests/test_plugin_managed_authorization.py::test_metadata_cannot_refresh_a_delegated_write_deadline
FAILED tests/test_plugin_managed_authorization.py::test_background_purpose_cannot_be_downgraded_by_a_callback
FAILED tests/test_plugin_management.py::test_managed_install_does_not_issue_full_grant_when_worker_starts
FAILED tests/test_plugin_migration_runtime.py::test_real_bills_migration_creates_and_verifies_schema_without_publishing
```

最后补充了首次业务编辑前的旧连接入口发现用例，并验证 155 项相关用例通过；完整套件结果为此之前的扩大回归记录。

## 验证边界

测试使用临时 HOME、用户数据、数据库与合成授权，不读取或改写真实用户授权。原授权表不复制、不重签发，故无需先对用户数据库执行破坏性升级。

根目录不带路径的 `pytest -q` 会误收集 `references/` 内第三方参考项目，在缺少 zstandard、CODEX_REPO_ROOT、参考 SDK 环境时出现 17 项收集错误；已改用 Makefile 指定的 `pytest tests -q`。浏览器在前端热更新后曾遇到测试模块的双实例状态，重启本任务启动的 Vite 后已完成最终回归。

运行策略和实际资源校验没有被关闭；未清除凭据、业务数据或旧授权。无关的质量检查脚本改动与重复文件没有纳入本次修改。
