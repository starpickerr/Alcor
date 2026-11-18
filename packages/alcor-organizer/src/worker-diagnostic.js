// Workers only return literal application reasons and package-relative frame locations for logging.
export function workerFailure(result, operation, elapsed) {
  const errorType = result.diagnosticType ?? result.errorType
  const type = /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(errorType ?? '') ? errorType : 'OperationError'
  const system = Number.isInteger(result.systemCode) ? 'errno ' + result.systemCode
    : /^SQLITE_[A-Z0-9_]+$/.test(result.systemCode ?? '') ? result.systemCode : ''
  const lines = [`[worker_operation_failed] ${operation} · ${type} · 耗时 ${elapsed} ms · 请求 ${result.id}`]
  if (/^[a-z][a-z0-9_]{0,63}$/.test(result.businessCode ?? '')) lines.push('业务错误：' + result.businessCode)
  if (system) lines.push('系统错误：' + system)
  if (typeof result.reason === 'string' && result.reason.trim()) lines.push('原因：' + result.reason.replace(/[\r\n]/g, ' ').slice(0, 400))
  for (const location of (Array.isArray(result.locations) ? result.locations.slice(-8) : [])) {
    if (/^(?:[A-Za-z_][\w-]*\/)*[\w-]+\.py:[1-9]\d* in (?:\w+|<\w+>)$/.test(location)) lines.push('位置：' + location)
  }
  return lines.join('\n')
}

export function reportHostFailure(ctx, domain, operation, error, reason, requestId) {
  try {
    const type = /^[A-Za-z][\w-]{0,63}$/.test(error?.code ?? '') ? error.code
      : /^[A-Za-z][\w-]{0,63}$/.test(error?.name ?? '') ? error.name : 'OperationError'
    const header = `[business_operation_failed] ${operation} · ${type}` + (/^[a-f0-9-]{36}$/i.test(requestId ?? '') ? ' · 请求 ' + requestId : '')
    const locations = (error?.stack ?? '').split('\n').slice(1).flatMap(line => {
      const match = line.match(/\/(?:@alcor\/|alcor-)([\w-]+)\/((?:src|lib)\/[\w./-]+:\d+:\d+)\)?$/)
      return match ? ['位置：' + match[1] + '/' + match[2]] : []
    }).slice(0, 6)
    ctx.logger('alcor-' + domain).error([header, '原因：' + reason, ...locations].join('\n'))
  } catch { /* Observing a failure must preserve its original result. */ }
}
