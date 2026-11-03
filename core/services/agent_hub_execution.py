"""Shared Agent Hub execution entry for tools and the collaboration page."""
from pathlib import Path
from typing import Any
from contextvars import copy_context
import threading

_OBSERVE_LOCK = threading.RLock()
_MONITORS = set()

def observe_serialized(method):
    def wrapped(*args, **kwargs):
        with _OBSERVE_LOCK:
            return method(*args, **kwargs)
    return wrapped


class AgentHubExecution:
    def _execution_service(self):
        from common.state_dir import executions_dir
        from core.execution import execution_service

        root = executions_dir(ensure=True)
        return execution_service(db_path=root / "executions.db", work_root=root / "work")

    def _submit(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        goal = (kwargs.get("goal") or "").strip()
        if not goal:
            raise ValueError("goal is required for submit")
        task_kind = kwargs.get("task_kind") or "general.delegate"
        wall = int(kwargs.get("max_wall_time") or 180)
        if not 5 <= wall <= 3600:
            raise ValueError("max_wall_time must be between 5 and 3600")
        svc = self._execution_service()
        created = svc.create(
            owner_plugin_id="stella.agent-hub",
            task_kind=task_kind,
            goal=goal,
            budget={"maxWallTime": wall, "maxAttempts": 1, "maxConcurrent": 1},
            acceptance={**(kwargs.get("acceptance") or {}), **({"hubRunConfig": kwargs["run_config"]} if kwargs.get("run_config") else {})},
            execution_id=kwargs.get("execution_id"),
        )
        try:
            return self._start_execution(svc, created, kwargs)
        except Exception as exc:
            eid = created["executionId"]
            row = svc.get(eid)
            if row["status"] == "queued":
                row = svc.transition(eid, "failed", error={"message": str(exc)})
            elif row["status"] in {"running", "cancel_requested"}:
                row = svc.transition(eid, "outcome_unknown", error={"message": str(exc)})
            return {"executionId": eid, "status": row["status"], "error": {"message": str(exc)}}

    def _start_execution(self, svc, created: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        from core.workspace.service import WorkspaceService
        from core.services import agent_hub_gateway
        eid = created["executionId"]
        goal, task_kind = created["goal"], created["taskKind"]
        # Select provider (may raise if none ready — still return executionId queued).
        selector = {}
        if kwargs.get("provider_config_id"):
            selector["providerConfigId"] = kwargs["provider_config_id"]
        if kwargs.get("provider_local_id"):
            selector["providerLocalId"] = kwargs["provider_local_id"]
        if kwargs.get("agent_id"):
            selector["agentId"] = kwargs["agent_id"]
        if kwargs.get("access_mode"):
            selector["accessMode"] = kwargs["access_mode"]
        try:
            agent_hub_gateway.apply_user_preferences()
            selection = {"selector": agent_hub_gateway.resolve_provider_selector(selector or None) or None}
            from core.security.approvals import agent_hub_operation_scope
            with agent_hub_operation_scope(kwargs, "select_provider", selection):
                provider = agent_hub_gateway.invoke_ok("select_provider", selection)
        except Exception as exc:
            svc.transition(eid, "failed", summary="no ready provider", error={"message": str(exc)})
            return {
                "executionId": eid,
                "status": "failed",
                "error": {"code": "no_provider", "message": str(exc)},
            }

        lease = WorkspaceService.allocate_lease(
            work_root=Path(svc.work_root),
            execution_id=eid,
            attempt_id=created["attemptId"],
            source_project=None,
        )
        svc.attach_workspace(eid, lease["workspaceLeaseRef"], lease["path"])
        svc.transition(eid, "running")
        start_payload = {
            "provider_config_id": provider["providerConfigId"],
            "task": {
                "executionId": eid,
                "attemptId": created["attemptId"],
                "goal": goal,
                "taskKind": task_kind,
                "workspacePath": lease["path"],
                "workspaceLeaseRef": lease["workspaceLeaseRef"],
                "budget": created["budget"],
                "acceptance": created.get("acceptance") or {},
                "runConfig": (created.get("acceptance") or {}).get("hubRunConfig", {}),
            },
        }
        from core.security.approvals import agent_hub_operation_scope
        try:
            with agent_hub_operation_scope(kwargs, "provider_start", start_payload):
                start = agent_hub_gateway.invoke_ok("provider_start", start_payload)
        except PermissionError as exc:
            # Host admission rejected before dispatch. A later observation or
            # artifact error cannot make this claim about an already-started run.
            svc.transition(eid, "failed", error={"message": str(exc)})
            return {"executionId": eid, "status": "failed", "error": {"message": str(exc)}}
        if start.get("status") != "started":
            err = start.get("error") or {"message": "provider rejected start"}
            svc.transition(eid, "failed", summary="provider start rejected", error=err)
            return {"executionId": eid, "status": "failed", "error": err, "provider": provider}

        row = self._observe_execution(svc, eid)
        self._start_monitor(svc, eid)
        return {
            "executionId": eid,
            "status": row["status"],
            "providerConfigId": provider["providerConfigId"],
            "providerLocalId": provider["providerLocalId"],
            "externalRunRef": start.get("externalRunRef"),
            "artifactRefs": row.get("artifactRefs") or [],
        }

    @observe_serialized
    def _observe_execution(self, svc, execution_id: str) -> dict[str, Any]:
        from core.execution.artifacts import seal_artifact
        from core.execution.store import TERMINAL
        from core.services import agent_hub_gateway

        row = svc.get(execution_id)
        if row["ownerPluginId"] != "stella.agent-hub":
            raise PermissionError("execution does not belong to Agent Hub")
        if row["status"] in TERMINAL:
            return row
        try:
            mapping = agent_hub_gateway.invoke_ok("get_external_run", {"execution_id": execution_id})
            if not mapping:
                return row
            stored = svc._store.events(execution_id)
            cursor = max((e['safePayload'].get('cursor', 0) for e in stored), default=0)
            events = agent_hub_gateway.invoke_ok("provider_observe", {"execution_id": execution_id, "after_cursor": cursor})
            mapping = agent_hub_gateway.invoke_ok('get_external_run', {'execution_id': execution_id})
            provider = agent_hub_gateway.invoke_ok('get_provider', {'provider_config_id': mapping['providerConfigId']}) if mapping else {}
            from core.services.agent_hub_events import classify_output
            for event in events or []:
                if event.get('type') != 'output' or event['cursor'] <= cursor:
                    continue
                for line in event['text'].splitlines():
                    kind, payload = (classify_output(provider.get('providerLocalId'), line) if event.get('structured', True) else ('process', {'text': line[:65536]}))
                    svc.append_event(execution_id, kind, payload)
                svc.append_event(execution_id, 'output_cursor', {'cursor': event['cursor']})
                cursor = event['cursor']
        except Exception as exc:
            return svc.transition(execution_id, "outcome_unknown", summary="执行观察失败，请确认结果后重试", error={"message": str(exc)})
        terminal = next((event for event in reversed(events or []) if event.get("type") == "terminal"), None)
        if not terminal or terminal.get("status") not in {*TERMINAL, "outcome_unknown"}:
            return row
        if terminal.get("outputPath"):
            output = Path(terminal["outputPath"]).resolve()
            workspace = Path(row["workspacePath"]).resolve()
            if not output.is_relative_to(workspace):
                raise PermissionError("provider output escapes execution workspace")
            artifact = seal_artifact(store_root=Path(svc.work_root).parent / "artifacts",
                                     source_path=output, execution_id=execution_id)
            svc.attach_artifacts(execution_id, [artifact["artifactRef"]])
        return svc.transition(execution_id, terminal["status"],
                              summary=terminal.get("summary"), error=terminal.get("error"))

    def _start_monitor(self, svc, execution_id):
        with _OBSERVE_LOCK:
            if execution_id in _MONITORS:
                return
            _MONITORS.add(execution_id)
        def collect():
            import time
            deadline = time.monotonic() + svc.get(execution_id)['budget']['maxWallTime'] + 15
            try:
                while time.monotonic() < deadline:
                    from core.execution.store import TERMINAL
                    if self._observe_execution(svc, execution_id)['status'] in TERMINAL | {'outcome_unknown'}:
                        return
                    time.sleep(.25)
            finally:
                with _OBSERVE_LOCK:
                    _MONITORS.discard(execution_id)
        threading.Thread(target=copy_context().run, args=(collect,), name='hub-output-collector', daemon=True).start()

    def _status(self, execution_id: str) -> dict[str, Any]:
        if not execution_id:
            raise ValueError("execution_id required")
        svc = self._execution_service()
        row = self._observe_execution(svc, execution_id)
        events = list(svc.watch(execution_id))
        mapping = None
        try:
            from core.services import agent_hub_gateway

            mapping = agent_hub_gateway.invoke_ok(
                "get_external_run", {"execution_id": execution_id}
            )
        except Exception:
            mapping = None
        from core.execution.artifacts import resolve_artifact
        artifacts = []
        for ref in row.get("artifactRefs") or []:
            artifact = resolve_artifact(store_root=Path(svc.work_root).parent / "artifacts",
                                        artifact_ref=ref, execution_id=execution_id)
            with Path(artifact["path"]).open("rb") as stream:
                content = stream.read(65537)
            artifacts.append({key: value for key, value in artifact.items() if key != "path"} |
                             {"text": content[:65536].decode("utf-8", errors="replace"), "truncated": len(content) > 65536})
        return {"execution": row, "watch": events, "external": mapping, "artifacts": artifacts}

    def _cancel(self, execution_id: str) -> dict[str, Any]:
        if not execution_id:
            raise ValueError("execution_id required")
        svc = self._execution_service()
        if svc.get(execution_id)["ownerPluginId"] != "stella.agent-hub":
            raise PermissionError("execution does not belong to Agent Hub")
        from core.execution.store import TERMINAL
        if svc.get(execution_id)["status"] in TERMINAL:
            return {"execution": svc.get(execution_id), "provider": {"status": "already_finished"}}
        ack = svc.request_cancel(execution_id)
        provider_ack = {"status": "unknown"}
        try:
            from core.services import agent_hub_gateway

            provider_ack = agent_hub_gateway.invoke_ok(
                "provider_cancel", {"execution_id": execution_id}
            )
        except Exception as exc:
            provider_ack = {"status": "error", "message": str(exc)}
        return {"execution": ack, "provider": provider_ack}
