import test from 'node:test'
import assert from 'node:assert/strict'
import {extensionLifecycle,extensionSelected} from '../client-shell/extension-lifecycle.mjs'
import {apply} from '../../packages/alcor-extension/src/index.mjs'
import badges from '../client-shell/plugin-badges.cjs'

test('native selection starts Alcor once; disabling selects the workbench and stops it; re-enabling restores it',async()=>{
  const calls=[],state=extensionLifecycle({start:async()=>calls.push('start'),stop:async()=>calls.push('stop'),selectWorkbench:async()=>calls.push('dsh')})
  assert.equal(state.snapshot().phase,'disabled')
  await state.setEnabled(true);await state.setEnabled(true)
  assert.deepEqual(calls,['start'])
  await state.setEnabled(false)
  assert.deepEqual(calls,['start','dsh','stop']);assert.equal(state.snapshot().phase,'disabled')
  await state.setEnabled(true);assert.equal(state.snapshot().phase,'ready')
  assert.deepEqual(calls,['start','dsh','stop','start'])
  assert.throws(()=>state.setEnabled('true'),/状态/)
})

test('disable during startup wins; no late startup survives the queued native disable',async()=>{
  let finish,began,active=false
  const started=new Promise(done=>began=done),pending=new Promise(done=>finish=done),calls=[]
  const state=extensionLifecycle({start:async()=>{began();await pending;active=true;calls.push('started')},stop:async()=>{active=false;calls.push('stopped')},selectWorkbench:async()=>calls.push('dsh')})
  const on=state.setEnabled(true);await started
  const off=state.setEnabled(false);finish();await Promise.all([on,off])
  assert.equal(active,false);assert.equal(calls.at(-1),'stopped');assert(calls.includes('dsh'));assert.equal(state.snapshot().enabled,false)
})

test('startup and stop failures remain visible and retryable',async()=>{
  let startFails=true,stopFails=true
  const state=extensionLifecycle({start:async()=>{if(startFails)throw Error('start')},stop:async()=>{if(stopFails)throw Error('stop')},selectWorkbench:async()=>{}})
  await assert.rejects(state.setEnabled(true),/start/);assert.equal(state.snapshot().phase,'failed')
  startFails=false;await state.setEnabled(true)
  await assert.rejects(state.setEnabled(false),/stop/);assert.equal(state.snapshot().enabled,true)
  stopFails=false;await state.setEnabled(false);assert.equal(state.snapshot().phase,'disabled')
})

test('a quick off/on cannot publish ready while an older stop is still in flight',async()=>{
  let finish,began,active=false,starts=0
  const started=new Promise(done=>began=done),pending=new Promise(done=>finish=done)
  const state=extensionLifecycle({start:async()=>{if(++starts===1){began();await pending}active=true},stop:async()=>{active=false},selectWorkbench:async()=>{}})
  const first=state.setEnabled(true);await started
  const off=state.setEnabled(false),on=state.setEnabled(true);finish()
  await Promise.all([first,off,on])
  assert.equal(active,true);assert.equal(starts,2);assert.equal(state.snapshot().phase,'ready')
})

test('closing aborts a pending startup without overwriting the saved mode or allowing another start',async()=>{
  let cancel,began,stops=0
  const started=new Promise(done=>began=done),pending=new Promise((_resolve,reject)=>cancel=()=>reject(Error('closed')))
  const state=extensionLifecycle({start:async()=>{began();await pending},stop:async()=>{stops++;cancel()},selectWorkbench:async()=>assert.fail('quit must retain the saved mode')})
  const on=state.setEnabled(true);await started
  const closed=state.close();await on;await closed
  await state.setEnabled(true);assert.equal(state.snapshot().phase,'disabled');assert(stops>=1)
})

test('disabling interrupts a stalled startup and a failed renderer can be restarted',async()=>{
  let began,cancel,starts=0,stops=0,selected=0
  const started=new Promise(done=>began=done)
  const state=extensionLifecycle({
    start:async()=>{if(++starts===1){began();await new Promise((_done,reject)=>cancel=()=>reject(Error('stopped')))}},
    stop:async()=>{stops++;cancel?.()},selectWorkbench:async()=>{selected++},
  })
  const on=state.setEnabled(true);await started
  await state.setEnabled(false);await on
  assert.equal(state.snapshot().phase,'disabled');assert(stops>0);assert(selected>0)
  await state.setEnabled(true);state.invalidate();await state.setEnabled(true)
  assert.equal(starts,3);assert.equal(state.snapshot().phase,'ready')
})

test('the native profile is authoritative; installed, disabled and removed are different states',()=>{
  const manifest={dependencies:{'@alcor/extension':'1.0.0'},dsh:{profile:{bundles:['@alcor/extension']}}}
  assert(extensionSelected(manifest))
  assert(!extensionSelected({...manifest,dsh:{profile:{bundles:[]}}}))
  assert(!extensionSelected({...manifest,dependencies:{}}))
  assert(!extensionSelected({}))
  assert.throws(()=>extensionSelected({...manifest,dsh:{profile:{bundles:'@alcor/extension'}}}),/配置/)
  const previous={shell:process.env.ALCOR_CLIENT_SHELL,role:process.env.ALCOR_APPLICATION}
  try{
    delete process.env.ALCOR_CLIENT_SHELL;assert.throws(()=>apply(),/客户端/)
    process.env.ALCOR_CLIENT_SHELL='1';process.env.ALCOR_APPLICATION='alcor';assert.throws(()=>apply(),/DSH/)
    process.env.ALCOR_APPLICATION='dsh';apply()
  }finally{
    for(const [key,value] of [['ALCOR_CLIENT_SHELL',previous.shell],['ALCOR_APPLICATION',previous.role]])if(value===undefined)delete process.env[key];else process.env[key]=value
  }
})

test('built-in scope labels describe the actual environment and keep Alcor management distinct',()=>{
  assert.equal(badges.pluginLabel('unchanged-third-party','alcor'),'Alcor 环境')
  assert.equal(badges.pluginLabel('unchanged-third-party','dsh'),'DSH 环境')
  assert.equal(badges.pluginLabel('unchanged-third-party','dsh',true),'安装到 DSH 环境')
  assert.equal(badges.pluginLabel('@alcor/extension','dsh'),'Alcor 环境')
})
