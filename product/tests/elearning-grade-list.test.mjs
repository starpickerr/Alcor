import test from 'node:test'
import assert from 'node:assert/strict'
import { summarizeCoursePage } from '../../packages/alcor-elearning/src/elearning-list.js'

test('grade list keeps zero and the possible score without exposing the full body', async () => {
  const page = { source_items: [{ source_id: 'grade-1', title: '测验成绩', snippet: '0 / 100',
    payload: { resource_type: 'grade', body: '完整评分原文', score: 0, points_possible: 100 } }] }
  const decision = await summarizeCoursePage(
    { name: 'mcp__elearning__fetch', agent: {}, arguments: { resource_types: ['grade'] } },
    { value: { structuredContent: { value: page } } },
    async () => ({ kind: 'accept' }),
  )
  const grade = JSON.parse(decision.content[0].text).value.source_items[0]
  assert.equal(grade.score, 0)
  assert.equal(grade.points_possible, 100)
  assert.equal(grade.resource_type, 'grade')
  assert.equal(JSON.stringify(grade).includes('完整评分原文'), false)
})
