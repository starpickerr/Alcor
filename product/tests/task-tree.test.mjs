import test from 'node:test'
import assert from 'node:assert/strict'
import { taskTree } from '../../packages/stella-secretary/src/task-tree.js'

const now = new Date(2026, 10, 7, 12)
const task = (id, parent, due, status = 'todo') => ({ id, title: id, due, status: status === 'completed' ? 'done' : 'open', business: { parent_id: parent, status } })

test('parents precede their sorted children and collect descendant urgency once', () => {
  const rows = [task('paper', 'day', '2026-11-07'), task('late', 'day', '2026-11-06'), task('day', 'project'), task('done', 'day', '2026-11-05', 'completed'), task('project')]
  const roots = taskTree(rows, rows, now)
  assert.deepEqual(roots.map(node => node.record.id), ['project'])
  const day = roots[0].children[0]
  assert.deepEqual(day.children.map(node => node.record.id), ['paper', 'late', 'done'])
  assert.deepEqual(roots[0].urgency, { overdue: 1, today: 1 })
  assert.equal(roots[0].group, 'overdue')
  assert.equal(roots[0].matchCount, 5)
})

test('filtered results preserve ancestors, omit unrelated siblings and count only matches', () => {
  const rows = [task('project'), task('day', 'project'), task('paper', 'day', '2026-11-07'), task('other', 'day')]
  const [root] = taskTree(rows, [rows[2]], now)
  assert.equal(root.matched, false)
  assert.equal(root.matchCount, 1)
  assert.equal(root.group, 'today')
  assert.equal(root.children[0].matched, false)
  assert.deepEqual(root.children[0].children.map(node => node.record.id), ['paper'])
  assert.equal(root.children[0].children[0].matched, true)
  assert.deepEqual(taskTree(rows, [], now), [])
})

test('orphans, cycles and repeat occurrences remain reachable without duplication', () => {
  const rows = [task('orphan', 'missing'), task('a', 'b'), task('b', 'a'), task('repeat'), { ...task('repeat', null, null, 'completed'), business: { occurrence_id: 'yesterday', status: 'completed' } }]
  const seen = []
  function visit(nodes) { for (const node of nodes) { seen.push(node.key); visit(node.children) } }
  visit(taskTree(rows, rows, now))
  assert.equal(seen.length, 5)
  assert.equal(new Set(seen).size, 5)
})

test('completed ancestor context does not hide a pending child or count as overdue', () => {
  const rows = [task('parent', null, '2026-11-01', 'completed'), task('child', 'parent', '2026-11-07')]
  const [root] = taskTree(rows, [rows[1]], now)
  assert.equal(root.matched, false)
  assert.equal(root.group, 'today')
  assert.deepEqual(root.urgency, { overdue: 0, today: 1 })
  assert.equal(root.matchCount, 1)
  assert.equal(root.children[0].record.id, 'child')
})
