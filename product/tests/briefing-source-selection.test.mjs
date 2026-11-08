import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeSources, selectCourseScope, briefingScope, visibleBriefingDetail } from '../../packages/alcor-secretary/src/briefing-source-selection.js'

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


test('current report shows only selected sources, accounts and courses while history stays intact', () => {
  const rows = [{source:'alcor_tasks',account_id:'local'}, {source:'email',account_id:'other'},
    {source:'elearning',account_id:'school',metadata:{course_id:'one'},payload:{course_id:'one'}},
    {source:'elearning',account_id:'school',metadata:{course_id:'two'},payload:{course_id:'two'}}]
  const detail={briefing:{body:'Old body includes unselected sources',source_windows:{tasks:{source:'alcor_tasks',account_id:'local'},school:{source:'elearning',account_id:'school'}}},mail:rows,items:rows}
  const sources=[{source:'elearning',accountId:'school',arguments:{course_ids:['one']}}]
  const visible=visibleBriefingDetail(detail,sources)
  assert.deepEqual(visible.items,[rows[2]])
  assert.deepEqual(visible.mail,[rows[2]])
  assert.deepEqual(Object.keys(visible.briefing.source_windows),['school'])
  assert.equal(visible.briefing.body,'')
  assert.equal(visible.scopeChanged,true)
  assert.equal(visibleBriefingDetail(detail,sources,true),detail)
  assert.equal(visibleBriefingDetail(detail,[]).items.length,0)
  assert.deepEqual(visibleBriefingDetail(detail,[]).briefing.source_windows,{})
})

test('historical source identifiers remain visible under the new local selection', () => {
  const row = { source: 'stella_tasks', account_id: 'local' }
  const detail = { briefing: { body: 'preserved', source_windows: { old: row } }, mail: [row], items: [row] }
  const result = visibleBriefingDetail(detail, [{ source: 'alcor_tasks', accountId: 'local' }])
  assert.deepEqual(result.items, [row])
  assert.equal(result.briefing.body, 'preserved')
  assert.equal(briefingScope({ source: 'stella_tasks' }), '未归档事务')
})

test('email cards aggregate accounts without subscribing, expose no addresses, and preserve unavailable selections', async () => {
  const { briefingSourceCards, sourceOwner } = await import('../../packages/alcor-secretary/src/briefing-source-selection.js')
  const accounts = ['one', 'two', 'three'].map(accountId => ({ source: 'email', accountId, label: accountId + '@private.test', tool: 'mcp__email__fetch_messages', arguments: { account_id: accountId } }))
  const saved = [{ ...accounts[0], arguments: { account_id: 'one', folder: 'selected-folder' } }, accounts[1]]
  const snapshot = JSON.stringify(saved)
  const cards = briefingSourceCards(saved, { sources: accounts, connections: [{ package: '@alcor/mail', status: 'ready' }] })
  assert.equal(cards.length, 1)
  assert.deepEqual(cards[0], { key: 'email', package: '@alcor/mail', title: 'Email', count: 3, selected: 2, unavailable: 0, connection: 'ready' })
  assert(!JSON.stringify(cards).includes('@private.test'))
  assert.equal(JSON.stringify(saved), snapshot)
  assert.equal(briefingSourceCards([], { sources: accounts })[0].selected, 0)
  assert.equal(briefingSourceCards(saved, { sources: [], connections: [] })[0].unavailable, 2)
  assert.equal(sourceOwner({ source: 'elearning' }), '@alcor/elearning')
  assert.equal(sourceOwner({ source: 'calendar_agenda' }), '@alcor/secretary')
})
