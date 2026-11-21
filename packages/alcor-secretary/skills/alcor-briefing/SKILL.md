---
name: alcor-briefing
description: 生成或更新每日简报，查看简报历史和来源、管理内容偏好和补充要求，将简报条目转为事务或日程。
---

# 简报

用户要求今日简报时优先调用 `alcor_generate_briefing({})`。它使用插件当前开启的邮箱/学校账号、本机来源偏好，启动简报任务，返回 requestId 和 phase；仅有待解读材料时才创建执行会话。所有待解读材料一次提交，各条结论仅引用本条原文；启动成功不等于简报已生成。不要先自行全量读邮件或课程，再拼成伪造的来源数据。

定时任务也通过同一入口读取已开启的外部与本机来源及已保存范围，无需逐次手动授权；定时调用会等待实际保存结果。来源开关、账号停用和范围撤销仍在运行中复核。

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

列表返回 items、total、next_offset。每页默认 20 条，在 arguments 中传 offset 和 limit（1 至 50）翻页。按 ID 查询详情。

用返回的 briefing id 调用 get_briefing(briefing_id)，list_revisions(briefing_id) 查看版本。需要含补充材料或指定日期时：

```json
{"domain":"briefing","method":"generate","arguments":{"date":"2026-11-06","notes":"请重点关注本次新增的截止时间"}}
```

示例日期须替换；父会话和操作标识由工具绑定，不编造 parentSessionId。邮箱可用性统一由邮件插件中的账号开关控制，邮箱开启只表示插件可读取，简报还受独立来源开关约束；两层都开启才会使用。来源开关位于简报设置卡片中，Agent 不得调用 configure_source_groups 自行启用。学校账号随插件启停，课程范围在学校插件内设置；本机来源也在简报设置卡片中启停。简报设置卡片展示聚合来源、独立开关并提供插件入口；不要调用 configure_sources 或偷偷补入未选的本地来源。没有所选来源时说明缺少配置，不自动启用其他来源。

简报每天一份。采集从上次成功检查点继续，最早到简报时区的前一日零点。重复运行整合同日内容；没有新增时保留正文，只记录成功检查点。部分来源采集失败时，其余来源照常生成。摘要应注明工具返回的 coverageWarning，下一次更新会补采未覆盖来源。整次失败或取消后可继续原运行。不要自行重置游标或加载全部历史“修复”失败。生成失败要如实报告；旧失败版本保留不代表新查询也失败。

规则用 create_rule，payload 至少含 text、source:"*"、enabled:true。将条目转事务/日程先读 get_briefing 的实际条目和已有 action 关联，再查单方法 catalog：

```json
{"domain":"briefing","method":"catalog","arguments":{"method":"update_action"}}
```

不要用 run_pipeline、直接写快照或游标代替正常生成；建议不等于已经创建事务或设置提醒。


## 内容偏好

用户可以通过对话开启、关闭或调整简报中的六类 eLearning 内容。先用 get_preferences 查询实际值，再用 update_preferences 部分更新 payload.elearning_policy；只传用户要改的类别，核对工具返回结果后报告已保存。该设置决定简报收录内容。

| 类别 | 字段 | 程序处理值 | 其他值 |
| --- | --- | --- | --- |
| 公告 | announcement | include，完整正文及图片附件引用 | exclude 关闭；model 按正文与补充要求判断 |
| 作业 | assignment | pending，仅未交且未过期 | exclude 或 model |
| 测验 | quiz | pending，仅未完成且未过期 | exclude 或 model |
| 单项成绩 | grade | published，已公布的作业／测验／考试成绩，含零分与改分 | exclude 或 model |
| 课程总评 | course_grade | include，包含总评与评分组小计 | exclude 或 model |
| 课程文件 | file | grouped，同一学校账户的文件更新汇总 | exclude 或 model |

例如「关闭公告，保留每项作业的分数，关闭课程总分」：

```json
{"domain":"briefing","method":"update_preferences","arguments":{"payload":{"elearning_policy":{"announcement":"exclude","grade":"published","course_grade":"exclude"}}}}
```

已提交或过截止时间的作业仍可能出分，grade 独立于 assignment。设置生效后下一次生成会重新评估当前日的候选，来源游标按正常采集流程推进。源字段不足时允许剩余原文进入同一次模型批次。

邮件判断及需要理解正文的例外仍可通过 create_rule / update_rule / delete_rule 管理。先 list_rules 查看已有要求，保留用户的具体内容，避免同时写入与收录设置冲突的重复自然语言条件。对话管理内容偏好复用页面相同服务，无需用户切换到页面操作。


内容偏好页面只展示开关和实际收录条件。用户口述特殊条件（例如单项成绩只提醒低于 60 分）时，先查询已有补充要求，保存明确限定到该类别的原文要求，再将该类别设置为 model。核对两步结果；若有一步失败，报告实际保存情况并修复。用户说恢复默认条件时，将该类别恢复表中的程序处理值。页面通过「查看要求」查看原文，支持恢复默认条件。不要让用户先判断使用哪种处理方式。
