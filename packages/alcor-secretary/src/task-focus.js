export function focusedTaskRecords(records) {
  return records.flatMap(record => {
    if (!Object.hasOwn(record.business ?? {}, 'current_occurrence')) return [record]
    const occurrences = [record.business.current_occurrence, record.business.last_completed_occurrence].filter(Boolean)
    if (!occurrences.length) return [{ ...record, business: { ...record.business, occurrence_unavailable: true } }]
    return occurrences.map(occurrence => ({
      ...record, due: occurrence.due_on, status: occurrence.status === 'completed' ? 'done' : 'open',
      business: { ...record.business, apple_history: !!occurrence.apple_history, status: occurrence.status, occurrence_id: occurrence.id, completed_at: occurrence.completed_at },
    }))
  })
}

export function taskCompletion(record) {
  return { todo_id: record.business?.occurrence_id ?? record.id, completed: record.status !== 'done' }
}
