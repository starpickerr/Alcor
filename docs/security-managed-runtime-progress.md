# 安全与托管运行环境重构：阶段记录

状态：**实施中，完整计划未完成，也未达到可合并验收条件**。更新于 2026-10-29。

当前剩余工作以[10-27 收尾清单](../../.agents/tasks/active/2026-10-27-macos-security-delivery.md)的四项待办为准；下文早期记录供追溯，不自动增加实施范围。

四个涉及仓库均在 `refactor/security-managed-runtime` 专用分支；Core 基线 `ee9b5d6f`。Stella-Plugins 包含 agent-hub 1.0.5 受控启动适配，两个 MCP 仓库新增 requirements.lock。用户于 2026-10-26 明确要求「及时提交」，现按已验证阶段提交；完整目标完成后的合并条件不变。保留最初无关改动，不推送或发布。

## 权限与页面

- 继续使用 RunPolicyStore、SecurityService 和原资源授权事实。初始只读的 `owner-default-v2` 模板可由可信用户修改，revision 防并发覆盖；废弃全局配置不自动复活。
- 新对话与用户独立任务复制默认快照；关联任务只引用对话上下文。修改默认或原对话不追溯改变已有任务。Agent 隐式创建采用默认与创建者权限交集，明确越权拒绝。
- 四种模式保留；自定义基准与规则集中在安全 → Agent 权限编辑。其他入口保留模式、摘要、对象定位和返回。旧客户端不能通过空规则清除自定义规则。
- 安全中心已接入总览、Agent 权限、运行隔离、外部连接、敏感资源保护五页和旧入口兼容跳转。能力详情布局保持原样。
- 凭据目录投影原存储状态、声明字段、已接入的 owner 管理接口和固定 OAuth 存储状态；稳定引用去重、不返回原文。模型配置复用原 API/存储。停用提供者不因目录查询而启动。
- 链接点击时记录来源、查询参数及滚动位置；返回资源/连接面板时等待该面板数据加载后恢复。浏览器用 30 行目录和延迟响应验证了恢复行为。

## 托管环境与进程边界

- 插件开发 Worker 与显式脚本本地 MCP 已接入共享 Python/Node 基础身份记录及独立依赖环境。Python 不继承 Core site-packages；Node 使用私有源码快照。
- venv、uv、npm 和显式 build 通过原 OS 沙箱执行。uv 使用固定工具版本；Python 要求带哈希的 `requirements.lock`，npm 要求完整 v2/v3 lock；构建不获得原始联网能力。
- 下载按内容哈希缓存并重新验证；Python 可复用兼容的离线锁定 wheel。npm 离线安装拒绝隐式安装脚本。环境/缓存命名空间拒绝符号链接跳转。
- 候选环境验证后原子选定，再发布插件能力，避免其他线程提前发现；失败保留旧环境指针；移除环境指针不删除凭据和业务数据。完整更新/回滚及所有来源安装流程尚未接齐。
- 第一方与第三方均使用 OS 沙箱；旧“信任”不再跳过隔离。清除了托管调用前后的旧信任门槛。发行摘要验证仍是第一方身份来源，旧信任撤销仍停止现有进程。
- Worker 的文件锁复用原机制，使用调用内的截止时间/取消检查，不再间接加载 Core 身份代码。域数据路径在启动前绑定；相关旧集成 fixture 已调整，仍保留跨域路径逃逸拒绝验证。
- Shell 和委派 Agent 各模式默认拒绝直接联网；共享解释器和 Core 代码对 Agent 只读。

## 连接、外发与凭据保护

- 独立连接意图与 revision 保存在现有安全库中。关闭同步撤销当前 socket/流，并拒绝新请求；重新开启不恢复旧请求或旧外发批准。跨 Core 变更由撤销 watcher 检查。
- 连接清单复用一份模型提供者目录，包含默认端点和真实配置键，按 origin 聚合；远程 MCP、公共浏览和包仓库接入独立开关。完整插件服务/其他连接目录仍待接入。
- 受控 HTTP 固定已验证 DNS 的数值地址，TLS 验证原主机；不使用代理环境变量、不自动跟随重定向、不开放 CONNECT/升级。公共浏览不能复用本地服务例外。真实卡住的 TLS 握手也能被连接撤销中断。
- SecurityService 外发批准复用原 Gateway、一次性确认与审计。绑定主体/任务、策略版本、连接版本、接收方、内容摘要、HTTP 操作、请求头、类别和期限；后台不自动批准。未知、凭据、受保护来源拒绝。
- requests 型模型、embedding、voice、vision 和远程 MCP 业务请求已接入受控传输。未适配的模型/语音 SDK、WebSocket 和私有浏览器执行明确阻止；不把关闭代理或刷新工具当作安全适配。
- `env_config` 从 Agent 发现/目录/提示词移除，直接调用也拒绝。原 `.env` 管理复用 owner-only adapter。`.env`、配置及 OAuth 保存使用受保护临时目录内的原子写入，修复旧可预测临时文件的符号链接覆盖。
- OAuth token 文件及旧临时文件进入统一文件工具/Shell 路径保护；目录只展示 owner 报告的 OAuth 状态。OAuth 网络协议本身尚未适配受控出口。
- 当前来源标签来自 Core 请求组装器，但完整数据来源传播、长期限定网站授权及全部后台调用接入尚未完成，不能宣称已实现完整防外发体系。

## 真实系统条件与业务限制

- 用户指定真实测试仅在 macOS。Linux 为 **NOT RUN**；Windows 不可信执行不提供无隔离降级。
- 本机 `es_new_client` 前置探测返回 **5 / not_privileged**。这只是资格探测，没有 guardian 部署或保护事件接入。Core/Electron 主进程与辅助进程明确未受应用级保护。
- 受保护文件夹规则保持不可用，未移动/删除用户文件。目录移动、别名、路径替换、旧句柄、索引和缓存的一致性撤销未实现/未验收。
- 系统 DNS 将公共包服务解析到 `198.18.0.0/15` 范围（本机实测 pypi.org 等）。受控出口拒绝该特殊地址；没有为下载增加绕过。产品受控下载尚未通过；开发侧已按官方 PyPI 摘要准备公共 uv/wheel 缓存，并通过真实隔离离线安装验证，没有修改产品地址限制。
- eLearning/email MCP 的独立锁依赖、自包含源码、固定业务存储绑定与配置读写回归已通过；邮箱旧环境凭据兼容已由下述回归验证；可信凭据连接器与真实账号受控联网仍待实现。不恢复通用原始凭据转交或 Core 依赖继承来掩盖问题。凭据和业务存储没有删除或迁移到另一套状态。
- 原生保护条件之外，仍有明确的实现待办：`uv.lock`/多来源安装、冻结 Worker 与 MCP 环境迁移、完整生命周期/回滚、Keychain 迁移、OAuth/插件分发等其余 Core 网络、可信来源传播、限定网站授权和公共浏览器隔离。

## 验证证据

日志与截图在 `.superpowers/sdd/2026-10-25-security-managed-runtime/`；工作区计划在 `.agents/tasks/archived/2026-10-25-security-managed-runtime.md`。

- 权限阶段独立审查的四个问题已 RED→GREEN 修复；当时针对回归 226 PASS，全量 2482 PASS /33 FAIL /22 SKIP。这是旧阶段证据，不能代表当前分支全量结果。
- 本轮针对连接撤销、TLS 握手、HTTP 操作绑定、公共/本地边界、命名空间跳转、离线 wheel、Worker 锁、语音/委派出口和 OAuth 临时文件分别保留失败复现与通过日志。
- `resources-scroll-green.log`：完整五页浏览器流程通过，包含延迟目录返回位置；模型凭据编辑竞态、默认/custom、旧入口与窄窗检查保留。桌面/窄窗截图已目视检查。
- TypeScript、Desktop ESLint、113 个相关 Python 文件 Ruff、`git diff --check` 通过；最初无关文件排除在本轮静态检查外。
- `project-pytest-continuation-final.log`（最新一轮部分修复前）：2525 PASS /40 FAIL /20 ERROR /22 SKIP。`continuation-failure-comparison.txt` 明确列出 50 个相对原 33 节点基线的新增失败/报错节点；不能称为“全是既有失败”。
- `targeted-2026-10-26.log`：198 PASS（两项已知 pydub 警告）；包含真实 macOS 沙箱、socket/TLS、独立依赖环境及安全权限。其后调整的插件发布顺序由 `environment-publication-green.log` 的 30 PASS 验证，并再次运行完整测试。
- `project-pytest-2026-10-26.log`：2545 PASS /39 FAIL /20 ERROR /22 SKIP，187.34 秒；这是发布顺序修复前的结果。该阶段 `project-pytest-latest.log` 为 **2546 PASS /39 FAIL /20 ERROR /22 SKIP，176.47 秒**。浏览器使用模拟 API 和临时数据；真实 OS/HTTP 测试使用合成数据，没有访问真实邮箱、学校账号或改变真实凭据。
- 最新全量失败/报错节点及相对原基线对比，逐项见 [完整测试诊断](../.superpowers/sdd/2026-10-25-security-managed-runtime/latest-test-diagnostics.md)。该阶段有 49 个相对原基线的新增失败/报错节点；本轮后续全量结果见下文。
- 本轮新只读审查因模型额度中断，没有取得最终意见；不标审查 PASS。已对其提出的具体关注点单独做作者复核与失败复现；完整计划完成后仍需独立审查。

## 实施判断与代价

1. guardian 未就绪时继续可独立实施的部分，不激活目录保护。代价：Core/Electron 及目录一致性保护仍未达到目标。
2. 自定义基准是权限上限，细则进一步限制；要放宽须先提高基准。代价：不能在只读基准中单独开放写入。
3. 保留任务/策略原跨库提交顺序，revision 不一致拒绝运行。代价：崩溃后可能需要用户修复任务权限。
4. 动态安装启动器、普通本地 MCP 的原始凭据转交及未适配协议保持阻止。代价：部分既有 MCP、模型/语音 SDK 和浏览器功能暂不可用；必须完成连接器与业务迁移后才能验收。

## 2026-10-26 MCP 独立依赖与输出权限续作

- 已读取最新目标文件 `a5518beb-a2d2-499e-b0af-d52f0698da69/goal-objective.md`。完整目标不变；新增全部完成后提交并合并的授权。目前尚未达到该条件。
- Core、email-mcp、fudan-elearning-mcp 均在 `refactor/security-managed-runtime` 专用分支；MCP 两仓新增精确版本/hash 的 requirements.lock，未安装到 Core、未提交轮子/工具缓存。
- 官方 PyPI 元数据校验过的 uv 0.13.5 与 wheel 缓存在开发验证目录；真实 uv 在隔离进程离线安装 MCP 依赖并验证不继承 Core site-packages。产品受控下载仍拒绝当前 DNS 特殊地址，没有放宽。
- Core 工厂绑定原业务存储与只读迁移目录；普通 JSON 同名字段不能授予根目录。固定 Core 提供者摘要验证失败时拒绝读取账号存储；用户信任标志不能建立第一方身份。
- 修复已撤销 MCP 可重新启动，以及空源码目录中的软链接漏检。已补调用前后完整实现摘要检查；非代码定义变化也会关闭连接。
- 文件同步从同一份既有设置读取目标，单次隔离子进程仅获该输出目录；不扩大常驻 MCP 权限，不改变工具定义、凭据或课程配置。输出仍经 SecurityService 原 Gateway 判断，目录替换、敏感/运行时根和软链接拒绝。
- 首轮相关回归 `mcp-scoped-output-regression.log`：86 PASS（2 项既有 pydub 警告）。随后调用期摘要检查 `mcp-live-pin-green.log`：21 PASS。完整测试和补充取消/活跃撤销验证正在进行，完成后补准确结果。
- 冻结分发包支持对全部文件及受限内部别名计算摘要；生产构建/签名后固定目录摘要的发布接入仍未完成，未提供固定 bundle 摘要时阻止访问私有存储，不能按开发源码摘要冒认冻结包。
- 完整计划依然未完成：原生应用 guardian、Keychain、可信连接器/真实账号受控联网、多来源环境与完整生命周期、来源传播/长期网站授权/公共浏览器、最终审查仍待完成。Linux 按用户要求不实测。

### 本轮最终验证与剩余问题

