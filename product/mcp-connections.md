# Alcor 使用 DSH 的 MCP

2026-11-06：连接使用宿主的 `@deepseek-ai/dsh-mcp-client`，安装与账号配置由 DSH 原生插件管理。Stella 主对话继承当前宿主的工具可见性、访问模式与审批，不另设逐工具白名单。新版本不再创建或读写 `stella_mcp` domain；旧数据在显式迁移核验后归档清理。

## 插件管理入口与封装方向

Alcor 侧栏「插件」直接打开宿主原生插件页面。保留当前 Profile、插件生命周期、配置和 `pluginManager/listBundles` 的访问检查；不另建安装器、账号表单或工具授权列表。

已安装的独立包为 `@alcor/elearning@0.1.7` 和 `@alcor/mail@0.1.9`；本机 Profile 已完成跨包迁移。每个来源只启用一个版本。Bundle 管理安装、启停、配置、原生凭据与 MCP Client；随包冻结的独立 MCP（eLearning 1.0.7、邮件 1.0.6）负责业务，不依赖开发目录或用户另装 Python。学校连接复用现有适配器和课程来源身份，候选凭据键为 `alcor-elearning/account`；邮件候选键为 `alcor-email/accounts`，多邮箱编辑保留其他账号。旧键必须先通过受控离线迁移核验，不能直接安装新包后假设账号已迁入。账号表单始终展开，测试连接位于各表单下方；已保存授权码以固定星号占位，空值保留原授权码。不同表单的未提交编辑保留原版本，冲突时拒绝覆盖并允许撤销修改。两包均不安装 DSH 核心组件。

在原生插件页打开相应包：学校填写学工号和密码；邮件填写邮箱、IMAP SSL 地址、客户端授权码与文件夹。保存仅建立本地 MCP 连接，不登录外部网站或读取正文；用户点击「测试连接」才请求课程列表或测试 IMAP 登录和文件夹。真实网站的额外认证由用户处理。对话使用 DSH 策略；邮箱开启/停用统一控制所有读取，简报还需启用独立来源开关，两层都开启才会采集。

简报设置使用聚合来源卡片和独立启停滑块，Email 多账号聚合为一张；点击卡片进入所属插件。邮箱开关位于邮件插件内，学校课程范围在 eLearning 插件内，本机来源在 Alcor 插件内设置。旧账号、课程范围与游标保留，来源目录只返回必要状态，不回传凭据或自动读取正文。

邮件 DSH 模式只发布 `list_accounts`、`list_sources`、`get_cursors`、`fetch_messages`、`test_connection`、`list_folders`；账号增删不作为 MCP 工具，且禁止伪造管理调用。账号密码仅来自当前 DSH 记录，MCP 忽略旧数据库账号和环境默认密码，业务库只保存游标。读取采用只读 IMAP，不标已读、不发送或删除邮件。停用插件关闭连接、撤销工具，保留凭据与历史数据；移除账号后阻断后续访问。

构建：`npm run product:sources:pack`，候选产物为 `.local/artifacts/alcor-elearning-0.1.7.tgz` 与 `alcor-mail-0.1.9.tgz`。依赖与源码摘要在 `source-releases.lock.json` 固定，修改来源后先验证再更新锁。冻结库在入包时解除符号链接，防止 npm 省略运行库；保留运行依赖许可证。

学校 1.0.7 的构建输入现已保存于学校仓库提交 `453c9634acc920ca2429221a0e0df4e0ef82befb`。邮件 1.0.6 对应邮件仓库提交 `7bb82f44f8c16d9b5494ed2748994a3ded4d98c2`。两处源码均以 `product/source-releases.lock.json` 中的摘要校验。

## eLearning 账号与凭据

用户明确选择 DSH 原生凭据机制，替代此前试验的 macOS Keychain 读取路线。`@alcor/elearning` 候选包通过 `ctx.authorization` 注册 `alcor-elearning/account` 登录流程，通过 `ctx.credentials` 保存账号密码；已安装旧包使用的 `stella-elearning/account` 是待迁移源。与模型 API Key 共用当前 DSH 凭据 provider；默认原生 local provider 保存到当前 DSH home 的 `.credentials.yaml`，权限 0600，沿用其原子写入、跨进程锁和变更通知。这不是加密文件或系统级隔离。

账号表单始终展开，测试连接位于表单下方；状态仅返回学工号及连接元数据，不回传密码。保存后清除页面中的真实密码，以固定星号占位显示已保存状态；只有输入新密码才能保存替换，未修改时不会提交占位符。账号凭据不进入业务 domain、Profile 配置、模型提示或工具定义。连接采用原生 stdio Client，秘密只在内存中的子进程启动环境传递，不作为 Cordis 子插件的可检查配置。Python 读取后立即移除该环境项，浏览器子进程不继承它。与其他 DSH 同进程可信插件之间不承诺秘密隔离。

