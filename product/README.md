# Alcor 原生 DSH 功能包

Alcor（原 Stella）是一组 DeepSeek Harness 原生插件。秘书插件提供事务、日程、账单、简报和自动化；DSH 提供桌面窗口、模型、会话、工作区和插件管理。窗口顶部的模式菜单可以切换 Alcor 和 DSH。

## 桌面用户安装

桌面版面向 macOS Apple Silicon，已核对的 DSH 版本为 `0.2.2-alpha.3` 和 `0.2.2-rc.1`。在已初始化的 DSH Profile 中，通过原生插件管理器安装秘书插件的 `.tgz` 包，退出并重新打开客户端，再从顶部模式菜单进入 Alcor。Apple 同步、eLearning、邮件和 AgentHub 均可按需安装。旧 Stella Profile 须先完成迁移，避免同时启用新旧插件。兼容版本与签名标识见[桌面版本清单](desktop-release.lock.json)。

界面扩展保存在 Alcor 包的 `vendor/` 中，原生组合使用相对 Host 入口及原模块 ID，与官方客户端文件分开。五个模块保留 MIT 和固定来源摘要，构建会校验其完整性。它们只补充现有模式、原生工作区／设置／自动化复用接口；宿主 Agent、模型、会话和工具执行不由此替换。旧的手工客户端兼容脚本仍可用于历史预览，但不再是这条桌面安装路径的前提。

## 当前源码与默认入口

`packages/` 保存原生插件源码，`product/tests/` 保存集成测试，`scripts/product/` 保存构建和预览入口。运行 `make setup` 安装依赖，`make check` 构建并测试，`npm run dev` 启动独立 DSH Profile 的开发预览。原生 CI 配置位于 [.github/workflows/native-product.yml](../.github/workflows/native-product.yml)。旧 Python/Electron 运行时的测试入口为 `make test-legacy`。

事务列表通过左侧清单导航、顶部视图和搜索筛选；任务行可进入详情，勾选完成和右侧菜单分别操作，并支持在当前清单快速添加。删除确认说明子事务或重复系列的影响，详情状态／日期／清单／优先级集中展示。检查项按当前设计从详情和编辑页面移除，既有数据保留，编辑其他任务字段不清空旧检查项。专项官方桌面验收入口：`product/tests/task-cards-check.py`。

任务表单顺序为标题、事务详情、截止日期／时间、优先级／标签，保存使用当前系统时区。新提醒从定时任务入口设置；表单只提交可编辑字段，已有任务的其他数据沿用后端局部更新契约保留。

月／年账单列表采用居中限宽的纵向紧凑卡片。简报更新历史以连续竖线连接当前版本和各历史节点，按时间由新到旧排列，保留状态文字、颜色区分和版本切换。官方桌面专项验收入口：`product/tests/statements-timeline-check.py`。

## 构建原生安装包

需要 Node >=24.9、Python >=3.9。macOS 构建 Apple 桥另需 Swift Command Line Tools。在仓库根目录运行：

```sh
npm ci --ignore-scripts
npm run product:pack
npm run product:apple:pack
```

当前源码构建的产物为 `.local/artifacts/alcor-secretary-0.1.62.tgz` 与可选的 `alcor-apple-sync-0.1.17.tgz`。以下命令仅用于已通过测试夹具初始化且不含个人数据的隔离 Profile，不能作为已有 Profile 的升级步骤。

```sh
dsh plugin --profile <隔离测试 Profile> add /absolute/path/alcor-secretary-0.1.62.tgz --ignore-scripts
dsh plugin --profile <隔离测试 Profile> add /absolute/path/alcor-apple-sync-0.1.17.tgz --ignore-scripts
```

通过顶部模式菜单进入 Alcor；事务与日程展开 Alcor Agent 即可在原页对话；新会话由侧栏提供，Alcor 设置只保留助手名称；工具权限使用 DSH 当前会话策略。它直接使用已有模型路由；New Session 在 Alcor 模式使用秘书 Preset，切回 DSH 后使用用户原有默认 Preset。安装不改写宿主的模型、凭据 provider、默认 Agent 或执行策略，不会自动读取个人来源；旧客户端使用显式离线迁移。停用/卸载使用原生管理器，Alcor 安装记录与设置默认保留；其他插件、会话和凭据由宿主管理。

`0.1.0` 开发版侧栏上方为五项业务应用和“插件”入口，下方复用 DSH 原生工作区与会话列表，支持搜索、筛选、新建工作区和会话菜单；“新对话”使用 Stella 身份及当前共享文件工作区，默认打开事务；旧“今天”、秘书对话和待确认位置引用兼容转向事务，底部复用 DSH 原生账号入口，设置统一进入原生窗口的 Stella 分区。统一线条图标与紧凑间距，支持折叠导航、窄窗抽屉及键盘焦点。AgentHub 已拆为独立开发者插件 `@stella/agent-hub`，仅在 DSH 工作台提供入口；安装、数据迁移与运行边界见 [AgentHub](../packages/alcor-agent-hub/README.md)。来源范围位于简报页，访问与安全使用宿主设施，顶部菜单切回 DSH 并恢复原生导航。MCP 查询继承 DSH 当前会话策略，eLearning 连接与旧账号限制见 [MCP 接入](mcp-connections.md)。后端高级接口与尚未完成的页面、旧业务数据迁移和真实外部验收见 [feature-parity.md](feature-parity.md)，不生成示例业务数据。