- 新增/修复的边界：撤销后新建客户端仍拒绝、源码空目录链接、完整实现输入复核、冻结包内部链接摘要、握手取消回收、父连接关闭/撤销回收输出子进程、精确输出目录与相邻目录隔离、邮箱/eLearning 旧目录凭据隔离。
- `mcp-boundary-latest.log`：**113 PASS**，65.42 秒；2 项 pydub 既有警告。使用合成账号、离线核验依赖及真实 macOS Seatbelt 进程，没有访问真实邮箱/学校账号。
- `project-pytest-mcp-final.log`：**2616 PASS /8 FAIL /22 SKIP**，340.36 秒，5 项警告。此为全量收集时的代码快照；随后新增的旧目录授权收窄由上述 113 项边界回归覆盖。完整失败节点和差异见 `mcp-test-diagnostics.md`，不得将 8 项一概标为既有缺陷。
- 8 项失败分布：外部 Agent CLI 4 项、公开提供者选择 2 项、后台简报权限 1 项、旧环境邮箱凭据兼容 1 项。已找到嵌套 Seatbelt 导致 CLI sandbox_apply 被拒绝；后续必须使用 Core 受控启动器，不能删除子进程隔离。后台系统流程与任务主体仍须正确归属；环境账号迁移必须保留初始化/删除事实与账号标识。
- MCP 元数据测试复用了其他测试已经启动的全局管理器，现改为隔离工作区；维护迁移测试在启动时绑定真实数据句柄，与生产迁移调用一致，保留实际 SQLite apply/verify 检查。相关 17 项回归和本轮全量均通过。
- Python 相关文件 Ruff 与 `git diff --check` 通过。完整计划尚未完成，未提交、合并、推送或发布。
- Ruling: 同步输出采用同一既有设置派生的单次进程权限，常驻 MCP 不扩权——复用环境/工具/账号而不维护第二份输出配置；代价是每次同步增加一次独立握手。
- Ruling: 未提供签名分发后的固定 bundle 摘要时拒绝冻结 MCP 的私有存储绑定——不能拿源码摘要或名字替代发行身份；代价是冻结版发布链接通摘要生成之前，相应能力不可用。
- 仍待后续完成：应用 guardian 与目录一致性保护、冻结签名发布接入、Keychain、可信联网连接器、Core 外部 Agent 启动器、多来源安装及完整生命周期、可信外发来源传播/长期授权/浏览器、完整独立审查。完整目标不缩减。

## 2026-10-26 Core 受控外部 Agent 与凭据兼容续作

上一目标回合分类：progress。继续读取完整目标，不缩减验收。Stella-Plugins 从原干净 develop 建立 refactor/security-managed-runtime；Core 和两个 MCP 分支保持不变，未提交/合并/推送。

- TDD delegated-launcher-red.log 复现嵌套 Seatbelt 的 sandbox_apply 拒绝。启动、版本探测、观察和取消现经原 core.invoke 私有回调，由 Core 持有实际进程；插件仅保留提供者适配。复用 ProcessSupervisor 与普通进程树回收，父 Worker 停止/崩溃、超时与策略变更使子进程终止。该进展不等于已解决 setsid 脱离、系统级旧句柄或全应用保护。
- 回调限定原 Hub 操作、Core invocationRef/代次、已存提供者二进制内容摘要和真实执行租约；插件不能提交 cwd/env/策略或创建第二份任务状态。目标与结果存储仍归 ExecutionService。
- 新第一方 gate RED→GREEN：用户 trust 或同名插件不能取得 Core launcher；不再从可变的兄弟仓库目录读取信任锚。复用 Core 现有 default-plugins.json 记录源摘要和随附 catalog；Stella-Plugins agent-hub 版本 1.0.5 及 official catalog 摘要已更新，8 个真实源树 pin 核对通过。未知新发行版本仍需可信发布元数据，后续发布/更新生命周期需接齐，不能自动放宽。
- AgentHub 相关测试改为当前 Stella-Plugins 源，未改旧临时副本。direct native UI 与真实 Agent/任务区分；已有工作区写入权限与 raw network 拒绝保留。delegated-first-party-green.log：81 PASS；插件仓库 tests：8 PASS。
- 邮箱原环境账号保持原 environment ID、MCP initialized/删除事实以及现有配置路径。仅 Core 工厂且精确匹配 Core pin 的邮箱连接器可接收其声明的 IMAP_PASSWORD；普通 MCP 原始凭据仍拒绝。移除 email 配置错误携带的 CANVAS_TOKEN；关闭 inherit_full_env 全量主环境继承；plain PASSWORD 等名称不再绕过检查。Keychain 与真实受控账号联网未完成，未把该兼容进展描述为凭据安全体系完成。
- mcp-owner-credentials-red.log 4 项失败复现；随后发现新增环境循环覆盖 runtime key，已修复并完整回归。mcp-owner-credentials-regression.log：82 PASS（2 项 pydub 警告）。没有访问真实账号或改写真实凭据。
- 后台简报默认只读误伤 RED→GREEN：仅 Core 原生业务工作流且源 pin 匹配、无 Agent/对话/任务/run 主体时不借用新主体默认。仍复用原资源限制、owner grant 和 peer 操作边界；存在真实 session/task 时不豁免其独立权限。native-background-green.log：74 PASS，包含只读对话/任务拒绝与原 peer 边界（2 项 pydub 警告）。
- 全量中间快照 project-pytest-launcher.log：2631 PASS /2 FAIL /22 SKIP，382.41 秒，5 项警告；两项为上述兼容/后台默认问题（已局部修复验证）。最新完整测试 project-pytest-launcher-credentials.log：2638 PASS /22 SKIP /0 FAIL，343.20 秒，5 项警告；完整计划的未实现项仍待后续验收。
- Ruling: Core 自身业务同步不使用新主体默认模板；仅有源身份、现有授权和明确 Core 工作流的无主体原生服务适用——默认模板仅定义后续新对话/用户独立任务；代价是此类系统流程必须一直维持明确入口，不能靠 background 标签授予 Agent 权限。
- Ruling: 第一方启动适配器必须是 Core pin 的发行内容，1.0.5 与对应 Core 改动联合验证——避免名字或 owner trust 变成文件/进程授权；代价是旧适配器和未来新发行不能跳过发布兼容验收。

完整目标仍未完成；后续至少包括实际 native guardian/目录身份事务、Keychain、发布签名/冻结摘要与功能兼容预检、完整多来源安装和生命周期、真实出口连接器/来源传播/长期网站授权/公共浏览器、整体验收及独立审查。Apple ES 当前资格不足和 Linux NOT RUN 的事实不变。

### 2026-10-26 阶段提交授权

用户最新要求「及时提交」，覆盖此前等待全部任务完成后才提交的时机约束。仅提交本任务已验证的阶段变更，保留原有无关改动；不合并 develop、不推送、不发布。完整目标保持实施中，最终独立审查及未实现项继续保留。

## 2026-10-26 候选环境预检与生命周期回收

- 更新预检移到维护窗口之前，按实际用户准备候选依赖并完成真实 Worker 握手；使用临时数据句柄和临时 HOME/cache，维护 Worker 不切换正式环境。预检失败仅记录 failed，旧包/环境/启停/活跃进程继续有效；不能复用失败操作绕过预检。
- 原进程持有集合接入 PluginManager 的更新、停用和移除；覆盖没有专用域网关的插件。按用户停用只回收该用户的进程，包更新回收所有旧实例。Worker 启动使用原 admission lease，拒绝已停用、维护中及过期选包。账号与业务存储保留。
- 回归真实复现并修复：候选可改写正式私有文件、验证失败使旧版进入维护、普通插件留下旧进程、已停用/旧版仍可启动、停用一个用户影响其他用户。candidate-owner-lifecycle-green.log 为 15 PASS；早先迁移/环境范围 candidate-lifecycle-regression.log 为 75 PASS。完整测试正在执行，终态另行记录。
- Ruling: 仅预检失败恢复旧版持续可用；进入 draining/真实数据迁移后失败继续阻止访问——可能已提交新数据，不能为了“回滚”让旧代码读取不兼容存储；代价是迁移错误仍需显式修复/恢复。

完整计划尚未完成，原生 guardian、受保护目录事务、Keychain、签名/冻结发行、多来源安装/剩余生命周期与受控连接器/来源传播/长期网站授权/公共浏览器及最终独立审查继续保留。

本轮补充验证：candidate-lifecycle-final-regression.log 为 106 PASS（生命周期、维护隔离、真实迁移/恢复、管理、分发与 bootstrap）。project-pytest-candidate-lifecycle.log 全量中间快照为 2646 PASS /1 FAIL /22 SKIP，354.41 秒，5 项既有警告；唯一失败 test_worker_crash_reaps_owned_cli_groups 的 fixture 在创建文件后尚未写入完成就被读取，JSONDecodeError 输入为空字符串。已将两个 PID 握手文件写完后原子发布，不修改生产进程回收或降低断言；candidate-cli-fixture-green.log 25 PASS。新增 draining 持久阶段中断/恢复覆盖。最新全量 project-pytest-candidate-final.log：2649 PASS /22 SKIP /0 FAIL，350.74 秒，5 项既有警告。运行命令：python scripts/run_isolated.py python -m pytest tests -q -o usefixtures=offline_runtime_artifacts（使用 /opt/anaconda3/envs/Stella/bin/python）。

本轮验收：上述最新完整后端测试终态通过；相关 5 个 Python 文件 Ruff 和 git diff --check 通过。原生系统 guardian / Keychain / 完整多来源与冻结发行 / 受控连接器和外发来源链路等完整目标仍未完成，不执行合并、推送或发布。

## 2026-10-26 插件分发下载与签名包边界

- 原 catalog 远程下载改用既有受控 HTTP 和 connection leases；关闭 public-packages 或实际服务会拒绝新下载并撤销活跃 socket，重新开启不复活旧请求。保留本地相对包/file URL 的离线安装。下载/解包使用每次独立临时 staging，失败完整清理；旧选包及原有账号/配置/业务数据不变。
- 继续校验原签名及源码树摘要；解包前限制包装大小、展开大小及成员数，拒绝路径跳转、大小写/Unicode 别名、重复成员、文件/目录冲突与特殊文件/链接。先验证并提取到临时目录，再发布全新候选；不会删除或覆盖已有解包目标。未变更既有签名格式或凭据/信任锚，完整签名发行仍未验收。
- 真实 HTTP 测试 4 项 RED→GREEN，覆盖连接停用/进行中撤销/重开/错误响应与重定向拒绝；真实签名非法 ZIP 6 项 RED→GREEN。下载失败原因改为保留可信拒绝说明，PluginManager 传递给用户，避免仅显示笼统 failed；原始远程响应正文和本地路径异常不外显。
- distribution-boundary-regression.log：99 PASS（1 项构造重复 ZIP 的预期警告）；追加原因传递后 distribution-reason-green.log：39 PASS（同一预期警告，现已由 pytest.warns 收集）。Ruff/空白检查通过。中间完整后端 project-pytest-distribution-final.log 为 2658 PASS /1 FAIL /22 SKIP，失败及处理记录见后文。合成测试仅在 fixture 限定一个 loopback origin，生产公共地址限制不变；没有访问真实账户。
- Ruling: 解包只接受未占用的目标目录，不替换调用者已有目录——下载路径使用独立 staging，不需要覆盖旧目录；代价是历史手工重用同一解包路径必须由调用者明确另选路径，避免错误清理有效数据。

完整目标仍实施中：原生 guardian 与目录事务、Keychain、完整多来源安装/剩余生命周期、签名/冻结发行、可信连接器和来源传播/长期授权/公共浏览器及最终独立审查继续保留。

本地导入关联修复：原 copytree 会跟随项目内文件/目录/清单链接，已用实际文件 3 项 RED 复现；PackageStore 在读取 manifest、复制、inspect/prepare/commit 前拒绝内部链接，复制时保留链接并复查，复用原 FAILED 状态。显式选择的根目录别名仍兼容。补充内容相同但链接替换的提交绕过 RED→GREEN。distribution-local-boundary-regression.log 为 106 PASS，无警告。阶段提交 9087e80a。这不等于已证明应用级目录身份/旧句柄/任意 OS 竞争无窗口，原生 guardian 与一致性事务仍待完成。

全量中间快照 project-pytest-distribution-final.log：2658 PASS /1 FAIL /22 SKIP，362.73 秒，5 项既有警告；唯一失败 test_absolute_http_url_installs_signed_package 原先把 loopback 当作公共 CDN，现被受控传输正确拒绝。已在该测试先断言私网地址拒绝且不安装，再以 fixture 仅允许该单一合成 origin，保留真实下载、签名校验和 bootstrap 行为；没有修改生产私网策略。distribution-loopback-boundary-green.log：27 PASS，无警告。随后完整后端（包含本地 alias 修复）project-pytest-distribution-boundary-final.log 为 2665 PASS /1 FAIL /22 SKIP，详见后文简报失败诊断。

