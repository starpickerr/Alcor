import test from 'node:test'
import assert from 'node:assert/strict'
import { bearWorkspace, bearSession, normalizePosition, visiblePresetStore } from '../../packages/alcor-secretary/src/bear-state.js'

const catalog = {phase:'ready', byId:Object.fromEntries(['alcor', 'stella', 'alcor-briefing','agent-hub'].map(id => [id,{id,projectionValues:{agentPreset:id}}]))}
const spaces = {phase:'ready', items:[{workspaceId:'a', sessionIds:['alcor','stella','alcor-briefing']},{workspaceId:'b', sessionIds:['agent-hub']}], archivedSessionIds:[]}
test('the bear follows the selected workspace and resumes only ordinary Alcor conversations', () => {
  assert.equal(bearWorkspace(spaces, 'agent-hub', 'a'), 'b')
  assert.equal(bearWorkspace(spaces, undefined, 'a'), 'a')
  assert.equal(bearWorkspace(spaces, undefined), undefined)
  assert.equal(bearSession('a','alcor',catalog,spaces),'alcor')
  for (const id of ['stella','alcor-briefing','agent-hub',undefined]) assert.equal(bearSession('a',id,catalog,spaces),undefined)
  assert.equal(bearSession('b','alcor',catalog,spaces),undefined)
  assert.equal(bearSession('a','alcor',catalog,{...spaces,archivedSessionIds:['alcor']}),undefined)
})
test('only the three internal or historical choices disappear from the native picker', () => {
  const options = ['standard','alcor','ptc','stella','alcor-briefing','stella-briefing','agent-hub'].map(id=>({id}))
  const state = {current:'alcor',options}, original = {getSnapshot:()=>state,subscribe(){}}
  const store = visiblePresetStore(original)
  assert.deepEqual(store.getSnapshot().options.map(x=>x.id),['standard','alcor','ptc','agent-hub'])
  assert.equal(store.getSnapshot(),store.getSnapshot())
  assert.equal(original.getSnapshot().options.length,7)
})
test('drag positions stay within the window and a corrupt position returns to the lower right', () => {
  assert.deepEqual(normalizePosition([1.4,-0.1]),[1,0])
  assert.deepEqual(normalizePosition([0.2,NaN]),[1,1])
})