事务与日程的 Alcor Agent 展开后直接显示原生嵌入式会话，支持历史、新会话、工作区与模型选择；快捷会话与主会话独立，发送不跳转。浏览器仅保存各页面的 Session ID 引用，消息与草稿仍由 DSH 管理。首次展开不调用模型；业务工具和页面共用服务。Apple 连接统一由“插件 → @alcor/apple-sync”的配置页管理：日历和提醒事项各提供“同步”“暂停同步”，自动纳入全部来源并保留同名分类；日历覆盖全部历史到未来两年，见 [插件说明](../packages/alcor-apple-sync/README.md)。

界面复用宿主 `Button`、`Input`、`Checkbox`、系统字体和明暗偏好。Alcor 模式使用白色／浅灰与炭灰表面、蓝色主要操作及选中态，小熊图标使用暖棕／沙金色；事务分组、日程网格、简报时间轴及账单图表保留各自的信息结构。主题通过现有语义变量限定在 Alcor 模式内，切回 DSH 或停用插件恢复宿主样式，不改业务数据或核心交互。

## 官方 Desktop 验证

从官方 DMG 只读挂载或安装的 App 验证，发布版本、架构、签名 Team 与 `app.asar` 摘要由 `product/desktop-release.lock.json` 锁定。设置 `STELLA_CHECK_DESKTOP_APP` 可指定已安装的官方 App，默认使用 `.local/desktop-official/mount/DeepSeek Harness.app`。`make test-desktop` 使用独立 HOME、DSH_HOME 与 Electron 用户目录，经官方 Desktop 自带 CLI 验证冷安装、顶部双向切换、六应用、模式／业务重启恢复、停用与卸载；不读取个人账号或运行真实 Apple 同步。

原生界面根节点热重组仍存在注册空窗；`--hot-install` 是单独的热安装检查，失败不能当作冷启动验收通过。普通交付要求安装／启停／卸载后重启，不承诺无重启热更。日常 Alcor / DSH 模式切换不受此限制。

完整重建 UI 扩展时，在 `product/upstream.lock.json` 指定的官方源码和补丁上构建五个 UI 包，再执行 `ALCOR_UPSTREAM_DIR=/absolute/prepared-upstream npm run product:ui:build`。普通 `product:build` / `product:pack` 只消费并校验包内已生成的衍生产物，不把 `references/` 或个人 App 作为输入。

## DSH 更新兼容

插件只依赖自身业务库；`@deepseek-ai/*` SDK 均由运行中的宿主提供，声明为可选 peer，禁止随插件安装旧宿主组件。`npm test` 检查这条约束与明确的版本范围。旧版本插件曾把 alpha.3 核心组件装进 Profile，rc.1 升级兼容检查会停用这些旧组件，导致原生服务缺失；修复版秘书 0.1.7（及后续版本） 和 Apple 同步 0.1.1 必须一起升级，由包管理器移除它们遗留的宿主依赖。不要清空 Profile、业务库或账号来修复。

扩展支持版本前，更新已核实的 `desktop-release.lock.json`，执行完整官方 Desktop 生命周期和旧 Profile 升级检查，确认配置、业务数据及不兼容时的原生启动。后者需要保留 0.1.5／0.1.0 旧包作为升级输入、已构建的新包和独立 alpha.3 官方 CLI：

```sh
STELLA_CHECK_DESKTOP_APP="/absolute/DeepSeek Harness.app" \
STELLA_LEGACY_CLI="/absolute/alpha-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js" \
make test-desktop-upgrade
```

检查使用隔离目录和合成数据，不连接个人账号。这是历史同名包升级检查，不验证 `@stella/*` 到 `@alcor/*` 的跨包迁移；使用更名后的包时会跳过。当前个人 Profile 的跨包核验结果见本页开头。

## 简报来源与插件适配

侧栏不再提供独立「数据」页，来源卡片位于「简报 → 设置」，开关和范围位于对应插件详情的「简报来源」；旧页面位置自动恢复到简报。来源配置、历史简报和业务库保留，原数据页的存储展示接口已删除。账号配置和插件生命周期统一使用 DSH 原生插件页，Alcor 不再提供连接、逐工具授权或旧工作区表单。

适配层统一发现来源元数据、读取已选范围、校验返回身份和转换简报材料。兼容现有邮件／学校 MCP，并支持提供明确简报契约的 DSH 原生插件；不会扫描或执行全部插件工具。选择来源不等于开放对话工具或外部后台任务。插件接入协议和验证边界见 [简报插件适配](briefing-plugin-adapters.md)。

