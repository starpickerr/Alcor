---
name: stella-tasks
description: 管理 Stella 事务：新增、查询、完成、修改待办和重复任务，设置分类、截止时间或检查项。
---

# 事务

使用 `stella_business`，`domain="tasks"`。下面 JSON 都是该工具的完整入参；日期、时间和 ID 仅为示例，按用户实际意图填写。工具和页面共用数据，无需让用户到页面重新操作。

新增事务的业务字段必须包在 `arguments.payload` 中。日常状态使用 `todo`（未完成）或 `completed`（完成），不要把界面文字“待处理”当枚举。`due_on` 是日期，`due_time` 是当地 HH:MM，`timezone` 是 IANA 时区。

```json
{"domain":"tasks","method":"create_todo","arguments":{"payload":{"title":"每日复习","due_on":"2026-11-06","due_time":"20:00","timezone":"Asia/Shanghai","repeat_frequency":"daily","repeat_interval":1}}}
```

频率：`none` 一次、`daily` 每天、`weekly` 每周、`monthly` 每月、`yearly` 每年。自定义每两周用 `weekly` 和 `repeat_interval:2`；周几用 `repeat_days:[0,2]`（周一、周三；0–6）。重复任务必须有 due_on，可用 repeat_until 指定结束日期。重复提醒使用 due_time；不要额外设置 remind_at，也不要预先创建每次重复的独立事务。

查询与定位：

```json
{"domain":"tasks","method":"list_todos","arguments":{"include_completed":false}}
```

从返回结果取得真实 ID。`get_todo` 传 todo_id；`update_todo` 传 todo_id 和 payload（仅修改字段）；`complete_todo` 传 todo_id 和 completed 布尔值。重复事务的完成由服务推进下一次，不把原序列直接改成永久完成。

分类用 categories 查询，create_category 的名称等字段放在 payload；set_todo_category 传 todo_id/category_id。子任务和依赖只在用户提出时设置。字段不明确时查询单个方法：

```json
{"domain":"tasks","method":"catalog","arguments":{"method":"update_todo"}}
```

修改、删除先定位唯一目标；失败或超时先查已保存对象再决定是否重试，不能重复创建。Apple 来源以服务返回的同步结果为准，保存本地不代表已写入 Apple。
