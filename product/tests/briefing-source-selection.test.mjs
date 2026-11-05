import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeSources, selectCourseScope, briefingScope } from '../../packages/stella-secretary/src/briefing-source-selection.js'

test('discovery preserves the saved course scope and disconnected sources', () => {
  const source = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', arguments: { course_ids: ['12'], offset: 0 } }
  const stale = { source: 'email', accountId: 'removed' }
  assert.deepEqual(mergeSources([source, stale], [{ ...source, arguments: {} }]), [source, stale])
  assert.equal(briefingScope(source), '已选择 1 门课程')
})

test('course selection cannot accidentally turn an empty subset into all courses', () => {
  const source = { source: 'elearning', arguments: { course_ids: ['old'], page_size: 50 } }
  assert.throws(() => selectCourseScope(source, false, []), /至少选择/)
  assert.deepEqual(selectCourseScope(source, false, ['12', '12']).arguments, { course_ids: ['12'], page_size: 50 })
  assert.deepEqual(selectCourseScope(source, true, []).arguments, { page_size: 50 })
  assert.deepEqual(source.arguments.course_ids, ['old'])
})
