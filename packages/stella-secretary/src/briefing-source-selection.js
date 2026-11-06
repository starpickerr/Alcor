export const sourceKey = source => `${source.source}:${source.accountId}:${source.tool || ''}`
export const sourceName = source => source.label || ({ email: '邮箱', elearning: 'eLearning', calendar_agenda: 'Stella 日程', stella_tasks: 'Stella 事务', stella_bills: 'Stella 账单' })[source.source] || source.source
export function briefingScope(source) {
  if (source.source === 'elearning') return source.arguments?.course_ids?.length ? `已选择 ${source.arguments.course_ids.length} 门课程` : '全部可访问课程'
  return ({ email: '当前邮箱文件夹', calendar_agenda: '未来 24 小时日程', stella_tasks: '未归档事务', stella_bills: '未归档账单' })[source.source] || '由连接插件提供'
}
export function mergeSources(saved, discovered) {
  return [...new Map([...discovered, ...saved].map(source => [sourceKey(source), source])).values()]
}
export function selectCourseScope(source, all, ids) {
  if (!all && !ids.length) throw new Error('至少选择一门课程，或选择全部课程。')
  const args = { ...source.arguments }
  delete args.course_ids
  if (!all) args.course_ids = [...new Set(ids)]
  return { ...source, arguments: args }
}

export function visibleBriefingDetail(detail, sources, historical = false) {
  if (historical) return detail
  const selected = row => sources.some(source => source.source === row.source && source.accountId === (row.account_id || 'default') &&
    (source.source !== 'elearning' || !source.arguments?.course_ids?.length || source.arguments.course_ids.includes(String(row.payload?.course_id || row.metadata?.course_id || ''))))
  const mail = (detail.mail || []).filter(selected)
  const items = detail.items.filter(selected)
  const windows = Object.fromEntries(Object.entries(detail.briefing.source_windows || {}).filter(([, window]) => sources.some(source => source.source === window.source && source.accountId === (window.account_id || 'default'))))
  const changed = mail.length !== (detail.mail || []).length || items.length !== detail.items.length || Object.keys(windows).length !== Object.keys(detail.briefing.source_windows || {}).length
  return { ...detail, scopeChanged: changed, mail, items, briefing: { ...detail.briefing, source_windows: windows, ...(changed ? { body: '' } : {}) } }
}

export const sourceOwner = source => source.source === 'email' ? '@stella/mail' : source.source === 'elearning' ? '@stella/elearning' : '@stella/secretary'
export function briefingSourceCards(saved, catalog, retained = []) {
  const selected = new Set(saved.map(sourceKey))
  const available = new Set((catalog.sources || []).map(sourceKey))
  const cards = new Map()
  for (const source of mergeSources([...retained, ...saved], catalog.sources || [])) {
    const key = source.source.startsWith('plugin:') ? source.source.split(':').slice(0, 2).join(':') : source.source
    if (!cards.has(key)) cards.set(key, { key, package: sourceOwner(source), title: source.source === 'email' ? 'Email' : source.source === 'elearning' ? 'eLearning' : source.source.startsWith('plugin:') ? key.slice(7) : sourceName(source), count: 0, selected: 0, unavailable: 0 })
    const card = cards.get(key)
    card.count++; if (selected.has(sourceKey(source))) { card.selected++; if (!available.has(sourceKey(source))) card.unavailable++ }
  }
  for (const connection of catalog.connections || []) {
    const key = connection.package === '@stella/mail' ? 'email' : connection.package === '@stella/elearning' ? 'elearning' : null
    if (!key) continue
    if (!cards.has(key)) cards.set(key, { key, package: connection.package, title: key === 'email' ? 'Email' : 'eLearning', count: 0, selected: 0, unavailable: 0 })
    cards.get(key).connection = connection.status
  }
  return [...cards.values()]
}
