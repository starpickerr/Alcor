# 安全与托管运行环境重构：阶段记录

状态：**实施中，完整计划未完成，也未达到可合并验收条件**。更新于 2026-10-26。

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

日志与截图在 `.superpowers/sdd/2026-10-25-security-managed-runtime/`；工作区计划在 `.agents/tasks/active/2026-10-25-security-managed-runtime.md`。

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
