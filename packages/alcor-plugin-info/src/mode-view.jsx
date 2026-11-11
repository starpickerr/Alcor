import React,{useSyncExternalStore} from 'react'
import {assignments,permits,modeNames,applicationEnvironment} from './assignments.js'

export const uiMode=()=>applicationEnvironment() ?? globalThis[Symbol.for('@alcor/secretary.workspace-navigation')]?.mode?.getSnapshot() ?? 'dsh'
const subscribeMode=listener=>{
  window.addEventListener('alcor-mode-changed',listener)
  return ()=>window.removeEventListener('alcor-mode-changed',listener)
}
export function withModeView(ctx,name,View) {
  const spec=assignments[name], form=ctx.configForms.get(spec.entry)
  return function AssignedView(props){
    const mode=useSyncExternalStore(subscribeMode,uiMode)
    const snapshot=useSyncExternalStore(listener=>form.subscribe(listener),()=>form.getSnapshot())
    if(snapshot.status==='loading')return null
    const assigned=snapshot.value?.productScope ?? spec.initial
    if(!applicationEnvironment() && !permits(assigned,mode))return props.view==='summary'?null:<p>此功能已分配给 {modeNames[assigned]}。</p>
    return <View {...props}/>
  }
}
