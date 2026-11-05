# Stella 使用 DSH 的 MCP

2026-11-05：复用当前受支持宿主提供的 `@deepseek-ai/dsh-mcp-client`。连接在 DSH Profile 管理，Stella 在设置 → 连接与对话权限逐工具授权，默认无授权。授权保存在 `stella_mcp` domain，停用或卸载 Stella 后保留。

## 插件管理入口与封装方向

Stella 侧栏“应用 → 插件”直接打开宿主原生 `plugins` 页面，保留 Stella 模式与当前会话，和 DSH 的入口共用当前 Profile 的插件及设置。入口遵循宿主的 `pluginManager/listBundles` 权限，不另建安装器或插件列表。安装和启用连接不等于授权 Stella 使用全部工具，秘书仍在“连接与对话权限”逐工具授权。

已提供可独立安装的 `@stella/elearning@0.1.1` 和 `@stella/mail@0.1.1`。Bundle 管理安装、启停、配置、原生凭据与 MCP Client；随包冻结的独立 MCP 1.0.5 负责业务，不依赖开发目录或用户另装 Python。学校连接复用现有适配器，保留 `stellaElearning`、原生凭据键与课程来源身份；邮件使用 `stella-email/accounts` 原生凭据记录，多邮箱编辑保留其他账号。账号表单始终展开，测试连接位于各表单下方；已保存授权码以固定星号占位，空值保留原授权码。不同表单的未提交编辑保留原版本，冲突时拒绝覆盖并允许撤销修改。两包均不安装 DSH 核心组件。

在原生插件页打开相应包：学校填写学工号和密码；邮件填写邮箱、IMAP SSL 地址、客户端授权码与文件夹。保存仅建立本地 MCP 连接，不登录外部网站或读取正文；用户点击「测试连接」才请求课程列表或测试 IMAP 登录和文件夹。真实网站的额外认证由用户处理。对话读取与简报来源仍分别授权。

简报「信息来源」保留已加载插件的未配置／未就绪状态，提供「设置账号」直接打开对应原生插件页；这些连接状态不作为可选来源保存。配置后通过刷新显示实际账号及课程范围，不自动选择来源。来源目录只返回必要状态，不回传凭据或自动读取内容。简报页与来源窗口的刷新使用图标按钮，保留无障碍名称。

邮件 DSH 模式只发布 `list_accounts`、`list_sources`、`get_cursors`、`fetch_messages`、`test_connection`、`list_folders`；账号增删不作为 MCP 工具，且禁止伪造管理调用。账号密码仅来自当前 DSH 记录，MCP 忽略旧数据库账号和环境默认密码，业务库只保存游标。读取采用只读 IMAP，不标已读、不发送或删除邮件。停用插件关闭连接、撤销工具，保留凭据与历史数据；移除账号后阻断后续访问。

构建：`npm run product:sources:pack`，产物为 `.local/artifacts/stella-elearning-0.1.1.tgz` 与 `stella-mail-0.1.1.tgz`。依赖与源码摘要在 `source-releases.lock.json` 固定，修改来源后先验证再更新锁。冻结库在入包时解除符号链接，防止 npm 省略运行库；保留运行依赖许可证。

## eLearning 账号与凭据

用户明确选择 DSH 原生凭据机制，替代此前试验的 macOS Keychain 读取路线。`@stella/secretary/elearning` 通过 `ctx.authorization` 注册 `stella-elearning/account` 登录流程，通过 `ctx.credentials` 保存账号密码。与模型 API Key 共用当前 DSH 凭据 provider；默认原生 local provider 保存到当前 DSH home 的 `.credentials.yaml`，权限 0600，沿用其原子写入、跨进程锁和变更通知。这不是加密文件或系统级隔离。

账号表单始终展开，测试连接位于表单下方；状态仅返回学工号及连接元数据，不回传密码。保存后清除页面中的真实密码，以固定星号占位显示已保存状态；只有输入新密码才能保存替换，未修改时不会提交占位符。账号凭据不进入业务 domain、Profile 配置、模型提示或工具定义。连接采用原生 stdio Client，秘密只在内存中的子进程启动环境传递，不作为 Cordis 子插件的可检查配置。Python 读取后立即移除该环境项，浏览器子进程不继承它。与其他 DSH 同进程可信插件之间不承诺秘密隔离。

