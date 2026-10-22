# 日志工作台

2026-10-22。本文记录本次日志优化实现；日志用于诊断，业务正文和凭据不应写入日志。用户在系统区打开「日志」，先按功能或错误级别缩小范围，再搜索消息、查看诊断详情；暂停后可加载历史，导出当前已加载的筛选结果。

## 当前链路

`common.log` 保留原文本格式、统一脱敏和命名 logger。独立 Python 使用 5 MiB、3 个备份的轮转 FileHandler；Electron 模式不创建 Python 文件 handler，主进程 `RuntimeLog` 是桌面唯一日志文件写入者，镜像后端输出并接收 renderer/updater 诊断。新桌面事件为含 timestamp/level/source/component/message/location 的 JSON 行。旧日志兼容，不迁移现有文件。

插件 runner 将标准 logging 记录编码为 stderr 上的 `@stella-log:` v1 诊断帧。worker 导入 `common.log` 时同样只安装诊断帧 handler，不创建文件或额外控制台 handler。host 分块读取、限制帧长、验证字段和级别，绑定真实 plugin_id 并再次脱敏；原始 stderr/print 仍丢弃，不能借此直接写入任意正文。worker 日志默认 INFO，host 的插件拒绝和 failed/unknown 结果记录方法及错误码，不记录请求参数、返回正文。MCP stderr 保留可识别的实际级别，无法识别时为 WARNING；超过长度限制的行记录省略摘要。独立 MCP Server 自己设置的日志级别仍决定哪些记录被发出。

## 查询与界面

`common/log_records.py::query_logs` 只读取 host 根目录的 run.log 与 .1/.2/.3，拒绝链接与非普通文件；桌面写入也检查文件类型并使用非阻塞打开，日志路径不可用时不阻断业务。兼容旧日志、JSON 桌面事件、shell 标记及异常堆栈。完整的有界记录先脱敏再截断展示，损坏的结构化记录安全省略。仅返回脱敏后的字段，未知格式标为 UNKNOWN，不从消息中任意猜测严重级别。时间按本地时区解释旧无时区文本，JSON 保留原时间。

`GET /api/logs/entries` 沿用 `_require_auth`，支持 q、levels（逗号分隔）、source、since（ISO 时间）、cursor、limit（1–200，默认 100）。单次扫描最多最近 4 MiB，并返回 truncated 和 next_cursor；继续加载会向更早记录扫描。窗口开头的孤立堆栈不作为新记录发布，下一页按最早完整记录边界回读；超过扫描窗口的单行可省略，但仍返回前进游标以保留旧历史可达性。游标绑定设备、文件标识和字节位置，追加和正常 rename 轮转不会改变已有记录；游标所在文件已被清理则提示刷新。每条详情最多 16 KiB 字符窗口，未完成的尾行待下一次写完整后显示。来源菜单的动态统计只用于发现当前已扫描来源，不表示全文件总量。

页面每 3 秒查询一次，最多显示 1000 条；搜索防抖，切换条件取消旧请求。最近时间范围随实时查询滚动，加载历史沿用当前快照的时间下界。暂停、查看详情、加载历史时保留快照；过滤、刷新或恢复实时会重新查询。窄屏沿用应用可折叠侧栏，功能筛选改为下拉框，日志按两行排列。详情面板从标题栏下方展开，不锁住日志列表；关闭按钮、Escape 和再次点击同一条日志都能关掉它。复制按钮在日志消息旁边，复制该条诊断文本。

旧 `/api/logs` SSE 与 `/api/logs/download` 保留，输出再次脱敏。新「导出结果」导出当前实际加载的记录为 JSONL，包含筛选后的详情；不声称导出了全部历史。历史文件不存在或不可读不会影响业务运行。

## 验证和限制

`tests/test_log_records.py` 覆盖解析、中文检索、堆栈、追加/轮转分页、扫描边界、秘密及非法游标；`test_log_api.py` 覆盖鉴权、查询与旧下载；`test_plugin_diagnostics.py` 覆盖真实 worker、原始 stderr 隔离、帧大小和 MCP 级别；`runtimeLog.test.mjs` 覆盖桌面轮转、分块与脱敏；`ui_logs.py` 验证浏览器桌面/窄屏搜索、筛选、暂停恢复、历史、详情、导出、错误恢复与主题。

这是本地诊断工作台，不提供跨机器集中采集或全文索引；持续高吞吐日志按保留窗口轮转，不保证永久保存。插件必须发出标准诊断或在结果中返回失败才能被记录，静默实现不会自动产生完整业务流水。未知敏感正文不能依赖正则自动安全化，业务仍须遵守不记录凭据和正文的规则。

设计参考：[Grafana Logs](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/logs/) 的级别、详情和有界显示，以及 [Datadog Log Side Panel](https://docs.datadoghq.com/logs/explorer/side_panel/) 的日志详情检查。
