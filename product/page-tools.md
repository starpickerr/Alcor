# 页面与对话使用同一业务服务

Stella 根据用户指令调用工具，页面展示与管理同一份持久数据。已支持的操作无需转去页面完成，也不增加一次页面确认。

| 页面 | 对话能力 | 入口 |
| --- | --- | --- |
| 事务 | 创建、编辑、完成、层级、依赖、重复、分类、检查项与提醒登记 | `stella_business` → `tasks` |
| 日程 | 日程、分类、重复与例外、特殊日期、节假日；读取日期文件导入 | `stella_business` → `calendar`；`stella_import_dates` |
| 账单 | 单条增改查删、冲正、日小计、月／年汇总及分类／排行；导入微信／支付宝文件，按日期范围导出 CSV | `stella_business` → `bills`；`stella_import_bills`；`stella_export_bills` |
| 简报 | 生成／更新、历史修订、来源快照、条目处理、建立或关联事务／日程、整理规则 | `stella_business` → `briefing`；`stella_generate_briefing` |
| Agent 协作 | 启动任务、查询状态与输出、取消；管理与执行已保存流程 | `stella_business` → `agent_hub` |
| 自动化 | 列表、新建、编辑、暂停／恢复、删除、立即运行、执行历史 | `stella_business` → `automation` |

调用领域方法前先读取该领域 `catalog`，取得参数与示例。协作 `start_job` 只表示启动，须通过 `list_jobs` 核对终态及输出；父会话由执行身份绑定。自动化只管理 Stella 所属计划，复用 DSH 唯一调度器。文件工具仅限用户主动主对话；自动化与协作的启动／管理也拒绝委托或定时回合，避免递归创建。

文件通过执行 Agent 的 DSH fs 读写。日期导入沿用页面的 UTF-8 CSV／ICS 全天日期和简单年度重复能力，最大 1 MB／500 条，不支持任意带时刻的 ICS；CSV 必需 `title,date`，可选 `annual,description,uid`。账单 CSV 导出要求绝对新文件路径及起止日期（含当天），保留准确金额与币种、状态，处理公式型文本，不覆盖已有文件；底层 XLSX 返回接口仍可查询，但本轮没有新增 XLSX 落盘工具。

账号登录、Apple 系统权限及私人信息来源授权由现有设置管理，不因对话调用扩大。工具覆盖不表示旧版全部功能或私人数据迁移已经完成，未迁移项继续以 [功能对照](feature-parity.md) 为准。

验证：`page-tools.test.mjs`、`secretary-tools.test.mjs`、`business-domain.test.mjs`；`page-tools-chat-check.py` 使用隔离 Profile、合成模型及文件验证六领域实际对话调用、持久结果、自动化暂停、协作完成及页面可访问。设置 `STELLA_CHECK_NATIVE_DESKTOP=1` 和 `STELLA_CHECK_DESKTOP_APP` 验证指定官方桌面产物。