project-pytest-distribution-boundary-final.log：2665 PASS /1 FAIL /22 SKIP，352.20 秒，5 项警告。唯一失败 test_native_reminder_configuration_reaches_task_owner；日志显示前一个创建待办的业务状态已 failed，但原断言只检查操作封套 succeeded，随后提醒无有效关联待办。已加强先前业务状态断言，不改业务/权限规则。单用例 1 PASS、整文件 33 PASS、前置 42 个模块 401 PASS /4 SKIP；保留完整测试模块导入但只执行同一前缀也为 401 PASS /4 SKIP（2283 deselected）。尚未重现原始业务失败的具体原因，不能宣称问题已修复或归为既有失败。新完整测试 project-pytest-distribution-verified.log 使用增强断言继续核验；无论结果均保留本次失败证据。

本轮最新完整验证 project-pytest-distribution-verified.log：**2666 PASS /22 SKIP /0 FAIL，346.11 秒，5 项既有警告**。运行命令为 python scripts/run_isolated.py python -m pytest tests -q -o usefixtures=offline_runtime_artifacts（/opt/anaconda3/envs/Stella/bin/python）。增强断言后的简报用例本次通过，先前间歇业务失败仍未稳定复现，不标为已修复；保留原失败与 401 项顺序复现证据。相关 Python Ruff、git diff --check 通过。最终独立审查、原生 guardian、Keychain、多来源安装/剩余生命周期、签名冻结发行、受控连接器和完整来源传播/长期授权/公共浏览器仍待完成；不合并、不推送、不发布。

## 2026-10-26 插件依赖环境重建与恢复

- 在现有安全 → 运行隔离列表接通已安装源码插件的重建及中断后继续重建；复用 ManagementStore operation、EnvironmentStore 和 PluginSession，不从 catalog 更新代码，不增加凭据或工具开关存储。按实际用户固定候选，通过临时数据/维护 HOME 中的真实 Worker 握手后才停止该用户旧进程并选择新依赖环境；账号、配置、业务数据和启停保留。其他用户进程不停止，已停用插件不自动启用。
- 普通 Python/Node 准备优先使用当前已选中且匹配的环境，避免重建后又回到旧缓存；元数据绑定真实 owner，非法/损坏记录保守拒绝并允许显式重建恢复。候选验证失败标记 failed，保留旧指针和旧 Worker，不能被当作 ready 缓存。
- 使用原 prepared/draining/selected/completed 事实恢复，UI 只提供当前用户自己的重建恢复。其他未完成安装/迁移存在时不允许重建或恢复重建重新开放访问；固定源或启停意图已变更时拒绝。Agent/任务身份不能触发 API，旧 trusted/digest 输入也拒绝。
- 本轮仅接通源码插件环境；冻结 runner/无独立依赖运行时/缺少 OS 隔离显示实际不支持原因，远程 MCP 不出现本地重建按钮。MCP 的同等生命周期与冻结发行仍属于完整目标待办，没有将其标为完成。
- RED→GREEN：旧缓存选择、跨 owner 元数据、真实 Worker 新环境、失败保留、损坏指针恢复、停用/多用户、阶段中断、并行未完成操作、API owner 和 UI 成败/恢复。plugin-rebuild-backend-regression.log 79 PASS；plugin-rebuild-resume-api-green.log 27 PASS；损坏指针回归初次出现 9 项 fixture 错误，原因是测试在共享环境命名空间留下故意损坏指针，现将该 fixture 的 shared_root 按 tmp_path 隔离；plugin-rebuild-repair-regression.log 为 86 PASS（2 项 pydub 警告）。
- project-pytest-plugin-rebuild.log（损坏指针补充前快照）：2679 PASS /22 SKIP /0 FAIL，339.82 秒。最新完整后端 project-pytest-plugin-rebuild-final.log：2680 PASS /1 FAIL /22 SKIP，342.34 秒，5 项警告。唯一失败 test_mcp_download_repeats_and_cancellation_prevents_late_publication：取消后未发布 PDF，但输出目录遗留 .elearning-* 临时文件。不能把旧全绿快照当作当前结果，也未将该失败归为既有缺陷；需继续定位取消/进程终止与临时文件清理竞态。
- 浏览器先复用到辅助脚本遗留的本次 Vite 子进程，HMR 期间对话切换测试出现状态不一致；已核对 PID/启动命令/目录后仅清理本次进程，改用 exec 启动直接管理 Vite，之后完整五页/权限/凭据回归恢复通过，没有修改产品对话权限逻辑。plugin-rebuild-browser-clean-server.log 与后续 verified 日志保留；320px 下使用现有折叠侧栏验证操作可达，无横向溢出。目视截图发现执行环境标题颜色过浅，增加现有 text-content token，并以实际对比度浏览器断言 RED→GREEN；未改全局 UI 设计。
- Ruling: 重建仅切换依赖，不迁移或回退业务 schema，成功后按现有按需机制在下次使用启动新 Worker——保留数据与原机制；代价是主动重建会停止该用户当前 Worker，UI 已明确说明。
- Ruling: 未完成的其他安装/迁移先恢复，不让依赖重建绕过维护门——避免旧源重新读取已迁移的数据；代价是需在原操作链完成恢复后再重建。

完整目标仍实施中，尚未合并/推送/发布。后续优先处理 MCP 取消遗留文件与统一生命周期；原生 guardian/目录身份事务、Keychain、多来源安装、签名/冻结发行、可信连接器、来源传播/长期授权/公共浏览器、五页最终视觉验收和完整独立审查继续保留。先前简报待办间歇业务失败仍保留追踪，未宣称已修复。

本轮交付浏览器日志 plugin-rebuild-browser-delivery.log：完整流程 PASS，含重建忙态/失败保留/重试/中断继续、Owner 请求体、现有五页与凭据流程、320px 折叠侧栏下按钮滚动可达、执行环境标题对比度。TypeScript renderer/main 与相关 ESLint、Python Ruff、空白检查 PASS。最新全量仍为 2680 PASS /1 FAIL /22 SKIP，不以早先 2679 全绿快照覆盖当前 MCP 取消临时文件失败。

## 2026-10-26 MCP 输出操作临时文件回收

- 真实强制终止测试复现 .elearning-* 残留：Worker 的异常清理无法覆盖进程被终止。现在 Core 为单次输出操作创建并持有独立临时目录的目录句柄，子进程退出后清理该命名空间；不扫描用户输出目录、不按文件名前缀删除文件。
- eLearning 1.0.3 使用该临时目录下载，验证后仍通过原独占硬链接发布；原账号、课程范围、输出配置、文件/游标存储及本地编辑保留。独立运行未提供 Core 临时目录时继续原行为。Core 源码 pin 已同步；MCP 阶段提交 3a5f792。
- 临时目录清理不跟随链接，输出根移动后仍只回收原目录；命名空间被替换时保留替换目录并报告异常。普通配置的 STELLA_MCP_OUTPUT_TEMP 不构成授权，只从匹配的 Core 内部操作对象注入。该局部回收不等于应用 guardian、Core 崩溃恢复或任意旧句柄保护已经完成。
- RED→GREEN：强制退出残留、命名空间/输出根替换、链接不跟随、Core 参数防伪与真实 Seatbelt 相邻目录拒绝。mcp-output-final-regression.log：40 PASS，26.23 秒。MCP 全套：122 PASS /12 subtests PASS，0.74 秒；相关 Ruff 和空白检查通过，未访问真实账号。
- 首次不带 tests/ 的 pytest 误收集 references/ 中第三方测试，17 项收集错误（zstandard/deepseek_harness 缺失、CODEX_REPO_ROOT 未设置及同名测试模块冲突），日志 project-pytest-mcp-temporary.log 保留。现按 Makefile 的 tests/ 项目范围完整执行；终态另行记录。
- Ruling: 临时下载与最终输出必须可进行同文件系统独占发布；目标子目录跨挂载导致硬链接失败时明确失败，不退回失去 Core 回收归属的目标目录临时文件——代价是此类特殊目录布局暂不支持同步。

完整目标仍实施中：应用级 guardian/目录事务、Keychain、MCP/多来源完整生命周期、签名冻结发行、可信连接器/来源传播/网站授权/公共浏览器及最终独立审查仍待完成。Linux 按用户要求 NOT RUN，Apple ES 资格不足的事实不变；不合并、推送或发布。

本轮最新完整验证 project-pytest-mcp-temporary-final.log：**2687 PASS /22 SKIP /0 FAIL，346.09 秒，5 项既有警告**。命令按项目 Makefile 范围运行：python scripts/run_isolated.py python -m pytest tests -q -o usefixtures=offline_runtime_artifacts（/opt/anaconda3/envs/Stella/bin/python）。上轮取消残留文件失败本次通过，另有强制退出稳定复现测试覆盖；先前简报间歇业务失败本轮未出现，仍不标为已修复。Core 的 email/eLearning 两项源 pin 与实际源树核对一致，MCP 122 项测试与相关静态检查通过。完整目标及最终独立审查仍未完成，尚未合并/推送/发布。

## 2026-10-26 MCP 依赖环境重建与恢复

- 本地显式 Python/Node MCP 接入现有 EnvironmentStore 候选重建和 ManagementStore operation 事实；使用保留命名空间 mcp:，未新增 MCP 启停、工具开关或账号配置存储。远程 MCP、冻结/不透明可执行入口不提供依赖重建，显示实际原因。
- 候选使用 Core 内部维护配置、临时 HOME/业务目录、不带原迁移根及 owner 凭据环境变量；经过真实 initialize 与完整 tools/list 后才进入切换。普通 JSON 的 maintenance/rebuild_environment/maintenance_storage 不授予维护权限。真实 Seatbelt 探测证明候选读取原账号目录被拒绝，原 eLearning 业务文件字节保持一致。
- 原选包、账号、业务记录及所有工具/提供者开关保留。候选失败保留旧活跃进程与指针，失败环境不可被 ready 缓存再选中。通过后停止当前应用对应 user/launch 的旧连接，复用 ToolManager teardown、代次与工具投影；下次使用按需启动新环境，其他用户进程继续运行。
- 持久 draining/selected 维护门和原跨进程共享 admission lease 覆盖初始化、发现及工具调用；晚到发现还复查环境身份和连接代次。prepared/draining/selected 中断支持同一用户/工作区继续，其他工作区不能消耗或失败原操作；来源或该提供者开关变更拒绝原候选，其他提供者开关不误伤。Agent/任务不能调用重建 API。
- 切换指针与操作记录跨存储，选择期间发生异常保留维护阻止与恢复入口，不将不确定结果标成 failed 后直接恢复调用。复用安全 → 运行隔离的按钮、API 和反馈，本地 MCP 与插件一致支持重建及继续；未改能力详情布局。
- RED→GREEN：缺失重建/恢复模块、对象归属/单项 revision、API 投影、普通与维护进程、操作日志失败及浏览器 MCP 按钮。Node 实际进程初次因读取 Homebrew OpenSSL 配置被沙箱拒绝，现与隔离构建同样使用 /dev/null 配置；没有开放宿主配置目录。mcp-rebuild-isolated-node-green.log 为 22 PASS；mcp-rebuild-admission-green.log 为 4 PASS（含晚到初始化租约），mcp-rebuild-regression-green.log 是补充 Node/故障注入前 92 PASS 的中间快照。
- 中间回归 4 项工具发现失败：协议 Mock 未声明 _runtime_key，自动生成了假的本地身份；夹具明确无进程身份，未削弱生产检查。真实域用例的离线依赖曾因测试先准备缓存、随后覆盖 shared_root 而失败，已保持同一隔离运行根，没有放宽网络限制。
- 全量中间运行发现 test_runtime_inventory_separates_application_and_child_sandbox 失败：空 MCP 清单提前构造生命周期对象；改为只在实际本地提供者上构造，原应用保护断言保持不变。mcp-rebuild-inventory-green.log：73 PASS（2 项 pydub 既有警告）。中间全量终态及最新完整验证待后文记录。
- 桌面/320px 浏览器重建、失败保留、继续恢复、Owner 请求体、五页和原凭据流程通过；TypeScript renderer/main、相关 ESLint、Python Ruff 和空白检查通过。
- Ruling: 只复用既有 MCP 身份的操作事实与依赖选择；工作区绑定恢复入口，当前应用回收精确 user/launch 进程——避免跨用户/不同提供者误回收；代价是这不证明多个独立 Core 实例或脱离进程的即时全应用回收，仍需原生 guardian 验收。
- Ruling: 选择结果不确定时保持维护并要求恢复，不冒称已回滚——跨文件/SQLite 无原子提交；代价是部分 I/O 故障需用户显式继续恢复，期间相关执行保持阻止。