## 历史顶部切换兼容层

用户可显式选择为官方 npm DSH `0.2.2-alpha.3` 安装本地客户端补丁。该选择提供顶部 DSH / Alcor 切换、按模式创建会话、位置恢复和原生 `workspace.browser` 组件复用，属于基于官方 DSH 的兼容版本。使用已有 `product:prepare` 生成的固定源码和补丁构建产物，先停止目标宿主，再运行：

```sh
node scripts/product/client-compat.mjs --install /absolute/dsh-runtime
node /absolute/dsh-runtime/stella-client-compat/client-compat.mjs --check /absolute/dsh-runtime
```

`/absolute/dsh-runtime` 是包含官方 `node_modules/@deepseek-ai/dsh` 的安装目录，不是 DSH_HOME 或 Profile。仅替换四个客户端文件，原件和哈希保存在 `stella-client-compat/`。将第二条检查放在日常启动入口、官方 DSH 启动之前；检查失败时停止启动，不自动覆盖或重装。重启并刷新浏览器后出现顶部菜单，原有会话首次保持原位置。

需要回退时先停止宿主，然后运行：

```sh
node /absolute/dsh-runtime/stella-client-compat/client-compat.mjs --restore /absolute/dsh-runtime
```

回退不删除会话或秘书设置。宿主升级、客户端未知修改和备份损坏会拒绝覆盖；升级前先重新验收，不强行跳过检查。验证入口为 `product/tests/client-compat.test.mjs`，以及对隔离兼容安装显式设置 `STELLA_CHECK_CLI`、`STELLA_CHECK_CLIENT_COMPAT=1` 后运行 `product/tests/plugin_check.py`。未设置这两个变量的检查继续针对未修改官方安装。

## 可选 Keychain 凭据包

macOS 可单独构建 `npm run product:privacy:pack`，候选产物为 `.local/artifacts/alcor-privacy-0.0.2.tgz`。它通过原生 reference/record 契约为模型和插件保存凭据，安装默认停用。由用户明确选择全局 provider 后才使用 Keychain；不会导入或改写旧凭据文件，锁定或拒绝时直接失败。启用、回退和限制见 [privacy 使用说明](../packages/alcor-privacy/README.md)。此前仅实测旧包在 DSH `0.2.2-alpha.3`、临时 Keychain 和合成凭据下的行为；新包、正式签名、真实 OAuth 与升级授权仍待验收。

```sh
npm run product:privacy:test
# 含原版宿主安装、显式启用、模型工具往返、重启和锁定恢复；需要 Python Playwright
python product/tests/keychain_check.py --host
```

## 旧资产离线盘点

`scripts/product/inventory-legacy.py` 使用 Python 标准库和 SQLite >=3.37，不导入旧运行时。先在应用停机后制作一致的独立副本；SQLite 应使用 Backup API 或已完成 checkpoint 的停机快照，副本中存在 WAL、SHM 或 journal 时拒绝。`--confirm-offline-copy` 是操作者确认，工具无法证明其他进程没有写入。指定规范化的绝对快照根；符号链接、硬链接和特殊文件均拒绝。

```sh
python3 scripts/product/inventory-legacy.py --snapshot /absolute/offline-snapshot \
  --agent-root agents/default --user-root users/local-default --shared-root shared \
  --confirm-offline-copy > /absolute/reports/inventory.json
python3 product/tests/inventory_check.py
```

各 root 相对于快照根，按旧注册信息和 `common/state_dir.py`、`core/plugins/lifecycle.py` 确认，可重复指定多 Agent/用户。旧单目录布局可对三个角色显式指定 `.`；不猜测身份或自动合并不同用户。当前覆盖：

| 范围 | 盘点资产 |
| --- | --- |
| user | todos/calendar/briefing/bills/agent-hub/data 和 operations 数据库；当前 `plugins/memory`、历史 memory、output/work/runs/health、automation-records 文件数量；USER/MEMORY 与迁移标记是否存在 |
| agent | 会话/索引 `memory/long-term/index.db`、权威调度 `scheduler/automation.db`，旧 tasks.json 是否存在 |
| shared | knowledge 文件数量，`.env`、mcp.json 是否存在；不打开其内容 |

输出仅含固定资产名、schema/table/root 摘要、行数、文件数量/大小及固定状态桶，未知状态归 `<other>`；不输出正文、对象 ID、实际文件名、表名、DDL、账号或 secret。虚表、FTS shadow 与 view 不查询，报告排除数量。多个范围不可直接相加；automation worker 副本只统计文件，当前记忆与旧副本分别列示。报告始终 `migration_ready=false`，未知 schema 必须人工核对。

这是首批资产清单工具，尚不覆盖插件自定义目录、外部来源/Apple 映射、授权库与所有私有设置；不验证业务关系或完整备份，不执行复制、导入、激活或 Keychain 读取。保留现有 owner，待迁移步骤核对这些额外路径。

