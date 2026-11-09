---
name: alcor-briefing
description: 生成或更新每日简报，查看简报历史和来源、管理筛选规则，将简报条目转为事务或日程。
---

# 简报

用户要求今日简报时优先调用 `alcor_generate_briefing({})`。它使用插件当前开启的邮箱/学校账号、本机来源偏好与本次模型，启动独立简报任务，返回 requestId、执行会话 sessionId 和 phase；启动成功不等于简报已生成。不要先自行全量读邮件或课程，再拼成伪造的来源数据。

查询已选来源：

```json
{"domain":"briefing","method":"list_sources","arguments":{}}
```

查看当前运行进度（不返回原文检查点）：

```json
{"domain":"briefing","method":"generation_status","arguments":{}}
```

phase 为 completed 才能报告已生成；失败或中断时如实说明。用户要求继续/重试时调用 resume_generation，arguments 为实际 requestId；取消调用 cancel_generation，同样传 requestId。不要自动重跑中断任务或编造执行结果。未完成请求的模型消耗可能已发生，已完成来源和批次会复用。运行模型和来源范围固定，恢复时仍检查当前授权。

查看历史：

```json
{"domain":"briefing","method":"list_briefings","arguments":{}}
```

用返回的 briefing id 调用 get_briefing(briefing_id)，list_revisions(briefing_id) 查看版本。需要含补充材料或指定日期时：

```json
{"domain":"briefing","method":"generate","arguments":{"date":"2026-11-06","notes":"请重点关注本次新增的截止时间"}}
```

示例日期须替换；父会话和操作标识由工具绑定，不编造 parentSessionId。邮箱可用性统一由邮件插件中的账号开关控制，邮箱开启只表示插件可读取，简报还受独立来源开关约束；两层都开启才会使用。来源开关位于简报设置卡片中，Agent 不得调用 configure_source_groups 自行启用。学校账号随插件启停，课程范围在学校插件内设置；本机来源也在简报设置卡片中启停。简报设置卡片展示聚合来源、独立开关并提供插件入口；不要调用 configure_sources 或偷偷补入未选的本地来源。没有所选来源时说明缺少配置，不自动启用其他来源。

简报每天一份。采集从上次成功检查点继续，最早到简报时区的前一日零点。重复运行整合同日内容；没有新增时保留正文，只记录成功检查点。失败或取消后可继续原运行。不要自行重置游标或加载全部历史“修复”失败。生成失败要如实报告；旧失败版本保留不代表新查询也失败。

规则用 create_rule，payload 至少含 text、source:"*"、enabled:true。将条目转事务/日程先读 get_briefing 的实际条目和已有 action 关联，再查单方法 catalog：

```json
{"domain":"briefing","method":"catalog","arguments":{"method":"update_action"}}
```

不要用 run_pipeline、直接写快照或游标代替正常生成；建议不等于已经创建事务或设置提醒。
