# Apple 同步

独立 DSH 原生插件 `@stella/apple-sync@0.1.4`，需要 DSH `0.2.2-alpha.3` 或 `0.2.2-rc.1`、Stella 秘书 `@stella/secretary >=0.1.0`、macOS、Node >=24.9。官方 DSH Desktop 使用自带 Python；Web 开发预览需要 Python >=3.9。构建系统桥需要 Swift Command Line Tools。由 DSH 插件管理器安装、停用和卸载。

在 **Plugins → @stella/apple-sync** 的配置页中，日历和提醒事项各提供 **同步**、**暂停同步** 两个常驻按钮。首次点击同步会请求对应系统权限，自动纳入全部日历／清单，立即同步并持续跟踪新来源。按来源 ID 保留映射，自动匹配或创建同名 Stella 分类／清单；同名来源使用独立分类，避免混合。可写来源双向同步，只读来源仅导入并在界面提示。

日历覆盖全部历史到未来两年，以不超过三年的区间分段读取，避免 [EventKit 四年查询上限](https://developer.apple.com/documentation/eventkit/ekeventstore/predicateforevents(withstart:end:calendars:))截断历史。重复日程按实例导入；每个来源超过 20,000 个实例时标记该来源读取失败，保留原有记录，不将截断结果作为完整数据。提醒事项读取全部现有记录（含已完成项目）。持续同步需保持 DSH 运行；Apple 变更通过 EventKit 通知触发，Stella 变更由后台轮询收敛。

对应分类／清单中的已有 Stella 记录也会同步到 Apple。同步期间双方增删改自动传播；Apple 删除需经过独立查询和至少一分钟的连续缺失核对，读取失败不触发删除，双方同时修改保留冲突。暂停保留映射、分类和记录，重启后维持暂停状态。旧版手选／单向接口继续兼容，点击新版同步后才切换到全部来源及自动删除模式。

事务和日程仍通过当前 Profile 的 Stella 业务服务读写，同步映射单独保存在 `<profile>/stella/apple-sync/`。首次启动使用 SQLite backup 从 `<profile>/stella/business/apple-{calendar,reminders}.sqlite3` 复制已有映射，保留原文件；之后新目录是唯一有效副本。不会扫描或迁移历史私人数据库。

停用／卸载会停止同步进程及系统桥，保留业务记录和映射，重装后恢复已保存连接。停用秘书时本插件随依赖停止。冲突通过配置页选择保留哪一侧；有 Stella 子事务的提醒事项不会自动级联删除，需先移动子事务。

Host 通过 JSON 回调使用 `stellaOrganizer.applePeer`，不直接打开秘书业务数据库。兼容工具入口 `stella_business` 的 `apple` 域代理至本插件。同步算法和 EventKit 源自 Stella-Core 的 `agent/apple`、`agent/calendar/apple_sync.py`、`agent/todos/apple_sync.py`、`native/apple-calendar`，保留 MIT 许可。无旧 Stella Application／Plugin Runtime 依赖。

验证使用合成 EventKit 数据；真实系统授权、账户同步和跨设备更新需用户连接后验证。
