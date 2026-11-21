const terminal = new Set(['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'])
const errors = {
  subscription_sharing_usage_limit_exceeded: '已达到 ChatGPT 用量上限，请在 ChatGPT 用量设置查看。',
  subscription_sharing_user_not_eligible: '当前 ChatGPT 账号或工作区不能共享套餐。',
  subscription_sharing_usage_unavailable: 'OpenAI 暂时无法查询套餐用量，请稍后重试。',
  subscription_sharing_unsupported_capability: '当前 ChatGPT 套餐不支持这项请求。',
  chatpass_v2_scope_not_authorized: '此账号未授权使用 ChatGPT 套餐。',
  subscription_sharing_user_unavailable: 'OpenAI 暂时无法查询账号或工作区，请稍后重试。',
  subscription_sharing_route_not_supported: 'OpenAI 套餐不支持此请求接口。',
  chatpass_v2_invalid_authorization_context: 'OpenAI 未接受此账号的套餐授权，请重新连接。',
  subscription_sharing_invalid_user: 'OpenAI 无法验证此账号，请检查 ChatGPT 登录连接。',
}
export function publicError(code, status) {
  const message = Object.hasOwn(errors, code) ? errors[code] : ({
    401: 'OpenAI 未接受当前授权，请重新连接 ChatGPT。',
    403: 'OpenAI 拒绝了此请求，请检查账号授权、工作区或地区限制。',
    429: 'ChatGPT 请求受到限制，请稍后重试或查看用量。',
  })[status] ?? 'OpenAI 请求未完成，请稍后重试。'
  const error = new Error(status ? `${message}（HTTP ${status}）` : message)
  if (status) error.status = status
  if (Object.hasOwn(errors, code)) error.code = code
  error.terminal = terminal.has(code)
  return error
}

export async function upstreamError(response) {
  const body = await response.json().catch(() => ({}))
  const code = typeof body?.error === 'string' ? body.error : body?.error?.code
  const error = publicError(code, response.status)
  const requestId = response.headers.get('x-request-id')
  if (requestId && /^req_[A-Za-z0-9_-]{1,120}$/.test(requestId)) error.requestId = requestId
  return error
}
