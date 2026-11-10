import test from 'node:test'
import assert from 'node:assert/strict'
import {visiblePresetStore} from '../../packages/alcor-secretary/src/session-presets.js'

test('internal and transport presets disappear from the native picker', () => {
  const options = ['standard','alcor','ptc','stella','alcor-briefing','stella-briefing','qq-group-chat','agent-hub'].map(id=>({id}))
  const state = {current:'alcor',options}, original = {getSnapshot:()=>state,subscribe(){}}
  const store = visiblePresetStore(original)
  assert.deepEqual(store.getSnapshot().options.map(x=>x.id),['standard','alcor','ptc','agent-hub'])
  assert.equal(store.getSnapshot(),store.getSnapshot())
  assert.equal(original.getSnapshot().options.length,8)
})
