# 统一能力页面验证记录

> 此文保留当时实施与验证记录；现行标签、来源入口和管理接口见[统一能力详情验收](capability-details-sources-validation.md)。

2026-10-24，Core `capability-detail-pages`，从 develop 建立，并纳入已验证的桌面官方插件信任修复。仓库历史整理后对应基线 `966ae337`，官方信任修复 `04054690`，目录 API `0f450ad7`，列表/详情 `e700e879`，集成加固 `9e345fc6`；与整理前 `3371c0e7` 实现树相同，保留原验证证据。未改动 develop，未改动 Plugins。

## 交付内容与证据

列表提供全部、插件、MCP、Skill、内置工具五种筛选与搜索，整行打开详情；沿用 Stella 的 token、字体、圆角和明暗主题。统一详情头部仅提供启停操作，取消独立设置及更多按钮。内置工具只有概览，其他类型按实际登记内容显示功能、配置、运行或文档。

旧插件设置、邮箱/eLearning 与 Apple 集成书签转向同一能力详情，原配置组件及后台服务继续负责保存、CAS、密钥保护和身份检查。数据卡片直接进入能力配置，保留返回原页与滚动位置；事务、日程、简报业务页继续使用。Email/eLearning 保持真实 MCP 类型，插件拥有的工具列在插件功能中。

只读 `GET /api/capabilities` 聚合现有登记信息；带 kind/id 参数读取详情。它不启动扩展、不返回连接参数与环境变量，单类失败仍保留其他类别。README 限于已登记包内固定文件，拒绝逃逸 symlink，限制262144字节；读取前复查摘要及选择与元数据一致。启停与安装复用原有认证接口，没有新增权限/配置存储。

| 检查 | 结果 |
| --- | --- |
| 后端目录、启停、MCP、插件管理阶段门禁 | 64 passed |
| 新目录及选择变化边界 | 17用例；初始缺实现 RED，插件选择切换读取错误 README 另行实际 RED→GREEN |
| 完整 Core | 前阶段2401 passed /22 skipped /5 warnings；本轮修复后当前工作树2396 passed /22 skipped /5 warnings，178.39秒（另有并行质量脚本/测试维护，未纳入本任务提交） |
| Node | 24 passed |
| 桌面 typecheck、ESLint、renderer/main build | PASS |
| 完整浏览器脚本 | 18套 PASS；新增详情脚本另行检查390px、折叠侧栏与暗色 |
| 全分支变更 Python Ruff、git diff --check | PASS |

浏览器保留四类启停的失败、忙碌、持久化与键盘断言；新增搜索、过期读写、局部失败重试、安装、Markdown、配置草稿跨标签保留、CAS 冲突/合并及错误响应不导致页面崩溃。真实表单继续验证3邮箱、eLearning保存/按需同步、Apple映射及预览令牌、来源返回、嵌入Agent与插件更新。

所有 Python 与浏览器检查通过隔离 HOME/STELLA_DATA_DIR，使用合成数据与HTTP夹具，不连接真实账号或业务库。浏览器验证真实组件和请求载荷，不代表实际外部提供方登录成功。22项跳过及5项依赖/环境警告保留；未验证发布包及Linux/Windows实机。构建保留既有字体/CSS路径及chunk大小警告。

## 实施裁定及代价

1. 在新任务分支纳入待合并的官方桌面插件信任修复，以保证第一方详情/配置可用；代价是后续合并能力任务也会带入该独立修复，develop当前不变。
2. 缺少的分类解释为内置工具，Email/eLearning保留MCP，插件适配器归属插件；代价是示意图中的部分示例标签与实际页面不同，但与运行来源一致。
3. 统一的是能力配置页，保留业务操作页；代价是若原意包含删除业务导航，仍需另行调整，当前选择保护现有功能。
4. 添加入口复用catalog插件安装与既有Skill市场，不新建MCP运行命令配置协议；代价是任意MCP连接的新增仍使用原有配置流程。

最终独立审查：**SKIP**，唯一一次调用因额度不足未能执行，没有返回审查结论。依开发工作流的中断恢复规范，改为作者风险自查，不能视作独立审查通过。范围、发现及限制见 [差异自查记录](capability-details-review.md)。自查发现并修复固定详情插件从配置目录消失时错误选中其他插件的问题，浏览器测试实际 RED→GREEN；暗色截图使用真实 `cow_theme` 设置并重载验证。

修复后重新执行并确认退出码0：完整Core、18套UI、桌面typecheck、ESLint、renderer/main build，以及变更Python的Ruff和diff检查。Node24项为前阶段通过，本轮仅表单目标校验及浏览器夹具变化，复用该结果。

可复现入口（Python使用项目Stella环境）：

```sh
python scripts/run_isolated.py python -m pytest tests -q --tb=short
python scripts/run_isolated.py python scripts/ui_check.py
npm --prefix desktop run typecheck
npm --prefix desktop run lint
npm --prefix desktop run build
```

## 概述缺失修复（2026-10-24，初次方案已替换）

根因是8个第一方插件的已发布包缺少manifest简介和README，MCP目录未传递配置中的description，以及前端仅读取当前语言。初次方案曾在Core中补充第一方业务文案以兼容旧包；按用户纠正，这一归属方式已由下述包内元数据方案替换。空白README、本地化后备、MCP公开简介传递及工具用途展示的修复继续保留。

实际失败→通过：13项后端缺失/传递用例、1项空白README用例；浏览器先复现中文界面隐藏英文简介，修复后列表与详情均可读取。最终受影响Python回归117 passed /2 warnings（既有audioop/ffmpeg警告），完整18组UI、desktop typecheck/ESLint、Ruff和diff检查通过。Python范围为目录、MCP目录、工具目录、启停、默认MCP域与域路由；没有重复执行全量Core或构建，原有结果不作为本次代码的新增验证声明。沿用独立审查额度限制，以作者检查及上述回归提供证据。

## 描述归属修正（2026-10-24）

8个第一方插件的中英文简介由各自 `plugin.json` 提供，patch版本和 `catalog/official.json` 摘要同步更新。两个独立MCP在自己的Python包内维护 `capability.json`，patch版本升至1.0.1，既有package-data/collect-data规则负责发布。Core删除全部按具体插件/MCP ID映射的文案，仅加载已安装插件manifest/README或MCP包的公开元数据；缺失时显示通用提示，不借用新包文档装作旧包说明。

已有插件需按正常更新流程获取新元数据；没有修改真实用户已安装包、版本选择、管理状态或业务数据。MCP元数据加载不导入/启动服务，限65536字节，校验服务名称与包内路径，拒绝symlink；错误或缺失文件不阻断连接配置，额外字段不传入公开简介。

失败→通过：Core 8个特定ID仍硬编码文案用例及开发/冻结布局2个元数据加载用例先失败；Plugins 8个真实安装/目录加载用例先因包缺少description失败。最终Core相关回归155 passed /2既有警告，Plugins元数据集成8 passed；两组能力UI、Ruff及diff检查通过。临时签名ZIP实际打包安装8个插件后均能从包加载简介；两个MCP的wheel实际构建且包含元数据，Core可直接读取。冻结目录由夹具验证，未重新构建PyInstaller产物或发布应用；未重复全量Core、前端类型/构建检查（前端代码未变）。独立审查仍受已知额度限制，仅作者自查与上述验证。
