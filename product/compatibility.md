# Stella 与原版 DSH 的兼容记录

核对日期：2026-11-05（Asia/Shanghai）。安装包当前仅声明支持 `0.2.2-alpha.3`，不根据包名或版本顺序扩大支持范围。

## 桌面交付验收

最终要求为未修改的官方 DSH 桌面安装包加原生 Stella 插件，呈现现有完整 Stella 模式；无需用户启动网页服务器、克隆／构建宿主或手工替换客户端资产。以下均需从清洁隔离环境验证后才可声明完成：

- 从官方已发布桌面安装包安装、首次启动及原生插件管理器安装 Stella；核对该桌面版本与包的实际兼容性。
- 完整模式的应用导航、原生工作区／会话、账号设置与自动化；切换不修改宿主全局模型／默认 Agent。
- 退出和重新打开桌面客户端后的模式、配置和数据恢复。
- 原生停用、卸载及重新安装，恢复 DSH 界面并保留已有会话、配置与业务数据。
- 在干净终端检查运行依赖；开发机已有 Python、编译器或 CLI 不能算普通用户已经具备。

当前网页及兼容补丁测试维持各自的有效范围；`product/tests/desktop_check.py` 从源码运行的 Electron 也不能当作已下载官方桌面发布包的证据。`dsh.client.platform: web` 表示复用网页渲染器，不能仅凭此字段判断已支持或不支持桌面安装。

2026-11-05 当前产物验证：官方 macOS arm64 Desktop 冷安装、六应用、同窗口双模式切换、模式／数据重启恢复、停用／启用／卸载／重装均通过；使用官方自带 Python，前后严格签名校验与 `app.asar` 摘要均通过。业务 Python 使用 `-B`，防止导入库时改写 App 内已签名的字节码缓存。48 项原生测试通过；独立官方 Web 安装上的 Apple 合成同步、分类／清单保存、事务／日历界面与生命周期通过。真实 Apple 账号与无重启根 UI 替换不在通过范围。

## 已核对的官方版本

| 对象 | 事实 | 证据范围 |
| --- | --- | --- |
| npm `latest` | `0.2.1-rc.5` | `npm view @deepseek-ai/dsh dist-tags --json`；未运行该版本 |
| npm `alpha` | `0.2.2-alpha.3` | 现有 `.local/official-cli` 为未修改官方 npm 安装；原生生命周期测试使用此版本 |
| npm `next` | `0.2.2-rc.1` | 实时查询 npm 元数据，并只读检查官方 layout/workspace 发布归档；没有运行组合验收 |
| 官方 HEAD | `8bd073392eef6f54e921a093b95076248eb08cbb` | `git ls-remote`；不作为发布安装产物 |

`0.2.2-rc.1` 的 DSH npm integrity 为 `sha512-ijR9UEVYkG15Q/Q79JzvzLBzQOQfc63VLr9n6flpwHPhr5IKn2UxthnSoto/P9l7qGd7pPvI1Ra6TW7LH3oKpg==`。本记录不表示该版本已获 Stella 支持。

## 当前原生 Bundle 交付

当前秘书包通过相对 Host 入口加载五个 Profile 内的界面衍生模块，保留原模块 ID，并停用该 Profile 中相应的原始 UI 行；官方 App 签名文件保持不变。四组原生界面扩展提供下表接口，第五组 Models 界面只将首次使用提示绑定到替换后的设置命名空间，Host 沿用原有 onboarding 选择。安装、启停和卸载需要重启，日常 Stella / DSH 切换即时生效。

界面构建校验锁定版本、补丁摘要、模块身份、运行文件及 Models 适配；缺失、变动或过期产物拒绝打包。普通构建不再需要手动修改官方客户端。以下接口差额描述官方原始模块，以及 Bundle 内衍生模块补齐它们的方式。

## 官方接口与 Bundle 扩展

原生包通过 `dsh.bundle.patch` 和 `dsh.client` 交付 Host、页面、设置、持久记录与独立秘书 Preset。创建调用公开 `uiWorkspace.startSession(undefined, { agentPreset: 'stella' })`，由宿主选择当前或最近文件工作区；不改默认 Preset，沿用已有模型路由。普通构建和打包不会替换宿主文件。

官方原始模块有以下接口缺口，当前由 Bundle 内的衍生模块补齐：

| 契约 | 官方产物核对 | 已有最小原型与后续 |
| --- | --- | --- |
| 独立、可访问且不受侧栏状态影响的顶部入口 | alpha.3 与 rc.1 均没有 `shell.top`；品牌槽有隐藏语义或被按钮包围。`shell.leading` 已被原生开关占用，`shell.overlay` 不提供布局空间 | `patches/0002-client-product-entry.patch` 提供 additive `shell.top`，处理窄窗口抽屉、系统控件和布局留白；需在官方发布支持后重验 |
| 按当前窗口模式选择通用 New Session 的 Preset | 公开 `startSession` 可显式传入 Preset，但官方没有按调用方解析默认值的事件；不能通过安装插件改写全局默认值 | 同一补丁提供可异步解析的 `ui-workspace/default-preset` 事件；Stella 模式返回 `stella`，DSH 模式通过原生 Preset 目录读取用户当前默认值并显式传入，避免复用秘书空会话覆盖草稿；读取失败则拒绝创建 |
| 复用原生工作区与会话列表 | 官方工作区组件只填充原生侧栏的私有子槽，没有可复用 Factory | `patches/0004-workspace-browser-factory.patch` 将同一浏览组件、注入动作、store 和子槽归入 `workspace.browser`，原生及 Stella 侧栏都通过 Factory 渲染；不另建工作区数据或列表实现 |
| 复用原生账号入口与设置窗口 | 官方设置 Shell 只挂载在原生侧栏子槽，无公开 Factory | `patches/0005-settings-shell-factory.patch` 暴露 `settings.shell`，两种侧栏复用同一原生账号菜单、设置窗口、快捷键和分区；Stella 内容只贡献到 `settings.section/stella` |
| 记录并恢复每个模式的导航身份 | workspace 的导航 `selection` 在 alpha.3 与 rc.1 的公开声明中为私有；不能把访问运行时私有字段作为稳定接口 | 同一补丁暴露只读导航身份；恢复仍调用公开 `openSession`。继续核实既有公开 Session adapter 能否满足完整导航身份后再决定上游接口范围 |

