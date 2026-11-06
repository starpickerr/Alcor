---
name: stella-automation
description: 安排 Stella 定时执行：每天、每周、间隔、自定义计划，查询、暂停、恢复、编辑或删除自动化。
---

# 自动化

用 `stella_business` 的 automation 域；实际调度器由 DSH 管理。事务的重复频率只定义待办重复，不等于定时唤醒 Agent。

```json
{"domain":"automation","method":"create","arguments":{"title":"每日事务回顾","prompt":"使用 stella_review_tasks 回顾未完成事务并总结优先事项","timing":{"kind":"daily","daily":{"time":"20:00","time_zone":"Asia/Shanghai"}}}}
```

create 必填 title、prompt、timing。未指定 target 时由宿主创建 Stella 会话，不编造会话 ID。不要传 scope:all。

timing 选择一种：after(after_seconds 正整数)、at(at 为含时区 ISO 字符串)、every(every_seconds)、daily(daily.time/time_zone)、weekly(weekly.time/time_zone/weekdays 为 1–7，周一至周日)、cron(cron.expression/time_zone) 或 rrule(rrule 字符串)。注意 weekly 的星期编号不同于事务/日程的 0–6。

```json
{"domain":"automation","method":"list","arguments":{}}
```

用返回的真实 id、updatedAt 管理计划。暂停/恢复调用 edit，arguments 为 id、action:"update"、patch:{active:false/true}，expectedUpdatedAt 使用读取到的值。删除调用 edit，action:"delete"；立即触发用 run(id)，执行记录用 history(id,limit:20)。参数疑问先 catalog；冲突后刷新。

计划成功保存只代表安排成功。run 只代表发起执行，应查看 history 的状态和输出后再报告完成。自动化不能读取私人 MCP；定时简报只使用已选的本地来源，不自动继承手动查询的外部来源访问。