完整目标仍实施中：原生 guardian/目录事务、Keychain、完整多来源安装/更新/卸载及冻结签名发行、可信连接器/来源传播/限定网站授权/公共浏览器与最终独立审查仍待完成。macOS-only 实测约束、ES 资格不足和 Linux NOT RUN 的事实不变，不合并、推送或发布。

本轮全量中间快照 project-pytest-mcp-rebuild.log：**2709 PASS /1 FAIL /22 SKIP，380.47 秒，5 项既有警告**。唯一失败 test_runtime_inventory_separates_application_and_child_sandbox 为上述空清单提前构造生命周期对象，已由 73 项相关回归确认修复。未把该中间快照标为全绿；正在执行最终代码快照 project-pytest-mcp-rebuild-final.log。mcp-rebuild-browser-visual.log 完整流程 PASS；桌面及 320px 折叠侧栏截图已目视检查，本地 MCP 重建按钮可达、内容无横向溢出；TypeScript renderer/main 和对应 ESLint 终态通过。

## 2026-10-27 同名 MCP 执行环境身份边界补充

- 先前 project-pytest-mcp-rebuild-final.log 为 2710 PASS /22 SKIP（374.49 秒，5 项既有警告），但随后作者复核发现同一用户同名、不同实际启动配置的环境指针/维护门互相干扰，不能作为最终快照。真实不同连接 RED 及停用重建后重启未使用候选 RED 均已保留。
- 环境身份从真正的 command/args/env 和代码派生，扩展原 EnvironmentStore 的 owner 命名空间、指纹、当前选择及 admission lease；启停和展示字段不改变依赖身份。原 RuntimeTrust 的完整配置摘要匹配继续使用原算法，未重置旧撤销事实。维护配置绑定原执行身份，在临时数据目录验证，回到正式启动仍选择同一环境。
- 同用户同名不同启动配置在另一方维护期间仍可调用，另一方完成重建后仍保留原进程/选择；停用时重建再启用使用同一候选。环境记录、指针与锁显式绑定执行摘要，原未范围化的依赖缓存保留，没有移动/清空账号或业务文件。插件和原无额外身份的 EnvironmentStore 调用保持原路径与指纹。
- 中间代码出现函数与局部变量同名导致初始化 UnboundLocalError，已由 Ruff 与测试直接定位并改名；没有压低拒绝断言。后续范围化回归 mcp-rebuild-bound-scopes-green.log 为 102 PASS /1 FAIL；唯一失败是旧测试仍从无执行身份的槽位读取当前环境，现按实际 MCP 记录的 launch_digest 查询，独立环境/真实 echo 断言保留。最终范围回归与全量仍需后文终态。
- Ruling: 执行环境范围排除 enabled、超时和 UI 元数据，但撤销身份不改算法——重建后重新启用不会丢失选择，不把不同真实启动配置当成同一个环境；代价是改变实际启动参数/环境值会准备新的私有依赖命名空间，旧依赖缓存暂保留。
- 尚未完成的相关范围：普通 MCP 的私有运行 HOME 目前仍按 name/user 派生（mcp_runtime.prepare_mcp），同名不同启动配置的历史私有文件归属仍需验证迁移，不能以依赖环境分离宣称业务私有目录迁移已完成。旧目录缺少可靠来源元数据，后续必须保留旧数据且避免猜测归属/自动扩大访问。此项继续属于完整目标待办。

最终快照 project-pytest-mcp-rebuild-scoped-final.log：**2712 PASS /22 SKIP /0 FAIL，432.37 秒，5 项既有警告**。命令：/opt/anaconda3/envs/Stella/bin/python scripts/run_isolated.py /opt/anaconda3/envs/Stella/bin/python -m pytest tests -q -o usefixtures=offline_runtime_artifacts。包含最终执行身份、独立当前指针、同用户同名不同服务维护期间可用、重新启用复用候选、Agent/任务拒绝、实际 Python/Node/Seatbelt 与旧测试 scoped lookup。mcp-scoped-selection-lookup-green.log 单项 1 PASS。浏览器桌面/320px、TypeScript renderer/main、相关 ESLint/Ruff/空白检查通过，相关前端代码在执行身份补充后未变化；先前失败日志均保留，没有把中间全绿结果当作最终范围证据。先前简报间歇业务失败本次未出现，仍未宣称已修复。完整目标及最终独立审查仍未完成；本轮仅提交已验证阶段，不合并/推送/发布。

## 2026-10-27 MCP 私有运行目录边界

- 真实 stdio/Seatbelt RED 复现：同用户同名的两个不同服务可共用 HOME 读取/改写私有文件；完全访问下文件工具与 Shell 可读其他逻辑用户的 MCP 私有文件。现在普通 MCP 按实际配置族与实现摘要持有独立 HOME/cwd/tmp，新目录是旧递归授权根的兄弟目录，旧进程的递归访问不覆盖新目录。
- 复用原统一凭据/文件检查保护已知 mcp-runtime 根；固定 Core users 布局的 MCP 私有路径同样拒绝跨用户文件读取，不扫描其他人的文件。Shell 将 Core 管理的 users 私有数据根作为受保护范围（原本已保护当前用户根），避免完全访问模式跨逻辑用户读取；普通工作区布局保持原机制。
- 旧数据只检查 Core 已知路径的目录项，不读取文件内容、不遍历或跟随链接、不清空/移动目录。普通第三方旧目录含来源不明数据时阻止自动启动；实现变化且上一版本已有私有数据时也阻止静默切换空目录或直接继承，保留全部旧文件。运行隔离清单只读展示真实迁移原因，不通过查询启动或修改提供者。
- 固定 Core pin 的 eLearning/email 连接器继续其已声明的同一业务 data_root/旧迁移根；历史运行缓存保留且不授予新子进程。账号/密码/课程范围仍由原存储与原 API 管理，不建立第二份配置或访问授权。验证了真实 eLearning 保存账号后重连保持原配置，输出不含原始密码。
- RED→GREEN：同名实际服务互访、文件工具当前/其他用户读取、其他用户 Shell 读取、源码更新保留旧私有数据、空旧目录不复用、旧递归授权不覆盖新目录、链接不跟随，以及只读运行清单的迁移原因。mcp-private-boundary-regression.log：97 PASS，46.45 秒（2 项 pydub 既有警告）；相关 Ruff/空白检查通过。完整后端正在执行，终态另记。
- Ruling: 来源不明旧数据或新实现的既有私有数据，不自动归给新的 Worker——优先防止跨提供者读取及隐式丢失/继承；代价是这类普通 MCP 暂停启动，仍需完整目标中的来源确认/受控更新迁移入口，不能把磁盘保留或拒绝执行标为迁移完成。

尚未完成：归属不明/版本变化数据的 owner 迁移流程与状态入口、完整 MCP 安装更新卸载及签名冻结发行、Keychain/可信连接器、原生 guardian/目录一致性事务、外发来源/网站授权/浏览器及最终独立审查。仍未合并、推送或发布。

中间全量 project-pytest-mcp-private-storage.log：2722 PASS /2 FAIL /22 SKIP，436.86 秒，5 项既有警告。失败为 test_mcp_disable_terminates_a_real_process_during_initialize、test_mcp_trust_revoke_stops_ordinary_descendant；两者夹具写死旧运行根，进程提前 FileNotFoundError，现将子进程标记写入实际 cwd，父进程用可信 private_storage 推导同一位置，保留关闭/撤销回收与心跳停止断言。mcp-private-process-fixtures-green.log：69 PASS（2 项 pydub 警告）。额外 mcp-private-hardlink.log：1 PASS，真实 Seatbelt 不允许另一个提供者通过硬链接读取/改写私有文件。当前产品代码未因夹具适配改变，正在执行含全部 13 个私有目录用例的最终完整验证。原生 guardian/任意别名与旧句柄一致性仍未实现，不能以这次静态隔离测试宣称全应用保护完成。

最终私有目录快照 project-pytest-mcp-private-storage-final.log：**2725 PASS /22 SKIP /0 FAIL，428.25 秒，5 项既有警告**，包含 13 个新私有目录用例及修正后的真实启停/撤销夹具。命令为 /opt/anaconda3/envs/Stella/bin/python scripts/run_isolated.py /opt/anaconda3/envs/Stella/bin/python -m pytest tests -q -o usefixtures=offline_runtime_artifacts。相关 Python Ruff 与空白检查通过；本轮未改变前端、MCP 提供者源码、账号或凭据存储。此前简报间歇失败本次未出现，仍未标为已修复。完整目标与归属/更新迁移流程仍未完成；不合并、推送或发布。


## 本地扩展源码读取边界

- 普通原生 MCP 可执行入口只获得自身文件及运行库的读取权限，不再自动开放父目录。真实 C 探针先在无沙箱对照中读到相邻合成私有文件，再确认 Seatbelt 中程序已启动、仅文件读取失败（退出码 3），避免将加载失败算作隔离成功。
- Python/Node 源码执行、依赖准备及本地插件暂存前，统一检查源码根是否覆盖已登记的 Core 凭据/私有存储；发现重叠即拒绝，不扫描普通文件寻找凭据，也不复制或删除原数据。Core 已绑定的构建候选继续复用原沙箱授权。
- 原运行信任摘要算法与第一方实现 pin 保持不变；本轮没有改变工具开关、业务配置、凭据存储或前端。
- RED 已由原生相邻文件泄漏、含 Core config.json 的源码启动以及 Python/Node/插件暂存实测确认。相关回归 source-read-scope-regression.log：113 PASS（2 项既有 pydub 警告）；加强程序启动断言后 source-read-probe-confirmed.log：5 PASS。相关 Ruff 与空白检查通过。完整后端 project-pytest-source-read-scope.log 正在运行，尚不报告其终态。
- 尚未完成：完整 owner 数据归属迁移、多来源安装/更新/卸载、Keychain/可信连接器、原生保护组件与目录事务、完整外发链路及最终独立审查。此次局部隔离不代表 Core/Electron 已被保护；不合并、推送或发布。


全量中间运行暴露 automation 两项、bills 一项、briefing 一项集成测试使用未注册的 agent-a/test-agent。分别独立复现 StateDirError 后，三个测试文件复用现有 registered_agent 夹具补齐真实注册，保留 CRUD、任务权限上限、路由、租约和业务模型断言；没有为未知身份添加生产回退。source-scope-registered-fixtures.log：48 PASS，7.92 秒，相关 Ruff PASS。首次全量仍在运行，其四项已发生的失败会保留并报告，不能将此运行改称全绿。


## 第一方 MCP 版本报告与联合验证

两个真实 stdio 初始化都报告 SDK 1.30.0，分别与 eLearning 1.0.3/email 1.0.2 的原发行元数据不符（*-version-red-venv.log）。修复为显式传入随包发布的 __version__，并同步版本为 eLearning 1.0.4/email 1.0.3；真实握手测试逐项核对 pyproject 发行版本，避免通过其他安装的同名包推测版本。未将刷新或重新连接当成检查更新。两个提供者完整测试分别 123 PASS +12 subtests、48 PASS，Ruff/空白检查通过。Core 整包来源摘要同步更新，原来源 pin 与撤销逻辑未放宽。

验证过程纠正：最初 Core 解释器没有 MCP SDK，握手测试导入失败不是产品 RED；改用各仓库已有 .venv 后才取得上述真实版本不符 RED。我在首次 Core 全量期间新增 MCP 测试文件，而原整包摘要包含测试文件，破坏快照一致性。该运行已主动中止并保留：1486 PASS /16 FAIL /4 SKIP，298.43 秒，其中四项未注册身份已由 48 项回归验证修正，其余项同时受到未更新来源 pin 的影响，不能当作最终产品结论。现在固定两个 MCP 的完整内容和 Core 摘要重新执行 project-pytest-source-and-versions-final.log，等待终态。不会复用被中止运行冒称全量通过。


## 本轮固定快照验证终态