下面的独立数据根、客户端补丁与桌面启动器仅属于旧隔离预览，不是用户安装功能包的步骤。不要对个人 DSH 使用 `product/cordis.patch.yml`；它会覆盖宿主默认配置。

## 历史隔离预览的构建与启动

需要 Node >=24.9、Git、网络和 macOS arm64/x64（当前 Desktop 启动脚本支持范围）。根目录运行：

```sh
npm ci --ignore-scripts
npm run product:prepare
npm run product:preview:build
npm run product:web -- --no-open --port 5174
# 或
npm run product:desktop
```

`product:prepare` 在 `.local/upstream` 克隆固定发行 tag，校验提交、顺序应用最小补丁，禁用依赖安装脚本后使用官方构建入口，准备官方 runtime 和 Electron。构建会下载固定校验和的运行资源；已有不匹配源码或冲突改动会拒绝，不能覆盖。第一次 runtime 解包后的系统校验可能耗时；失败必须查看日志，不能跳过 smoke。`npm ci` 会还原 npm Client 文件，之后重新执行准备/构建。`references/` 不参与构建。

Web 默认数据根 `.local/dsh-preview`，Desktop 为 `.local/desktop-preview`。可用 `ALCOR_PRODUCT_HOME` 指定**新的专用空目录**。预览会写归属标记、设置 0700 并持有官方跨进程锁；已有未知数据、个人 `.dsh/.stella/.codex` 与项目源码目录拒绝使用。Web/Desktop 必须使用不同目录。不自动接管没有标记的旧预览目录。不要把旧数据复制进这里绕过检查。

旧的 `STELLA_PRODUCT_HOME` 和 `STELLA_UPSTREAM_DIR` 仅作为迁移期回退；新旧变量同时存在且值不同时会停止，避免意外使用错误目录。

launcher 不继承模型 API Key 等宿主变量。模型配置通过原生 profile/owner 设置；合成测试只使用临时假 credential。预览沿用 DSH 原生 local 凭据 provider；用户已明确选择模型 Key 和 MCP 账号均使用此机制，不启用 macOS Keychain。原生凭据文件权限为 0600，同账号可信工具仍可能读取。Web 偏好按 origin 存储，重启恢复必须使用同一个端口。Desktop 使用稳定的应用 origin。禁用/卸载秘书 bundle 后不会在重启时自动重装或启用。

## 当前行为与边界

2026-11-05 后端：已迁入原事务、日程、账单、简报与 AgentHub 的独立领域服务。2026-11-07 起 AgentHub 独立为开发者插件，协作数据归其私有目录，Stella 不再开放协作 `catalog`。事务、日程、账单与简报仍由页面和 `stella_business` 共用权威对象，业务库位于当前 DSH Profile 的 `stella/business/`，不使用 macOS Keychain。层级／依赖／重复、准确账目／期间／XLSX、简报修订／行动／outbox 均可通过 `catalog` 查询并调用。现有开发记录幂等迁入，原 KV 保留；旧 Stella 私人业务库仍待离线迁移。

简报可选择使用当前模型整理。通过 Alcor 对话配置已连接的邮件／eLearning MCP 来源，凭据沿用 DSH；来源统一使用上次成功检查点与前一自然日零点中的较晚者。任一来源失败会保留上次简报和游标，重试复用成功来源。正文、来源游标、修订和采集回执由同一 SQLite 事务提交。模型会接收来源正文，过大的原文可能超出上下文，失败后不更新游标。事务桌面提醒由持久回执管理，失败重试不重跑 Agent；DSH 仍是唯一 Agent 调度器。Apple 桥随独立 Apple 同步插件构建，原生配置页已接，系统实际授权与真实数据同步尚未验证。前端高级入口仍按 [功能对照](feature-parity.md) 标注，不能把后端 API 可用视为全部页面验收完成。

- 首次保留当前 DSH 页面与会话；由模式菜单进入 Alcor，之后恢复本窗口上次模式。窗口之间不监听彼此的模式变更。
- 切换只恢复当前模式的页面、会话引用；不会创建会话、调用模型或改写已有会话。秘书新会话使用 `stella`；隔离预览在 DSH 新会话创建时读取并显式绑定宿主当前默认 Preset，避免复用秘书空会话而覆盖草稿，读取失败会拒绝创建。本隔离预览的默认值是 `standard`，原生安装不挂接该事件。
- 原生 Client 保留草稿；当前已验同进程返回，未宣称所有工作区、崩溃及升级恢复完成。
- 秘书 Preset 使用原生 scoped restriction 屏蔽未授权全局工具，包括后续新增注册，保留 `alcor_overview` 及精确授权的 MCP 工具；执行 guard 拒绝非主动用户主会话和已撤销调用。DSH 会话的全局工具与用户执行策略不变。旧隔离预览另外将 DSH 限制为 `todo_write` / `ask_user_question`，关闭通用执行入口；工具限制不能证明 OS 隐私隔离。
- Secretary 是标准原生 bundle：Host Remote、Preset 内工具、页面、设置、storage-domain 安装记录。原版 DSH 可从首页显式开始秘书对话；预览顶部切换依赖 Client 槽补丁，原版没有切换栏。
- 原生 Host 插件是同进程可信代码。独立数据根、工具目录与环境筛选不构成恶意插件的 OS 沙箱；安装插件仍可运行宿主代码。P1 必须验证 executor 拒绝路径、凭据与资源授权后才开放执行。
- 没有替换或删除旧 `core/security`、Codex 适配及旧插件成果。旧安全实现不会自动保护新 DSH 进程。

