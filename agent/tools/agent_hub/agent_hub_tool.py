"""Delegate professional tasks via Agent Hub — returns executionId immediately."""

from __future__ import annotations

from typing import Any

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger


from core.services.agent_hub_execution import AgentHubExecution


class AgentHubTool(AgentHubExecution, BaseTool):
    name: str = "agent_hub"
    description: str = (
        "外部专业 Agent 协作（Agent Hub）：向 Codex / Cursor ACP / Claude Code 委托任务。\n"
        "长任务立即返回 executionId，不阻塞对话等待结束。\n\n"
        "actions:\n"
        "- submit: 提交目标与验收条件，返回 executionId\n"
        "- status: 查询 executionId 状态与事件\n"
        "- cancel: 请求取消\n"
        "- continue: 在支持时继续（否则 UNSUPPORTED）\n"
        "- providers: 列出/探测已配置 Provider\n"
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["submit", "status", "cancel", "continue", "providers"],
            },
            "goal": {"type": "string", "description": "submit: 任务目标"},
            "task_kind": {
                "type": "string",
                "description": "submit: 开放任务类型，如 coding.task / research.report",
            },
            "acceptance": {"type": "object", "description": "submit: 验收条件"},
            "provider_config_id": {"type": "string"},
            "provider_local_id": {
                "type": "string",
                "enum": [
                    "codex-app-server",
                    "codex-cli",
                    "cursor-acp",
                    "cursor-cli",
                    "claude-code",
                ],
            },
            "agent_id": {
                "type": "string",
                "enum": ["codex", "cursor", "claude-code"],
                "description": "submit: 按 Agent 身份选择（可与 access_mode 组合）",
            },
            "access_mode": {
                "type": "string",
                "enum": ["cli", "app-server", "acp"],
                "description": "submit: 接入方式；与 agent_id 一起使用",
            },
            "execution_id": {"type": "string"},
            "max_wall_time": {"type": "integer", "minimum": 5, "maximum": 3600},
        },
        "required": ["action"],
    }

    def is_available(self) -> bool:
        try:
            from core.services.agent_hub_gateway import agent_hub_plugin_enabled

            return agent_hub_plugin_enabled()
        except Exception:
            return False

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")
        try:
            if action == "providers":
                return ToolResult.success(self._providers())
            if action == "submit":
                return ToolResult.success(self._submit(kwargs))
            if action == "status":
                return ToolResult.success(self._status(kwargs["execution_id"]))
            if action == "cancel":
                return ToolResult.success(self._cancel(kwargs["execution_id"]))
            if action == "continue":
                return ToolResult.fail(
                    {"code": "UNSUPPORTED_CAPABILITY", "capability": "continue"}
                )
            return ToolResult.fail(f"unknown action: {action}")
        except Exception as exc:
            logger.exception("[agent_hub] %s failed", action)
            return ToolResult.fail(str(exc))

    def _providers(self) -> dict[str, Any]:
        from core.services import agent_hub_gateway

        return agent_hub_gateway.invoke_ok("status_overview")
