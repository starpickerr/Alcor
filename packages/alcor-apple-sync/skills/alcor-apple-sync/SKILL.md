---
name: alcor-apple-sync
description: 查看、启动或暂停 Apple 日历与提醒事项同步，区分系统授权、只读来源、本地保存与同步完成。
---

# Apple 同步

需当前安装并启用 Apple 同步插件。通过 `alcor_business` 的 apple 域；每次传 kind:"calendar" 或 kind:"reminders"，避免默认操作错对象。

```json
{"domain":"apple","method":"status","arguments":{"kind":"calendar"}}
```

需要同步参数时先查询目录：

```json
{"domain":"apple","method":"catalog","arguments":{"kind":"reminders"}}
```

用户要求启动同步时用 start_sync，arguments 指定 kind；系统未授权时由原生授权流程请求，必须根据真实系统返回说明状态。用户要求暂停用 set_auto，arguments 为 kind 和 enabled:false；恢复是否需要重新 start_sync 依据 status，不能声称未运行的客户端仍在实时同步。

编辑事务或日程使用各自业务域，由同步插件传播变化。只读来源可导入，无法写回；保存本地成功不等于 Apple 已接受更改。本地项目缺失时，用户明确选择恢复可用 `restore_local_item`，arguments 为 kind 和 identity（status 冲突的 alcor_id 或 todo_id）。仅从已关联 Apple 来源恢复缺失本地项目，不覆盖现有对象、不向 Apple 写入；后台定时任务不能代替用户选择。结果待核对时不会重复提交，再次选择先核已有回执。双方项目仍存在且绑定与版本明确时，`resolve_conflict` 使用 kind、alcor_id（日历）或 todo_id（提醒）和 choice。keep_apple 采用 Apple 内容更新本地，Apple 不变；keep_alcor 经具体内容审批后更新原 Apple 项目，不新建、移动或删除。来源/绑定有歧义或内容已变化须重新核对，不用 confirm、force、替换映射或清理链接绕过。操作结果未知时再次选择原处理方式只核已有回执，不重发；Apple 已成功但本地无提交回执时，保留两侧并在 Apple 核对已更新内容，不重复发送。