## 供应链修复与发行差异

固定发行版的依赖审计发现 Zod/Undici/fflate/MCP client 的已知问题。npm 与 Desktop 源码构建均锁定 Zod 4.6.7、Undici 8.12.2、fflate 0.8.3、MCP client 2.3.0；上游源码配置与 lockfile 差异作为第三个补丁保留。`npm audit` 当前对根 npm 依赖图报告 0 项漏洞；这不是对上游所有内联产物、OS runtime 或社区包的安全审计。

当前仍保留上游图标、账户菜单、欢迎页、更新与平台专属窗口承载，未宣称完成白标安装包。产品独立数据根与顶部切换已加入；遥测/反馈插件在 overlay 禁用，但未完成所有网络出口实证。正式发行前需要品牌/协议/更新渠道归属、签名与恢复验收，不能发布指向官方 DSH 更新渠道的 Alcor 安装包。

## 验证入口

```sh
npm run product:test
# 需要安装 Python Playwright 及 Chromium
python product/tests/web_check.py
python product/tests/quick_chat_check.py
python product/tests/desktop_check.py
```

Web 使用本地合成 SSE 模型，覆盖实际模型工具目录、Alcor 工具往返、DSH 独立人格、新会话、草稿切回、双窗口、390px 抽屉真实点击、键盘/Escape、同 origin 重启和设置持久化。Desktop 使用隔离临时 home，无账号/模型，覆盖真实 Electron 中切换、刷新与 520px renderer。`STELLA_CHECK_ARTIFACTS` 指定截图目录。

未修改官方 Web 包与合成 Apple 生命周期（官方运行环境须放在 Alcor 工作区外，避免宿主优先解析到开发包）：

```sh
STELLA_TEST_RUNTIME="$(mktemp -d /private/tmp/stella-official-runtime.XXXXXX)"
npm install --prefix "$STELLA_TEST_RUNTIME" --ignore-scripts --no-audit --no-fund @deepseek-ai/dsh@0.2.2-alpha.3
npm run product:pack
npm run product:apple:pack
STELLA_CHECK_CLI="$STELLA_TEST_RUNTIME/node_modules/@deepseek-ai/dsh/lib/bin.js" python product/tests/organizer-ui-check.py
```

该检查使用实际打包安装文件，强制验证模式按钮、双向同步、分类／清单映射、事务树、日历各视图及窄窗口；秘书启停按冷启动契约验证，Apple 插件单独验证停用、卸载和重装。解析到 Profile 之外的开发包会立即拒绝。测试不连接真实账号或真实模型。`plugin_check.py` 的主程序仍是历史热生命周期诊断，包含本组合不支持的根 UI 热替换，不作为本轮通过证据；其公共启动 helper 为当前检查复用。补丁关联上游回归：

```sh
cd .local/upstream
node ../../node_modules/pnpm/bin/pnpm.cjs exec vitest run packages/client/ui-workspace/tests/workspaces-service.client.spec.ts packages/client/ui-layout/tests
```

## 上游来源与补丁退出

精确组合在 [upstream.lock.json](upstream.lock.json)；依赖完整版本与 integrity 在根 `package-lock.json`。主线研究版本与实际运行的发行版本分开。

| 补丁 | 改动 | 移除条件 |
| --- | --- | --- |
| 0001 | Desktop 不运行用户 login shell 启动文件；日志进独立 Electron userData | 官方具备受控环境来源与独立日志路径 |
| 0003 | npm/源码构建同步锁定四个修复版本，保留更新后的 pnpm lockfile | 官方发行依赖已包含这些修复并通过组合回归 |
| 0002 | Client 声明 `shell.top`；为 44px 产品栏保留空间/抽屉层级；公开只读导航 identity；新会话默认 Preset event | 官方提供等效可访问入口、导航引用与按调用方解析的默认 Preset |

未修改上游 Agent/Tools/Session/授权执行核心。补丁必须按固定 commit 重验，不可直接套到 master。使用与改写 DSH MIT 源码，详见 [NOTICE.md](NOTICE.md) 和 [DSH-LICENSE.txt](DSH-LICENSE.txt)。

## 社区候选结果

