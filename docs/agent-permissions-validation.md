# 三档 Agent 权限交付记录

2026-10-24，Core 分支 `unified-agent-permissions`，基线 `bbbf5ccc`。用户最新三档模式及调用来源流程图取代先前逐能力/插件授权设计。当前行为见[权限与执行边界](security-permission-modes.md)；旧 permission-unification 验收与审查记录属于前一版实现，不能作为本轮语义说明。

## 已交付范围

- 默认、对话、后台任务统一使用只读、工作区写入、完全访问；所有模式均可调用内置工具、插件、MCP、Skill，操作仍受模式限制。
- 权限管理以三卡片编辑默认设置，后台任务在同页；移除 Agent 可用能力表及插件独立授权管理。保留连接/资源限制、版本冲突和历史策略恢复。
- 可信原生 UI 操作跳过 Agent 模式，UI 发起的协作保留来源；Agent/任务/子运行不能借用 UI 身份提权。
- 第一方信任校验固定包摘要，第三方执行业务前需用户信任当前摘要。实际 OS 隔离绑定所属数据目录，修复 Memory/Knowledge 等领域的 EPERM；不开放整个用户目录。
- Memory 正常进程不读取旧 Core 目录；迁移仅收到 Markdown 快照。Automation 通过 Core broker 获得当前用户/Agent 的任务副本，不能访问原始共享库、其他用户及 Core 执行/投递表。目标、所有权、撤销、取消检查通过后才发布变更。

实现提交：`c4798612`（后端）、`d7105632`（UI）、`dbfda106`（数据绑定/信任）、`7f527082`（历史恢复入口）；最终审查修复由本记录所在提交保存。Plugins `6fd762c` 无本轮修改，未合并 develop、未推送。

## 独立审查与修复

唯一一次全分支独立审查覆盖 `bbbf5ccc..7f527082`。0 Critical、3 Important、1 Minor，暂不裁定项为空。三项 Important 均在同一次修复阶段处理，未进行第二次审查。

| Important | 实际失败与修复证据 |
| --- | --- |
| Memory 旧目录暴露 Core 会话索引 | 实际隔离子进程可读取索引的失败已观察；普通及迁移探针修复后拒绝原索引，迁移仍能导入 Markdown |
| Automation 共享 SQLite 暴露其他用户和 Core 表 | 实际 worker 原能读取/删除其他用户行；改为按用户/Agent 的任务副本，Core 校验后提交。覆盖取消后不发布、篡改目标/所有权/符号链接拒绝、并发更新保留及原库迁移拒绝；固定句柄读取到内存 SQLite，拒绝 Unix 专用路径的模拟也可正常提交 |
| 原生 Data UI 被默认只读模式拦截 | 真实认证 HTTP configure/sync/cleanup 失败已观察；修复后通过。委托 MCP 与旧 Application 来源作用域分别复现第二次模式审核误拦后修复，明确连接拒绝仍生效 |

边界回归位于 `tests/test_permission_review_regressions.py`，原始六项均先失败后通过，取消、委托 MCP、旧 UI 来源及原库迁移另有各自失败到通过证据。

延期 Minor：`docs/architecture/data-service.md:38` 的简报协作段仍引用已退休的 `check_scoped`/独立 Grant，属于旧文档说明，不是运行权限来源。本轮按执行规则保留此项，未混入修复阶段。

## 实施裁定与代价

| 裁定及依据 | 判断错误的代价 |
| --- | --- |
| 最新明确要求取代逐能力/插件独立授权；统一按三档模式决定操作 | 旧逐能力选择不再独立拦截工具/插件；账号及资源限制保留 |
| 只读允许读取，工作区写入允许普通业务修改与受限命令，完全访问允许普通操作；敏感操作按模式确认/拒绝 | 用户可能对业务修改范围有不同预期，卡片说明及模式矩阵测试固定当前解释 |
| 本轮不推定先前“合并”授权延续到新设计 | 修复留在专用分支，需明确指令才更新 develop |
| 工作区模式下非文件系统的外部系统执行需确认，受限文件系统命令可执行 | 外部/后台执行可能需要确认或调整模式，不能依赖旧的隐藏能力预授权 |
| 原共享调度库继续归 Core，broker 仅发布当前用户/Agent 请求目标的任务变更 | 每次调用增加副本及提交开销，写事务可能产生有限等待；已覆盖取消与并发更新 |

## 验证及限制

最终 Core 命令：`python scripts/run_isolated.py python -m pytest tests -q --tb=short`（Stella Python 环境）。

所有 Python 验证使用 `scripts/run_isolated.py` 的临时 HOME/STELLA_DATA_DIR；未连接真实账号，未读写真实业务库。

| 检查 | 结果 |
| --- | --- |
| 最终 Core 全量 | 2374 passed / 22 skipped / 5 warnings，151.83 秒 |
| 最后原生 UI/MCP/Data/执行边界组 | 66 passed |
| Plugins Bills/Tasks/Calendar/Briefing 实际领域套件 | 101 passed |
| Node 测试 | 24 passed |
| 桌面 typecheck、ESLint、renderer/main build | PASS |
| 完整浏览器脚本 | 17 套 PASS；检查桌面、390px 窄屏与弹层截图 |
| 全分支变更 Python Ruff、git diff --check | PASS |

最后修复仅修改后端，前端/Plugins 未变化，复用本轮已核对的构建、浏览器及 Plugins 证据。构建保留现有字体/CSS 路径与 chunk 大小警告；Python 保留依赖弃用、ffmpeg 及线程 fork 等 5 项警告。22 项跳过涉及平台/冻结环境；实际 OS 探针只验证当前 macOS，Linux/Windows 实机与真实账号未验证。
