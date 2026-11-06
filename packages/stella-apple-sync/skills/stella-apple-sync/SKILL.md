---
name: stella-apple-sync
description: 查看、启动或暂停 Apple 日历与提醒事项同步，区分系统授权、只读来源、本地保存与同步完成。
---

# Apple 同步

需当前安装并启用 Apple 同步插件。通过 `stella_business` 的 apple 域；每次传 kind:"calendar" 或 kind:"reminders"，避免默认操作错对象。

```json
{"domain":"apple","method":"status","arguments":{"kind":"calendar"}}
```

需要同步参数时先查询目录：

```json
{"domain":"apple","method":"catalog","arguments":{"kind":"reminders"}}
```

用户要求启动同步时用 start_sync，arguments 指定 kind；系统未授权时由原生授权流程请求，必须根据真实系统返回说明状态。用户要求暂停用 set_auto，arguments 为 kind 和 enabled:false；恢复是否需要重新 start_sync 依据 status，不能声称未运行的客户端仍在实时同步。

编辑事务或日程使用各自业务域，由同步插件传播变化。只读来源可导入，无法写回；保存本地成功不等于 Apple 已接受更改。批量冲突、删除确认参数仅在用户明确授权对应范围后按当前目录使用，不用 force、替换映射或清理链接掩盖同步问题。