补丁没有修改 Agent、Tools 或 Session 执行核心。补丁来源与许可见 [NOTICE](NOTICE.md)。此前直接替换 npm 客户端文件的本机兼容路线仅作为历史预览保留；当前桌面交付将这些扩展放入原生 Bundle，不要求用户改动官方安装。

## 历史本机兼容安装

`scripts/product/client-compat.mjs` 仅支持固定的 `0.2.2-alpha.3`。安装前校验上游 commit、源码补丁和四个客户端版本；只替换 `dsh-client-ui-layout/lib/client.js`、`dsh-client-ui-workspace/lib/client.js`、`dsh-client-ui-settings-general/lib/client.js` 与 `dsh-client-ui-schedule/lib/client.js`。宿主代码、Profile、模型和凭据配置不由此脚本改写。必须先停止目标宿主，避免同一窗口混用两套前端。

安装目录下 `stella-client-compat/` 保存四个原文件、补丁文件、校验清单和独立检查/恢复脚本。安装可重复；同一固定版本的已审核构建更新先恢复原资产，再更新补丁及哈希，保留最初备份。恢复只接受原文件或已知补丁文件；遇到未知修改拒绝覆盖。中断状态在下次启动检查时拒绝运行，恢复同样校验备份。该目录只包含程序文件与哈希，不复制用户数据。

本机 `dsh` 启动入口在启动前执行检查；版本、补丁文件或安装阶段不匹配时拒绝启动，并要求重新核对。直接调用官方 Node 入口会绕过此检查，因此此机制不是对其他启动方式或原生更新器的拦截承诺。DSH 升级前重新验收补丁；不自动套用到新版本。恢复原文件后，从“设置 → Stella → 打开 Stella”进入秘书页面和专用对话，顶部完整切换回到未提供状态。

后续官方具备等效扩展点时重验并移除兼容补丁。当前没有提交上游 issue/PR，也没有发布 npm 包。

## 数据与执行范围

`0.1.0` 开发版保存安装记录、称呼、身份快照、业务试验数据及 MCP 工具授权。六项业务完整度继续见 feature-parity.md；私人来源按用户明确选择的本机可信模式，通过官方 MCP Client 接入，范围及凭据重输说明见 [MCP 连接](mcp-connections.md)。快速输入通过原生 `prompt` / `whenDraftExists: 'append'` 准备草稿，用户发送前不调用模型。兼容模式在 Stella 下替换侧栏，DSH 下撤销替换；原版侧栏和子槽继续由宿主维护。

从 `0.0.6` 起使用官方 `ctx.tools.restrict` 在秘书 Preset 的保留 revision 上屏蔽全局工具；注册变化时同步重算，原生 Agent/子 Agent 继承限制。当前保留 scoped `stella_overview`、本机业务工具 `stella_generate_briefing` / `stella_review_tasks` 和用户精确授权的 MCP 工具。私人 MCP 执行 guard 在审批之后检查撤销及主动主会话身份，拒绝子 Agent／定时／未知主体。本机业务工具仅接受用户或已保存自动化发起的主会话轮次，拒绝子 Agent／未知来源。DSH 会话仍使用原有全局工具和策略；空白会话离开该 Preset 时限制随 scope 关系解除。先前“不能排除全局工具”的判断已由公开接口及运行验证修正。

## 统一自动化

`patches/0006-schedule-manager-factory.patch` 提供 `schedule.manager.view`，复用原生列表、完整编辑器与运行记录。Stella 仅保存所属任务 ID 引用，DSH 保存唯一计划与历史。Stella 入口按引用过滤，DSH 入口显示全部并标记来源。

每日简报模板保存前不调用模型；执行时本机业务工具读取的结果会发送给当前模型，简报会保存。新会话目标在两个管理入口均保持 Stella Preset；这不限制其他可信插件或 DSH 模型工具直接修改宿主任务。停用／卸载暂停 Stella 的任务，启用不自动恢复，宿主正常退出保留原状态。当前原生 Bundle 内提供该扩展；未安装 Bundle 的官方 DSH 保留其原生自动化入口和已有任务。日程提醒与 Agent 自动化分开，系统通知尚未接入。

此限制约束原生工具声明和执行查找，不隔离同进程可信代码或宿主其他会话的任意进程。因此尚不启用依赖 OS 隐私保护的个人来源。可选 Keychain provider 已通过临时密钥库与原版 Web 验证，默认停用；执行隔离、真实 OAuth、签名升级、业务权限、迁移与来源撤销仍属于 P1–P6 后续验收。
