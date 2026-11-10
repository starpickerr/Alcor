import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { FollowupNote } from './followup.jsx'
import mascot from './assets/alcor-mascot.svg'
import { bearSession, bearWorkspace, normalizePosition } from './bear-state.js'
import { canonicalPanel } from './mode.js'
import { Menu, Modal } from '@deepseek-ai/dsh-client-ui-primitives'

const margin = 12
const pages = { 'alcor-tasks':'事务', 'alcor-calendar':'日程', 'alcor-bills':'账单', 'alcor-briefing':'简报', 'alcor-schedules':'自动化', plugins:'插件' }
function promptText(text, panel) {
  if (!pages[panel]) return text
  const root = document.querySelector('[data-alcor-bear-page="' + panel + '"]')
  const date = root?.querySelector('[data-alcor-bear-date]')?.dataset.alcorBearDate
  const record = root?.dataset.alcorBearRecord
  const context = [pages[panel], date && '日期 ' + date, record && '选中记录 ' + record].filter(Boolean)
  return text + '\n\n当前页面：' + context.join(' · ')
}

function ChatView({renderSlot}) {
  return renderSlot('conversation.session', {view:'chat'})
}
export function BearTimeline({useSession, useProjection, renderFactorySlot}) {
  const phase = useSession(state => state.openState), preset = useProjection('agentPreset')
  if (phase === 'error') return <p role="alert">会话连接失败。</p>
  if (preset !== 'alcor') return null
  return renderFactorySlot('conversation.content', {variant:'embedded', phase:phase === 'open' ? 'active' : 'settling', hero:false}, {slots:{views:ChatView}})
}

function Transcript({id, sessions, SessionProvider, renderSlot}) {
  const [ref, setRef] = useState(null), [failed, setFailed] = useState(false)
  useEffect(() => {
    let active = true, retained
    setFailed(false)
    try {
      retained = sessions.retain(id, {source:'alcorBear'})
      retained.ready.then(() => {if (active) setRef(retained)}).catch(() => {if (active) setFailed(true)})
    } catch { setFailed(true) }
    return () => {active = false; retained?.release()}
  }, [id, sessions])
  if (failed) return <p role="alert">无法加载会话，可在主聊天查看。</p>
  return <div className="alcor-bear-thread">{ref?.sessionId === id
    ? <SessionProvider session={ref}>{renderSlot('alcor.bear.timeline', {})}</SessionProvider>
    : <span role="status">正在连接…</span>}</div>
}

function BearChat({workspace, state, ctx, panel, catalog, spaces, ...native}) {
  const key = 'draft.' + (workspace ?? 'unassigned'), sessionKey = 'session.' + workspace
  const draft = useSyncExternalStore(fn => state.subscribe(key,fn), () => state.read(key, ''))
  const saved = useSyncExternalStore(fn => state.subscribe(sessionKey,fn), () => state.read(sessionKey, null))
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const id = bearSession(workspace, saved, catalog, spaces)
  function change(value) { state.write(key, value) }
  async function send(event) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || state.sending.has(workspace)) return
    if (!workspace) { setError('请先在侧栏打开一个工作区，再发送消息。'); return }
    state.sending.add(workspace); setBusy(true); setError('')
    const message = promptText(text, panel)
    try {
      const obtain = async () => {
        const workspaces = ctx.workspaces.list.getSnapshot(), list = ctx.sessions.list.getSnapshot()
        if (workspaces.phase !== 'ready' || list.phase !== 'ready' || !workspaces.items.some(item => item.workspaceId === workspace)) throw Error('工作区暂不可用，请稍后重试。')
        const stored = state.read(sessionKey, null)
        const existing = bearSession(workspace, stored, list, workspaces)
        if (existing) return existing
        const created = await ctx.sessions.create({workspaceId:workspace, agentPreset:'alcor'})
        state.write(sessionKey, created)
        return created
      }
      const target = navigator.locks ? await navigator.locks.request('alcor-bear.' + workspace, obtain) : await obtain()
      await ctx.sessions.using(target, {source:'alcorBear'}, async reference => {
        const {session} = await reference.ready
        if (session.projections.faceOf('agentPreset').getSnapshot() !== 'alcor') throw Error('会话模式已更改，请重新打开发送框。')
        const result = await session.prompt([{type:'text', text:message}], 'queue')
        if (!result.ok) throw Error('消息未获确认，请在主聊天核对后再发送。')
      })
      if (state.read(key, '') === draft) change('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '发送结果待确认，请在主聊天核对。') }
    finally { state.sending.delete(workspace); setBusy(false) }
  }
  return <>
    {id && <><button className="alcor-bear-history" type="button" onClick={() => ctx.uiWorkspace.openSession(id)}>打开完整对话 ↗</button>
      <Transcript id={id} sessions={ctx.sessions} {...native} /></>}
    {error && <p className="alcor-bear-error" role="alert">{error}</p>}
    <form className="alcor-bear-compose" onSubmit={send}>
      <textarea aria-label="给 Alcor 发消息" placeholder="给 Alcor 发消息…" rows={2} value={draft} onChange={event => change(event.target.value)}
        onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) send(event)}} />
      <button type="submit" aria-label="发送消息" title="发送消息" disabled={!draft.trim() || busy || catalog.phase !== 'ready' || spaces.phase !== 'ready'}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>
      </button>
    </form>
  </>
}

