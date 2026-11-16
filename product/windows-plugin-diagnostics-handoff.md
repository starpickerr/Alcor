# Windows 插件错误诊断验收交接

本轮修复已在 Mac 完成源码及故障回执验证。Windows 原生桌面尚未验收，先验证本分支，不将 Mac 专用归档安装到 Windows。

## 分支与范围

- Alcor：`fix/elearning-download-errors-20261115`，基线 main `dc269c018874cb13ec6d2d2908fa792a2481685d`。
- dsh-fudan-elearning：`fix/elearning-download-errors-20261115`，MCP 1.0.10。
- dsh-email：`fix/plugin-error-diagnostics-20261115`，MCP 1.0.7。
- dsh-agent-hub：`fix/plugin-error-diagnostics-20261115`，旧版插件诊断和目录摘要维护，不安装到原生 Profile。

## 给 Windows Codex 的提示词

继续验收并完成 Alcor 插件错误诊断修复。先检查仓库状态、个人 DSH Profile、当前官方客户端版本和已有改动，保留个人数据及必要备份。在专用分支操作，不覆盖未知改动，不强推。

1. 获取 starpickerr/Alcor 的 `fix/elearning-download-errors-20261115`。阅读 `product/plugin-error-diagnostics.md`，核对两个 MCP 的 source-releases.lock.json 精确修订。独立仓库的对应分支见上表。
2. 使用项目 Windows 依赖安装入口，构建 @alcor/secretary 0.1.108、@alcor/agent-hub 0.1.6，运行根 npm test 及受影响的原生桌面检查。保留宿主版本约束；Mac 专用的 eLearning、mail、Apple 和 privacy 冻结包不计作 Windows 交付，也不要删除平台声明来强装。
3. 在隔离 Profile 和真实官方 Windows DSH 中检查普通、Alcor 主会话的正常查询，以及后台进程启动失败、停止、无效响应、缺失错误文本、数据库失败的提示。应有原因和下一步，写入结果未知时不得自动重试，不泄露异常原文中的模拟凭据。不得修改审批策略来使测试通过。
4. 修复 Windows 实测发现的兼容问题，再备份并更新本机原来已启用的插件；保留会话、工作区、业务数据库、自动化及凭据，不启用原来未启用的插件。不执行真实消息发送、作业提交或 Apple 写入。记录实际运行版本与验证结果。
5. 全部必要检查通过后，按用户已有授权提交本任务改动，普通合并到 main/develop 并推送；先检查远程差异与跨仓库源锁，禁止强推。提交消息沿用仓库规范。若有无法独立解决的问题，清楚报告具体失败及恢复位置。

Mac 证据不能代替 Windows；本交接不宣称跨平台已经完成。当前 Mac 已有隐私组件源码/打包验证，但个人 Profile 未切换凭据提供方。
