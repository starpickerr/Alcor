import React, { useEffect, useState } from 'react'
import { Button, Modal, MarkdownText, SegmentedControl, IconNewChatOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { markdownLabels } from './briefing-report.jsx'
import { BearLogo } from './bear-logo.jsx'
import { BriefingToggle } from './briefing-toggle.jsx'
import { Notice } from './notice.jsx'
import { maxPersonaExamples, maxExampleText } from './memory/identity-schema.js'

const sections = [
  { value: 'identity', label: '角色设定' },
  { value: 'conversation', label: '主动交流' },
  { value: 'memory', label: '长期记忆' },
]

export function PersonaPage({ api }) {
  const [section,setSection] = useState('identity')
  const [saved,setSaved] = useState(false)
  const [state, setState] = useState(), [soul, setSoul] = useState(''), [error, setError] = useState('')
  const [editing, setEditing] = useState(), [content, setContent] = useState(''), [busy, setBusy] = useState(false)
  const [identity,setIdentity] = useState(), [name,setName] = useState('')
  const [examples,setExamples] = useState([])
  function showIdentity(value) {
    setIdentity(value)
    setName(value.name)
    setSoul(value.soul)
    setExamples(value.examples ?? [])
  }
  useEffect(() => {
    let active = true
    Promise.all([api.memoryState({}), api.exportIdentity({})]).then(([memory, profile]) => {
      if (!active) return
      if (!memory.ok || !profile.ok) throw Error((memory.error ?? profile.error).message)
      setState(memory.value)
      showIdentity(profile.value)
    }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [api])

  async function save(action, refreshIdentity = false) {
    setBusy(true)
    setError('')
    try {
      const result = await action()
      if (!result.ok) throw Error(result.error.message)
      if (refreshIdentity) {
        const profile = await api.exportIdentity({})
        if (!profile.ok) throw Error(profile.error.message)
        showIdentity(profile.value)
        setSaved(true)
      } else {
        const memory = await api.memoryState({})
        if (!memory.ok) throw Error(memory.error.message)
        setState(memory.value)
        setEditing(undefined)
      }
      window.dispatchEvent(new Event('alcor-memory-changed'))
    } catch (reason) {
      setError(reason.message)
    } finally {
      setBusy(false)
    }
  }

  function changeExamples(next) {
    setSaved(false)
    setExamples(next)
  }
  const complete = examples.every(row => row.user.trim() && row.assistant.trim())
  const changed = identity && (name !== identity.name || soul !== identity.soul ||
    JSON.stringify(examples) !== JSON.stringify(identity.examples ?? []))
  const canSave = changed && !busy && name.trim() && soul.trim() && complete
  return <section className="alcor-desk alcor-persona-page" aria-label="人设">
    <header className="alcor-page-heading"><h1>人设</h1></header>
    <SegmentedControl id="alcor-persona-section" label="人设页面" value={section} onChange={setSection} options={sections}/>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {!state && !error && <p className="alcor-muted" role="status">正在读取人设与记忆…</p>}
    <div className="alcor-memory-settings">
    <div hidden={section!=='identity'} className="alcor-settings-group">
      <div className="alcor-profile-heading">
        <div className="alcor-profile-avatar"><BearLogo size={46} /></div>
        <div>
          <label htmlFor="alcor-identity-name">助手名称</label>
          <input id="alcor-identity-name" value={name} maxLength={80} disabled={!identity || busy}
            onChange={event => { setSaved(false); setName(event.target.value) }} />
        </div>
      </div>
      <div className="alcor-field-caption"><label htmlFor="alcor-soul">角色设定</label><small>桌面与 QQ 共用</small></div>
      <textarea id="alcor-soul" value={soul} maxLength={12000} rows={8} disabled={!identity || busy} aria-describedby="alcor-soul-help"
        onChange={event => { setSaved(false); setSoul(event.target.value) }} />
      <p id="alcor-soul-help" className="alcor-muted">可写下别称、性格、说话习惯，以及什么时候会主动接话。</p>
      <div className="alcor-persona-examples">
        <div className="alcor-example-heading"><h2>对话示例</h2>
          <Button variant="outline" size="sm" disabled={!identity || busy || examples.length >= maxPersonaExamples}
            onClick={() => changeExamples([...examples, { user:'', assistant:'' }])}>添加示例</Button>
        </div>
        <small>Alcor 会从示例中提炼交流习惯。示例原文保留在这里，供你编辑。</small>
        {examples.map((row,index) => <fieldset key={index} disabled={busy}>
          <legend>示例 {index + 1}</legend>
          {[['user','对方说'],['assistant','助手回应']].map(([role, label]) => <label key={role}>
            {label}
            <textarea aria-label={label} rows={2} required maxLength={maxExampleText} value={row[role]} onChange={event =>
              changeExamples(examples.map((item,i) => i === index ? {...item, [role]:event.target.value} : item))} />
          </label>)}
          <Button variant="ghost" size="sm" aria-label={'删除示例 '+(index+1)}
            onClick={() => changeExamples(examples.filter((_,i) => i !== index))}>删除示例</Button>
        </fieldset>)}
      </div>
      <div className="alcor-identity-actions">
        {saved && <span role="status">已保存</span>}
        <Button variant="primary" size="sm" disabled={!canSave}
          onClick={() => save(() => api.updateSoul({ name, soul, examples, expectedRevision: identity.revision }), true)}>保存人设</Button>
      </div>
    </div>
    <div hidden={section!=='conversation'}><div className="alcor-settings-group"><h2>主动交流</h2><div className="alcor-initiative-card"><div className="alcor-bear-option-row"><span className="alcor-card-caption"><IconNewChatOutlineRegular size={18}/>主动跟进</span><BriefingToggle label="允许 Alcor 主动跟进" disabled={!state || busy} checked={state?.initiative === true} onChange={enabled=>save(()=>api.allowInitiative({enabled}))} /></div>
      <small className="alcor-channel-consent">Alcor 可以跟进聊过的事，也可以在一段时间没聊天时，按人设向已允许的 QQ 联系人或群打招呼。</small>
    </div></div><div className="alcor-settings-group"><h2>消息送达</h2><div className="alcor-initiative-card"><div className="alcor-bear-option-row"><div><span>桌面便笺</span><small>在 Alcor 页面显示到期的跟进</small></div>
      <BriefingToggle label="桌面便笺" disabled={!state || busy} checked={state?.desktopDelivery===true} onChange={enabled=>save(()=>api.allowDesktopDelivery({enabled}))}/></div></div></div></div>
    <div hidden={section!=='memory'} className="alcor-settings-group"><h2>长期记忆</h2>
    <div className="alcor-bear-option-row"><div><span>跨聊天回忆</span><small>桌面、QQ 私聊和已接入的群共用回忆，Alcor 自行决定是否向当前听众提起，包括私聊细节。</small></div>
      <BriefingToggle label="跨聊天回忆" disabled={!state || busy} checked={state?.sharedRecall===true} onChange={enabled=>save(()=>api.allowSharedRecall({enabled}))}/></div>
    {state?.entries.length === 0 && <p className="alcor-muted">暂无记忆</p>}
    <div className="alcor-memory-cards">{state?.entries.map(item => <MemoryCard key={item.id} item={item} entries={state.entries} manage={() => {setEditing(item);setContent(item.content)}} />)}</div>
    </div>
    <Modal open={!!editing} title="管理记忆" closeLabel="关闭" onClose={() => { if (!busy) setEditing(undefined) }}>
      <div className="alcor-memory-editor"><textarea aria-label="记忆内容" readOnly={editing?.kind==='interest'} rows={5} maxLength={1000} value={content} onChange={event => setContent(event.target.value)} />
        <div><Button variant="outline" disabled={busy} onClick={() => save(() => api.changeMemory({ id: editing.id }))}>忘记</Button>
          {editing?.kind!=='interest' && <Button variant="primary" disabled={busy || !content.trim()} onClick={() => save(() => api.changeMemory({ id: editing.id, content }))}>保存更正</Button>}</div></div>
    </Modal></div>
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
