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
