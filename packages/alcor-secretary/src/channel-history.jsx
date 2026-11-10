import React, { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { BearLogo } from './bear-logo.jsx'
import { channelRoute } from './spaces.js'
import { Button, IconFolderCloseRegular, IconFolderOpenRegular, IconTriangleRightFillRegular, IconUsersOutlineRegular, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

const expansionKey = 'alcor.qq-workspace.expanded'
export function ChannelBrowser({ navigation, catalog, styles, expandSidebar }) {
  const list = useSyncExternalStore(listener => catalog.subscribe(listener), () => catalog.getSnapshot())
  const { selection, metadata } = useSyncExternalStore(navigation.state.subscribe, navigation.state.getSnapshot)
  const routes=(metadata?.channelRoutes ?? []).filter(route=>list.ids.includes(route.sessionId))
  const instances=[...new Set(routes.map(route=>route.channel+'\0'+route.account))]
  return <>{instances.map(key=><ChannelFolder key={key} navigation={navigation} metadata={metadata} routes={routes.filter(route=>route.channel+'\0'+route.account===key)} selection={selection} styles={styles} expandSidebar={expandSidebar}/>)}</>
}
function ChannelFolder({navigation,metadata,routes,selection,styles,expandSidebar}) {
  const first=routes[0],name=first.channelName
  const storageKey='alcor.channel.'+first.channel+'.'+first.account
  const [expanded,setExpanded]=useState(()=>(localStorage.getItem(storageKey) ?? (first.channel==='qq'?localStorage.getItem(expansionKey):'false'))==='true')
  const ids=routes.map(route=>route.sessionId)
  if (!ids.length) return null
  function toggle() {
    const next = expandSidebar ? true : !expanded
    localStorage.setItem(storageKey, String(next))
    setExpanded(next)
    expandSidebar?.()
  }
  const open = expanded && !expandSidebar
  const Folder = open ? IconFolderOpenRegular : IconFolderCloseRegular
  return <div className="alcor-channel-browser alcor-qq-browser"><button type="button" role="treeitem" aria-label={name} title={name} aria-expanded={!!open}
    className={styles.projectRow} onClick={toggle}>
    <span className={styles.slot+' '+styles.folder}><Folder /></span>
    <span className={styles.slot+' '+styles.chevron}><IconTriangleRightFillRegular className={styles.arrow+(open?' '+styles.arrowOpen:'')} /></span>
    {!expandSidebar && <span className={styles.projectText}><span className={styles.title}>{name}</span></span>}
  </button>
    {open && <div role="group">{['private', 'group'].map(kind => {
      const rows = ids.filter(id => routes.find(route=>route.sessionId===id)?.kind===kind)
      const label=kind === 'private' ? '私聊' : '群聊'
      return rows.length > 0 && <section key={kind} aria-label={label}><h3>{label}</h3>{rows.map(id => {
        const route=routes.find(row=>row.sessionId===id), number=route.peer, title=route.label || metadata?.qq?.[id] || (kind==='group'?'群 '+number:number)
        return <button key={id} type="button" role="treeitem" className={styles.sessionRow} aria-current={selection === id ? 'page' : undefined}
          title={title+' · '+number} onClick={() => navigation.openQQ(id)}><span className={styles.title}>{title}</span></button>
      })}</section>
    })}</div>}
  </div>
}

const responseLabels={private:'私聊回复',mention:'提及时回复',ambient:'自主参与',addressed:'点名时回复',disabled:'已停用'}
export function ChannelHistory({navigation,api,secretary}) {
  const {selection:id}=useSyncExternalStore(navigation.state.subscribe,navigation.state.getSnapshot)
  return id ? <Conversation key={id} id={id} navigation={navigation} api={api} secretary={secretary}/> : null
}
function Conversation({id,navigation,api,secretary}) {
  const { metadata } = useSyncExternalStore(navigation.state.subscribe, navigation.state.getSnapshot)
  const [pages, setPages] = useState([]), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [more, setMore] = useState(false), [before, setBefore] = useState()
  const [version, setVersion] = useState(0)
  const [info,setInfo]=useState(),[details,setDetails]=useState(false),[selectedMessage,selectMessage]=useState(null)
  useEffect(()=>{
    let current=true
    secretary.channelConversation({sessionId:id}).then(result=>{
      if (current) {setInfo(result.ok ? result.value : null);if(result.ok && result.value?.name)window.dispatchEvent(new Event('alcor-channel-catalog-changed'))}
    }).catch(()=>{if (current) setInfo(null)})
    return ()=>{current=false}
  },[id,version,secretary])
  const scroller=useRef(),place=useRef(null)
  useLayoutEffect(()=>{
    if (!pages.length || !scroller.current || !place.current) return
    const el=scroller.current, old=place.current
    el.scrollTop=old==='latest' ? el.scrollHeight : old.absolute!=null ? old.absolute : old.top+el.scrollHeight-old.height
    place.current=null
  },[pages])
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    setPages([]); setBefore(undefined); setMore(false); setError(''); setBusy(true)
    secretary.channelHistory({sessionId:id}).then(result => {
      if (controller.signal.aborted) return
      if (!result.ok) throw Error(result.error.message)
      place.current='latest'
      setPages(result.value.messages); setMore(result.value.hasMore)
      setBefore(result.value.beforeSeq ?? undefined)
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message) }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [id, api, version])
  useEffect(()=>{
    let active=true,reading=false
    async function refresh(){
      if(reading || document.visibilityState!=='visible')return
      reading=true
      try{
        const result=await secretary.channelHistory({sessionId:id})
        if(!active || !result.ok)return
        const el=scroller.current
        place.current=!el || el.scrollHeight-el.scrollTop-el.clientHeight<80 ? 'latest' : {absolute:el.scrollTop}
        setPages(previous=>{
          const merged=new Map(previous.map(row=>[row.seq,row]))
          for(const row of result.value.messages)merged.set(row.seq,row)
          return [...merged.values()].sort((a,b)=>(a.time ?? 0)-(b.time ?? 0) || a.seq-b.seq)
        })
        setError('')
        const info=await secretary.channelConversation({sessionId:id})
        if(active && info.ok)setInfo(info.value)
      }catch{ /* Keep the last readable transcript during a transient disconnect. */ }
      finally{reading=false}
    }
    const timer=setInterval(refresh,4000)
    document.addEventListener('visibilitychange',refresh)
    return()=>{active=false;clearInterval(timer);document.removeEventListener('visibilitychange',refresh)}
  },[id,secretary])
  async function older() {
    if (busy || before === undefined) return
    const requestedId = id
    setBusy(true); setError('')
    try {
      const result = await secretary.channelHistory({sessionId:id,beforeSeq:before})
      if (navigation.state.getSnapshot().selection !== requestedId) return
      if (!result.ok) throw Error(result.error.message)
      place.current={height:scroller.current.scrollHeight,top:scroller.current.scrollTop}
      setPages(previous => [...result.value.messages, ...previous]); setMore(result.value.hasMore)
      setBefore(result.value.beforeSeq ?? undefined)
    } catch (reason) { if (navigation.state.getSnapshot().selection === requestedId) setError(reason.message) }
    finally { if (navigation.state.getSnapshot().selection === requestedId) setBusy(false) }
  }
  const route=channelRoute(id,metadata), group=route?.kind==='group', number=route?.peer
  const messages=pages
  const contact=info?.name || metadata?.qq?.[id] || (!group && messages.findLast(item=>item.nickname)?.nickname)
  const title=contact || (group?'群聊':'私聊')
  return <section className="alcor-channel-history alcor-qq-history">
    <header className="alcor-qq-heading">
      <span className="alcor-qq-avatar" aria-hidden="true">{group?<IconUsersOutlineRegular size={20}/>:Array.from(contact || 'Q')[0]}</span>
      <div className="alcor-qq-identity"><h1>{title}</h1><small>{group?'群聊':'私聊'} · {number}{info?.members!=null && ' · '+info.members+' 人'}</small></div>
      <span className="alcor-qq-readonly">只读</span>
      <button className="alcor-qq-details-toggle" type="button" aria-expanded={details} aria-controls="alcor-qq-attributes" onClick={()=>setDetails(!details)}>会话资料</button>
      <button type="button" className="alcor-qq-refresh" aria-label="刷新记录" title="刷新记录" disabled={busy} onClick={()=>setVersion(value=>value+1)}><IconRefreshOutlineRegular size={16}/></button>
    </header>
    {details && <div id="alcor-qq-attributes" className="alcor-qq-attributes">{info?<dl>
      <div><dt>{route?.channelName || route?.channel} 接入</dt><dd>{info.online?'已连接':'离线'}</dd></div>
      <div><dt>回应方式</dt><dd>{responseLabels[info.response]}</dd></div>
      <div><dt>忙时消息</dt><dd>{info.queue==='queue'?'排队':'允许插话'}</dd></div>
      <div><dt>过程回复</dt><dd>{info.interim?'显示':'仅最终回复'}</dd></div>
      <div><dt>消息频率</dt><dd>{info.limit>0?info.limit+' 条 / 分钟':'不限'}</dd></div>
      {info.admin!=null && <div><dt>联系人身份</dt><dd>{info.admin?'插件管理员':'联系人'}</dd></div>}
    </dl>:<small>{info===undefined?'正在读取会话资料…':'当前没有可用的插件资料。'}</small>}</div>}
    <div className="alcor-qq-transcript" ref={scroller}>
      {error && <p role="alert">{error}</p>}
      {more && <Button variant="ghost" size="sm" onClick={older} disabled={busy}>更早的消息</Button>}
      {busy && <small role="status">正在读取…</small>}
      <div className="alcor-qq-thread">{messages.map((item,index)=>{
        const assistant=item.role==='Alcor', date=item.time && new Date(item.time), previous=messages[index-1]
        const firstDay=date && (!previous?.time || date.toDateString()!==new Date(previous.time).toDateString())
        const author=assistant ? 'Alcor' : item.nickname || (!group && contact) || item.role
        return <React.Fragment key={item.seq}>{firstDay && <time className="alcor-history-day" dateTime={date.toISOString()}>{date.toLocaleDateString(undefined,{month:'long',day:'numeric',year:'numeric'})}</time>}
          <article data-speaker={assistant?'assistant':'contact'}>
            <span className="alcor-qq-speaker-avatar" aria-hidden="true">{assistant?<BearLogo size={23}/>:Array.from(item.nickname || (!group && contact) || 'Q')[0]}</span>
            <div className="alcor-message-author"><button type="button" aria-label={'消息详情 · '+author} aria-expanded={selectedMessage===item.seq} onClick={()=>selectMessage(selectedMessage===item.seq?null:item.seq)}><strong>{author}</strong></button>{item.ambient && <small>自主参与</small>}
              {date && <time dateTime={date.toISOString()} title={date.toLocaleString()}>{date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time>}
            </div><p>{item.text}</p>
            {selectedMessage===item.seq && <small className="alcor-qq-message-meta">{[assistant?'Alcor 回复':item.role,...(group?['群 '+number]:[]),date ? date.toLocaleString() : ''].filter(Boolean).join(' · ')}</small>}
          </article></React.Fragment>
      })}</div>
      {!busy && !error && !messages.length && <p>暂无消息</p>}
    </div>
  </section>
}