- project-pytest-source-and-versions-final.log：**2728 PASS /2 FAIL /22 SKIP，438.98 秒，5 项既有警告**。完整命令：/opt/anaconda3/envs/Stella/bin/python scripts/run_isolated.py /opt/anaconda3/envs/Stella/bin/python -m pytest tests -q -o usefixtures=offline_runtime_artifacts。
- FAIL 1：tests/test_todos_plugin_gateway.py::test_application_port_routes_todos_via_plugin，同样使用未注册 test-agent。复用 registered_agent 补齐注册，保留真实待办业务与权限断言，无生产回退。
- FAIL 2：tests/test_briefing_peer_authorization.py::test_nondefault_user_uses_only_its_own_owner_grants[alice]，原生跨 Worker 创建待办返回 failed。全量日志未显示 receipt.error（字典被断言格式截断），单项 1 PASS、整个原生权限模块 33 PASS；仍未定位原因，不能视为已修复或直接认定既有失败。已调整该项断言失败说明仅明确展示 error/result，成功条件不变，便于后续稳定复现诊断。
- source-scope-todos-peer-final.log：上述夹具和诊断变更后 **50 PASS，8.94 秒**，Ruff/空白检查通过。未再次运行完整后端，不将该局部通过代替有两项失败的完整结果。
- 本轮提交：Core 5c89bdf3（源码/执行文件读取范围）、026abe3d（自动化/账单/简报身份夹具）、16071ad4（真实版本对应来源 pin）；eLearning 67cc9cf、email 7d471f7（真实服务版本）。后续待办夹具/失败诊断和本记录独立提交。两个提供者源摘要已再次核对匹配，账号、凭据和业务配置未变更，用户原有 7 项无关改动保留。

完整目标继续实施，未做最终独立审查；本轮不合并 develop、不推送、不发布。接续优先调查非默认用户简报间歇失败，并推进已有数据归属确认/受控迁移、Keychain/可信连接器、环境多来源生命周期和外发链路。ES entitlement/原生 guardian 仍不可用，Core/Electron 不宣称已保护；Linux 按用户要求 NOT RUN。


## 顺序验证与间歇失败诊断

前一目标回合分类为 progress：已提交源码读取边界、身份夹具、真实 MCP 版本与来源 pin。此回合固定同一源码/依赖/摘要快照运行 peer-failure-ordered-diagnostic.log：**2730 PASS /22 SKIP /0 FAIL，436.00 秒，5 项既有警告**；命令与前轮完整后端一致，额外 -x 以便捕获首个失败，但本次全部完成。40 个独立非默认用户真实 Worker 重复用例 peer-native-reproduction-cases.log：40 PASS，16.30 秒，没有复现。最初重复同一 pytest node 参数被去重，仅 1 PASS，未误报为 30 次验证。探针在隔离临时目录，生产代码、测试来源与两个 MCP pin 均未变更。

本次全量通过证明待办注册夹具后的现有快照可通过完整回归，不能证明简报间歇问题已修复；保留前轮业务 failed 与所有失败日志，现有成功条件不变，后续继续依据 receipt.error/result 诊断，不猜测增加授权或自动重试。


## 环境凭据接入 macOS Keychain

- EnvironmentFile 保持原 owner API/配置路径，macOS 保存采用 Security.framework 的精确 service/account 引用，不使用带密钥的命令行或扫描系统钥匙串。先创建新的随机版本、回读比对，再用原 private_atomic_write 发布 .env 引用；引用绑定规范配置路径与字段，复制到另一个配置目录不能解密。旧明文仅在 owner 成功保存时切换，读取旧文件不会自动改写。非 macOS 保留原存储兼容，无法解析 macOS 引用时拒绝把引用当作凭据使用；Linux 实测仍 NOT RUN。
- 启动与热加载复用同一解析器；条目丢失/锁定时清理相关旧环境值，绝不把引用原样传给业务。原凭据目录只查询已登记条目的属性，不请求原文，不枚举系统 Keychain。真实锁定测试不触发认证 UI，原文件保持不变。Agent/任务仍无法使用 owner 管理接口；真实 Seatbelt Worker 即使可读测试 Keychain 密文文件，也不能经系统服务解密。
- 原设置保存失败时不提前改进程环境；文件发布前失败保留旧值。额外故障注入发现 rename 已成功、后续 fsync 报错时若清理新 Keychain 值会破坏已发布引用，已改为发布状态不确定时保留新旧加密版本，不冒称原子回滚；要求重新加载核对实际状态。加密旧项清理异常只记录无原文的待清理状态，不修改资源授权。
- RED：原 .env 明文保存、Keychain 不可用仍覆盖、启动/原子发布（4 FAIL）；发布后 I/O 异常（1 FAIL）；空凭据状态（1 FAIL）。对 owner 元数据与保存进程环境的回归还实际切回 HEAD 原 center 逻辑复现 2 FAIL，随后恢复实现，没有弱化断言。
- keychain-native-boundaries.log：13 PASS（真实临时 Keychain、锁定、丢失、配置域隔离、回读、发布不确定、启动/热加载、元数据、Worker 拒绝）。keychain-credential-regression-final.log：**114 PASS，7.44 秒，2 项既有 pydub 警告**；Ruff/空白检查通过。所有测试使用各自临时 Keychain，创建后立即恢复原搜索列表，退出时仅解锁/删除自己创建的测试 Keychain，不访问真实账号。
- 中间关联回归 112 PASS /2 FAIL 为新测试对 os.environ 的残留，导致原 MCP 入口测试先被原始凭据检查拒绝。现以 monkeypatch.setenv 注册加载前状态并自动恢复，原入口和原拒绝检查不变。过早启动的全量已主动停止（258 PASS /1 SKIP，49.47 秒），不是完整验证；固定最终测试夹具后另行执行全量，结果待后文。

本阶段覆盖原环境凭据存储；模型 config.json、邮箱/课程、MCP/OAuth 的安全存储与可信连接器迁移、数据归属迁移、原生保护组件/目录事务、其余托管来源和外发链路仍属完整目标，未标为全部完成。Core/Electron 仍无已验证的原生 guardian。没有改动真实账号、MCP/插件源码、工具开关或对话/任务权限。


## 环境凭据的 owner 修复与状态展示

- 实际缺失条目修复 RED：旧入口在保存前读取全部凭据，导致缺失旧值时无法重新设置。现在 EnvironmentFile 的同一原子保存实现支持按字段更新/删除：未改动的加密引用原样保留，无需解密其他凭据；旧明文仍在 owner 保存时验证迁移。复用原 set_secret 管理 API，不引入第二套配置或授权。缺失条目可以重新设置/删除，其他同样不可用的条目不被清除或解密。
- 配置表单此前按列表存在性显示“已配置”，导致 configured=False/None 不真实；浏览器 RED 已复现。现按实际三态显示已配置、未配置或状态不可用，保持原布局与空密码输入。TypeScript renderer/main、对应三文件 ESLint、Ruff 与空白检查通过。
- keychain-owner-recovery-green.log：67 PASS；keychain-recovery-final-regression.log：**116 PASS，9.11 秒，2 项既有 pydub 警告**。新增两项真实 Keychain 修复/删除验证保留其他原始引用。keychain-editor-status-browser-confirmation.log：完整五页、账号编辑返回、三态、原会话竞态断言与窄窗流程 PASS。
- 浏览器中间运行在原会话切换竞态断言失败（store 仍为 project-chat），尚未进入新增凭据三态步骤；同一实现完整重跑通过。未为此修改权限逻辑或弱化竞态断言，不标为已修复。Keychain 全量中间快照 project-pytest-keychain-environment-final.log 因新恢复 RED 主动停止（880 PASS /4 SKIP，157.58 秒），不是最终完整证据；固定恢复/UI 最终代码后另行验证。

阶段 7cb4c2c3 已提交 .env Keychain 存储；本次恢复与状态作为后续阶段独立提交。真实业务凭据仍未在开发环境执行迁移；其他模型/账号/OAuth 安全存储及整个托管/原生保护/外发目标仍未完成，无合并/推送/发布。


## Shell 的 macOS 凭据服务边界

- 真实 full-access Shell RED 复现：即使不读取 .env、只持有测试提供的 service/account，原 allow-default Seatbelt 可经系统服务解密合成 Keychain 值（keychain-shell-boundary-red.log，1 FAIL）。严格插件 Worker 已拒绝，但 Shell 必须同样拒绝；不能以随机引用难猜或原文件黑名单代替这个边界。
- 原 Shell Seatbelt 增加 deny mach-lookup，阻止向宿主服务代理凭据/文件/网络访问，三档执行权限均不可覆盖。既有统一文件保护增加固定 macOS Keychains 目录（用户/系统），不扫描账号、不触碰真实存储，不改变资源授权、对话/任务模式或原受控连接器。
- 真实 Keychain 探针明确将密文文件纳入允许读取范围，子进程正常启动、调用系统接口后返回 blocked，同时成功读取普通工作区文件，Core owner 仍可回读原值。read-only/workspace-write/full-access 三档和实际 full-access 文件工具均覆盖；不是将程序加载失败算成隔离成功。
- 受限模式探针最初因 Core 包父目录不在沙箱读取范围而导入失败，后改为从已有 Core 只读根显式加载探针模块；修正期间误改 Worker 参数的夹具失败亦保留日志，生产沙箱未为探针扩大读取范围。keychain-shell-probes-final.log：19 PASS。keychain-shell-regression-final.log：**147 PASS，13.83 秒，2 项既有 pydub 警告**；Ruff/空白检查通过。macOS 真 Keychain 创建/锁定/删除、Worker 与 Shell 实测无 Mock。
- project-pytest-keychain-recovery-final.log 在发现此实际越权 RED 后主动停止（1250 PASS /4 SKIP，210.82 秒），不是完整验证，也不是超时重启。现在固定整个 Keychain/恢复/UI/Shell 最终快照重新执行完整后端；旧日志全部保留。

本次只证明这些子进程的静态隔离与凭据通路；Core/Electron 的全应用原生保护、任意别名/旧句柄与策略切换事务仍未具备，不宣称已保护。完整目标仍实施中，不合并/推送/发布。


## Keychain 环境凭据最终快照验证

最终固定代码快照 project-pytest-keychain-boundary-final.log：**2749 PASS /22 SKIP /0 FAIL，432.02 秒，5 项既有警告**。完整命令：/opt/anaconda3/envs/Stella/bin/python scripts/run_isolated.py /opt/anaconda3/envs/Stella/bin/python -m pytest tests -q -o usefixtures=offline_runtime_artifacts。包含最终 19 项真实 Keychain/owner 修复/Shell 三模式/Worker/文件工具用例，以及既有 MCP/插件、任务权限与业务回归；测试代码和生产代码在该运行中未变更。TypeScript renderer/main、对应 ESLint、Python Ruff/空白检查和五页/三态/账号配置返回/窄窗浏览器通过；此前浏览器会话竞态和简报间歇失败仍保留证据，没有宣称已修复。两个第一方 MCP 的来源 pin 与当前提供者源码仍匹配。

本轮任务阶段提交：d97c15b9（前阶段顺序验证证据）、7cb4c2c3（既有环境凭据 Keychain）、aca83a82（owner 修复与真实三态 UI）、f61e626d（Shell 系统凭据服务边界）。保留原有 7 项无关工作区改动，无合并/推送/发布。目标回合为 progress，不是完成整个目标。

接续工作：模型配置保存位于 channel/desktop/desktop_channel.py 的 ModelsHandler 与 ConfigHandler（_read_config_file_for_write / _config_path 等），安全页 set_secret 模型分支仍写 config.json；config.load_config JSON 自愈分支仅应处理结构损坏，后续 Keychain 不可用不能被误判为 JSON 损坏而隔离/覆盖用户配置。应复用本次已验证的精确引用/回读/发布/owner 修复模式覆盖已声明的模型字段与自定义提供者，继续原入口和同一配置；普通值/模型绑定不额外复制。该事项尚未实施。另有业务账号/OAuth 可信连接器、已有 MCP 数据归属迁移、完整来源环境生命周期、外发范围/网站信任/公共浏览器、原生 guardian/目录切换与最终独立审查未完成。ES 资格不足、Core/Electron 未保护、Linux 按用户要求 NOT RUN 的事实不变。


## 2026-10-27 macOS 交付范围与模型凭据迁移

新规格已替代旧计划剩余范围：只交付应用内安全控制和受管执行隔离；Core/Electron 是可信宿主，不再要求 Endpoint Security、系统 guardian、全局文件别名/旧句柄撤销或通用自动浏览器。Linux 保留但未实测，Windows 不允许无隔离的不可信执行。当前执行计划为 `.agents/tasks/active/2026-10-27-macos-security-delivery.md`；旧计划已归档，四份已集成任务移到 completed，未完成事项继续在 27 日计划跟踪。

