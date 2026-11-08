export const checkMessages = {
  dns_error: '无法解析 IMAP 服务器地址，请检查服务器地址和 DNS 设置。',
  tls_error: 'SSL 连接失败，请检查 SSL 端口和证书设置。',
  timeout: '测试连接超时，请检查网络及 SSL 端口，稍后重试。',
  connection_refused: '邮箱服务器拒绝连接，请检查服务器地址和 SSL 端口。',
  connection_lost: '邮箱服务器断开了连接，请稍后重试。',
  auth_failed: '邮箱服务器拒绝登录，请确认已开启 IMAP，并使用有效的客户端授权码。',
  network_error: '无法连接邮箱服务器，请检查网络连接。',
  permission_denied: '邮件访问被拒绝，请检查当前 DSH 访问权限。',
  account_unavailable: '邮箱账号已移除，请刷新账号列表。',
  invalid_config: '邮箱设置不完整或格式无效，请检查账号和 IMAP 设置。',
  folder_denied: '账号已登录，但无法读取此文件夹，请检查文件夹名称和邮箱访问权限。',
  query_failed: '邮件服务未返回有效测试结果，请刷新状态后重试。',
}

export const checkMessage = result => result.status === 'disconnected' ? '邮件服务未连接，请刷新状态后重试。' : checkMessages[result.code] || checkMessages.query_failed