每次执行重新读取原生凭据，凭据变更立即阻止后续旧连接调用并重建 MCP；删除凭据关闭连接。重新启动使用 DSH 保存的凭据登录。Cookie 只保留在当前 MCP 进程内存，不保存到旧 session 文件；旧密码、Token、Cookie 或 Keychain 引用均不用于新连接，也不自动复制或删除。复旦登录地址固定，只有在原账号一致时沿用其课程选择。网页登录或学校额外认证要求仍可能阻止自动登录。

下列手动 Profile 方式仅为旧配置兼容；新安装使用独立 Bundle，不需要填写路径。已有同名服务时新 Bundle 沿用它，原手动连接仍由旧 owner 管理；改用随包服务前先停用旧配置。

```yaml
- insert:
    - id: stella-elearning-mcp
      name: '@stella/secretary/elearning'
      config:
        command: /absolute/dsh-fudan-elearning/.venv/bin/python
        cwd: /absolute/dsh-fudan-elearning
        dataDir: /absolute/elearning-data
```

此插件在内部启动 `python -m elearning_mcp.server --data-dir ... --dsh-credentials`，仅发布 `list_courses`、`fetch`、`list_files`。不要再同时配置同名 `elearning` 原生 Client；不要使用依赖旧 Core 的 `--hosted-connector`。普通独立 MCP 模式保持原有行为，新凭据路径仅在显式 DSH 模式生效。

候选包通过 DSH 插件 → @alcor/elearning 的原生插件页保存账号并测试连接。连接成功后，其只读工具随宿主目录提供给 Alcor 主对话；简报课程范围在该插件的「课程范围」中设置。连接测试仅读取课程元数据；主对话查询结果会发送给当前模型。

## 其他 MCP 与执行边界

其他标准 MCP 同样使用宿主原生 Client、工具目录和策略。凭据由所属插件通过 `ctx.credentials` 管理，不将学校凭据传给其他服务，不推断其他 MCP 为只读。安装 Alcor 不改写宿主权限或默认 Preset。简报通过专用适配层使用独立的内容来源选择，定时简报不继承外部来源。

Alcor 不再遮罩宿主工具或保存第二套授权。DSH 的工具限制、拒绝、审批后撤销与连接停用继续生效；私人 MCP 保留主动用户主对话的调用主体检查，协作、定时和未知主体不能借其读取私人来源。凭据每次执行复核，移除账号阻止后续访问。撤销不撤回已有对话结果或已发送的网络请求。

## 验证

`npm run product:test` 验证宿主工具继承、宿主拒绝与撤销、后注册工具、私人来源上下文、凭据轮换和来源停用。`source-plugins-check.py` 覆盖原生账号配置与生命周期，替代已移除的通用设置账号表单用例。`mcp_check.py` 验证真实 DSH 模型 → MCP → 模型往返、重启及旧白名单文件保持原位且不再生效；使用合成模型与课程。

独立 eLearning 仓库 `tests.test_dsh_credentials` 验证三项只读工具、伪造写入拒绝、忽略旧凭据、内存 Cookie、环境移除及无业务秘密落盘。真实账号测试与合成测试分开记录，不把安装或进程运行视为登录成功。

独立邮件仓库 `tests.test_dsh_credentials` 验证原生账号环境移除、六只读工具、伪造写入拒绝、无密码落盘及普通模式兼容。`mail-credentials.test.mjs` 覆盖多账号保留、空授权码保留、并发版本冲突、凭据轮换／撤销与重启；`source-remotes.test.mjs` 使用真实 DSH Client Gateway 挂载所有 Alcor Remote，防止方法名与宿主保留接口冲突，并验证沿用旧学校连接。

`source-plugins-check.py` 从未修改的官方 rc.1 Desktop 安装三个原生包，验证冻结协议、紧凑设置、原生插件导航、合成凭据、来源元数据、停用／重启／恢复／移除与配置保留，包含 390px 页面。
简报仅采集设置中选中的来源与账号。学校 `fetch` 接受可选 `cursors` 并返回建议的 `elearning_state`；首次使用前一天 20:00 的有限窗口，后续从上次成功游标读取。课程范围变更会重建游标，课程文件仅检查近期元数据；简报不下载文件。游标与已生成快照原子保存，失败／取消不推进；当天未变化的筛选和条目报告直接复用，跨天不重复装入历史候选。

插件各自随包提供 DSH 原生 Skill（alcor-elearning、alcor-mail），启用时发现、调用原生 skill 工具时才读取正文，停用即撤销；Skill 不授权额外工具或扩大信息源范围。MCP 查询示例按冻结服务的实际 schema 验证。

随包 Skill 的活动名称已改为 `alcor-*`。历史会话内容保留原样；DSH 按名称精确查找，旧的 `/stella-*` Skill 调用不会自动映射到新名，需要改用对应的 `/alcor-*`。

邮件连接测试保留等待状态，35 秒截止；仅通过白名单诊断码区分登录拒绝、DNS、SSL、网络、文件夹权限及超时，不返回服务器异常原文或授权码。凭据发生变化时丢弃旧测试结果。
