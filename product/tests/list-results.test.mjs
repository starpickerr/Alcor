import test from 'node:test'
import assert from 'node:assert/strict'
import { businessList } from '../../packages/alcor-secretary/src/list-results.js'
import { summarizeCoursePage } from '../../../dsh-fudan-elearning/plugin/src/elearning-list.js'

for (const [domain, method] of [['tasks', 'list_todos'], ['briefing', 'list_briefings']]) {
  test(`${method} paginates summaries while retaining identifiable records`, async () => {
    const rows = Array.from({ length: 23 }, (_, id) => ({ id, title: String(id), body: 'detail', native_snapshot: {} }))
    const query = async request => { assert.deepEqual(request.arguments, {}); return rows }
    const first = await businessList(domain, method, {}, query)
    const last = await businessList(domain, method, { offset: first.next_offset }, query)
    assert.deepEqual([...first.items, ...last.items], rows.map(({ id, title }) => ({ id, title })))
    assert.equal(first.total, 23)
    assert.equal(first.next_offset, 20)
    assert.equal(last.next_offset, null)
    await assert.rejects(businessList(domain, method, { offset: -1 }, query))
    await assert.rejects(businessList(domain, method, { limit: 51 }, query))
  })
}

test('course summaries preserve status, pagination, exact detail and the original briefing input', async () => {
  const page = { total: 2, next_offset: 1, status: 'partial', errors: ['incomplete'], source_items: [{
    title: 'Quiz', snippet: 'a'.repeat(200), source_id: 's', payload: { body: 'full text', course_id: 'c', due_at: '2026-11-16', grade: 0 },
  }] }
  const result = { value: { structuredContent: { value: page } } }
  const exec = { agent: {}, name: 'mcp__elearning__fetch', arguments: {} }
  const accept = { kind: 'accept' }
  const summary = await summarizeCoursePage(exec, result, () => accept)
  const { value } = JSON.parse(summary.content[0].text)
  assert.deepEqual(value, { ...page, source_items: [{ title: 'Quiz', snippet: 'a'.repeat(120), source_id: 's', course_id: 'c', due_at: '2026-11-16', grade: 0 }] })
  for (const call of [{ ...exec, agent: undefined }, { ...exec, arguments: { limit: 1 } }]) {
    assert.equal(await summarizeCoursePage(call, result, () => accept), accept)
  }
  const veto = { kind: 'block', feedback: [] }
  assert.equal(await summarizeCoursePage(exec, result, () => veto), veto)
  assert.equal(page.source_items[0].payload.body, 'full text')
})
