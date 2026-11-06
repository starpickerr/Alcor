---
name: stella-collaboration
description: 启动、查看或取消 Stella Agent 协作，设置有依赖关系的多步流程并读取实际执行结果。
---

# Agent 协作

简单委派用 `stella_business` 的 agent_hub 域：

```json
{"domain":"agent_hub","method":"start_job","arguments":{"title":"整理提供的材料","prompt":"基于用户在本次对话中提供的材料整理要点，明确缺失信息，不查询私人外部账号。"}}
```

父会话和唯一操作标识由工具绑定，不编造 id/parentSessionId。

```json
{"domain":"agent_hub","method":"list_jobs","arguments":{}}
```

start_job 返回已启动不等于完成。读取 list_jobs 的状态/输出，必要时再查询；失败或取消如实报告。cancel_job 传实际返回的 id。

用户明确要求依赖流程时才使用 create_task、set_workflow、run_workflow：从 create_task 的返回取得 taskId、participantId 和 revision，保存步骤的 stepId/dependsOn，再运行。使用单方法目录确认嵌套结构：

```json
{"domain":"agent_hub","method":"catalog","arguments":{"method":"set_workflow"}}
```

request_id 与 revision 用当前任务真实值。重试指定实际 step_id 和 retry:true；结果未知先检查已有结果，不自动重复外部动作。协作子 Agent 不继承私人 MCP 访问，不能把主对话可查询的学校或邮箱权限视为子 Agent 的权限。
