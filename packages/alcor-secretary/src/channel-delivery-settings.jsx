import React,{useEffect,useState} from 'react'
import {Button,IconUsersOutlineRegular,IconNewChatOutlineRegular} from '@deepseek-ai/dsh-client-ui-primitives'
import {Notice} from './notice.jsx'
const statusLabel={pending:'待投递',desktop:'已选桌面便笺',sending:'正在发送',sent:'已送达',unknown:'结果未知，请在原通道核对',cancelled:'已取消',expired:'已过期'}
export function ChannelDeliverySettings({api}) {
  const [state,setState]=useState(),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{
    let live=true
    api.deliverySettings({}).then(result=>{if(!live)return;if(!result.ok)throw Error(result.error.message);setState(result.value);window.dispatchEvent(new Event('alcor-channel-catalog-changed'))})
      .catch(reason=>{if(live)setError(reason.message)})
    return()=>{live=false}
  },[api])
  async function configure(sessionId,enabled){
    setBusy(true);setError('')
    try{
      const changed=await api.allowChannelDelivery({sessionId,enabled})
      if(!changed.ok)throw Error(changed.error.message)
      const result=await api.deliverySettings({})
      if(!result.ok)throw Error(result.error.message)
      setState(result.value);window.dispatchEvent(new Event('alcor-channel-catalog-changed'))
    }catch(reason){setError(reason.message)}finally{setBusy(false)}
  }
  return <>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {state?.targets.map(({route,until,available})=>{
      const active=until===null || until>Date.now(), Icon=route.kind==='group'?IconUsersOutlineRegular:IconNewChatOutlineRegular
      return <div className="alcor-initiative-card" data-channel-session={route.sessionId} key={route.sessionId}>
        <div className="alcor-channel-target"><Icon size={18}/><div><strong>{route.label || route.peer}</strong><small>{route.channelName} · 账号 {route.account} · {route.kind==='group'?'群聊':'私聊'} · {route.peer}</small></div>
          {active?<Button size="sm" variant="outline" disabled={busy} onClick={()=>configure(route.sessionId,false)}>撤销授权</Button>:
          <Button size="sm" variant="outline" disabled={busy || !available} onClick={()=>configure(route.sessionId,true)}>允许主动发送</Button>}</div>
        <small className="alcor-channel-consent">{active?(until===null?'持续授权，可随时撤销。':'授权至 '+new Date(until).toLocaleString()):route.kind==='group'?'允许 Alcor 在本群主动问候或跟进话题。':'允许 Alcor 主动问候这个联系人或跟进话题。'}</small>
        {!available && <small className="alcor-channel-consent">通道离线或当前没有投递权限。</small>}
      </div>
    })}
    {!!state?.receipts.length && <details className="alcor-delivery-history"><summary>投递记录</summary>{state.receipts.slice().reverse().map(row=><div key={row.id}><strong>{row.route.label || row.route.peer}</strong><span>{statusLabel[row.status]}</span><p>{row.content}</p><small>{new Date(row.updatedAt).toLocaleString()}</small></div>)}</details>}
  </>
}
