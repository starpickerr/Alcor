# 原生运行日志

入口是 Alcor 左侧「系统 → 日志」。在顶部选类别或搜索关键词，从表头的时间和来源列直接筛选。页面支持展开详情、复制和加载更早记录。刷新入口沿用其他页面的图标按钮。页面隐藏时不轮询，加载历史时暂停自动刷新；点击刷新返回最新记录。

日志由当前 Host 的 Cordis exporter 收集，使用 DSH storageDomain `alcor_logs` 保存。使用同一 Host 日志接口的插件会显示为独立来源。宿主对话执行失败时可按「DSH 对话」、会话编号或错误码查找，通用工具执行失败可按「DSH 工具」查找。邮件与 eLearning 的专用诊断保留各自的来源。模型和页面复用 `alcorSecretary.logs`。

`alcor_read_logs` 是只读模型工具，支持同页面的 `category`（success/failure/error/warn/info/debug）、`query`（最多 200 字符）、`source`（来源名称）、`since`（UTC ISO 时间）、`before`（上一页返回的 nextBefore），以及原始 `level`、`outcome` 筛选。每次最多返回 100 条，倒序排列；只有用户主动发起的当前主会话可调用，沿用宿主工具策略和当前轮次来源校验。日志内容是诊断数据，不构成执行指令；没有查到记录不证明操作成功。

最多保留 1,000 条，其中最多 100 条为调试记录。达到总上限时优先淘汰最旧的调试记录，并为最近的错误和警告各预留 100 条，较新的同级故障可替换旧故障。每条最多 4,000 字符，写入前过滤常见凭据、邮箱、URL 查询参数和用户目录。新增调用方仍须避免输出个人内容及原始业务参数，过滤不能代替调用方的数据控制。业务 worker 的失败日志只增加安全操作名、错误类别和生成的请求编号。

业务 worker 的成功结果记录为信息级别，包含安全操作名、耗时和请求编号，不记录参数及返回内容。内部跨服务细节使用调试级别；失败仍按错误级别记录。

来源按实际插件归属分为 Alcor：事务、Alcor：日程、Alcor：简报、Alcor：账单、Alcor：自动化、Apple 同步、eLearning和邮件；Alcor 用于生命周期和未归属业务的诊断。筛选始终提供完整分类，无记录的分类显示空结果。历史合并来源无法可靠拆分的记录归入 Alcor，保留原始消息。

类别默认显示常用日志，可切换全部或选择某一类别。每条记录只有一个类别。调试记录显示为调试，其他记录优先显示明确的执行结果（成功或失败），普通诊断按原始级别显示为错误、警告或信息。

列表展示时间、类别、来源和操作或消息。原始级别、请求编号和完整原文在详情中，有采集耗时的记录也在详情显示。查询返回匹配总数 `matched`，条目可包含 `outcome`、`operation`、`durationMs`、`requestId`；磁盘记录格式保持兼容。

日志延迟约一秒串行保存，正常关闭会等待保存完成；进程崩溃时最后未落盘的记录可能丢失。日志存储初始化或保存失败时业务继续运行，页面标记保存失败；不能将此状态解释为已持久化。采集启动前或没有经过该日志接口的输出不可查询。

验证入口：`node --test product/tests/runtime-logs.test.mjs product/tests/alcor-business-principal.test.mjs product/tests/plugin-error-diagnostics.test.mjs`；隔离官方桌面验证 `product/tests/runtime-logs-ui-check.py`，环境沿用 `official_desktop_check.py`。
