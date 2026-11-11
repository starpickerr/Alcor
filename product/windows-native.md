# Windows 原生插件验证

适用基线为 02217cd4。目标为 Windows x64 官方 DSH 0.2.2-rc.1 和标准插件。官方安装文件的 Authenticode 签名、发布者及归档摘要在本机核验后使用。测试安装和直接启动均使用独立的空白 Profile。

- 已在原版 DSH 窗口调用秘书概览和事务分类接口。Python 业务进程支持超过 260 字符的插件路径，标准输入输出以 UTF-8 传输。开发模式使用 Windows 的 python 命令，正式 Host 使用随 DSH 提供的解释器。
- 页面删除确认通过 Windows 原生窗口展示本次对象和范围；默认项为取消。单次操作标识通过子进程管道核对，取消、超时或返回标识不匹配时终止操作。已使用合成请求检查两个按钮及中文显示。
- 邮件与学校连接使用合成 MCP 验证凭据轮换和撤销。Windows 测试核对凭据文件的访问控制。现有邮件、学校、Apple 同步和 Keychain 安装包仍声明 macOS 平台；本次未发布它们的 Windows 版本。
- 以上验证覆盖共享环境所需的基础能力。顶部切换、官方插件启停、旧 Profile 升级和跨官方版本更新需要在共享方案分支验证。

从仓库根目录运行以下定向测试，使用 Node 24.9 以上、Python 3 以及已经安装的开发依赖。

```powershell
node --test product/tests/business-python.test.mjs product/tests/alcor-delete-confirmation.test.mjs product/tests/elearning-credentials.test.mjs product/tests/mail-credentials.test.mjs
```
