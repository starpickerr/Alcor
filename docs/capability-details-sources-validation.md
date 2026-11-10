# 能力详情与来源入口验收

状态：Verified（2026-10-25）。分支 `refactor/capability-details-sources`，基线 `23d07b5e`。只修改 Stella-Core，保留已有无关改动；未提交、合并或推送。

## 本轮实现

- 插件／MCP 仅功能、配置、关于，默认功能；Skill 仅内容，内置工具直接说明。删除概览、运行及整个技术信息展示。顶部与原有工具开关保留。
- 信息源按服务和实际 owner 分组，整卡进入对应能力配置；未知映射不生成错误链接。点击时记录查询参数与当前滚动，来源加载完成后恢复返回位置。删除内嵌账号／授权表单。账号、密码、课程、连接测试及文件配置仍使用原组件、接口与存储。
- 删除 `/api/data/access`、handler、客户端 data 上下文及对应管理分支，删除安全中心权限标签。旧权限链接回总览，任务链接进入任务管理。SecurityService、ResourceGrantStore 与历史 MCP grant 的访问范围、撤销、过期及父子关系保持；工具开关和三档权限不代替资源授权。
- 插件关于复用 `dual-update-plan`，只显示当前插件结果，不自动安装；未列出更新时仅说明“未发现可用更新”，不推断未纳入目录的插件已是最新。MCP 真实版本来自 `initialize.result.serverInfo.version`；缺失或不支持更新检查时明确说明。
- MCP `tools/list` 完整读取分页，校验响应、定义及游标；错误不再变成成功空列表。首次和再次发现共用代次检查及发布，保留公开工具筛选、规范 ID 与原开关。已有连接重查同一 client，失败／未连接复用原生命周期恢复，停用不自动启用。旧 wrappers 不会在重发现后复活；Agent 缓存同步支持同 client 的新定义。功能页在 pending 时轮询，切标签或离开停止。

## 验证结果

后端相关回归 **199 passed**：包括 MCP 分页、失败、空列表、过滤、发现 API 认证、同 client 刷新、Agent list/dict wrappers、禁用状态持久、旧线程发布丢弃，以及授权保留、撤销、过期、任务独立权限。命令：

```sh
/opt/anaconda3/envs/Stella/bin/python scripts/run_isolated.py /opt/anaconda3/envs/Stella/bin/python -m pytest tests/test_mcp_discovery.py tests/test_capability_catalog.py tests/test_capability_features.py tests/test_feature_access_settings.py tests/test_capability_enablement.py tests/test_capability_tool_switches.py tests/test_mcp_capability_catalog.py tests/test_data_api.py tests/test_permission_control.py tests/test_task_run_policy.py tests/test_scheduler_conversation.py tests/test_run_policy.py -q
```

浏览器九组通过：`ui_capability_details.py`、`ui_capability_features.py`、`ui_capability_tool_switches.py`、`ui_capability_navigation_discovery.py`、`ui_data_management.py`、`ui_security_modes.py`、`ui_feature_access_settings.py`、`ui_run_policy.py`、`ui_tasks.py`。覆盖真实 eLearning 服务工具定义、模拟 API 下的配置读写、来源 owner／多账号／未映射、返回查询和滚动、工具发现各状态及停止轮询、版本与检查更新、旧安全链接、独立权限、键盘、桌面／窄窗和暗色。浏览器用隔离数据与模拟业务后端，不代表真实学校或邮箱账号连通性已测试。

TypeScript typecheck、Desktop ESLint、相关 Python Ruff、`git diff --check` 通过。正常及窄窗口截图已实际检查。

独立审查发现的旧 worker 等待验证缺口已修正为等待真实发布尝试；用故意跳过准入检查的变体运行，测试实际失败，恢复实现后通过。更新检查空结果文案已补浏览器失败→通过回归。无未处理审查项。

## 全量结果与边界

`pytest tests -q`：**2461 passed / 33 failed / 22 skipped**，耗时 156.39 秒。失败节点与上一轮 [工具开关验收](capability-tool-switches.md) 的 33 项逐项集合比较一致，无新增失败。既有原因涉及插件存储句柄／信任、Agent Hub 和 MCP 元数据测试隔离，本轮未扩大修复范围。初次全量另暴露一项仍引用已删除 DataAccessSettingsHandler 的认证测试，已迁至保留的 SessionAccessSettingsHandler 并通过。

直接从仓库根运行不限定 tests/ 的 pytest 会额外收集 references 外部工程，出现缺失外部依赖及同名模块的 17 个收集错误；正式全量使用项目 tests/，未安装或修改外部参考工程。

本地完整日志：`.superpowers/sdd/2026-10-25-capability-details-sources/`。因尚未提交，保留本轮账本与验证证据。

既有失败节点：

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