`dsh-mnemon@0.5.39` / `dsh-email@0.21.3` 只安装在 `.local/candidates-home`。需要重建这份隔离安装时，在根目录运行 `DSH_HOME="$PWD/.local/candidates-home" node node_modules/@deepseek-ai/dsh/lib/bin.js plugin --profile candidates add dsh-mnemon@0.5.39 dsh-email@0.21.3 --ignore-scripts`，不在个人 Profile 安装。`python product/tests/candidates_check.py` 使用未修改官方 CLI，在 macOS Seatbelt 中禁止个人 home 读取、禁止外网、仅允许临时目录写入。测试显式设置临时文稿目录，避免默认工作区探测访问系统文稿；Email 无账号且提前关闭共享 updater 的自动检查。

Mnemon 在 custom dataDir、本地 provider、关闭 writeback/embedding/skills 下，通过原生 Remote 验证 runtime memory 添加、去重、另一会话读取、纠错、容量拒绝、ZIP 导出内容、删除及重启后写入拒绝。合成模型请求证明：关闭 lifecycle 时不注入测试记忆；开启 lifecycle 后，同一 custom root 的用户记忆会进入普通 DSH 对话，无需选择 Alcor。此组合不满足私人记忆仅进入秘书的准入条件，保持不安装到个人 Profile。Memory Spaces CLI 在该组合中不存在，不能声称已验证其跨会话召回。这里的成功日志只代表功能和阻塞证据已复现，不代表候选获准生产使用。

Mnemon 的 Preset 范围与跨 Agent 事件仍需在 P1/P2 收紧；不能原样加入两个模式。Email 的 `@stardustlc/dsh-updates@0.1.3` 默认 checkEnabled=true，会注册独立后台更新协调器；产品不能并行采用它和自己的更新流程。其页面还引用非商业/版权不明的同人素材，发行许可需先澄清或去掉对应素材。真实账号范围、Keychain、消息副作用、运行中撤销、Memory Spaces 召回及后台流程尚未验收。两包均未进入默认产品组合。

## 工作区接入

开发预览的文件工作区完全共用 DSH：单一目录列表、当前／最近目录选择、已有目录登记与原生会话创建；Alcor 不再保存独立个人／项目绑定。身份与业务数据独立保存，旧身份可显式预览、导入与回退。身份仅在用户发起的 Alcor 主会话使用，不进入普通 DSH、协作或定时任务。文件原位复用，业务库和记忆等仍待迁移；配置及边界见[工作区说明](workspace-migration.md)。

### 统一自动化

Alcor 侧栏“自动化”与 DSH 自动化共用原生列表、完整编辑器和运行记录。Alcor 默认只列出所属任务，DSH 显示全部并标记来源；修改、启停和删除作用于同一 ID。每日简报、每周事务回顾模板显式保存后由 DSH 调度，保存过程不调用模型。执行时本机业务工具读取的结果会发送给当前模型；简报会保存为业务记录。新建会话保持 Alcor 身份，可修改文件工作区。

停用／卸载 Alcor 暂停其任务，重新启用不自动恢复，不影响其他任务。DSH 宿主需运行才能调度；日程通知单独处理，本轮未接入系统通知。完整页面由原生 Bundle 内的界面扩展提供，官方 App 无需另打补丁。


## 对话导入账单

在 Alcor 对话提供微信 XLSX／支付宝 CSV 的绝对路径并要求导入，Alcor 可调用 `alcor_import_bills`，直接读取 DSH 允许访问的文件并写入账单。无需先在页面确认；要求预览时可 dry_run。账单页复用同一持久流水与期间汇总。

支持支付平台原始导出（支付宝 UTF-8／GB18030 CSV、微信标准 XLSX），每次最多 5 个文件、单个 5 MB、合计 20 MB、每文件 10000 笔。按平台和交易号持久去重；同号内容变化拒绝整批，不覆盖用户修改。删除过的已导入流水保留去重身份。退款／退还、失败、非收支及异常行不会猜测入账，结果返回跳过计数与原因，最多展示 50 条。金额以整数分保存，汇总分值以十进制字符串返回；平台时间按 Asia/Shanghai 解析。

专用工具通过执行 Agent 的 DSH fs 读取，遵循其权限；无法读取时直接报错。文件不修改，原文不回传模型；只返回导入／重复／跳过数量及汇总。没有新增通用文件／Shell 能力。`bills.catalog` 中的 `import_files(files, dry_run)` 是同一领域接口；正常对话文件导入优先调用专用工具。

验证：`product/tests/bill-import*.test.mjs` 与 `bill-files.test.mjs`；`bill-import-chat-check.py` 使用合成模型与两种合成文件验证模型→工具→持久账单与重复导入，设置 `STELLA_CHECK_NATIVE_DESKTOP=1` 可验证指定的未修改官方桌面。未自动读取或导入个人账单。


## 页面业务的对话调用

