import React, { useEffect, useRef, useState } from 'react'
import { Button, MarkdownText, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Notice } from './notice.jsx'
import { showTime } from './ui.jsx'
import { markdownLabels } from './ui.jsx'
import { jobRunning, stepLabels, hubMentions, hubMessageKey, readHubMessage } from './agent-hub.js'

export function HubConversation({ job, api, openSession, currentSession, refreshJob }) {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [sender, setSender] = useState('all')
  const [draft, setDraft] = useState(() => {
    try { return readHubMessage(window.sessionStorage, job.id) ?? { body: '', intent: 'task', recipientIds: [], requestId: crypto.randomUUID() } }
    catch { return { body: '', intent: 'task', recipientIds: [], requestId: crypto.randomUUID() } }
  })
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [savedNotice, setSavedNotice] = useState('')
  const [storageError, setStorageError] = useState('')
  const [mention, setMention] = useState(null)
  const [option, setOption] = useState(0)
  const input = useRef(null)
  const end = useRef(null)
  const follow = useRef(true)
  useEffect(() => { if (follow.current && end.current) end.current.scrollTop = end.current.scrollHeight }, [entries])
  const people = job.collaboration?.participants ?? []
  const steps = job.collaboration?.steps ?? []
  const mentions = hubMentions(draft.body, people, draft.recipientIds)
  const choices = mention === null ? [] : people.filter(person => person.name.toLocaleLowerCase().includes(mention.query.toLocaleLowerCase()))
  const limit = draft.intent === 'task' ? 1000 : draft.intent === 'persona' ? 8000 : 16000
  useEffect(() => {
    try { window.sessionStorage.setItem(hubMessageKey(job.id), JSON.stringify(draft)); setStorageError('') }
    catch { setStorageError('消息草稿暂时无法自动保存，关闭页面前请复制内容。') }
  }, [draft, job.id])
  function update(patch) { setDraft(previous => ({ ...previous, ...patch, requestId: crypto.randomUUID() })); setSendError(''); setSavedNotice('') }
  function editBody(event) {
    const body = event.target.value, caret = event.target.selectionStart
    update({ body }); setOption(0)
    const match = body.slice(0, caret).match(/(?:^|\s)@([^@\n]*)$/u)
    setMention(match && match[1].length <= 80 ? { start: caret - match[1].length - 1, end: caret, query: match[1] } : null)
  }
  function pick(person) {
    const range = mention ?? { start: draft.body.length, end: draft.body.length }
    update({ body: draft.body.slice(0, range.start) + '@' + person.name + ' ' + draft.body.slice(range.end), recipientIds: [...new Set([...draft.recipientIds, person.id])] })
    setMention(null); input.current?.focus()
  }
  async function send(event) {
    event?.preventDefault()
    if (sending || !draft.body.trim()) return
    if (mentions.unknown.length || mentions.ambiguous.length) { setSendError('请从 @ 菜单选择当前任务中的助手；同名助手需明确选择。'); return }
    if (mentions.ids.length > 3 || draft.intent !== 'message' && !mentions.ids.length || draft.intent === 'persona' && mentions.ids.length !== 1) { setSendError(draft.intent === 'persona' ? '设定人设时请选择一位助手。' : '每次分配给 1–3 位助手；普通消息可以发送给全部参与者。'); return }
    if (draft.body.trim().length > limit) { setSendError(`当前操作最多 ${limit} 字符，内容已保留，请调整后重试。`); return }
    const parentSessionId = draft.intent === 'task' ? currentSession() : undefined
    if (draft.intent === 'task' && !parentSessionId) { setSendError('请先在协作设置中开始开发对话。'); return }
    setSending(true); setSendError(''); setSavedNotice('')
    try {
      const result = await api.hubSend({ id: job.id, ...draft, recipientIds: mentions.ids, ...(parentSessionId ? { parentSessionId } : {}) })
      if (!result.ok) throw new Error(result.error?.message || '消息未能发送，请重试。')
      follow.current = true; update({ body: '' }); setMention(null); setRefresh(value => value + 1); refreshJob()
      setSavedNotice(draft.intent === 'task' ? '任务已分配，执行回复会回到此交流中。' : draft.intent === 'persona' ? '人设已更新，下一次执行生效。' : '消息已发送。')
    } catch (failure) { setSendError(failure.message || '发送失败，内容已保留，请重试。') }
    finally { setSending(false) }
  }
  useEffect(() => {
    let disposed = false
    let pending = false
    async function load() {
      if (pending || document.visibilityState !== 'visible') return
      pending = true
      try {
        const result = await api.hubActivity({ id: job.id })
        if (!result.ok) throw new Error(result.error?.message || '无法读取交流记录。')
        if (!disposed) { setEntries(result.value); setError('') }
      } catch (failure) { if (!disposed) setError(failure.message || '交流记录加载失败，请重试。') }
      finally { pending = false; if (!disposed) setLoading(false) }
    }
    setLoading(true)
    void load()
    const timer = jobRunning(job) ? setInterval(() => void load(), 4000) : null
    return () => { disposed = true; clearInterval(timer) }
  }, [api, job.id, job.status, refresh])
  const visible = entries.filter(entry => sender === 'all' || (sender === 'user' ? ['created', 'user'].includes(entry.kind) : entry.senderId === sender || entry.recipientIds?.includes(sender)))
  const personName = id => people.find(person => person.id === id)?.name ?? '协作助手'
  const latestSession = id => steps.filter(step => step.participantId === id).flatMap(step => step.attempts).filter(attempt => attempt.childId).at(-1)?.childId
  return <section className="alcor-hub-chat" aria-label="协作交流">
    <div className="alcor-hub-chat-toolbar">
      <label><span className="alcor-hub-compose-label">查看参与者</span><select aria-label="查看参与者" value={sender} onChange={event => setSender(event.target.value)}><option value="all">全部交流</option><option value="user">我的消息</option>{people.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></label>
      <span className="alcor-muted" role="status">{loading ? '正在读取…' : `${entries.length} 条记录${jobRunning(job) ? ' · 自动更新' : ''}`}</span>
      <Button variant="ghost" aria-label="刷新交流" title="刷新交流" disabled={loading} onClick={() => setRefresh(value => value + 1)}><IconRefreshOutlineRegular size={16} /></Button>
      <Button variant="outline" onClick={() => { follow.current = true; if (end.current) end.current.scrollTop = end.current.scrollHeight }}>跳至最新</Button>
    </div>
    {sender !== 'all' && sender !== 'user' && latestSession(sender) && <Button variant="outline" className="alcor-hub-chat-session" onClick={() => openSession(latestSession(sender))}>查看此助手原生会话</Button>}
    {error && <Notice tone="error">{error}<Button variant="ghost" disabled={loading} onClick={() => setRefresh(value => value + 1)}>重试加载</Button></Notice>}
    {!loading && !error && !visible.length && <Notice>{entries.length ? '此参与者暂时没有交流记录。' : '暂无已保存的交流记录。'}</Notice>}
    <ol ref={end} className="alcor-hub-chat-stream" aria-label="协作交流记录" onScroll={event => { const list = event.currentTarget; follow.current = list.scrollHeight - list.scrollTop - list.clientHeight < 80 }}>
      {visible.map(entry => {
        const own = ['created', 'user'].includes(entry.kind)
        const reply = entry.kind === 'reply'
        const name = own ? '你' : entry.senderId ? personName(entry.senderId) : reply ? '协作助手' : '系统'
        const step = steps.find(item => item.stepId === entry.stepId)
        const type = entry.kind === 'created' ? '任务目标' : reply ? entry.stepId === 'summary' ? '汇总结果' : entry.stepId?.startsWith('chat-') ? '任务回复' : '分工结果' : entry.kind === 'configuration' ? '配置更新' : entry.kind === 'started' ? '执行动态' : entry.kind === 'status' ? stepLabels[entry.status] ?? '任务动态' : '正式消息'
        return <li key={entry.id} className="alcor-hub-chat-entry" data-kind={entry.kind}>
          <span className="alcor-hub-avatar" aria-hidden="true">{own ? '你' : entry.stepId === 'summary' ? '汇' : entry.senderId ? people.findIndex(person => person.id === entry.senderId) + 1 || '助' : '·'}</span>
          <article>
            <header><strong>{name}</strong><span className="alcor-hub-chat-kind">{type}</span><time dateTime={entry.createdAt}>{entry.createdAt ? showTime(entry.createdAt) : '时间未记录'}</time></header>
            {entry.recipientIds?.length > 0 && <small className="alcor-hub-chat-recipients">发送给 {entry.recipientIds.map(personName).join('、')}</small>}
            {entry.kind === 'status' ? <Notice tone={['failed', 'outcome_unknown', 'interrupted'].includes(entry.status) ? 'warning' : 'info'}>{entry.body}</Notice> : <div className="alcor-hub-chat-body">{reply ? <MarkdownText text={entry.body} streaming={false} labels={markdownLabels} /> : <p>{entry.body}</p>}</div>}
            {(step || entry.sessionId) && <footer>{step && <small>{step.stepId === 'summary' ? '汇总各分工结果' : step.title}</small>}{entry.sessionId && <Button variant="ghost" size="sm" onClick={() => openSession(entry.sessionId)}>查看原生会话</Button>}</footer>}
          </article>
        </li>
      })}
    </ol>
    {jobRunning(job) && <div className="alcor-hub-chat-working"><span className="alcor-hub-status" data-status="running">助手正在执行</span><small>新的执行动态和正式答复会显示在这里。</small></div>}
    {job.id.startsWith('hub-') ? <form className="alcor-hub-chat-composer" onSubmit={send}>
      <div className="alcor-hub-compose-tools"><select aria-label="交流操作" disabled={sending} value={draft.intent} onChange={event => update({ intent: event.target.value })}><option value="task">分配任务</option><option value="message">发消息</option><option value="persona">设定人设</option></select><Button variant="ghost" disabled={sending} onClick={() => { const body = draft.body + (draft.body && !/\s$/.test(draft.body) ? ' ' : '') + '@'; update({ body }); setMention({ start: body.length - 1, end: body.length, query: '' }); setOption(0); input.current?.focus() }}>@ 助手</Button></div>
      {!!mentions.ids.length && <div className="alcor-hub-mention-chips">{mentions.ids.map(id => <Button key={id} variant="ghost" size="sm" disabled={sending} aria-label={`取消 @${personName(id)}`} onClick={() => { update({ body: draft.body.replaceAll('@' + personName(id), ''), recipientIds: draft.recipientIds.filter(value => value !== id) }) }}>@{personName(id)} ×</Button>)}</div>}
      <div className="alcor-hub-mention-input">
        <textarea ref={input} aria-label="交流消息" role="combobox" aria-autocomplete="list" aria-expanded={!!choices.length} aria-controls="hub-mention-options" aria-activedescendant={choices.length ? `hub-mention-option-${Math.min(option, choices.length - 1)}` : undefined} disabled={sending} rows={3} maxLength={16000} placeholder="输入 @ 选择助手，分配任务或设定人设…" value={draft.body} onChange={editBody} onKeyDown={event => {
          if (event.nativeEvent.isComposing) return
          if (choices.length && ['ArrowDown', 'ArrowUp', 'Enter', 'Escape'].includes(event.key)) {
            event.preventDefault(); event.stopPropagation()
            if (event.key === 'Escape') setMention(null)
            else if (event.key === 'Enter') pick(choices[Math.min(option, choices.length - 1)])
            else setOption(value => (value + (event.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length)
          } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send() }
        }} />
        {!!choices.length && <div className="alcor-hub-mention-menu" id="hub-mention-options" role="listbox" aria-label="选择 Agent">{choices.map((person, index) => <button key={person.id} type="button" role="option" id={`hub-mention-option-${index}`} aria-selected={option === index} onMouseDown={event => event.preventDefault()} onClick={() => pick(person)}><span className="alcor-hub-avatar">{index + 1}</span><span>{person.name}</span><small>助手 {people.indexOf(person) + 1}</small></button>)}</div>}
      </div>
      {sendError && <Notice tone="error">{sendError}</Notice>}{storageError && <Notice tone="warning">{storageError}</Notice>}{savedNotice && <Notice tone="success" role="status">{savedNotice}</Notice>}
      <small className="alcor-hub-compose-help">{draft.intent === 'task' ? '分配后将调用模型，可能产生费用；同一助手有未完成分工时会等待前序完成。' : draft.intent === 'persona' ? '人设仅作用于本任务中的这位助手，下一次执行生效。' : '消息保留在交流中，不触发执行。'}</small>
      <footer><small>{draft.body.length} / {limit} · ⌘ / Ctrl + Enter</small><Button variant="primary" type="submit" disabled={sending || !draft.body.trim() || draft.intent === 'task' && job.status === 'cancelled'}>{sending ? '正在发送…' : draft.intent === 'task' ? '分配并执行' : draft.intent === 'persona' ? '更新人设' : '发送'}</Button></footer>
    </form> : <Notice>此历史委托仅支持查看，继续交流请先“再建任务”。</Notice>}
  </section>
}