模型页、旧 Config API、安全页与启动加载现共用 config.json 中的声明字段和共享 Keychain 事务。固定模型字段按字段名、自定义提供者按稳定 ID 绑定；保存新版本并回读后发布，未改动引用不解密、不改写。保存失败不提前改变运行配置；丢失或锁定清空对应运行值及旧环境导出，合法 JSON 不进入损坏自愈。Owner 可在原模型入口修复/删除，缺失/不可用卡片保留入口；短密钥不再原样返回。凭据目录仍只返回声明条目的状态，不读取原文。首次从旧明文成功保存时迁移；本轮没有替用户运行真实账号迁移。

用户报告测试中重复出现 python3.12/test Keychain 认证弹窗；此前仅使用 SecItem 的 UI 禁用参数，对文件型钥匙串无效，旧阶段“无弹窗”结论不能覆盖这个实际失败。已终止被阻塞的测试进程，不要求用户输入密码。新增序列化作用域内的 SecKeychainSetUserInteractionAllowed(false)，退出恢复原值；生产操作与临时测试钥匙串创建/清理都禁用交互。只返回访问失败，不解锁用户钥匙串。依据：Chromium `crypto/apple/scoped_keychain_user_interaction_allowed.cc` 对 FB16959400 的说明；本机 Apple SDK SecKeychain.h。修复提交 `7129fdc8`。

验证：原生交互标志检查 RED 1 FAIL → GREEN 1 PASS；两项真实锁定测试在 15 秒进程组截止限制内 **2 PASS /0.35 秒**，环境 Keychain **20 PASS /0.65 秒**。随后发现属性查询在锁定时仍可成功，改为精确条目引用及其所属钥匙串锁状态查询，仍不请求密钥内容；模型锁定状态 RED → GREEN。Task 1 最终关联回归 **148 PASS /0 FAIL，2.23 秒**（两项既有 pydub 警告），日志 `.superpowers/sdd/2026-10-27-macos-security-delivery/task-1-tests.log`；包含原模型/自定义提供者/图像路由、重启、锁定/丢失/修复/顺序交换、失败不改运行值、重复旧环境清理失败、环境存储和 Owner 边界。

UI `model-ui-green-reload.log`：六种模型能力、厂商编辑及原凭据入口、需要修复/不可用状态和删除按钮、620px 窄窗通过；检查了真实截图。前端类型/相关 ESLint、Python Ruff/差异检查通过。早期 UI 测试 IPv4 连接失败和没有刷新 hash 页面导致的旧数据失败仅是验证脚本问题，未冒充产品 RED；补充的旧 UI RED 在刷新后确认缺少修复提示。`model-regression-third.log` 因弹窗主动终止，不是 PASS；`model-regression-no-ui.log` 为 140 PASS/1 FAIL（测试仍导入已移除的私有写入函数），已改用实际统一保存入口，保留符号链接拒绝断言。

以上仅为模型凭据阶段；其余业务账号/OAuth/插件 secret、应用内保护路径、旧数据归属和完整生命周期、可信连接与来源外发、最终独立审查及整体验收仍未完成。尚未合并 develop、推送或发布。

补充搜索凭据回退 RED 3 FAIL → GREEN：启动加载和原搜索凭据删除入口同时处理 BOCHA_API_KEY、ZHIPUAI_API_KEY 等兼容别名，避免 Keychain 丢失/删除后回退旧环境值。最终 148 项结果包含这些边界；全量后端和跨模块独立审查留待整体收尾，不将此阶段结果扩称全部完成。


## 2026-10-27 Electron 宿主边界阶段

- 所有 main/theme/icon IPC 经绑定主窗口、顶层 frame 与规范文档 URL 校验；导航/重定向严格比对文档，不再按 file 前缀放行。显式开启 renderer sandbox、contextIsolation、webSecurity，关闭 Node/subframe Node/webview/insecure content。
- 主 renderer 的网络仅到固定 UI 源和当前 Core backend 地址；其他端口、未知 renderer 和后台无 frame 请求拒绝。后端 token 仅给绑定文档 top frame 的请求，并在重定向前重新核对目的端；插件 iframe/独立窗口不能取得它。插件静态 HTML/JS 用已打开页的随机 nonce 访问，仍不授予业务 API 权限。
- 原通用 HTTPS relay 已移除（仓库无业务调用方）；图标接口只解码受限大小 PNG/ICO data URL，不替 renderer 任意下载。普通文件原打开入口保持；可执行/未知文件及 app bundle 只在 Finder 显示。权限默认拒绝，只保留明确主页面音频及固定本地连接所需类型；取消启动即弹麦克风授权，改为实际音频请求时调用原 macOS 授权。实际个人麦克风/TCC 点击流程未执行，不视为通过。
- Web 插件 iframe 改为 opaque sandbox，只允许 scripts；严格校验当前 iframe 的 event.source、opaque origin、nonce 和现有绑定；卸载/换页后的异步结果不回发旧页面。内容 CSP 禁止直接联网、嵌套 frame 和 form 外发。原第一方脚本通过初始 URL 获得原 channel 身份，不再依赖 history.replaceState/同源权限；没有修改插件业务代码或另建账号/状态。
- Native plugin 窗口禁止全部设备权限、任意同源 API 和文档前缀/其他 file 路径导航；仍可加载自身声明静态资源，保留正常初始化脚本及 Bridge 身份。

证据：新增 native renderer harness 的业务 IPC、真实 localhost Core 请求成功；同 preload 的外部窗口/改变主文档/子 iframe 请求拒绝，外端口接收计数为 0，脚本/app 文件只能 reveal。开发 HTTP 和含空格/中文的 packaged file URL 各验证一次。关联真实 Electron、静态 nonce HTTP、账单/任务/日程 Bridge 回归 `electron-final-regression.log`：**18 PASS，5.20 秒**（两项既有 pydub 警告）。原 native plugin 自源 fetch/前缀导航 RED（1 FAIL/5 PASS）后修复通过，原 UI opaque cookie 访问 RED 后 `plugin-iframe-ui-green.log` 完整 PASS：正常业务、opaque origin、直接 API fetch CSP 拒绝、同 nonce 外部 source 伪造拒绝及 620px。TypeScript main/renderer、相关 ESLint/Ruff/差异检查通过。

该阶段未完成受保护目录及旧系统保护依赖清理，Task 2 继续；其余最终独立审查和整体验收未完成，不合并、不推送、不发布。


## 2026-10-27 应用内保护规则与受管启动基础

新增 Owner-only ProtectedPathControl，规则与既有授权事实同库存放，按 canonical path/目录 device+inode 识别；同前缀邻居不被误伤，符号链接不能绕过，必要运行时/凭据/策略/业务目录及已知 MCP 入口冲突拒绝。changing→ready 两阶段发布，失败保持可重试状态；目录被替换时需要 Owner 修复，删除规则保留文件。规则只针对 Agent/受管执行，不限制可信 Core，也不声称跟踪其他应用任意移动或撤回历史数据。

真实 Popen 的 Bash、PluginHost、MCP、依赖构建、外部 Agent 五处入口复用同一启动锁/版本核对/进程登记；防止旧计划在规则发布后启动，切换取消已有 run 并回收已登记进程。沙箱 profile 最后追加保护范围与祖先目录写入限制，避免通过重命名父目录绕过；全访问不豁免。保护策略版本进入原审批 binding，保留其末项为模式策略 JSON 的原约定，因此修改会话模式后继续同一等待操作的既有行为保持。

边界测试暴露 prepare() 给只读 Shell 授予工作目录写入，现按 workspace_write 拒绝工作区写入，只允许其临时目录；共享/旧 private root 也不能重新提升。Linux 保留对应 mount 路径，未实测。测试授权数据库移到单独临时目录，避免自动 fixture 把安全数据库注入待执行源码树；未放宽生产源码与凭据范围交叠检查。

`protected-boundary-second-regression.log`：**93 PASS /4.70 秒**（两项既有 pydub 警告）。包括真实三模式 Shell/Extension 允许普通文件读写、拒绝保护内容/符号链接/硬链接读与写、拒绝移动祖先目录、旧进程回收/旧计划拒绝、可恢复失败、Owner 管理边界、审批恢复。初轮 86 PASS/7 FAIL 分别为只读写入、新 policy fixture 与源码重叠、binding 末项位置，均有明确修正，未弱化边界。

`protected-runtime-regression.log`：**121 PASS /57.17 秒**（两项既有 pydub 警告），使用隔离状态、离线锁定依赖和真实 macOS 进程，覆盖 Python/Node 环境、外部 CLI、PluginHost/域隔离、MCP 私有目录与重建、模型凭据。该运行在补充 shared/private 只读限制时开始；最终新增限制由后续相关权限回归补核，不能作为最终全量快照。Ruff/差异检查通过。

本节是基础机制阶段。原安全页面还未开放规则编辑；文件访问期间可识别替换、已有会话/索引/缓存/外发来源的处理，以及旧 ES/guardian 占位清理仍待接续。已登记普通子进程回收证据不扩称所有脱离会话子进程已可靠回收；这一边界仍待真实验证与必要实现。独立审查/整体交付尚未完成，不合并、不推送、不发布。

最终 shared/private 只读补核：`protected-final-local.log` **35 PASS /1.65 秒**（保护/实际 Shell/审批恢复）；`protected-share-regression.log` **21 PASS /0.34 秒**（共享权限、真实撤销及 Work 上下文，两项既有 pydub 警告）。代码与测试未再改变，作为本基础阶段提交证据。


## 2026-10-27 文件实际访问阶段

Read/Edit/Write 使用目录描述符逐层固定目标，在实际打开及每次 I/O 时核对路径、文件 identity 和保护策略版本；写入在校验后才截断。规则改变无需等待文档解析结束，未完成读取或旧版本工具结果停止返回。Agent 内容搜索采用逐文件受控读取，防止外部 grep 递归带出保护内容。

路径替换用例先复现 3 FAIL；持锁跨解析问题 1 FAIL 后改成每次 I/O 的短锁；递归搜索真实 RED 1 FAIL 后修正。最终 `file-io-regression-final.log` **156 PASS /4.43 秒**，包含真实 docx/xlsx/pptx/PDF 解析、普通读写及保护边界。Ruff/diff PASS。此阶段不包含 Vision 转换、实际上传/发送、持久上下文和索引来源处理；这些仍待接续，整体未完成。


## 2026-10-28 保护变更后的旧上下文停止复用

会话和排队消息在现有 ConversationStore 中记录保护版本；未验证历史数据保持 -1，迁移不自动认证旧内容。规则变更后，旧内存 Agent、持久会话和变更前排队的输入停止使用，清空上下文不能复活旧排队消息。原“清空上下文”保留消息并重设边界，新会话可正常执行；显式 clear_history 的既有行为继续可用。恢复加载的保护拒绝不再被通用异常处理吞掉，原聊天入口显示处理方法。Agent 运行和外发 permit 捕获同一上下文检查，规则变更后的旧调用不能继续发送。

`context-protection-red.log` 4 FAIL；补充队列/显式清理场景 `context-queued-red.log` 2 FAIL 后修复。最终 `context-protection-final.log` **109 PASS /2.79 秒**（两项既有 pydub 警告），覆盖实际 SQLite 迁移/保留、旧内存上下文、新上下文恢复、排队输入、外发 permit、原 SSE/多 Agent/共享数据库恢复以及保护路径回归。Ruff/diff PASS。

这是保守的版本失效处理：任何保护规则变更都会停止旧会话复用，尚未按具体来源选择性放行。普通 SessionManager 路径、记忆/知识索引、附件和来源授权还需继续接通；不代表 Task 2c/5 完成。


## 2026-10-28 Vision 文件快照边界

Vision 的本地图片入口改用与文件工具相同的 checked_open；大图在内存中以现有 Pillow 依赖压缩受检字节快照，不再启动继承宿主环境的 sips/convert，也不再以路径重新打开来源。JPEG 输出使用实际 image/jpeg MIME。读取后到调用厂商前若保护版本改变，原上下文授权 scope 拒绝外发。

`vision-protection-red.log` 2 FAIL（直接保护文件/别名未拒绝、实际尝试启动宿主转换程序）→最终 `vision-protection-final.log` **60 PASS /2.04 秒**，普通小图字节保持、真实大图压缩、保护文件/别名拒绝、快照后规则变更时厂商调用次数为0、模型与文件工具关联回归。Ruff/diff PASS。具体图片来源的持久证据及会话数据范围授权仍属于后续来源联动；此阶段不宣称完整外发控制已完成。