六类业务页面与 Alcor 对话共用服务。用户可直接要求管理事务／日程、处理简报、导入或导出账单、启动协作、创建与暂停自动化；支持范围、文件限制与验证入口见 [页面业务工具](page-tools.md)。页面展示同一份结果，无需额外页面确认。

## 账单页面

明细按发生日期分组，显示分类、时间、收支和当日小计。月／年账单先列出各期间，进入后查看收入、支出、结余、周期、汇总时间、分类构成及收支排行。多币种分别查看；待处理与已冲正流水仍显示在明细，但不计入已记账统计。归档不改变账本收支。

页面与对话通过同一 bills owner 获取 `ledger_report(start,end)` 和 `statement_reports(period_type,year)`，后者读取既有期间快照与明细聚合。金额最小单位返回十进制字符串，避免 JavaScript 丢失精度。单条页面记账默认已记账，支持类别、秒级时间、状态及备注；编辑保留未更改的原始时间精度／时区，写入和删除检查 revision。删除须确认；修改后刷新相关期间与年度。CSV 导出当前所选期间及币种，包含分类和原始时刻。

验证入口：`bill-reports.test.mjs`、`bill-view.test.mjs`、`bills-ui-check.py`。UI 检查使用隔离 Profile 与合成账单，可通过 `STELLA_CHECK_NATIVE_DESKTOP=1` 验证未修改官方客户端。服务暂时不可用的 HTTP 注入只在 Web 验证，桌面使用原生 RPC 桥；真实账号和旧私人账本迁移不在该检查范围。


## 学校与邮件插件

学校与邮件插件的安装包为 `alcor-elearning-0.1.7.tgz` 与 `alcor-mail-0.1.9.tgz`。两包包含所需 MCP 运行环境，不使用开发目录。保存账号后点击检查；简报来源范围在对应插件中选择，对话工具继承 DSH 当前会话策略。账号与来源安装不自动触发读取。生命周期、凭据及验证边界见 [MCP 连接](mcp-connections.md)。

## 插件附带业务指南

秘书随包提供事务、日程、账单、简报、自动化和协作六项 Skill，仅挂载到 Alcor preset；eLearning、邮件与 Apple 同步插件分别提供自己的 Skill。复用 DSH 原生 skills registry 和 bundled filesystem provider；Alcor preset 显式挂载 DSH 原生 skill 工具，先展示简短目录，正文按需读取，停用/卸载按所属插件撤销，不复制到用户全局 Skill 目录。已有用户 Skill 和宿主工具策略保留。

未知业务方法可查询 `alcor_business(domain=..., method="catalog", arguments={"method":"目标方法"})`，只返回该方法；不传 method 保持原目录兼容。按需指南示例由 `product/tests/bundled-skills.test.mjs` 执行验证，打包后用原生宿主检验发现、加载与持久化。

简报 `0.1.39` 使用独立 DSH 执行会话和仅允许结构化提交的 `stella-briefing` Preset。页面、聊天及原生定时入口共用运行记录；聊天手动调用返回任务，定时调用等待实际保存结果。运行固定模型、来源/窗口和规则，完成的来源与模型批次保存在 Profile 私有业务存储；刷新和切页不取消，重启后显式继续。失败/取消不推进游标，旧结果已提交时按操作 ID 对账。中断期间若结果或来源游标已更新，旧运行不能继续覆盖新结果。未完成来源可能重采，尚未收到并保存的模型批次可能再次调用。专项验收：`product/tests/briefing-runs.test.mjs`、`product/tests/briefing-runs-check.py`。

简报 `0.1.40`：工具栏以单一齿轮图标进入完整设置页，合并信息来源与筛选/写作规则；返回简报保留当前报告与版本选择。来源授权、保存冲突保护和规则接口保持原有契约。

简报 `0.1.41`：规则用摘要卡片展示，点击以宿主 Markdown 组件查看全文，可编辑、启停和确认删除；桌面设置采用来源与规则双栏，窄窗自动单栏。

简报 `0.1.42`：来源页只读聚合卡片，Email 多账号合并一张。Email 使用插件内账号开启/停用按钮统一控制全部读取入口，已开启邮箱自动成为简报来源，不再维护第二份订阅名单；学校账号随原生插件启停。通过官方 plugins.detail.section 在 eLearning 和 Alcor 插件详情管理课程/本机范围。既有账号、范围及游标保留。通用适配器尚无包归属元数据的来源由 Alcor 的简报适配层管理。

邮件 `0.1.6` 在宿主统一调用入口复核账号开关，覆盖邮件正文、目录、游标和连接测试；停用/移除立即撤销后续调用。邮箱与学校实时来源取自已启用插件目录，旧邮箱订阅记录仅保留作兼容，不再授予或阻止访问。原有 DSH 调用权限与定时任务的明确本机范围保持有效。

简报 `0.1.43`：按用户最终确认采用双层开关。插件/账号可用性与简报来源启停独立，两层均有效才采集；来源关闭不写插件/邮箱配置，课程范围与游标保留。来源与规则卡片、规则详情/编辑统一使用灰色滑块；两侧工具栏及首张卡片对齐，规则刷新仅读取规则列表。来源开关单独存储并保持并发冲突保护，Agent 不能自行开启。


