import { taskCompletion } from './task-focus.js'
import React, { useEffect, useState } from 'react'
import { Button, Modal, IconUsersOutlineRegular, IconChecklistOutlineRegular, IconDeliverDocRegular, IconAlarmClockOutlineRegular, IconPlusOutlineRegular, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { AgentHub } from './agent-hub.jsx'
import { localDate } from './presentation.js'
import { BillsPage, BillEditor } from './bills.jsx'
import { TasksPage } from './tasks-page.jsx'
import { CalendarPage } from './calendar-page.jsx'
import { TaskEditor, CalendarEditor } from './organizer-editors.jsx'

function CalendarIcon({ size }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="16" rx="2.5" /><path d="M7.5 3v4m9-4v4m-13 4h17M8 15h2m4 0h2m-8 3h2" /></svg>
}
function BillIcon({ size }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 3.5h12a1 1 0 0 1 1 1V21l-3-2-4 2-4-2-3 2V4.5a1 1 0 0 1 1-1Z" /><path d="M9 8h6m-6 4h6m-6 4h3" /></svg>
}

export const applicationViews = [
  { id: 'stella-agents', label: 'Agent 协作', icon: IconUsersOutlineRegular },
  { id: 'stella-tasks', label: '事务', icon: IconChecklistOutlineRegular },
  { id: 'stella-calendar', label: '日程', icon: CalendarIcon },
  { id: 'stella-briefing', label: '简报', icon: IconDeliverDocRegular },
  { id: 'stella-bills', label: '账单', icon: BillIcon },
  { id: 'stella-schedules', label: '自动化', icon: IconAlarmClockOutlineRegular },
].map(item => ({ ...item, group: '应用' }))

export { localDate } from './presentation.js'
const localTime = value => { const date = new Date(value); return `${localDate(date)}T${date.toTimeString().slice(0, 5)}` }
const kindFor = { 'stella-tasks': 'task', 'stella-calendar': 'event', 'stella-briefing': 'briefing', 'stella-bills': 'bill' }
const titles = { task: '事务', event: '日程', bill: '账单' }

export function BusinessDesk({ view, api, currentSession, startSession, openSession, quickChat }) {
  const [data, setData] = useState({ records: [], jobs: [], schedules: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [editor, setEditor] = useState(null)
  const [categories, setCategories] = useState([])
  const [calendarManage, setCalendarManage] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const kind = kindFor[view]
  const current = applicationViews.find(item => item.id === view)
  async function unwrap(promise) {
    const result = await promise
    if (!result.ok) throw new Error(result.error?.message || '操作失败，请刷新核对后重试。')
    return result.value
  }
  async function refresh() {
    try { const [value, groups] = await Promise.all([unwrap(api.read({})), unwrap(api.business({ domain: 'calendar', method: 'categories' }))]); setData(value); setCategories(groups); setLoading(false); setReloadKey(previous => previous + 1) }
    catch { setLoading(false); setError('连接中断或业务服务不可用，请刷新重试。') }
  }
  useEffect(() => {
    let disposed = false
    const load = async () => {
      try { const [value, groups] = await Promise.all([unwrap(api.read({})), unwrap(api.business({ domain: 'calendar', method: 'categories' }))]); if (!disposed) { setData(value); setCategories(groups); setLoading(false) } }
      catch { if (!disposed) { setLoading(false); setError('连接中断或业务服务不可用，请刷新重试。') } }
    }
    void load()
    const interval = setInterval(() => { if (document.visibilityState === 'visible') void load() }, 4000)
    return () => { disposed = true; clearInterval(interval) }
  }, [api])
  async function run(operation, success = '已保存') {
    setBusy(true); setError(''); setNotice('')
    try { const value = await unwrap(operation()); await refresh(); setNotice(success); return value ?? true }
    catch (failure) { setError(failure.message || '操作失败，请刷新核对。'); return null }
    finally { setBusy(false) }
  }
  const records = data.records.filter(item => item.kind === kind && !item.archived).sort((a, b) => kind === 'briefing' ? b.date.localeCompare(a.date) || b.createdAt.localeCompare(a.createdAt) : b.updatedAt.localeCompare(a.updatedAt))
  function edit(record) { setEditor(record.kind === 'event' && !record.allDay ? { ...record, start: localTime(record.start), end: localTime(record.end) } : record) }
  function create(day, hour = 9) { const start = day ? new Date(`${day}T${String(hour).padStart(2, '0')}:00`) : null; setEditor(start ? { title: '', details: '', start: localTime(start), end: localTime(new Date(start.getTime() + 3600000)), allDay: false } : {}) }
  const mutate = (domain, method, arguments_, success) => run(() => api.business({ domain, method, arguments: arguments_, requestId: crypto.randomUUID() }), success)
  function toggleArchive(record) { return run(() => api.archive({ id: record.id, expectedRevision: record.revision, archived: !record.archived }), record.archived ? '已恢复' : '已归档') }
  function download(content, name, type) {
    const link = document.createElement('a'); const url = URL.createObjectURL(new Blob([content], { type }))
    link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url)
  }
  const primary = kind === 'task' ? '新建事务' : kind === 'event' ? '新建日程' : null
  function newItem() {
    if (kind === 'task' || kind === 'event') create()

  }
  if (view === 'stella-agents') return <AgentHub jobs={data.jobs} loading={loading} busy={busy} error={error} notice={notice} refresh={() => { setError(''); void refresh() }} openSession={openSession} startSession={startSession} cancel={id => run(() => api.cancelJob({ id }), '取消请求已处理')} start={task => {
    const parentSessionId = currentSession()
    if (!parentSessionId) throw new Error('请在「管理接入」中先开始 Stella 对话，再回到这里启动任务。')
    return run(() => api.startJob({ ...task, parentSessionId }), '协作已交给原生子 Agent')
  }} />
  return <div className={`stella-business${kind === 'bill' ? ' stella-bills-page' : ''}`}>
    <header className="stella-app-header"><div><h1>{current.label}</h1></div><div className="stella-actions">
      <Button variant="outline" aria-label="刷新" title="刷新" onClick={() => { setError(''); void refresh() }}><IconRefreshOutlineRegular size={16} /></Button>
      {kind === 'event' && <><Button variant="outline" disabled={busy} onClick={() => setCalendarManage('import')}>导入日期</Button><Button variant="outline" disabled={busy} onClick={() => setCalendarManage('categories')}>分类管理</Button></>}
      {primary && <Button variant="primary" icon={<IconPlusOutlineRegular size={16} />} onClick={newItem}>{primary}</Button>}
    </div></header>
    <div className="stella-page-feedback">{loading && <p role="status">正在读取已保存记录…</p>}{error && <p role="alert" className="stella-error">{error}</p>}{notice && <p role="status">{notice}</p>}</div>
    {kind === 'task' && <TasksPage records={records} edit={edit} mutate={(method, args) => mutate('tasks', method, args, method === 'delete_todo' ? '已删除' : '已保存')} api={api} reloadKey={reloadKey} busy={busy} loading={loading} quickChat={quickChat} toggle={record => mutate('tasks', 'complete_todo', taskCompletion(record), '事务状态已更新')} />}
    {kind === 'event' && <CalendarPage archive={toggleArchive} archived={false} templates={data.records.filter(item => item.kind === 'event')} edit={record => setEditor(record)} create={create} busy={busy} loading={loading} quickChat={quickChat} api={api} mutate={(method, args) => mutate('calendar', method, args)} categories={categories} reloadKey={reloadKey} manage={calendarManage} setManage={setCalendarManage} />}
    {kind === 'bill' && <BillsPage records={data.records.filter(item => item.kind === 'bill')} api={api} reloadKey={reloadKey} edit={edit} create={() => create()} archive={toggleArchive} download={download} busy={busy} loading={loading} icon={<BillIcon size={32} />} />}
    <Modal open={editor !== null} headless title={`${editor?.id ? '编辑' : '新增'}${titles[kind] ?? ''}`} onClose={() => { if (!busy) setEditor(null) }} className="stella-editor-dialog">
      {editor !== null && (kind === 'task' ? <TaskEditor key={editor.id ?? 'new'} record={editor.id ? editor : undefined} busy={busy} cancel={() => setEditor(null)} save={async payload => { if (await mutate('tasks', editor.id ? 'update_todo' : 'create_todo', editor.id ? { todo_id: editor.id, payload } : { payload })) setEditor(null) }} /> : kind === 'event' ? <CalendarEditor key={editor.id ?? 'new'} record={editor.id ? editor : undefined} draft={editor} categories={categories} busy={busy} cancel={() => setEditor(null)} save={async payload => { if (await mutate('calendar', editor.id ? 'update_event' : 'create_event', editor.id ? { id: editor.id, patch: payload } : payload)) setEditor(null) }} /> : <BillEditor key={editor.id ?? 'new'} record={editor.id ? editor : undefined} busy={busy} cancel={() => setEditor(null)} save={async payload => { if (await mutate('bills', editor.id ? 'update_ledger' : 'create_ledger', editor.id ? { id: editor.id, patch: payload, expectedRevision: editor.business.revision } : payload)) setEditor(null) }} remove={async () => { if (await mutate('bills', 'delete_ledger', { id: editor.id, expectedRevision: editor.business.revision }, '已删除')) setEditor(null) }} archive={async () => { if (await toggleArchive(editor)) setEditor(null) }} />)}
      {error && <p role="alert" className="stella-error">{error}</p>}
    </Modal>

  </div>
}