## 2026-10-28 OAuth 凭据存储阶段

MCP OAuth 的 access_token、refresh_token、client_secret 复用已有 verified-reference transaction，命名绑定固定存储位置、服务名、resource_url 与字段；保存并回读验证后才发布引用和运行时 token/client。非 secret 元数据继续保留在原 mcp_oauth.json。已有 plaintext 可经原持久化入口迁移，未变引用不解密、不重写，单 token 轮换不解密其他字段/账号。多实例 CAS 拒绝旧 refresh 响应覆盖新授权。Keychain 锁定、丢失、损坏引用时不使用旧缓存或 literal reference；客户端每次取 token 重新读取原记录。变更服务地址不会继承旧服务的凭据。无效结构保持原文件并明确失败，不空存储覆盖。

真实隔离 Keychain `oauth-storage-red.log` 7 FAIL /1 PASS；补注册原子发布与禁止 reference 作为 token 的 RED 2 FAIL /1 PASS；最终 `oauth-storage-final.log` **132 PASS /2.58 秒**（两项既有 pydub 警告），涵盖原模型/环境/凭据目录/MCP 开关与授权回调。Ruff/diff PASS。旧临时路径测试改用可信读取入口验证恢复值，保留其符号链接目标未修改断言，不再要求持久化明文。

这是 OAuth 存储/缓存阶段，尚未完成 OAuth 受控 HTTP 传输及原配置页修复/删除交互；插件 secret、业务账号和其余 Task 3 要求继续。普通 Agent 不能枚举 OAuth Owner 元数据。未进行真实个人账号登录或外部服务授权。


## 2026-10-28 OAuth 受控认证传输阶段

移除 OAuth 独立 urllib 网络出口，发现/动态注册/token exchange/refresh 统一经 Core HTTP transport。复用 DNS/IP 核验、实际地址固定、TLS、拒绝重定向/升级/代理、有限响应体和实时 socket 撤销。认证材料作为可信连接器的固定 OAuth 协议字段提交到绑定认证端点；普通内容的未知/凭据来源外发仍被原授权入口拒绝。请求 receipt 绑定接收方、内容摘要、方法/headers、保护与执行策略版本、期限；已有账号或端点变化、Keychain 不可用、运行实例关闭时拒绝旧请求。HTTP 认证仅支持原声明的数字本地服务，不允许元数据自动引入其他本地接收方。

HTTP 通道新增相关服务 lease，OAuth 认证服务器请求同时受原 MCP 服务连接开关约束；关闭会中断实际在途读取，重开不复活旧请求，新的认证请求仍可正常完成。调用预算同时限制 socket timeout 和 receipt/lease；真实 150ms 调用预算的慢认证会在1秒内停止，保留旧 token。改变原 token_endpoint 且仍携带旧 client_secret/refresh_token 的保存拒绝，要求移除旧授权后重新注册，防止通过元数据把凭据交给另一端点。资源 URL 变更在解密前排除原记录。

证据：`oauth-http-red.log` 4 FAIL；端点转移、可选空 secret、更换资源不解密及预算均有语义 RED 后修正。最终 `oauth-http-final.log` **118 PASS /11.58 秒**（两项既有 pydub 警告），包含真实本地认证成功、代理忽略、重定向拒绝、关闭前后/在途请求、异接收方相关 lease、普通内容外发限制、远程 MCP echo 与执行预算。Ruff/diff PASS。未使用个人账号；Linux 未实测。

OAuth 原 MCP 配置页中的状态/修复/删除入口仍须接续；因此此处不将 OAuth 或 Task 3 标为全部完成。其余插件 secret、业务账号、来源/索引联动、脱离会话子进程、旧 ES 清理、生命周期总验收与独立审查仍按27日计划执行；未合并 develop。


## 2026-10-28 OAuth 原配置入口与授权生命周期

原 MCP 配置页新增 OAuth 四态、刷新、重新授权、取消流程和删除授权；只返回 Owner 元数据与限时授权链接，无已保存密钥输入回填。后台 401 改为在配置页等待用户操作，不自动打开浏览器。已移除连接的已保存 OAuth 授权保留在原 MCP 详情入口，可清理；安全目录链接复用该入口。删除不改变 MCP 配置、业务数据或工具开关。

真实正向授权暴露上一阶段的生命周期问题：初始化收到401后关闭客户端，使同一 check_active 拒绝用户完成 token exchange。现在回调校验原配置和管理代次，凭据发布在该代次锁内完成；服务停用、来源变化、取消/删除或过期时旧回调不能发布。删除写入同一记录中的无凭据 tombstone，防止旧空记录的异步注册在删除后重建凭据。取消重新授权保留原引用；正常成功后清除一次性链接期限，后续 refresh 不受该链接过期影响。交换 token 期间持续展示进度并保留取消入口，不提前停止页面状态轮询。错误回调 HTML 转义并使 state 失效，回调页面禁止脚本、嵌入及 referrer/cache 传播。

证据：`oauth-owner-flow-red.log` 1 FAIL/1 PASS；Owner 管理入口 RED7；孤立记录恢复入口/错误回调 RED2；交换进度/成功后的后续刷新 RED2。最终 `oauth-owner-final.log` **135 PASS /20.72 秒**（两项既有 pydub 警告），使用真实隔离 Keychain 和本地 HTTP AS/MCP，覆盖完整授权→发现→有认证 echo 调用成功、停用/删除/取消的在途交换拒绝、旧回调不能恢复、空记录 ABA、状态不解密、旧记录保留、Agent/未认证管理拒绝及原 MCP/传输回归。原 stale callback fixture 改为期待“会话已过期”，不再把已撤销流程展示成成功。

原配置页 `oauth-owner-ui-final.log` PASS：1280px/390px 的修复、不可用、删除、重新授权、取消、处理进度自动轮询到已配置；无密码回填或横向溢出。截图 `/tmp/stella-oauth-owner-desktop.png` 与 `/tmp/stella-oauth-owner-narrow.png` 已检查，标题颜色按现有 text-content 约定修正。曾出现 StrictMode 清理后的 busy 标志未复位，浏览器复现后修复；新测试首次被缺失模型 fixture 的 onboarding 遮挡，补合成模型配置后再验证，不绕过产品引导。TypeScript(main/renderer)、相关 ESLint、Ruff/diff PASS。

这完成已适配 Streamable HTTP OAuth 的本阶段实现。丢失的 confidential client 凭据采用原入口显式删除后重新授权；锁定的 Keychain 可解锁后刷新，应用不请求钥匙串密码。第三方 SDK/不受控协议仍不支持。Task 3 的普通 MCP 配置凭据、业务账号与插件声明 secret，以及 Task 2c/4/5/6 仍需继续；未合并 develop，未推送发布。

## 2026-10-28 插件声明 secret 与原配置恢复

插件 secret 复用 ManagementStore 的 settings SQLite 记录和 credential_values 验证/发布事务；稳定身份是插件、用户、settings、category、field，不按字段顺序。Keychain 新值回读成功、SQLite 实际 commit 后才清理旧引用；验证/提交失败保持原配置和旧凭据。声明过且仍有值的敏感字段保存在同一数据库，包更新删除字段或 secret 标记也不能把原值作为普通设置交给 Worker。当前第一方描述文件没有 secret:true 字段；本阶段实现声明字段的存储/管理，不给普通扩展原文或通用网络回调。

Owner 原插件配置页提供空密码草稿、状态、替换/修复、保存后删除、失败保留草稿和状态刷新；已移除字段/插件的保留凭据继续通过原能力配置入口清理，版本冲突可刷新后重试。凭据目录只投影已声明/保留字段，不解密。运行时普通设置读取不访问 Keychain；真实受管 Worker 能读取普通设置，同时被真实沙箱拒绝直接读取管理凭据库。旧明文经 Owner 读取迁移，secure_delete 清理活动 SQLite 删除页；不承诺磁盘取证层或外部副本的撤回。合法 JSON 中损坏字段保留并可单独修复，不妨碍修复其他字段。

证据：初始 7 FAIL→GREEN，原生管理/目录入口 RED2、孤立能力入口 RED1、损坏字段 RED→GREEN；专用原生15项通过。Worker 测试先修正空 grantRef 和旧 handle 形状这两个夹具错误，再验证真实受管调用，未把夹具错误当产品缺陷。最终 plugin-secret-final.log **150 PASS /22.53秒**（两项既有 pydub 警告），含模型/OAuth/插件/目录/上下文关联回归。plugin-secret-ui-final.log PASS：原配置路径1280px/390px的替换、失败草稿、保存后空输入、删除、卸载后清理及版本冲突刷新；截图已目视检查。TypeScript(main/renderer)、相关ESLint、Ruff/diff PASS。

本轮完整项目快照 plugin-secret-project-suite.log 为 **2857 PASS /2 FAIL /22 SKIP，471.34秒**。两项失败是 test_agent_trim_new_messages 的 __new__ 夹具缺少当前保护版本，按真实初始化补齐后包含在上述150项通过中；未声称修正后完整项目已重跑通过。首次不指定 tests 的命令误收集 references 外部项目并产生17项收集错误，按用户要求将默认 testpaths 设为 tests；reference-exclusion-collection.log 验证2881项均来自Stella tests。现有 PyInstaller/Electron资源清单未包含 references，无需扩大打包修改。默认发现范围及夹具修正提交5a450579。

10-27活动清单已对照最新目标、历史设计和当前代码校正：完成子项及时勾选；复用已有环境/授权/来源事实，在真实边界校验，避免重复检查和每请求审批。Task 3e/3f、路径/来源/进程闭环、生命周期及整体验收继续；未合并、推送、发布。

## 2026-10-28 普通 MCP 配置凭据与原入口

静态认证字段复用原 mcp.json（mapping/list 两格式）或 config.json 的 mcp_servers，不创建第二份账号配置。常用认证 header/env、Cookie 和显式 secretHeaders/secretEnv 声明使用同一 Keychain 引用事务；身份绑定配置位置、服务、实际接收方及字段，header 大小写归一，字段顺序不决定身份。Owner 在原 MCP 配置页查看四态、单字段修复/删除和冲突刷新，已存值不回填，保存失败保留草稿，其他字段输入和引用保持。目录只查声明字段状态、不解密；错误 MCP 配置不遮蔽无关模型凭据状态。合法但无效的 MCP 结构保持原文件并报错，不回退另一套旧凭据。

ToolManager 从实际原文件建立可信引用绑定，配置源切换也改变客户端身份；复制相同 JSON/引用到另一配置位置不会保留旧客户端或获得旧凭据。HTTP/SSE 在实际请求处读取对应字段，配置变化、条目丢失或锁定时不使用缓存/原样引用。认证只发往配置服务，普通本地 MCP 不注入宿主凭据或引用；本地认证业务仍需已适配连接器。认证错误响应可能回显材料，现不把认证响应正文/reason作为普通工具错误展示，并关闭该响应；保留 HTTP 状态。此项不宣称完成所有工具返回内容的来源追踪或统一外发授权。

证据：存储10项 RED；原始传输/修复/格式4项 RED；Manager3项和原API/目录2项 RED；来源切换、header大小写、Cookie及认证错误回显分别真实复现后修正。最终 mcp-config-final.log **184 PASS /46.72秒**（两项既有 pydub 警告），包括真实 HTTP/SSE 认证 echo、轮换、锁定、旧配置/接收方/配置源拒绝、Owner/API/目录、OAuth、普通本地 MCP及环境重建回归。Ruff/diff PASS。

mcp-config-ui-final.log PASS：原配置页1280px/390px的空密码、状态、单字段保存/删除、错误草稿、CAS刷新保留其他草稿；截图已检查。截图发现相邻组件 key 冲突导致重复 OAuth 面板，补 RED 后使用不同 key 修复；输入复用现有组件。mcp-config-oauth-ui-regression.log PASS，原授权/取消/删除/轮询继续可用；旧脚本的全页“未配置”选择器改为各自命名区域，fixture补齐新增状态接口，未降低授权断言。TypeScript(main/renderer)及相关ESLint通过。

本阶段覆盖已声明静态 header/env 的存储/修复及已适配 HTTP/SSE 认证。邮箱/eLearning等业务账号和对应客户端缓存闭环归Task 3f，来源/保护联动、生命周期支持矩阵、统一外发及整体验收仍开放。未运行新的全量项目快照，不以184项代替整体证明；未合并、推送或发布。

