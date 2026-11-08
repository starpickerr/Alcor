import test from 'node:test'
import assert from 'node:assert/strict'
import { recordPermissions, recordMenuEntries, recordEditTarget, recordClipboardText, recordRepeatLabel, recordAlarms, recordId, taskDeleteMessage } from '../../packages/alcor-secretary/src/organizer-actions.js'

test('viewing and copying remain available while Apple controls edits and deletion independently', () => {
  const record = {id:'read',kind:'event',business:{apple:{can_modify:false,can_delete:true}}}
  const rights = recordPermissions(record)
  assert.equal(rights.edit,false)
  assert.equal(rights.remove,true)
  const entries = recordMenuEntries(record,[{id:'work',name:'工作'}])
  assert.equal(entries.find(item => item.id==='details').disabled,undefined)
  assert.equal(entries.find(item => item.id==='copy').disabled,undefined)
  assert.equal(entries.find(item => item.id==='group').disabled,true)
  assert.equal(entries.find(item => item.id==='delete').disabled,false)
  assert.equal(recordPermissions({...record,business:{...record.business,apple_pending:[{id:'pending'}]}}).remove,false)
})

test('context actions keep occurrence identity and edit a task series without moving its start date', () => {
  const root = {id:'task-id',kind:'task',due:'2026-08-19',business:{id:'task-id',due_on:'2026-08-19'}}
  const occurrence = {id:'task:task-id@2026-11-07',kind:'task',due:'2026-11-07',business:{id:'task-id@2026-11-07',series_id:'task-id',due_on:'2026-11-07'}}
  assert.equal(recordId(occurrence),'task-id')
  assert.equal(recordEditTarget(occurrence,[root]),root)
  const event = {id:'event-id@2026-11-07',kind:'event',business:{series_id:'event-id'}}
  assert.equal(recordEditTarget(event,[]),event)
  assert.equal(recordPermissions({...occurrence,business:{...occurrence.business,apple_history:true}}).complete,false)
})

test('group choices reflect the actual destination permissions and completion state', () => {
  const record={id:'task',kind:'task',status:'done',business:{category_id:'home'}}
  const entries=recordMenuEntries(record,[{id:'home',name:'个人'},{id:'read',display_name:'订阅',name:'Internal alias',apple:{can_modify:false}}])
  assert.equal(entries.find(item => item.id==='complete').label,'标为未完成')
  const group=entries.find(item => item.id==='group')
  assert.equal(group.label,'清单')
  assert.equal(group.submenu[0].disabled,true)
  assert.equal(group.submenu[1].disabled,true)
  assert.equal(group.submenu[1].label,'订阅')
  assert.equal(recordMenuEntries(record,[],true).find(item => item.id==='complete').disabled,true)
})

test('copied text contains user-facing details and never internal identifiers or synchronization snapshots', () => {
  const record={id:'private-id',kind:'task',title:'读书',due:'2026-11-07',details:'第三章',business:{due_time:'13:30',url:'https://example.com',native_snapshot:{secret:'internal'}}}
  const text=recordClipboardText(record)
  assert.match(text,/读书/)
  assert.match(text,/2026-11-07 13:30/)
  assert.match(text,/第三章/)
  assert.match(text,/https:\/\/example.com/)
  assert.doesNotMatch(text,/private-id|internal|native_snapshot/)
})

test('the inspector describes native recurrence and respects an explicitly empty alarm list', () => {
  assert.match(recordRepeatLabel({native_recurrence:{frequency:'monthly',interval:2,count:12,weekday_ordinals:[{day:0,week:2}]}}),/每 2 个月/)
  assert.match(recordRepeatLabel({native_recurrence:{frequency:'daily',count:12}}),/12 次/)
  assert.match(recordRepeatLabel({native_recurrence:{frequency:'monthly',weekday_ordinals:[{day:0,week:2}]}}),/第 2 个周一/)
  assert.deepEqual(recordAlarms({alarms:[],remind_at:'2026-11-07T13:30:00Z'}),[])
  assert.deepEqual(recordAlarms({remind_at:'2026-11-07T13:30:00Z'}),[{absolute:'2026-11-07T13:30:00Z'}])
})

test('delete warnings include native repeat rules even when the local editor cannot represent them', () => {
  const record={kind:'task',business:{repeat_frequency:'none',native_recurrence:{frequency:'monthly'},apple:{can_delete:true}}}
  assert.match(taskDeleteMessage(record),/停止后续重复/)
  assert.doesNotMatch(taskDeleteMessage(record),/独立历史.*保留/)
})

test('same-name calendars remain distinct by account and stable destination identity', () => {
  const groups=[{id:'personal-work',name:'Work',apple:{source_name:'iCloud'}},{id:'company-work',name:'Work',apple:{source_name:'公司'}}]
  const entries=recordMenuEntries({id:'event',kind:'event',business:{category_id:'personal-work'}},groups)
  const choices=entries.find(item => item.id==='group').submenu
  assert.deepEqual(choices.map(item => item.id),['group:personal-work','group:company-work'])
  assert.deepEqual(choices.map(item => item.label),['Work · iCloud','Work · 公司'])
  assert.equal(choices[0].disabled,true)
  assert.equal(choices[1].disabled,false)
})

test('copied multi-day all-day events include the last included date', () => {
  const text=recordClipboardText({kind:'event',title:'出差',allDay:true,start:'2026-11-07',end:'2026-11-10',business:{}})
  assert.match(text,/2026-11-07 — 2026-11-09 · 全天/)
  assert.doesNotMatch(text,/2026-11-10/)
})
