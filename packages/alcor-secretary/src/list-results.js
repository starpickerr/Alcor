const taskFields = ['id', 'series_id', 'title', 'status', 'completed_at', 'due_on', 'due_time', 'timezone', 'parent_id', 'priority']
const briefingFields = ['id', 'title', 'status', 'generated_at', 'kept_count']

export async function businessList(domain, method, { offset = 0, limit = 20, ...filters }, query) {
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 50) {
    throw new Error('offset 须为非负整数，limit 须为 1 至 50 的整数。')
  }
  const rows = await query({ domain, method, arguments: filters })
  const fields = domain === 'tasks' ? taskFields : briefingFields
  const items = rows.slice(offset, offset + limit).map(row =>
    Object.fromEntries(fields.filter(key => row[key] != null).map(key => [key, row[key]])))
  return { items, total: rows.length, next_offset: offset + items.length < rows.length ? offset + items.length : null }
}