## 2026-10-28 邮箱原账号密码存储基础

新增EmailCredentialStore，复用credential_values与原email.db的password字段，不创建第二份账号配置。引用绑定用户、稳定账号ID、认证主机/端口、用户名及字段；文件夹改变不改变认证身份。迁移批次先验证所有新值，SQLite真实commit后才清理旧Keychain项；失败回滚原行。单账号修复/删除使用原行revision，不读取其他账号密码；状态只查属性，运行时解析每次读取当前引用，锁定、丢失、端点改变或引用损坏明确拒绝。

初始email-storage-red.log记录8项缺失适配器错误；实现后发现两处测试夹具不符合现有接口（原生锁应调用SecKeychainLock、Agent主体需session），修正后8项通过，补充实际事务提交失败和条目丢失用例。最终email-storage-regression.log **85 PASS /7.85秒**，含10项邮箱专用用例及模型/环境/插件/MCP凭据关联回归，两项既有pydub警告；Ruff/diff通过。测试使用真实临时Keychain和原MCP邮箱SQLite schema，未访问个人账号。

这是尚未接入原业务入口的存储基础，不能宣称邮箱业务或Task3f完成。可信IMAP认证、原设置状态/修复、旧环境/文件导入及真实受管MCP正常业务仍需继续；不合并develop、不推送发布。

## 2026-10-28 受控IMAP与原账号连接基础

Core的EmailCredentialStore.connect将原账号当前引用绑定到IMAPS端点，使用原连接lease及地址规则，一次DNS解析固定实际socket地址，TLS验证原主机。明确配置的数字本地端点可用于本地服务，域名解析到私网不获得此例外。认证失败不返回服务器回显，引用不能作为密码发送。普通扩展没有此入口或通用网络回调。

连接开关按实际IMAPS主机和端口聚合，目录直接读取原数据库的非敏感端点列，不启动Worker或解密；多个账号共用服务开关。账号变更、密码轮换、条目丢失/锁定、连接关闭或任务取消使已登记socket失效；重新开启不能复活旧请求。IMAPS服务标识不会让HTTP transport接受IMAPS URL，HTTP协议仍单独检查。

imap-transport-red.log初始5 FAIL/1 PASS；账号连接入口imap-account-red.log 2 FAIL后接入；目录imap-catalog-red.log 1 FAIL后补原账号端点。最终imap-final.log **86 PASS /19.99秒**（两项既有pydub警告），含9项真实TLS/原生Keychain的IMAP专用用例，及原HTTP/OAuth/MCP认证和连接回归。原EmailService通过trusted opener完成实际测试连接、文件夹查询、邮件解析和只提议游标；轮换和锁定停止旧连接，认证证书、私网域名和literal reference拒绝。Ruff/diff通过；未使用个人账号或弹密码框。

尚未接入生产MCP回调、原设置页和旧环境/文件导入；本阶段不证明邮箱可交付、受管MCP整链路可用或Task3f完成。原沙箱与普通MCP禁网保持，后续必须将账号配置及认证留在明确可信的Core连接器，继续复用原业务协议和资源授权，不通过原始密码注入恢复功能。

## 2026-10-28 邮箱原入口与真实受管MCP认证闭环

邮箱MCP 1.0.4增加Stella专用hosted模式。受管Worker仍在独立进程与沙箱内，既不能访问原账号/legacy目录，也不接收密码参数或IMAP_PASSWORD。stdio回调仅带父请求ID，Core只允许完成当前已准入操作一次；额外URL、方法或参数不授予其他宿主能力。普通本地MCP原始凭据检查移除旧邮箱豁免。Core复用原邮箱service/imap等四个明确可信模块，验证固定来源摘要并执行同次读取的字节及SQL快照；没有导入Server入口或建立通用代码加载接口。

账号保存复用原SQLite事务，通过可信credential publisher先验证Keychain新值再提交引用，原设置的行版本冲突明确失败。空密码保持现有引用，账号删除后才清理对应Keychain项，失败保留账号。认证绑定当前账号端点；实际IMAP读取持续检查原资源授权，撤销会中断在途fetch。旧环境账号只属于local-default，Owner采用时保存原environment ID，重开不复活删除记录；原.env对应引用丢失不会恢复旧进程明文，单独重输密码可修复。一个旧账号迁移失败只标记该批未迁移账号，已有可用引用状态保持。

原邮箱页显示已配置/未配置/不可用/需修复，密码输入始终为空草稿，失败保留草稿，成功清空；原删除、测试连接、返回位置及旧链接保留。凭据目录直接读取原账号声明及Keychain属性，提供者停用时仍可汇总，不启动Worker或解密。跨仓库更新了MCP版本和Core来源摘要。

证据：email-connector-red.log初始1FAIL/4ERROR；真实MCP初始化及回调联调后完成保存→发现→读取，SDK基类Request会丢弃自定义params，使用固定HostedRequest字段后通过。环境3项RED、原表单CAS RED、目录停用RED及局部状态RED均有针对修正；首次资源重检回归105PASS/1FAIL的Agent夹具缺少真实read grant，补Owner授权后未放宽生产规则。

email-production-final.log **212 PASS /97.70秒**（两项既有pydub警告），包含受管MCP、环境重建/存储、OAuth/HTTP、目录和账号边界。随后补充局部迁移失败状态修正，以及实际重启、失败重试和“hash后磁盘变化仍只执行已验证快照”的用例，email-owner-final.log **27 PASS /8.99秒**通过；没有用这27项冒称新的全项目快照。真实Worker探针确认账号文件及直连网络被拒绝，Root参数不进入stdio，带URL的回调被拒绝；真实TLS邮件读取及资源撤销在途停止均通过。

邮箱仓库email-provider-final.log **48 PASS /1.02秒**；email-ui-final.log通过原路径1280px及390px（使用既有收起侧栏按钮），四态、空输入、失败草稿、保存清空、刷新、测试连接和删除均验证，截图已目视检查。TypeScript main/renderer、受影响ESLint、Python Ruff及diff通过；Core pin与当前MCP源逐字节摘要匹配。测试仅用隔离数据、临时Keychain和本地TLS服务，没有个人账号或认证弹窗。

此项完成当前macOS邮箱原业务接入的阶段验收；eLearning、模型客户端缓存、来源/受保护数据联动、托管环境矩阵及最终全量/独立审查仍未完成，不合并develop、不推送发布。Linux未实测；冻结分发及完整支持矩阵继续由Task4/6核对。

## 2026-10-29 eLearning 原凭据与登录状态存储基础

ElearningCredentialStore在原elearning.json内保存password/token引用，在原elearning-sessions/<account>.json内保存该登录状态的引用；课程、游标、下载清单及未知配置字段保持。账号ID继续使用原base_url与username的算法，Keychain字段绑定用户、账号端点、用户名和字段；不按列表位置归属。两个原文件各自验证并原子发布，未创建第二份账号配置，也不宣称跨文件事务。

单字段修复/删除无需解密其他凭据；实际读取只解析所需字段，没有缓存明文或原样引用回退。合法JSON中的坏字段和坏session保留，可单独修复/删除。登录状态轮换使用账号配置及原session共同版本，迟到的旧登录不能覆盖新状态；只有发布新引用成功后才清理旧Keychain项，文件发布失败保留原登录。Owner状态仅查Keychain属性，不解密；普通Agent没有管理入口。

初始elearning-storage-red.log为8项缺少适配器错误，首次实现8项通过。随后session轮换/迟到响应2项RED、损坏引用wrapper状态1项RED后补实现。elearning-storage-final.log **57 PASS /1.66秒**，包含12项eLearning专用原生用例和邮箱/环境/OAuth凭据关联回归；真实隔离Keychain覆盖锁定、验证失败、发布失败、复制/改账号引用拒绝与Owner管理边界。Ruff/diff通过，未访问个人账号或弹认证窗口。

此阶段仅为尚未接入生产链路的存储基础。eLearning受控API/特定SSO登录、真实受管MCP、下载/资源撤销与原设置的逐字段状态/修复仍待接续；没有把仅token可用当作密码登录交付，没有将当前第一方业务改标不支持。全目标保持实施中。

## 2026-10-29 eLearning 受控读取出口

ElearningTransport复用现有Core HTTP实现、EgressPermit和连接lease，只提供账号绑定的HTTPS读取。每次实际请求解析当前选用的token或session，状态检查不解密其他字段；token客户端不会因无关密码轮换而失效。实际API接收方固定为配置服务，拒绝重定向，异接收方下载不携带账号认证且不获得本地地址例外。TLS、地址固定、代理隔离及实际socket撤销继续由原HTTP出口执行。

保护/执行策略、当前认证引用、Keychain可用性与原资源授权在实际请求和在途读取时复核；账号/凭据变更、锁定、连接关闭或资源撤销拒绝旧请求。认证错误关闭响应后只返回状态码，不把回显认证信息的正文/reason送入工具错误。请求审计只记服务身份和协议操作。

elearning-transport-red.log初始7 FAIL（缺少模块）→7 PASS；补充真实资源授权撤销和无关密码轮换后，elearning-transport-final.log **72 PASS /22.42秒**，包含9项真实本地TLS、原生Keychain和socket用例，以及eLearning存储/HTTP/OAuth/IMAP/连接关联回归。Ruff/diff通过。此出口尚未接入原Canvas/MCP；密码SSO、认证session续期、具体下载跳转及原设置仍未完成，不将token/Cookie读取当作整个业务交付。

SSO浏览器仅做了可行性实验：Seatbelt全禁network会同时阻止Chromium私有Unix IPC；允许Unix IPC后，真实Chromium DOM操作成功且直接IP网络被实际拒绝。已安装Chrome尝试叠加自身sandbox出现sandbox initialization failed，尚未采用为实现或宣称浏览器隔离验收。后续应验证一个清晰的实际OS边界、必要私有IPC、私有文件限制及限定接收方的受控请求，不能用浏览器route回调本身冒充禁网。公开学校登录页本轮获取超时，未把该失败视为账号/实现事实。


## 2026-10-29 eLearning 后端业务接通

Core 复用原 eLearning 登录、课程、同步和存储实现，通过固定摘要验证的模块接入可信连接器；复用邮箱的单次调用桥接，普通 MCP 不接收认证参数、Keychain 引用或账号存储权限。MCP 配套版本为 1.0.5，提交 `cfcd286`；Core 来源摘要已同步。

- 原 JSON 发布密码/Token 引用，Cookie 保存在原账号 session 文件对应的 Keychain 条目中。单字段修改不读取其他凭据；保存失败保留原记录。业务使用拒绝未迁移的明文，Owner 测试新账号可使用明确输入的草稿；新账号测试的 Cookie 只存于本次调用内存，不覆盖现有账号。
- 复用原 SSO 选择器及浏览器原生隔离，使用真实 Chromium 和合成登录页验证。API/下载继续通过 Core 实际 DNS、TLS 和接收方校验；连接关闭在登录前生效，登记的登录操作撤销后不能因重开而恢复。
- 原受管输出子进程保留调用桥接属性；Core 同步检查选定目录、文件权限和保护路径。重复同步保留本地编辑，删除账号保留课件。已知旧业务目录由 Owner 读取时复制并迁移，保留原文件，拒绝符号链接来源。

验证：`elearning-final-followup.log` **85 PASS**（含完整简报 HTTP、peer 授权、调用预算及新业务集成）；较早扩大回归 `elearning-stage-final.log` 为 **194 PASS /1 FAIL**，其中环境测试依赖全局 .env 的问题已用独立声明文件修正并包含在上述 85 项中。MCP 仓库 **123 PASS、12 subtests PASS**。Core 相关 Ruff 和 diff 检查通过；MCP 静态检查保留原 `store.py` 的 E402，忽略该既有项后通过。

Core 全量 `core-elearning-stage-full.log` 为 **2952 PASS /6 FAIL /22 SKIP**（533.49 秒，修正前快照）。五项旧预期/测试配置已调整并定向通过；非默认用户简报“服务忙”的失败在单独及整文件回归通过，尚未取得修正后的全量结果。不得据此标完整回归通过。测试仅用临时数据、隔离原生 Keychain 和合成服务；浏览器测试只对临时自签名服务放宽证书校验，Core TLS 验证保持实际生效，未使用个人账号。

剩余：原 eLearning 设置页的准确凭据状态及单项修复/删除、凭据目录汇总、模型运行时缓存，以及清单其余收尾和最终独立审查。尚未合并 develop，也未推送或发布。
