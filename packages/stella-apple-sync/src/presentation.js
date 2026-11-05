export function connectionStatus(state, busy, readFailed = false) {
  if (busy) return { label: '处理中', tone: 'pending' }
  if (!state) return { label: readFailed ? '状态读取失败' : '读取中', tone: readFailed ? 'error' : 'pending' }
  if (state.authorization !== 'full_access') return { label: state.authorization === 'not_determined' ? '待授权' : '无法同步', tone: 'warning' }
  if (state.error) return { label: '同步异常', tone: 'error' }
  if (state.conflicts?.length) return { label: '待处理冲突', tone: 'warning' }
  if (state.connected && state.auto_enabled) return { label: '自动同步已开启', tone: 'success' }
  return { label: state.connected || state.last_success || state.links?.length ? '已暂停' : '未开启', tone: 'neutral' }
}

export function formatSyncTime(value) {
  if (!value) return null
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return null
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(date)
}
