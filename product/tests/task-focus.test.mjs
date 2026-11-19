import test from 'node:test'
import assert from 'node:assert/strict'
import { focusedTaskRecords, taskCompletion } from '../../packages/alcor-secretary/src/task-focus.js'
import { taskDate, taskDeadline, taskGroup, taskMatches } from '../../packages/alcor-secretary/src/presentation.js'

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

test('today shows the saved completion and plan shows the next occurrence by task timezone', () => {
  const now = new Date('2026-11-06T20:00:00Z')
  const series = { id: 'study', business: {
    timezone: 'Asia/Shanghai', repeat_frequency: 'daily',
    current_occurrence: { id: 'study@2026-11-08', due_on: '2026-11-08', planned_on: '2026-11-07', status: 'todo' },
    last_completed_occurrence: { id: 'study@2026-11-07', due_on: '2026-11-07', status: 'completed', completed_at: '2026-11-06T18:00:00Z' },
  } }
  const [next, done] = focusedTaskRecords([series])
  assert.equal(taskMatches(next, 'today', now), false)
  assert.equal(taskMatches(next, 'plan', now), true)
  assert.equal(taskMatches(done, 'today', now), true)
  assert.equal(taskMatches(done, 'plan', now), false)
  assert.equal(taskMatches(done, 'today', new Date('2026-11-07T20:00:00Z')), false)
  assert.equal(taskMatches(done, 'completed', new Date('2026-11-07T20:00:00Z')), true)
})

test('scheduled days and actual deadlines have different urgency', () => {
  const now = new Date('2026-11-20T10:00:00Z')
  const task = { due: '2026-11-19', business: { timezone: 'UTC', status: 'todo' } }
  const routine = { ...task, business: { ...task.business, repeat_frequency: 'weekly' } }
  assert.equal(taskGroup(task, now), 'overdue')
  assert.equal(taskDeadline(routine), null)
  assert.equal(taskGroup(routine, now), 'today')
  assert.equal(taskGroup({ ...routine, business: { ...routine.business, due_time: '09:00' } }, now), 'overdue')
  assert.equal(taskGroup({ ...routine, business: { ...routine.business, source_type: 'apple_reminder' } }, now), 'overdue')
  const scheduled = { ...task, due: '2026-11-21', business: { ...task.business, planned_on: '2026-11-20' } }
  assert.equal(taskDate(scheduled), '2026-11-20')
  assert.equal(taskMatches(scheduled, 'today', now), true)
  assert.equal(taskMatches({ ...scheduled, due: null }, 'today', now), true)
  assert.equal(taskMatches({ ...task, due: null }, 'today', now), false)
  assert.equal(taskMatches({ ...task, due: null }, 'all', now), true)
})
