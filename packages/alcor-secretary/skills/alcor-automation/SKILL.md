---
name: alcor-automation
description: 安排 Alcor 定时执行：每天、每周、间隔、自定义计划，查询、暂停、恢复或编辑自动化。
---

# 自动化

用 `alcor_business` 的 automation 域；实际调度器由 DSH 管理。事务的重复频率只定义待办重复，不等于定时唤醒 Agent。

```json
{"domain":"automation","method":"create","arguments":{"title":"每日事务回顾","prompt":"使用 alcor_review_tasks 回顾未完成事务并总结优先事项","timing":{"kind":"daily","daily":{"time":"20:00","time_zone":"Asia/Shanghai"}}}}
```

create 必填 title、prompt、timing。未指定 target 时由宿主创建 Alcor 会话，不编造会话 ID。不要传 scope:all。

timing 选择一种：after(after_seconds 正整数)、at(at 为含时区 ISO 字符串)、every(every_seconds)、daily(daily.time/time_zone)、weekly(weekly.time/time_zone/weekdays 为 1–7，周一至周日)、cron(cron.expression/time_zone) 或 rrule(rrule 字符串)。注意 weekly 的星期编号不同于事务/日程的 0–6。

```json
{"domain":"automation","method":"list","arguments":{}}
```

用返回的真实 id、updatedAt 管理计划。暂停/恢复调用 edit，arguments 为 id、action:"update"、patch:{active:false/true}，expectedUpdatedAt 使用读取到的值。删除暂不可用；立即触发用 run(id)，执行记录用 history(id,limit:20)。参数疑问先 catalog；冲突后刷新。

计划成功保存只代表安排成功。run 只代表发起执行，应查看 history 的状态和输出后再报告完成。自动化不能直接调用私人 MCP。定时简报调用 `alcor_generate_briefing({})`，与手动简报共用已开启的来源、有效账号和已保存范围（包括邮件、学校及其他简报来源插件），无需逐次手动授权。来源内容会交给本次固定模型；关闭来源或停用账号后，后续采集与生成仍受限制。

自动化删除（含清理已结束任务）暂不可用：公开宿主接口不能证明没有待投递或未知投递。不要用空历史、自动 allowed-once、owner 或版本号放行；不要绕过 Alcor 调原生删除，也不要把删除请求自动改为暂停。可告诉用户选择暂停，获其明确指令后再执行暂停。
