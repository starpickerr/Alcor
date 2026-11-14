# Windows 原生插件验证

Windows x64 使用官方 DSH 0.2.2-rc.1、0.2.2-rc.2、0.2.2-rc.3 和标准 `@alcor/secretary` 插件。安装文件、可执行文件和 app.asar 的摘要记录在 `product/desktop-release.lock.json`，已核对 Authenticode 签名。以下验收始于全新的测试 Profile，包含真实官方桌面进程和合成业务记录。

- 已在原版 DSH 窗口调用秘书概览和事务分类接口。Python 业务进程支持超过 260 字符的插件路径，标准输入输出以 UTF-8 传输。开发模式使用 Windows 的 python 命令，正式 Host 使用随 DSH 提供的解释器。
- 页面删除确认通过 Windows 原生窗口展示本次对象和范围；默认项为取消。单次操作标识通过子进程管道核对，取消、超时或返回标识不匹配时终止操作。已使用合成请求检查两个按钮及中文显示。
- 邮件与学校连接使用合成 MCP 验证凭据轮换和撤销。Windows 测试核对凭据文件的访问控制。现有邮件、学校、Apple 同步和 Keychain 安装包仍声明 macOS 平台；本次未发布它们的 Windows 版本。
- AgentHub 能启动 Windows 命令行提供方、读取中文输出并取消本次进程树。Apple 迁移关闭 SQLite 源与目标文件句柄。Apple 业务契约使用合成助手验证。

## 共享桌面验收

使用原侧栏恢复分支 `d34795e0` 的秘书包 0.1.102 完成以下检查。AuraBot 1.2.1 来自上游 `aureth-labs/dsh-aurabot-qq` 的 `a138d14e`，通过官方 CLI 安装到同一测试 Profile。

- DSH 和 Alcor 双向切换，工作区 ID 相同，插件目录返回一致。Alcor 的「系统」分组与 DSH 的 AuraBot 入口都打开原插件的「大肥鱼」页面。DSH 模式隐藏 Alcor 的应用和系统分组。
- 在 1180 和 720 像素宽的原生窗口验证顶部切换、键盘菜单、标题栏和三个窗口控制按钮的命中位置。720 像素宽时，实际拖动标题栏使窗口横纵各移动 32 像素。业务弹窗阻止点击背景切换按钮。
- 在集成提交 `0cb019ef` 的 `rc.2` 插件页复测了窄窗修复。720 像素宽的原生窗口对应 704 像素宽的页面，侧栏按钮位于标题栏左上角，鼠标命中中心可展开、收起侧栏。Windows 原生 Ctrl+B 在 Alcor 与官方 DSH 中均可切换侧栏。该桌面快捷键由 Electron 接收，验收从系统前台窗口发送按键。
- 正常退出和重启保留选择。冷停用恢复官方 DSH，重新启用、卸载及重装后保留工作区与原事务 ID。此验证通过官方 CLI 与 Profile 管理 API 更改插件状态。
- 将同一 Profile 从签名 `rc.1` 交给 `rc.2` 打开，原事务 ID、工作区和配置保留。另用尚未声明支持 `rc.2` 的旧包验证过不兼容回退，官方插件页仍可操作。
- Alcor 使用原品牌侧栏，依次展示「新对话」「应用」「系统」和工作区。DSH 模式恢复官方侧栏。在 `rc.2` 上连续三次冷启动，原工作区树和共享目录仍可见；原先的 `useSectionScroller` 错误已修复。未测试真实 QQ 消息发送、个人迁移或 Apple 账号。
- 共享代码的 Windows 全量回归快照为 `f56af492` 加入 `f9664d63` 的长路径测试修正，共 527 项，520 通过，3 跳过，4 失败均在归档独立客户端的 `environments.test.mjs`。以上原侧栏实机验收使用后续提交，未在该次完整回归中计数。

## rc.3 补验与测试平台范围

2026-11-14，在共同候选 `232fca661e09a14d89696f5b592d05af1aa7628d` 上完成 rc.2 → rc.3 的保留 Profile 升级：秘书 0.1.105、AgentHub 0.1.5 启用、双插件停用／恢复，旧事务、自动化任务 ID、工作区和会话关联读回通过。两种模式共享工作区与插件目录；模板和手动自动化、范围筛选、原生同一任务、540px 高菜单与 Escape 焦点、宽窄标题栏及右侧两条分隔线实际拖动通过。Windows 专项 39 项通过，秘书 `lib/client.js` 与已验 Mac 产物摘要一致。

个人官方客户端也已从 rc.2 原地升级到 rc.3；双向切换后恢复原模式，两插件、工作区／会话及配置已核对。修复了安装登记中已不存在的 D 盘路径，保留七份共 5989 字节的小备份，未重启系统。未将 Apple／学校／邮件的 macOS 功能计入 Windows 验收。

`environments.test.mjs` 同时覆盖可移植的数据保护和保留的独立启动器。后者的 `officialDesktop()` 明确只接受 macOS，Windows 验证该拒绝边界，不执行 POSIX `ps` 进程识别；它不影响当前官方 DSH 中的共享插件。目录别名使用 Windows junction 实测拒绝；文件符号链接检查单独执行，仅在 Windows 返回缺少创建权限的 `EPERM` 时标记该子项跳过，其余错误仍失败。安装包缓存验证使用平台路径函数。Linux／Windows CI 均包含此测试文件，macOS 完整产品测试继续执行进程与文件链接断言。

PR 检查按现有规则跳过 `native-source-connectors`、Apple 原生打包和隔离 Keychain 步骤；这些跳过不能记为通过，完整 Native 工作流需通过手动运行或主线推送单独核对。

## 本地复现

在共享分支构建秘书包。Windows 开发依赖树包含若干仅发布 macOS 版本的工作区，安装开发依赖时使用下面的选项。插件通过 DSH 自身的 CLI 安装。

```powershell
npm ci --force --ignore-scripts
npm run product:pack
python -m pip install playwright pywin32 tzdata
$env:PYTHONUTF8 = '1'
python product/tests/official_shared_windows.py --app "C:\DSH-rc1\DeepSeek Harness.exe" --package ".local/artifacts/alcor-secretary-0.1.102.tgz" --aurabot "C:\fixtures\dsh-aurabot-qq-1.2.1.tgz" --upgrade-app "C:\DSH-rc2\DeepSeek Harness.exe" --expect-compatible-upgrade
```

命令创建独立的本机测试资料，正常退出每次启动的官方进程，并在 `.local/official-shared-windows` 下写入截图、窗口命中位置及日志。`--upgrade-app` 可省略。用旧的不兼容包验证官方回退时，省略 `--expect-compatible-upgrade`。也可直接将 `--app` 指向已锁定的 `rc.2` 可执行文件，独立验证全新 Profile。实际鼠标拖动只在测试窗口持有前台时执行，未执行时会在 `caption.json` 中明确记录。

从仓库根目录运行以下定向测试，使用 Node 24.9 以上、Python 3 以及已经安装的开发依赖。

```powershell
node --test product/tests/business-python.test.mjs product/tests/alcor-delete-confirmation.test.mjs product/tests/elearning-credentials.test.mjs product/tests/mail-credentials.test.mjs
```
