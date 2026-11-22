const agentRemedies = new Map([
  ...['NO_ADAPTER', 'UNKNOWN_MODEL', 'REGISTRATION_DISPOSED', 'INVALID_MODEL_INFO'].map(code =>
    [code, '检查模型提供商是否已启用，并在当前对话重新选择可用模型。']),
  ['UNSUPPORTED_REASONING_EFFORT', '在模型选择器中更换推理强度后重试。'],
  ['CONTEXT_WINDOW_EXCEEDED', '缩短输入或新建对话后重试。'],
  ['QUOTA', '查看提供商的用量与重置时间。'],
  ['AUTH', '在模型设置中检查账号连接。'],
  ['ENOENT', '检查工作区或依赖文件是否仍在原位置。'],
  ['EACCES', '检查 DSH 对工作区及配置目录的访问权限。'],
  ['ENOSPC', '释放存储空间后重试。'],
  ['ETIMEDOUT', '检查网络和代理连接后重试。'],
])
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const toolRemedies = new Map([
  ['UNKNOWN_TOOL', '刷新工具列表，确认提供该工具的插件已启用。'],
  ['INVALID_ARGS', '核对工具要求的参数后重试。'],
  ['TOOL_ACTIVATION_FAILED', '检查工具插件的配置和启用状态。'],
  ['TOOL_EXECUTOR_UNAVAILABLE', '检查执行工具的插件是否已连接。'],
  ['INVALID_TOOL_OUTPUT', '检查工具插件版本与输出格式。'],
  ['UNSUPPORTED_SCHEMA', '检查工具参数定义与当前 DSH 版本是否兼容。'],
])

export function recordGenericToolFailure(ctx, execution, result) {
  if (!result.isError || !result.error) return
  const code = result.error?.info?.code
  if (code === 'ABORTED' || code === 'ABORTED_BEFORE_DISPATCH') return
  const id = execution.callId
  const header = '[business_operation_failed] tools.execute · ' + (toolRemedies.has(code) ? code : 'TOOL_FAILED')
    + (typeof id === 'string' && uuid.test(id) ? ' · 请求 ' + id : '')
  // Unregistered names and native tool result text can contain arbitrary model or user input.
  const name = code !== 'UNKNOWN_TOOL' && ctx.tools?.get(execution.name)?.name
  const label = typeof name === 'string' && /^[\w.-]{1,80}$/.test(name) ? '工具 ' + name : '工具调用'
  const hint = toolRemedies.get(code) ?? '在原对话中查看该次工具反馈，并检查插件连接和权限。'
  ctx.logger('DSH 工具').error(header + '\n原因：' + label + '失败。' + hint)
}

export function recordAgentFailure(ctx, entries, { agent, turn, step, error }) {
  const failure = error?.failure ?? error
  const id = failure?.requestId
  const request = typeof id === 'string' && (/^req_[A-Za-z0-9_-]{1,120}$/.test(id) || uuid.test(id)) ? id : null
  // A provider may have already logged this same upstream request with a more precise diagnosis.
  if (request && entries.some(row => row.level === 'error' && row.message.split(' · ').includes('请求 ID ' + request))) return
  const code = failure?.code
  const status = failure?.status
  const http = Number.isInteger(status) && status >= 400 && status <= 599 ? status :
    typeof code === 'string' && /^HTTP_[45]\d\d$/.test(code) ? Number(code.slice(5)) : null
  const fields = ['对话运行失败']
  if (http) fields.push('HTTP ' + http)
  if (Number.isSafeInteger(turn) && turn >= 0) fields.push('第 ' + turn + ' 轮')
  if (Number.isSafeInteger(step) && step >= 0) fields.push('步骤 ' + step)
  const session = agent?.session?.id
  if (typeof session === 'string' && uuid.test(session.replace(/^session-/, ''))) fields.push('会话 ' + session)
  if (request) fields.push('请求 ID ' + request)
  const hint = agentRemedies.get(code) ?? (http === 401 || http === 403 ? '在模型设置中核对账号和访问权限。' :
    http === 429 ? '查看提供商的限额或稍后重试。' : '按发生时间和轮次查看会话详情及对应插件的日志。')
  const safeCode = agentRemedies.has(code) ? code : http ? 'HTTP_' + http : 'AGENT_ERROR'
  const lines = ['[business_operation_failed] agent.turn · ' + safeCode, '原因：' + fields.join(' · ') + '。' + hint]
  // Surface a package-relative frame for unexpected host failures without logging private paths or the error body.
  if (typeof error?.stack === 'string') {
    const frame = error.stack.split('\n').slice(1).find(line => /^\s+at /.test(line)
      && /\/(?:@deepseek-ai|@alcor)\/[\w-]+\/(?:src|lib)\/[\w/-]+\.[cm]?[jt]s:\d+:\d+\)?$/.test(line))
    if (frame) lines.push('位置：' + frame.slice(frame.lastIndexOf('/@') + 1).replace(/\)$/, ''))
  }
  ctx.logger('DSH 对话').error(lines.join('\n'))
}
