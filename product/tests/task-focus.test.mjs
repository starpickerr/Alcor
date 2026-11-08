import test from 'node:test'
import assert from 'node:assert/strict'
import { focusedTaskRecords, taskCompletion } from '../../packages/alcor-secretary/src/task-focus.js'
import { taskMatches } from '../../packages/alcor-secretary/src/presentation.js'

test('one current local occurrence and a separate completion retain the editable series identity', () => {
  const series = { id: 'series', due: '2026-08-19', status: 'open', business: {
    repeat_frequency: 'daily', status: 'todo', current_occurrence: { id: 'series@2026-11-06', due_on: '2026-11-06', status: 'todo' },
    last_completed_occurrence: { id: 'series@2026-11-05', due_on: '2026-11-05', status: 'completed' },
  } }
  const [active, history] = focusedTaskRecords([series])
  assert.equal(active.id, 'series')
  assert.equal(active.due, '2026-11-06')
  assert.deepEqual(taskCompletion(active), { todo_id: 'series@2026-11-06', completed: true })
  assert.deepEqual(taskCompletion(history), { todo_id: 'series@2026-11-05', completed: false })
  assert.equal(series.due, '2026-08-19')
})

test('Apple identities are neither merged by title nor extrapolated into local daily occurrences', () => {
  const rows = ['a', 'b'].map(id => ({ id, title: 'Same title', status: 'open', due: '2026-08-19', business: { repeat_frequency: 'daily', source_type: 'apple_reminder', status: 'todo' } }))
  assert.deepEqual(focusedTaskRecords(rows), rows)
  assert.deepEqual(taskCompletion(rows[0]), { todo_id: 'a', completed: true })
  assert.equal(taskMatches(rows[0], 'pending', new Date('2026-11-06T12:00:00Z')), true)
  assert.equal(taskMatches({ ...rows[0], status: 'done', business: { status: 'completed' } }, 'pending', new Date()), false)
})

test('a finished series remains available in all tasks without an actionable checkbox', () => {
  const record = { id: 'ended', due: '2026-01-01', status: 'open', business: { status: 'todo', repeat_frequency: 'daily', current_occurrence: null } }
  const [ended] = focusedTaskRecords([record])
  assert.equal(ended.id, 'ended')
  assert.equal(ended.business.occurrence_unavailable, true)
  assert.equal(taskMatches(ended, 'pending', new Date()), false)
  assert.equal(taskMatches(ended, 'all', new Date()), true)
})
