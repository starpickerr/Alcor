import test from 'node:test'
import assert from 'node:assert/strict'
import { bearWorkspace, bearSession, createBearState, normalizePosition, visiblePresetStore } from '../../packages/alcor-secretary/src/bear-state.js'

const catalog = {phase:'ready', byId:Object.fromEntries(['alcor', 'stella', 'alcor-briefing','agent-hub'].map(id => [id,{id,projectionValues:{agentPreset:id}}]))}
const spaces = {phase:'ready', items:[{workspaceId:'a', sessionIds:['alcor','stella','alcor-briefing']},{workspaceId:'b', sessionIds:['agent-hub']}], archivedSessionIds:[]}
test('the bear uses the Alcor default workspace and resumes only ordinary Alcor conversations', () => {
  assert.equal(bearWorkspace(spaces, 'a'), 'a')
  assert.equal(bearWorkspace(spaces, 'missing'), undefined)
  assert.equal(bearWorkspace(spaces, undefined), undefined)
  assert.equal(bearSession('a','alcor',catalog,spaces),'alcor')
  for (const id of ['stella','alcor-briefing','agent-hub',undefined]) assert.equal(bearSession('a',id,catalog,spaces),undefined)
  assert.equal(bearSession('b','alcor',catalog,spaces),undefined)
  assert.equal(bearSession('a','alcor',catalog,{...spaces,archivedSessionIds:['alcor']}),undefined)
})
test('internal and transport presets disappear from the native picker', () => {
  const options = ['standard','alcor','ptc','stella','alcor-briefing','stella-briefing','qq-group-chat','agent-hub'].map(id=>({id}))
  const state = {current:'alcor',options}, original = {getSnapshot:()=>state,subscribe(){}}
  const store = visiblePresetStore(original)
  assert.deepEqual(store.getSnapshot().options.map(x=>x.id),['standard','alcor','ptc','agent-hub'])
  assert.equal(store.getSnapshot(),store.getSnapshot())
  assert.equal(original.getSnapshot().options.length,8)
})
test('drag positions stay within the window and a corrupt position returns to the lower right', () => {
  assert.deepEqual(normalizePosition([1.4,-0.1]),[1,0])
  assert.deepEqual(normalizePosition([0.2,NaN]),[1,1])
})

function memoryStorage(settings) {
  const values = new Map([['alcor.bear.v1.settings', JSON.stringify(settings)]])
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  }
}

test('existing bear preferences retain their position and receive the default name and size', () => {
  const storage = memoryStorage({ enabled:false, hover:false, position:[0.25,0.75] })
  assert.deepEqual(createBearState(storage).preferences.getSnapshot(), {
    enabled:false, hover:false, position:[0.25,0.75], name:'Alcor 小熊', size:60,
  })
})

test('bear name and size normalize and survive hiding, restoring and state recreation', () => {
  const storage = memoryStorage({})
  const preferences = createBearState(storage).preferences
  preferences.update({ name:'  星星  ', size:84, position:[0.3,0.6] })
  assert.equal(preferences.getSnapshot().name, '星星')
  assert.equal(preferences.getSnapshot().size, 84)
  preferences.update({ size:48 })
  assert.equal(preferences.getSnapshot().size, 48)
  for (const size of [24, 100, '84', null]) {
    preferences.update({ size })
    assert.equal(preferences.getSnapshot().size, 60)
  }
  preferences.update({ name:'  ' + '熊'.repeat(45) + '  ' })
  assert.equal(preferences.getSnapshot().name, '熊'.repeat(40))
  preferences.update({ name:' \t\n ' })
  assert.equal(preferences.getSnapshot().name, 'Alcor 小熊')

  preferences.update({ name:'星星', size:84, hover:false })
  const visible = preferences.getSnapshot()
  preferences.update({ enabled:false })
  assert.deepEqual(preferences.getSnapshot(), { ...visible, enabled:false })
  assert.deepEqual(createBearState(storage).preferences.getSnapshot(), { ...visible, enabled:false })
  preferences.update({ enabled:true })
  assert.deepEqual(preferences.getSnapshot(), visible)
  assert.deepEqual(createBearState(storage).preferences.getSnapshot(), visible)
})
