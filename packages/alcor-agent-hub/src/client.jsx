import {withModeView,uiMode} from '../../alcor-plugin-info/src/mode-view.jsx'
import {startHubSession} from './agent-hub.js'
import {applicationEnvironment} from '../../alcor-plugin-info/src/assignments.js'
import React, {useEffect,useState} from 'react'
import {IconUsersOutlineRegular} from '@deepseek-ai/dsh-client-ui-primitives'
import {remote} from './remote.js'
import {AgentHub} from './agent-hub.jsx'
import style from './agent-hub.css'
import noticeStyle from './notice.css'
import baseStyle from './style.css'
export const inject=['configForms','slots','remote','layout','uiWorkspace','uiSession']

function DeveloperHub({api,currentSession,startSession,openSession}) {
  const [jobs,setJobs]=useState([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('')
  async function unwrap(request) {const result=await request;if(!result.ok) throw new Error(result.error?.message || 'AgentHub 操作失败。');return result.value}
  async function refresh() {
    try {const value=await unwrap(api.read({}));setJobs(value.jobs);setLoading(false);setError('')}
    catch(failure) {setLoading(false);setError(failure.message)}
  }
  useEffect(()=>{
    let disposed=false
    async function load() {try {const value=await unwrap(api.read({}));if(!disposed){setJobs(value.jobs);setLoading(false);setError('')}}catch(failure){if(!disposed){setError(failure.message);setLoading(false)}}}
    void load();const timer=setInterval(()=>{if(document.visibilityState==='visible')void load()},4000)
    return()=>{disposed=true;clearInterval(timer)}
  },[api])
  async function run(work) {setBusy(true);setError('');try{const value=await unwrap(work());await refresh();return value}catch(failure){setError(failure.message);return null}finally{setBusy(false)}}
  function parent() {const id=currentSession();if(!id) throw new Error('请先在协作设置中开始开发对话。');return id}
  return <div className="agent-hub-developer" data-developer-tool="agent-hub"><AgentHub jobs={jobs} api={api} loading={loading} busy={busy} error={error} notice="" refresh={refresh} currentSession={currentSession} startSession={startSession} openSession={openSession}
    start={task=>run(()=>api.startJob({...task,parentSessionId:parent()}))} cancel={id=>run(()=>api.cancelJob({id}))} retry={(id,stepId)=>run(()=>api.retryJob({id,stepId,parentSessionId:parent()}))}/></div>
}
export async function apply(ctx) {
  ctx.effect(()=>{const element=document.createElement('style');element.textContent=style+noticeStyle+baseStyle;document.head.append(element);return()=>element.remove()})
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.agentHub'],ctx=>{
    ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:'agent-hub',inject:()=>({api:ctx.remote.agentHub,currentSession:()=>ctx.uiSession.adapter.current.getSnapshot().props.sessionId,startSession:()=>startHubSession(ctx.uiWorkspace,globalThis[Symbol.for('@alcor/secretary.workspace-navigation')],uiMode()),openSession:id=>ctx.uiWorkspace.openSession(id)})},withModeView(ctx,'@alcor/agent-hub',DeveloperHub)))
    const form=ctx.configForms.get('agent-hub')
    ctx.slots.inject('sidebar.panellist',()=>{
      let release
      const update=()=>{
        if(form.getSnapshot().status==='ready' && (applicationEnvironment() || form.getSnapshot().value?.productScope!=='alcor')){
          release ??= ctx.slots.register({name:'sidebar.panellist',id:'agent-hub',label:'AgentHub',order:35},()=> <IconUsersOutlineRegular size={18}/>)
        }else if(release){release();release=undefined}
      }
      update()
      const off=form.subscribe(update)
      return ()=>{off();release?.()}
    })
  })
}
