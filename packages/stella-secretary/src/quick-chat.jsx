import React, { useEffect, useRef, useState } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'

const storageKey = kind => `stella.quick-chat.${kind}.v1`
function readHistory(kind) {
  try {
    const value = JSON.parse(window.localStorage.getItem(storageKey(kind)))
    if (!Array.isArray(value?.ids) || !value.ids.every(id => typeof id === 'string' && id.length > 0)) return { ids: [], current: null }
    return { ids: [...new Set(value.ids)], current: value.ids.includes(value.current) ? value.current : null }
  } catch { return { ids: [], current: null } }
}

function ChatView({ renderSlot }) {
  return renderSlot('conversation.session', { view: 'chat' })
}

export function QuickConversation({ useSession, useProjection, renderFactorySlot }) {
  const state = useSession(value => value)
  const preset = useProjection('agentPreset')
  if (state.openState === 'error') return <p role="alert">会话连接失败，请重新选择历史会话，或新建会话。</p>
  if (preset !== 'stella') return <p role="status">这段会话未使用 Stella，请新建会话。</p>
  return renderFactorySlot('conversation.content', {
    variant: 'embedded', phase: state.openState === 'open' ? 'active' : 'settling', hero: false,
  }, { slots: { views: ChatView } })
}

export function QuickChat({ kind, sessions, getCurrentSession, useSessions, useWorkspaces, SessionProvider, renderSlot }) {
  const catalog = useSessions(value => value)
  const workspaces = useWorkspaces(value => value)
  const [history, setHistory] = useState(() => readHistory(kind))
  const [reference, setReference] = useState(null)
  const [workspaceId, setWorkspaceId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const initialized = useRef(false)
  const creating = useRef(false)
  const mounted = useRef(true)
  const historyRef = useRef(history)
  const choices = history.ids.map(id => catalog.byId[id]).filter(item => item && !item.origin
    && (item.projectionValues?.agentPreset === undefined || item.projectionValues.agentPreset === 'stella')
    && !workspaces.archivedSessionIds.includes(item.id))
  const currentWorkspace = workspaces.items.find(item => item.sessionIds.includes(history.current))
  const mainWorkspace = workspaces.items.find(item => item.sessionIds.includes(getCurrentSession()))
  const targetWorkspace = workspaceId || currentWorkspace?.workspaceId || mainWorkspace?.workspaceId || workspaces.items[0]?.workspaceId

  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  function remember(value) {
    historyRef.current = value
    try { window.localStorage.setItem(storageKey(kind), JSON.stringify(value)) }
    catch { if (mounted.current) setNotice('浏览器未能保存快捷会话位置；对话仍保存在 DSH 历史中。') }
    if (mounted.current) setHistory(value)
  }
  async function create(target = targetWorkspace) {
    if (creating.current) return
    if (!target) { setError('请先在 DSH 添加文件工作区，再在这里新建会话。'); return }
    creating.current = true; setBusy(true); setError('')
    try {
      const id = await sessions.create({ workspaceId: target, agentPreset: 'stella' })
      remember({ ids: [...historyRef.current.ids, id], current: id })
    } catch { if (mounted.current) setError('无法新建对话，请检查工作区与连接后重试。') }
    finally { creating.current = false; if (mounted.current) setBusy(false) }
  }
  useEffect(() => {
    if (initialized.current || catalog.phase !== 'ready' || workspaces.phase !== 'ready') return
    initialized.current = true
    const previous = choices.find(item => item.id === history.current) || choices.at(-1)
    if (previous) remember({ ...historyRef.current, current: previous.id })
    else void create()
  }, [catalog.phase, workspaces.phase])
  useEffect(() => {
    if (!history.current || catalog.phase !== 'ready' || !catalog.byId[history.current]) return
    let disposed = false
    let retained
    setReference(null); setError('')
    try {
      retained = sessions.retain(history.current, { source: 'stellaQuickChat' })
      retained.ready.then(() => { if (!disposed) setReference(retained) }).catch(() => {
        if (!disposed) setError('无法打开这段对话，请重试或新建会话。')
      })
    } catch { setError('无法打开这段对话，请新建会话。') }
    return () => { disposed = true; retained?.release() }
  }, [history.current, catalog.phase])

  const label = kind === 'task' ? '事务' : '日程'
  return <section className="stella-quick-chat" role="region" aria-label={`${label}快捷对话`}>
    <div className="stella-quick-chat-toolbar">
      <label><span>历史会话</span><select aria-label="历史会话" value={history.current || ''} disabled={busy || !choices.length}
        onChange={event => { setWorkspaceId(''); remember({ ...historyRef.current, current: event.target.value }) }}>
        {!choices.length && <option value="">当前会话</option>}
        {choices.map((item, index) => <option key={item.id} value={item.id}>{item.title || (item.id === history.current ? '当前会话' : `会话 ${index + 1}`)}</option>)}
      </select></label>
      <label><span>工作区</span><select aria-label="快捷对话工作区" value={targetWorkspace || ''} disabled={busy || !workspaces.items.length}
        onChange={event => { setWorkspaceId(event.target.value); void create(event.target.value) }}>
        {!workspaces.items.length && <option value="">尚无工作区</option>}
        {workspaces.items.map(item => <option key={item.workspaceId} value={item.workspaceId}>{item.title || item.path.split('/').pop()}</option>)}
      </select></label>
      <Button variant="ghost" size="sm" disabled={busy || workspaces.phase !== 'ready'} onClick={() => void create()}>新会话</Button>
    </div>
    {notice && <p role="status" className="stella-quick-chat-notice">{notice}</p>}
    {error && <div className="stella-quick-chat-notice"><p role="alert">{error}</p><Button variant="outline" size="sm" onClick={() => void create()}>重试</Button></div>}
    {reference && reference.sessionId === history.current
      ? <div className="stella-quick-chat-body"><SessionProvider session={reference}>{renderSlot('stella.quick-chat.conversation', {})}</SessionProvider></div>
      : !error && <p role="status" className="stella-quick-chat-notice">正在打开对话…</p>}
  </section>
}
