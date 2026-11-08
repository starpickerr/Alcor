export const checkMessages = {
  browser_unavailable: '无法启动登录浏览器，请安装或修复 Edge、Chrome 或 Chromium。',
  network_error: '无法连接学校服务，请检查网络或校园网连接。',
  login_form_error: '学校登录表单未能提交，请检查认证页面是否变化。',
  login_timeout: '学校登录未完成，可能需要验证码或额外认证；尚不能判断密码是否错误。',
  login_required: '学校登录状态无效，请重新登录。',
  permission_denied: 'eLearning 访问被拒绝，请检查 DSH 访问权限。',
  timeout: '测试连接超时，请检查网络或稍后重试。',
  query_failed: 'eLearning 服务未返回有效测试结果，请稍后重试。',
}

export const checkMessage = result => result.status === 'disconnected' ? 'eLearning 服务未连接，请重新保存账号后重试。' : checkMessages[result.code] || checkMessages[result.status] || checkMessages.query_failed
