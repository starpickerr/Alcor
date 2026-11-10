# 权限重构最终审查与处理

2026-10-23，Core 基线 `172819f6`、Plugins 基线 `c3667a3`。唯一一位独立审查者检查两仓完整权限差异及日志集成交叉边界，最终检查点为 Core `21372cac` / Plugins `6fd762c`；中断后恢复同一审查，没有第二轮审查。确认 0 Critical、8 Important、1 Minor。

下列问题经执行者按影响重新定级后，在同一次修复阶段处理。Core `ddb0ba35`、`6792ecf9`、`f788aaa6` 保存修复、适配器集成及边界回归，采用先观察失败、再修复转绿的方法；补充拒绝路径与参数完整性检查。最终全量结果见[验收记录](permission-unification-acceptance.md)。

| 编号 | 级别 | 已确认问题与修复 | Core 回归证据 |
| --- | --- | --- | --- |
| I1 | Important/High | MCP 信任撤销遗漏后代；复用受控进程组清理 | test_extension_sandbox.py::test_mcp_trust_revoke_stops_ordinary_descendant |
| I2 | Important/High | 直接入口未覆盖导入代码；摘要覆盖本地包，拒绝不固定的受信任启动 | test_extension_sandbox.py 的 entrypoint_digest、trusted_console、trusted_module 三项 |
| I3 | Important/High | 插件返回前未查 RunPolicy；绑定前后策略并复查授权，丢弃正文且保留真实副作用；MCP 同类路径纳入 | test_inflight_policy.py 的真实延迟 worker read/mutation、grant/policy 撤销及 MCP 正文丢弃 |
| I4 | Important/High | 子运行创建任务忽略捕获上限；使用有效策略，保留隐式文件限制、额外路径和旧拒绝 | test_task_run_policy.py 的 captured_ceiling_through_real_worker、implicitly_denied_file_tool、legacy_restrictions、delegated_path |
| I5 | Important/Medium | 未知 MCP 无法取得资源授权；连接层采用 system_execute，能力层仍保守判断 | test_resource_grants.py::test_unknown_mcp_real_call_uses_explicit_system_execute_resource（一次审批和后台预授权） |
| I6 | Important/Medium | 任务策略提交中断后无法修复；owner 可以对缺失行 revision 0 CAS 修复并确认损坏旧策略，模型不可修复 | test_task_run_policy.py 的 owner_can_repair、owner_can_confirm、model_cannot_repair |
| I7 | Important/Medium | AgentHub 选择器转换触发再次审批；Core 精确绑定选择和启动载荷，未启动的权限失败记为 failed | test_agent_hub_reliability.py 的三种 once_approved_public_selector、changed_public_request、derived_once_approval |
| I8 | Important/Medium | 日志合并后隔离 worker 无法导入 common.log；仅开放日志桥必要只读文件 | test_plugin_diagnostics.py::test_real_worker_logs_and_failed_results_reach_host；保留文件/网络拒绝探针 |

最终验证另发现一项 Important：PermissionSelector 在设置响应缺少 permission 时读取 undefined.policy，导致嵌入式聊天崩溃。`f788aaa6` 恢复空值保护和不可用控制，`ui_embedded_agents.py` 从实际页面异常转绿，并验证权限控件保持加载态时聊天仍可用。此项来自最终验证，未冒充独立审查者的发现。信息架构浏览器夹具同步新增日志查询的完整空页契约，保留导航/选中态和页面无异常断言。

延期 Minor M1：新建任务表单丢弃 extra_roots；可在保存后重新打开任务权限编辑器授予路径。没有在本次修复阶段混入 UI 延期项。

审查采用风险优先的差异分流、关键依赖追踪及隔离合成探针，不是每行/每分支覆盖率证明。发现的 I1–I7 有独立合成复现，I8 为独立真实 worker 测试失败；M1 为静态数据流确认。未对真实账户、生产迁移、Linux/Windows 实机、冻结后端或真实 provider 作通过结论。复杂历史 source SDK 关系、安装/OS/DNS 威胁边界保留为覆盖限制。所有审查暂不裁定项与执行取舍保留在完成记录中。