每次执行重新读取原生凭据，凭据变更立即阻止后续旧连接调用并重建 MCP；删除凭据关闭连接。重新启动使用 DSH 保存的凭据登录。Cookie 只保留在当前 MCP 进程内存，不保存到旧 session 文件；旧密码、Token、Cookie 或 Keychain 引用均不用于新连接，也不自动复制或删除。复旦登录地址固定，只有在原账号一致时沿用其课程选择。网页登录或学校额外认证要求仍可能阻止自动登录。

下列手动 Profile 方式仅为旧配置兼容；新安装使用独立 Bundle，不需要填写路径。已有同名服务时新 Bundle 沿用它，原手动连接仍由旧 owner 管理；改用随包服务前先停用旧配置。

```yaml
- insert:
    - id: stella-elearning-mcp
      name: '@stella/secretary/elearning'
      config:
        command: /absolute/fudan-elearning-mcp/.venv/bin/python
        cwd: /absolute/fudan-elearning-mcp
        dataDir: /absolute/elearning-data
```

此插件在内部启动 `python -m elearning_mcp.server --data-dir ... --dsh-credentials`，仅发布 `list_courses`、`fetch`、`list_files`。不要再同时配置同名 `elearning` 原生 Client；不要使用依赖旧 Core 的 `--hosted-connector`。普通独立 MCP 模式保持原有行为，新凭据路径仅在显式 DSH 模式生效。

打开 Stella 设置 → 连接与对话权限 → 学校连接，填写并保存账号后检查连接。在「Stella 对话工具」中单独授权查询能力；简报来源与课程范围在「简报 → 信息来源」中选择。课程列表由用户点击后读取元数据，不发送模型。使用侧栏新会话发起查询时，查询结果才会交给所选模型。

## 其他 MCP 与执行边界

其他标准 MCP 使用原生 Client 连接，再选择具体工具。不同服务的凭据格式由其拥有插件接入 `ctx.credentials`，不将 eLearning 账号传给其他 MCP。授权按精确公开工具名保存，不按 `mcp__` 前缀放行，不推断其他 MCP 为只读。同名服务重接沿用原授权，改换身份应先撤销旧授权。MCP Resources、Prompts 未开放。手动简报通过专用适配层使用独立的来源选择，定时模板不继承外部来源授权。

Stella restriction 持续屏蔽未授权全局工具，执行 guard 在审批后重新检查授权及调用主体。仅主动用户主会话可执行；子 Agent、定时和未知主体拒绝。撤销约束后续调用，不撤回已有对话结果，也不承诺撤回已发送的网络请求。DSH 原有 Preset 和权限策略保留；停用连接由原生 Client 撤销工具，已有授权记录保留以显示断开状态和支持撤销。

## 验证

`npm run product:test` 覆盖原生凭据保存、更新、删除、重启、审批间撤销、工具来源边界与模型适配器配置。`product/tests/credentials_check.py` 使用合成 stdio Server 验证真实设置页面、密码隐藏与清空、0600 原生保存、连接、查询、重启和删除。`product/tests/mcp_check.py` 验证真实 DSH 模型 → MCP → 模型往返及授权持久化，使用合成模型和课程。

独立 eLearning 仓库 `tests.test_dsh_credentials` 验证三查询目录、伪造写入拒绝、忽略旧凭据、内存 Cookie、环境移除和无业务秘密落盘。真实学校账号需用户在新入口重新输入后验证；未读取真实课程或发送真实模型消息。

独立邮件仓库 `tests.test_dsh_credentials` 验证原生账号环境移除、六只读工具、伪造写入拒绝、无密码落盘及普通模式兼容。`mail-credentials.test.mjs` 覆盖多账号保留、空授权码保留、并发版本冲突、凭据轮换／撤销与重启；`source-remotes.test.mjs` 使用真实 DSH Client Gateway 挂载所有 Stella Remote，防止方法名与宿主保留接口冲突，并验证沿用旧学校连接。

`source-plugins-check.py` 从未修改官方 rc.1 Desktop 安装三个原生包，验证冻结服务协议、配置页面、合成凭据、目录发现但不采集内容、授权分离、停用／重启／恢复／移除与卸载配置保留，包含 390px 页面。真实账号尚待用户输入并点击测试连接，不把合成配置当成真实登录成功。
