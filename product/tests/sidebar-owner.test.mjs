import {test} from 'node:test'
import assert from 'node:assert/strict'
import {sidebarOwner} from '../../packages/alcor-secretary/src/sidebar-owner.js'

function registry(failAt){
  const native={options:{name:'sidebar'},component:()=>null}
  const entries=[native]
  let count=0
  return {native,entries,slots:{register(options,component){
    if(++count===failAt)throw Error('registration failed')
    assert(!options.children,'native child declarations must remain untouched')
    assert.notEqual(options.name,'sidebar','the native root must retain ownership')
    const entry={options,component};entries.push(entry)
    return()=>{const index=entries.indexOf(entry);if(index>=0)entries.splice(index,1)}
  }}}
}

test('Alcor extends native slots while preserving the original sidebar and workspace owners',async()=>{
  const {slots,entries,native}=registry()
  const Actions=()=>null, Brand=()=>null
  const owner=sidebarOwner(slots,Actions,Brand)
  await owner.select(true)
  assert.strictEqual(entries[0],native)
  assert.deepEqual(entries.slice(1).map(row=>row.options.name),['sidebar.brand.mark','sidebar.brand.name','sidebar.session.action'])
  assert.strictEqual(entries[3].component,Actions)
  assert.equal(entries[3].options.id,'alcor-navigation')
  await owner.select(true);assert.equal(entries.length,4)
  await Promise.all([owner.select(false),owner.select(true),owner.select(false)])
  assert.deepEqual(entries,[native])
  await owner.select(true);await owner.dispose();await owner.select(true)
  assert.deepEqual(entries,[native])
})

test('partial registration failure removes only Alcor contributions and allows retry',async()=>{
  const {slots,entries,native}=registry(3)
  const owner=sidebarOwner(slots,()=>null,()=>null)
  await assert.rejects(owner.select(true),/registration failed/)
  assert.deepEqual(entries,[native])
  await owner.select(true);assert.equal(entries.length,4)
  await owner.dispose();assert.deepEqual(entries,[native])
})

test('native rc.2 workspace receives its public section scroller hook', async () => {
  const {sharedWorkspaceSlot,sharedWorkspaceContext}=await import('../../packages/alcor-secretary/src/shared-workspace.js')
  const hook=sharedWorkspaceSlot.inject.hooks.sectionScroller({},sharedWorkspaceContext)
  assert.equal(typeof hook,'function')
  assert.equal(typeof hook().bindScrollerRef,'function')
  assert.strictEqual(hook(),hook())
  const legacyHook=sharedWorkspaceSlot.inject.hooks.sectionScroller({})
  assert.equal(typeof legacyHook().bindScrollerRef,'function')
})
