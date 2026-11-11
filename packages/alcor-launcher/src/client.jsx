import React,{useState} from 'react'
import {Button} from '@deepseek-ai/dsh-client-ui-primitives'
import {remote} from './remote.js'

export const inject=['slots','remote']
function Launcher({api}){
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('')
  async function open(){
    setBusy(true);setError('');setMessage('')
    try{const result=await api.open();if(!result.ok)throw Error(result.error.message);setMessage('已在独立窗口打开。')}
    catch(error){setError(error.message)}finally{setBusy(false)}
  }
  return <section style={{padding:24}}><h1>Alcor</h1><Button disabled={busy} onClick={open}>{busy?'正在打开…':'打开 Alcor'}</Button>
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}</section>
}
export async function apply(ctx){
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.alcorLauncher'],inner=>{
    inner.slots.inject('sidebar.panellist',()=>inner.slots.register({name:'sidebar.panellist',id:'alcor-launcher',label:()=> 'Alcor'},()=>null))
    inner.slots.inject('main',()=>inner.slots.register({name:'main',key:'alcor-launcher',inject:()=>({api:inner.remote.alcorLauncher})},Launcher))
  })
}