function FloatingBear({prefs, state, workspace, openSettings, openPersona, ...chat}) {
  const [open, setOpen] = useState(false), [pinned, setPinned] = useState(false)
  const [menu, setMenu] = useState(null)
  const size = prefs.size
  const viewport = () => [window.innerWidth, window.innerHeight]
  const [[width,height], setViewport] = useState(viewport)
  const [dragPos,setDragPos] = useState(null)
  const drag = useRef(null), moved = useRef(false), timer = useRef(), button = useRef()
  const rangeX = Math.max(1,width - size - 2*margin), rangeY = Math.max(1,height - size - 2*margin)
  const pos = dragPos ?? prefs.position
  const x = margin + pos[0]*rangeX, y = margin + pos[1]*rangeY
  const panelWidth = Math.min(380, width - margin*2)
  const below = y < (height - size)/2
  const room = below ? height - y - size - 2*margin : y - 2*margin
  useEffect(() => {
    const resize = () => setViewport(viewport())
    window.addEventListener('resize',resize)
    return () => {window.removeEventListener('resize',resize); clearTimeout(timer.current)}
  }, [])
  function close() { clearTimeout(timer.current); setOpen(false); setPinned(false) }
  function contextMenu(event) {
    event.preventDefault(); event.stopPropagation()
    close()
    const rect = button.current.getBoundingClientRect()
    const keyboard = event.type === 'keydown' || (!event.clientX && !event.clientY)
    setMenu({x:keyboard ? rect.left : event.clientX, y:keyboard ? rect.bottom : event.clientY})
  }
  useEffect(() => {
    if (!open) return
    function escape(e) { if (e.key === 'Escape' && !e.defaultPrevented) {close(); button.current?.focus()} }
    document.addEventListener('keydown',escape)
    return () => document.removeEventListener('keydown',escape)
  }, [open])
  function move(event) {
    if (drag.current?.pointer !== event.pointerId) return
    const {startX,startY,left,top} = drag.current
    const dx = event.clientX-startX, dy = event.clientY-startY
    if (!moved.current && Math.hypot(dx,dy) < 5) return
    moved.current = true; clearTimeout(timer.current)
    const value = normalizePosition([(left+dx-margin)/rangeX,(top+dy-margin)/rangeY])
    drag.current.value = value
    setDragPos(value)
  }
  function end(event, commit) {
    if (drag.current?.pointer !== event.pointerId) return
    if (commit && drag.current.value) state.preferences.update({position:drag.current.value})
    drag.current = null; setDragPos(null)
  }
  const enter = e => {
    clearTimeout(timer.current)
    if (prefs.hover && !menu && e.pointerType === 'mouse' && !drag.current && !open) timer.current = setTimeout(() => setOpen(true),320)
  }
  const leave = () => {clearTimeout(timer.current); if (!menu && !pinned && !drag.current) timer.current = setTimeout(close,340)}
  const pin = () => {clearTimeout(timer.current); setPinned(true); setOpen(true)}
  return <div className="alcor-bear" onPointerEnter={enter} onPointerLeave={leave}>
    <button className="alcor-bear-handle" ref={button} type="button" aria-label={prefs.name} aria-expanded={open} title={`${prefs.name} · 拖动以移动，右键打开菜单`}
      style={{left:x,top:y,width:size,height:size}} onContextMenu={contextMenu} onPointerDown={e => {
        if (!e.isPrimary || e.button !== 0) return
        clearTimeout(timer.current); moved.current = false
        drag.current = {pointer:e.pointerId,startX:e.clientX,startY:e.clientY,left:x,top:y}
        e.currentTarget.setPointerCapture(e.pointerId)
      }} onPointerMove={move} onPointerUp={e => end(e,true)} onPointerCancel={e => end(e,false)} onLostPointerCapture={e => end(e,false)}
      onKeyDown={e => {
        if (e.key === 'ContextMenu' || e.key === 'F10' && e.shiftKey) { contextMenu(e); return }
        const delta = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key]
        if (!delta) return
        e.preventDefault()
        state.preferences.update({position:normalizePosition([pos[0]+delta[0]*16/rangeX,pos[1]+delta[1]*16/rangeY])})
      }} onClick={() => {if (moved.current) {moved.current = false; return} if (pinned) close(); else pin()}}>
      <img src={mascot} alt="" draggable={false} />
    </button>
    <Menu open={!!menu} contextual portal autoFocus anchor={null} onClose={() => setMenu(null)}
      className="alcor-bear-menu" listClassName="alcor-bear-menu-list"
      getAnchorRect={() => menu ? new DOMRect(menu.x,menu.y,0,0) : null} returnFocus={() => button.current}
      items={[{id:'persona',label:'人设'},{id:'settings',label:'设置'},{id:'hide',label:'隐藏'}]}
      onSelect={id => {
        setMenu(null)
        if (id === 'hide') state.preferences.update({enabled:false})
        if (id === 'settings') openSettings()
        if (id === 'persona') openPersona()
      }} />
    {open && <section className="alcor-bear-panel" role="region" aria-label="Alcor 小熊对话" onFocus={pin} onPointerDown={pin}
      onPointerEnter={() => clearTimeout(timer.current)}
      style={{width:panelWidth, maxHeight:Math.max(100,room), left:Math.max(margin,Math.min(x + size - panelWidth,width - margin - panelWidth)),
        ...(below ? {top:y+size+6} : {bottom:height-y+6})}}>
      <BearChat key={workspace ?? ''} workspace={workspace} state={state} {...chat} />
    </section>}
  </div>
}

