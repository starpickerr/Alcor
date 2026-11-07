export function connectionStatus(state, busy, readFailed = false) {
  if (busy) return { label: '处理中', tone: 'pending' }
  if (readFailed) return { label: '状态读取失败', tone: 'error' }
  if (!state) return { label: readFailed ? '状态读取失败' : '读取中', tone: readFailed ? 'error' : 'pending' }
  if (state.authorization !== 'full_access') return { label: state.authorization === 'not_determined' ? '待授权' : '无法同步', tone: 'warning' }
  if (state.error) return { label: '同步异常', tone: 'error' }
  if (state.category_conflicts?.length || state.operations?.length || state.links?.some(link => link.name_conflict)) return { label: '部分同步 · 待处理', tone: 'warning' }
  if (state.conflicts?.length) return { label: '待处理冲突', tone: 'warning' }
  if (state.readonly_issues?.length) return { label: '部分同步 · 含只读内容', tone: 'warning' }
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

const actions = { delete: '删除', exclude: '跳过本次', complete: '完成本次', edit: '编辑', truncate: '删除本次及将来' }

export function reviewItems(state, kind) {
  const groupLabel = kind === 'calendar' ? '日历' : '清单'
  const groups = state?.categories ?? []
  const links = state?.links ?? []
  const categories = [...(state?.category_conflicts ?? []), ...links.filter(link => link.name_conflict && !(state?.category_conflicts ?? []).some(item => item.apple_id === link.apple_id && item.kind === 'name')).map(link => ({ ...link, kind: 'name', reason: link.name_conflict }))]
  return [
    ...categories.map(item => {
      const local = groups.find(group => group.id === item.stella_id)
      const link = links.find(link => link.apple_id === item.apple_id)
      const native = (state.calendars ?? state.lists ?? []).find(group => group.id === item.apple_id)
      return { ...item, type: 'category', key: `category:${item.apple_id}:${item.kind}`, title: local?.display_name || local?.name || link?.stella_name || link?.apple_name || `${groupLabel}需要确认`,
        label: { name: `${groupLabel}名称冲突`, color: `${groupLabel}颜色冲突`, missing_source: `找不到 Apple ${groupLabel}`, legacy_retained: '升级后保留的内容', legacy_delete: '旧删除尚未完成' }[item.kind] || `${groupLabel}需要确认`,
        local, native, source: native?.source || local?.apple?.source_name || '', groupLabel }
    }),
    ...(state?.conflicts ?? []).map(item => ({ ...item, type: 'record', key: `record:${item.stella_id ?? item.todo_id}`, title: item.title || (kind === 'calendar' ? '日程内容冲突' : '事务内容冲突'), label: '两侧内容需要确认', source: groups.find(group => group.id === item.category_id)?.name || '' })),
    ...(state?.operations ?? []).map(item => ({ ...item, type: 'operation', key: `operation:${item.id}`, title: item.document?.title || item.binding?.native?.title || `${item.entity === 'category' ? groupLabel : kind === 'calendar' ? '日程' : '事务'}操作`, label: `${actions[item.action] || '更改'} · 等待 Apple 确认`, source: '' })),
  ]
}

export function resolutionRequest(item, kind, choice, sourceId) {
  if (item.type === 'operation') return { method: 'cancel_operation', arguments: { operation_id: item.id } }
  if (item.type === 'record') return { method: 'resolve_conflict', arguments: { [kind === 'calendar' ? 'stella_id' : 'todo_id']: item.stella_id ?? item.todo_id, choice } }
  return { method: 'resolve_category_conflict', arguments: { apple_id: item.apple_id,
    ...(['name', 'color'].includes(item.kind) ? { field: item.kind } : {}),
    ...(choice.startsWith('rebind:') ? { choice: 'rebind', replacement_id: choice.slice(7) } : { choice }),
    ...(choice === 'keep_stella' && item.kind === 'missing_source' && sourceId ? { source_id: sourceId } : {}),
  } }
}
