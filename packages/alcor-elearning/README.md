# Alcor eLearning

使用 DSH 原生凭据连接复旦 eLearning。登录优先使用本机 Microsoft Edge，其次为 Chrome、Playwright Chromium；使用独立会话，支持学校认证页的中英文登录按钮。

在对话中查询课程、作业与课件，下载课程文件网页中的附件到明确指定的本地路径，或将本地文件上传并提交 Canvas 作业。插件提供 `list_courses`、`fetch`、`list_files`、`download_file`、`get_assignment` 和 `submit_assignment` 六项 MCP 工具，继续遵循 DSH 工具权限和账号撤销。

下载不覆盖已有文件，每个文件最大 512 MB。提交前读取作业要求和当前提交次数，支持一次 1 至 20 个普通文件；文件直接发送到学校指定的上传服务器。仅支持 Canvas 原生文件上传作业，文本、网址、测验和第三方作业系统不在此功能范围。

以正式提交回执判断成功；上传成功不等于交作业成功。重交可能替换上一轮附件，须由用户明确要求。超时或结果未知时先查询提交状态，避免重复提交；部分失败可能保留已上传的未提交文件。
