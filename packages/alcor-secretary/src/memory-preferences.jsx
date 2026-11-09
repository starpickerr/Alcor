import React, { useEffect, useState } from 'react'
import { Button, Modal, MarkdownText, IconNewChatOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { markdownLabels } from './briefing-report.jsx'
import { ChannelDeliverySettings } from './channel-delivery-settings.jsx'
import { BearLogo } from './bear-logo.jsx'
import { BriefingToggle } from './briefing-toggle.jsx'
import { Notice } from './notice.jsx'

export function MemoryPreferences({ api }) {
  const [state, setState] = useState(), [soul, setSoul] = useState(''), [error, setError] = useState('')
  const [editing, setEditing] = useState(), [content, setContent] = useState(''), [busy, setBusy] = useState(false)
  const [identity,setIdentity] = useState(), [name,setName] = useState('')
  function showIdentity(value) { setIdentity(value); setName(value.name); setSoul(value.soul) }
  async function load(refreshIdentity=false) {
    const result = await api.memoryState({})
    if (!result.ok) throw Error(result.error.message)
    const version = await api.exportIdentity({})
    if (!version.ok) throw Error(version.error.message)
    setState(result.value)
    if (refreshIdentity || !identity || (name===identity.name && soul===identity.soul)) showIdentity(version.value)
  }
  useEffect(() => { let active = true
    Promise.all([api.memoryState({}),api.exportIdentity({})]).then(([result,version]) => { if (!active) return; if (!result.ok || !version.ok) throw Error((result.error ?? version.error).message); setState(result.value); showIdentity(version.value) })
      .catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [api])
  async function save(action,refreshIdentity=false) {
    setBusy(true); setError('')
    try { const result = await action(); if (!result.ok) throw Error(result.error.message); await load(refreshIdentity); setEditing(undefined); window.dispatchEvent(new Event('alcor-memory-changed')) }
    catch (reason) { setError(reason.message) }
    finally { setBusy(false) }
  }
  return <section className="alcor-memory-settings"><div className="alcor-settings-group"><div className="alcor-profile-heading"><div className="alcor-profile-avatar"><BearLogo size={46} /></div><div>
    <label htmlFor="alcor-identity-name">助手名称</label>
    <input id="alcor-identity-name" value={name} maxLength={80} disabled={!identity || busy} onChange={event=>setName(event.target.value)} /></div></div>
    <div className="alcor-field-caption"><label htmlFor="alcor-soul">Alcor 的人格</label><small>桌面与 QQ 共用</small></div>
    <textarea id="alcor-soul" value={soul} maxLength={12000} rows={4} disabled={!state || busy} onChange={event => setSoul(event.target.value)} />
    <div className="alcor-identity-actions"><Button variant="outline" size="sm" disabled={!identity || busy || !name.trim() || !soul.trim() || (soul === identity.soul && name === identity.name)} onClick={() => save(() => api.updateSoul({ name,soul,expectedRevision:identity.revision }),true)}>保存人格</Button>
    </div></div>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    <div className="alcor-settings-group"><h2>主动交流</h2><div className="alcor-initiative-card"><div className="alcor-bear-option-row"><span className="alcor-card-caption"><IconNewChatOutlineRegular size={18}/>主动跟进</span><BriefingToggle label="允许 Alcor 主动跟进" disabled={!state || busy} checked={state?.initiative === true} onChange={enabled=>save(()=>api.allowInitiative({enabled}))} /></div>
    </div></div><div className="alcor-settings-group"><h2>消息送达</h2><div className="alcor-initiative-card"><div className="alcor-bear-option-row"><div><span>桌面便笺</span><small>在 Alcor 页面显示到期的跟进</small></div>
      <BriefingToggle label="桌面便笺" disabled={!state || busy} checked={state?.desktopDelivery===true} onChange={enabled=>save(()=>api.allowDesktopDelivery({enabled}))}/></div></div><ChannelDeliverySettings api={api}/></div>
    <div className="alcor-settings-group"><h2>长期记忆</h2>
    {state?.entries.length === 0 && <p className="alcor-muted">暂无记忆</p>}
    <div className="alcor-memory-cards">{state?.entries.map(item => <MemoryCard key={item.id} item={item} entries={state.entries} manage={() => {setEditing(item);setContent(item.content)}} />)}</div>
    </div>
    <Modal open={!!editing} title="管理记忆" closeLabel="关闭" onClose={() => { if (!busy) setEditing(undefined) }}>
      <div className="alcor-memory-editor"><textarea aria-label="记忆内容" readOnly={editing?.kind==='interest'} rows={5} maxLength={1000} value={content} onChange={event => setContent(event.target.value)} />
        <div><Button variant="outline" disabled={busy} onClick={() => save(() => api.changeMemory({ id: editing.id }))}>忘记</Button>
          {editing?.kind!=='interest' && <Button variant="primary" disabled={busy || !content.trim()} onClick={() => save(() => api.changeMemory({ id: editing.id, content }))}>保存更正</Button>}</div></div>
    </Modal>
  </section>
}


function MemoryCard({item,entries,manage}) {
  const [expanded,setExpanded]=useState(false)
  const source=item.kind==='interest'?'小熊的兴趣':item.source.kind==='preference'?'对方亲口说过':item.kind==='habit'?'助手的习惯':({'legacy-import':'历史导入','qq-private':'QQ 私聊',desktop:'桌面',compiled:'对话整理'}[item.source.kind] || 'Alcor')
  const date=item.source.at?.slice(0,10)
  const long=item.content.length>140 || item.content.split('\n').length>4
  return <article className="alcor-memory-row alcor-memory-card"><header><span>{source}</span>{date && <time dateTime={item.source.at}>{date}</time>}<Button variant="ghost" size="sm" onClick={manage}>管理</Button></header>
    <div className={'alcor-memory-body'+(long && !expanded?' alcor-memory-preview':'')}>{expanded?<MarkdownText text={item.content} streaming={false} labels={markdownLabels}/>:<p>{item.content.replace(/^\s{0,3}(?:#{1,6}\s+|>\s?)/gm,'')}</p>}</div>
    {long && <button type="button" className="alcor-memory-expand" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?'收起':'展开全文'}</button>}
    {item.evidence?.length>0 && <details><summary>形成依据</summary>{item.evidence.map(id=><p key={id}>{entries.find(entry=>entry.id===id)?.content || '可通过对话检索这条依据'}</p>)}</details>}
  </article>
}