日程 `0.1.47`：页面通过“＋”选择新建日程或事务，编辑器可切换类型并保留各自草稿。有日期的事务以圆形完成控件显示在日／周／月／年／列表视图，写回同一事务，不复制为日程；事务页可选择和管理清单（如 planA）。旧日期文件导入退出主界面，后端接口及已有日期记录保留。编辑按记录时区保存，全天日程结束日期保持排他语义。

Apple `0.1.11`：双方分类／清单取并集并建立一一映射，补齐 Stella → Apple 新建、双方名称／颜色／删除，以及项目更换分类的原地移动。旧 list_name 事务补建稳定分类身份，简报和未分类记录也纳入同步。首次配置与后台入口复用同一分类生命周期逻辑；权限、失败重试与公开 EventKit 的字段限制见 [Apple 插件说明](../packages/alcor-apple-sync/README.md)。官方 Desktop 隔离验证：`product/tests/unified-calendar-check.py`。


日程 `0.1.48` / Apple `0.1.12`：日程页将所有事务清单聚合为一个“事务”筛选，实际清单与 Apple 一一映射保持不变。日程和事务必须归属有效分类／清单；旧无分类数据迁入默认分类，保留内容和 ID。省略分类的旧创建接口使用默认分类，显式传空拒绝，数据库也阻止无效归属。删除分类时同一事务内删除其中日程／重复系列，或事务／子事务及待发送提醒，并保留来源墓碑；不再保留无分类记录。分类管理采用颜色、名称和数量列表，单独新建／编辑与危险操作确认。验证：`required-categories.test.mjs`、`business-recovery.py`、`unified-calendar-check.py`。

日程 `0.1.50` / Apple `0.1.14`：完成 Apple 同步契约修复，含分离权限、持久待确认操作、身份重建保护、跨账户同名、复杂提醒只读保留、冲突恢复、原生实例范围、重复提醒完成历史、时区／浮动时间、公开字段及私有回执、按需范围与 Apple 默认归属。旧数据不清理，详细能力见 Apple 插件 README。

日程／事务 `0.1.51`：卡片和月、周、日、列表支持右键及 Shift+F10 菜单；“显示简介”打开分组面板，支持沿用编辑、归属移动、完成、拷贝文本和删除确认。分类选择在同一菜单内继续选择，窄窗可滚动，跨账户同名显示来源。Apple `0.1.15` 修复单次重复日程移动时原日历查找与目标日历写入的区分。

事务与清单 `0.1.52`：事务列表采用清单侧栏、紧凑任务行、备注预览与快速添加；清单管理支持搜索、配色、账户选择、切换保留草稿、分项权限、待确认操作取消和删除确认。新增子事务默认继承父事务清单，不额外创建默认清单；已有记录不迁移。官方 Desktop 的 `tasks-layout-check.py`、`lists-manager-check.py`、`unified-calendar-check.py` 与 `task-cards-check.py` 验证相关交互和数据保留。

日程与统一侧栏 `0.1.53`：日程日期导航、五种视图、全天区域和时间网格重新整理。日程与事务共享浅灰侧栏和账户分组样式；日历以复选框控制显示，事务按单清单切换。官方 Desktop 隔离 Profile 验证视图、筛选、事务完成、只读保护、详情/编辑、深色、窄窗口和已有字段保留。

日程右侧面板 `0.1.54`：日历内日程和事务的查看、编辑及新建在右侧完成，空间不足时日历来源侧栏折为“日历与日期”。支持固定操作区、草稿放弃确认、失败后重试、键盘与焦点返回、深色和窄窗口；业务数据及同步规则不变。

事务右侧面板 `0.1.55`：事务页与日程复用同一详情及编辑面板。单击、右键简介、编辑和新建统一在右侧完成，原列表筛选保持；保存或取消编辑回到详情。保留子事务、重复按次完成、旧字段和权限限制；隔离官方 Desktop 验证数据保留、草稿确认、删除确认、深色、窄窗口及重启。

简报 `0.1.58`：每次运行由一个持久 `stella-briefing` 会话执行筛选和写作，不再为批次启动子 Agent。小型全新窗口可一次提交筛选及报告；较大或增量窗口由同一会话分批处理，较长原文可通过受限工具按顺序分页读取，业务层校验完整覆盖后才保存检查点。规则、模型和来源范围按运行冻结，每步与读取、提交时复核授权；取消在保存前生效时不推进游标；若保存已先完成，按已保存结果对账。未通过校验不推进游标。旧版未完成任务须新建，不会把旧批次当作新协议恢复。空窗口和内容未变化的刷新不调用模型。上下文超限或输出不完整时会失败并保留来源游标；模型摘要和会话历史可能增加调用或成本，不能保证任何窗口均节省 token 或与旧报告完全相同。
