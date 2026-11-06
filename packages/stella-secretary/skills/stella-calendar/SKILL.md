---
name: stella-calendar
description: 管理 Stella 日程：查询时间安排、新建或修改事件、分类、重复日程，以及导入节日和纪念日。
---

# 日程

通过 `stella_business` 的 calendar 域操作。与事务不同，新建事件的字段直接放在 arguments 中，不加 payload。时间必须是含时区的 ISO 8601，先按用户时区解释“今天、明天”；示例日期须替换为实际日期。

```json
{"domain":"calendar","method":"create_event","arguments":{"title":"项目例会","start_at":"2026-11-06T09:00:00+08:00","end_at":"2026-11-06T10:00:00+08:00","repeat_frequency":"weekly","repeat_days":[4],"repeat_timezone":"Asia/Shanghai"}}
```

repeat_days 为 0–6（周一到周日）；不重复用 repeat_frequency:none。仅有截止时间的作业应建事务，不编造会议起止时间。全天事件使用 all_day:true，结束日期不含当天；连续两天须以第三天零点为 end_at。

```json
{"domain":"calendar","method":"list_events","arguments":{"start":"2026-11-06T00:00:00+08:00","end":"2026-11-07T00:00:00+08:00"}}
```

先查询实际 ID；get_event/delete_event 传 id；update_event 传 id 和 patch。分类 categories 返回真实分类 ID 和颜色，set_event_category 传 id/category_id。重复事件的某次例外先查 catalog 的 exclude_occurrence，不把“只取消这一次”当成删除整个系列。

```json
{"domain":"calendar","method":"catalog","arguments":{"method":"update_event"}}
```

用户提供 CSV/ICS 日期文件时用专用 `stella_import_dates`，参数 path 为绝对路径，可选 timezone。它支持全天日期与简单年度重复，不支持任意带时间的 ICS 事件。CSV 列为 title,date，可选 annual,description,uid；最多 1MB/500 条。

Apple 同步由 Apple 插件维护；创建或修改日程只报告服务实际返回的结果，不用脚本直接改库或伪造同步成功。
