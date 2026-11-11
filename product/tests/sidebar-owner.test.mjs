import {test} from 'node:test'
import assert from 'node:assert/strict'
import {sidebarOwner} from '../../packages/alcor-secretary/src/sidebar-owner.js'

test('sidebar ownership restores the original loader entry after rapid switching and teardown', async () => {
  let active=true,custom=false
  const entry={id:'native-sidebar',options:{name:'@deepseek-ai/dsh-client-ui-sidebar',disabled:false}}
  const loader={entries:()=>[entry],update:async(id,options)=>{assert.equal(id,entry.id);active=!options.disabled}}
  const owner=sidebarOwner(loader,()=>{assert.equal(active,false);custom=true;return()=>{custom=false}})
  await owner.select(true);assert.equal(custom,true)
  await Promise.all([owner.select(false),owner.select(true),owner.select(false)])
  assert.equal(active,true);assert.equal(custom,false)
  await owner.select(true);await owner.dispose()
  assert.equal(active,true);assert.equal(custom,false)
})

test('failure to register Alcor sidebar restores native ownership and rejects', async () => {
  let disabled=false
  const entry={id:'native',options:{name:'@deepseek-ai/dsh-client-ui-sidebar'}}
  const loader={entries:()=>[entry],update:async(_id,options)=>{disabled=options.disabled}}
  const owner=sidebarOwner(loader,()=>{throw Error('registration failed')})
  await assert.rejects(owner.select(true),/registration failed/)
  assert.equal(disabled,false)
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
