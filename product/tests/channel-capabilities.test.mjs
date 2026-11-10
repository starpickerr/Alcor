import test from 'node:test'
import assert from 'node:assert/strict'
import {ChannelCapabilities,emptyChannelSelection} from '../../packages/alcor-secretary/src/channel-capabilities.js'

function fixture() {
  const stored=new Map()
  const ctx={agentDefaultModel:{currentSelection:()=>({provider:'host',model:'default'})},get:()=>undefined,
    llm:{listProviders:()=>[{id:'test',name:'Test'}],listModels:async()=>[
      {id:'text',name:'Text',inputModalities:['text']},{id:'vision',name:'Vision',inputModalities:['text','image']},
    ]}}
  const service=new ChannelCapabilities(ctx,{get:key=>stored.get(key),put:async(key,value)=>{await Promise.resolve();stored.set(key,value)}})
  return {ctx,service}
}
test('shared selections validate modality and retain the host default',async()=>{
  const {service}=fixture()
  assert.deepEqual(service.main(),{provider:'host',model:'default'})
  assert.deepEqual((await service.catalog()).synthesis,[])
  await assert.rejects(service.save({...emptyChannelSelection,vision:{provider:'test',model:'text'}},emptyChannelSelection),/不可用/)
  const chosen={...emptyChannelSelection,main:{provider:'test',model:'text'},vision:{provider:'test',model:'vision'}}
  await service.save(chosen,emptyChannelSelection)
  assert.deepEqual(service.main(),chosen.main)
  assert.deepEqual((await service.catalog()).selection,chosen)
  await assert.rejects(service.save({...chosen,transcription:'missing'},chosen),/不可用/)
})
test('concurrent windows cannot replace a newer shared selection',async()=>{
  const {service}=fixture()
  const selected={...emptyChannelSelection,main:{provider:'test',model:'text'}}
  const results=await Promise.allSettled([service.save(selected,emptyChannelSelection),service.save({...emptyChannelSelection,main:{provider:'test',model:'vision'}},emptyChannelSelection)])
  assert.equal(results[0].status,'fulfilled')
  assert.equal(results[1].status,'rejected')
  assert.deepEqual(service.selection(),selected)
})
test('speech resolves the selected service and rejects results after selection revocation',async()=>{
  const {service,ctx}=fixture()
  let finish
  ctx.get=()=>({listProviders:()=>[{id:'speech',name:'Speech'}],resolve:input=>input,transcribe:input=>{
    assert.equal(input.providerId,'speech');assert.equal(input.audio[0],82)
    return new Promise(resolve=>{finish=()=>resolve({text:'recognized'})})
  }})
  const selected={...emptyChannelSelection,transcription:'speech'}
  await service.save(selected,emptyChannelSelection)
  const task=service.transcribe(new Uint8Array([82]),new AbortController().signal)
  await service.save(emptyChannelSelection,selected)
  finish()
  await assert.rejects(task,/改变/)
})
