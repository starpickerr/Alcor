import test from 'node:test'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {ownedChildren,stopChildren} from '../client-shell/child-processes.mjs'

test('stopping a Host also stops detached children without signalling unrelated or recycled processes',{skip:!['darwin','linux','win32'].includes(process.platform)},async()=>{
  const worker=spawn(process.execPath,['-e',`const {spawn}=require('node:child_process');const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'});child.unref();process.send({pid:child.pid});setInterval(()=>{},1000)`],{stdio:['ignore','ignore','ignore','ipc']})
  const [{pid}]=await once(worker,'message')
  const unrelated=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'})
  await once(unrelated,'spawn')
  try{
    const children=await ownedChildren(worker.pid)
    assert(children.some(row=>row.pid===pid));assert(!children.some(row=>row.pid===unrelated.pid))
    await stopChildren([{pid:unrelated.pid,identity:'a previous process using this PID'}]);process.kill(unrelated.pid,0)
    await stopChildren(children)
    assert(!(await ownedChildren(worker.pid)).some(row=>row.pid===pid))
    process.kill(worker.pid,0);process.kill(unrelated.pid,0)
  }finally{
    for(const child of [worker,unrelated])if(child.exitCode===null && child.signalCode===null){child.kill('SIGKILL');await once(child,'exit')}
    try{process.kill(pid,'SIGKILL')}catch(error){if(error.code!=='ESRCH')throw error}
  }
})