export function BearOverlay({state, ctx, product, followupApi, renderSettings, openPersona, useMode, usePanelInfo, useMainSelection, useWorkspaces, useSessions, ...native}) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const mode = useMode(value => value), panel = canonicalPanel(usePanelInfo(value => value.activePanelId))
  const selected = useMainSelection(value => value.sessionId)
  const catalog = useSessions(value => value), spaces = useWorkspaces(value => value)
  const prefs = useSyncExternalStore(state.preferences.subscribe,state.preferences.getSnapshot)
  const {metadata} = useSyncExternalStore(product.subscribe, product.getSnapshot)
  const workspace = bearWorkspace(spaces, metadata?.defaults.alcor)
  useEffect(() => {
    if (mode !== 'alcor' || !workspace) return
    if (state.read('workspace') !== workspace) state.write('workspace', workspace)
    if (!panel && bearSession(workspace, selected, catalog, spaces) && state.read('session.'+workspace) !== selected) {
      state.write('session.'+workspace, selected)
    }
  }, [mode, panel, workspace, selected, catalog, spaces, state])
  if (mode !== 'alcor' || !panel || panel === 'alcor-qq-history') return null
  return <>
    {followupApi && <FollowupNote api={followupApi} />}
    {prefs.enabled && <FloatingBear prefs={prefs} state={state} ctx={ctx} workspace={workspace} panel={panel} spaces={spaces} catalog={catalog} openSettings={() => setSettingsOpen(true)} openPersona={openPersona} {...native} />}
    <Modal open={settingsOpen} title="Alcor" closeLabel="关闭" onClose={() => setSettingsOpen(false)} className="alcor-bear-settings-dialog">{renderSettings()}</Modal>
  </>
}
