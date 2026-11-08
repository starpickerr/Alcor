---
name: alcor-bills
description: Alcor 记账、查询收支、修改流水、账单汇总，导入微信或支付宝账单文件及导出 CSV。
---

# 账单

用 `alcor_business` 的 bills 域操作已有账本。金额、名称、收支方向不明确时先澄清；余额、预算、汇总不自动作为新流水。

新建流水字段直接放在 arguments 中，不加 payload。新版使用整数 amount_minor：CNY 的 13.90 元是 1390 分，JPY 按整数日元；不能传旧版 amount 元字段或浮点分。entry_type 为 expense/income，金额为正数；currency 用实际币种，status 通常 posted，明确待入账时才用 pending。日期时间用带时区 ISO 8601，按用户时区解释相对日期，示例仅演示格式。

```json
{"domain":"bills","method":"create_ledger","arguments":{"name":"咖啡","amount_minor":1390,"currency":"CNY","entry_type":"expense","occurred_at":"2026-11-06T09:00:00+08:00","category":"餐饮","note":"","status":"posted"}}
```

name 简洁表示交易，note 记录用户提供的商家、方式或用途；不猜商家，不使用旧版 counterparty 字段。分类优先沿用已有口径。

```json
{"domain":"bills","method":"list_ledger_between","arguments":{"start":"2026-11-01","end":"2026-11-30"}}
```

流水查询区间 start/end 为 YYYY-MM-DD，包含两端。汇总用 ledger_report；月/年度报表用 statement_reports(period_type:monthly/yearly,year)。先定位唯一流水，从 get_ledger/list 返回取得真实 id/revision；update_ledger 传 id、patch、expectedRevision，delete_ledger 传 id、expectedRevision。冲突时刷新，不覆盖他人修改。未知参数可查单方法：

```json
{"domain":"bills","method":"catalog","arguments":{"method":"update_ledger"}}
```

导入用户文件用 `alcor_import_bills({paths:[绝对路径]})`；仅在用户要求预览时传 dry_run:true。支持微信 XLSX 和支付宝 CSV，无需进入页面，也不要自行解析或粘贴账单内容。结果区分 imported、duplicate、skipped 与 warnings。

导出用 `alcor_export_bills({path:绝对.csv路径,start:日期,end:日期})`，不会覆盖已有文件。重复请求或超时先核对已有记录/操作结果，不能重复记账；仅根据真实返回报告成功。
