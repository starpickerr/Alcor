import { Notice } from './notice.jsx'
import { OrganizerRecordPanel } from './organizer-record-panel.jsx'
import { OrganizerDetails } from './organizer-details.jsx'
import { recordClipboardText, recordEditTarget, recordId, recordRepeats } from './organizer-actions.js'
import { taskCompletion } from './task-focus.js'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Modal, IconUsersOutlineRegular, IconChecklistOutlineRegular, IconDeliverDocRegular, IconAlarmClockOutlineRegular, IconPlusOutlineRegular, IconRefreshOutlineRegular, Menu, MenuItemButton, SegmentedControl } from '@deepseek-ai/dsh-client-ui-primitives'
import { localDate } from './presentation.js'
import { BillsPage, BillEditor } from './bills.jsx'
import { TasksPage } from './tasks-page.jsx'
import { CalendarPage, Categories } from './calendar-page.jsx'
import { TaskEditor, CalendarEditor } from './organizer-editors.jsx'

function CalendarIcon({ size }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="16" rx="2.5" /><path d="M7.5 3v4m9-4v4m-13 4h17M8 15h2m4 0h2m-8 3h2" /></svg>
}
function BillIcon({ size }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 3.5h12a1 1 0 0 1 1 1V21l-3-2-4 2-4-2-3 2V4.5a1 1 0 0 1 1-1Z" /><path d="M9 8h6m-6 4h6m-6 4h3" /></svg>
}

export const applicationViews = [
  { id: 'alcor-tasks', label: '事务', icon: IconChecklistOutlineRegular },
  { id: 'alcor-calendar', label: '日程', icon: CalendarIcon },
  { id: 'alcor-briefing', label: '简报', icon: IconDeliverDocRegular },
  { id: 'alcor-bills', label: '账单', icon: BillIcon },
  { id: 'alcor-schedules', label: '自动化', icon: IconAlarmClockOutlineRegular },
].map(item => ({ ...item, group: '应用' }))

export { localDate } from './presentation.js'
const localTime = value => { const date = new Date(value); return `${localDate(date)}T${date.toTimeString().slice(0, 5)}` }
const kindFor = { 'alcor-tasks': 'task', 'alcor-calendar': 'event', 'alcor-briefing': 'briefing', 'alcor-bills': 'bill' }
const titles = { task: '事务', event: '日程', bill: '账单' }

export function BusinessDesk({ view, api, currentSession, startSession, openSession, quickChat }) {
  const [data, setData] = useState({ records: [], jobs: [], schedules: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [editor, setEditor] = useState(null)
  const pendingBillDelete = useRef(null), billDeleteEpoch = useRef(0), billDeleteInFlight = useRef(false)
  useEffect(() => () => {
    billDeleteEpoch.current++
    const ticket = pendingBillDelete.current; pendingBillDelete.current = null
    if (ticket) void api.cancelPageDelete({ ticket }).catch(() => {})
  }, [api, view, editor?.id, editor?.business?.revision])
  const [details, setDetails] = useState(null)
  const [editorDirty, setEditorDirty] = useState(false)
  const [pendingPanelAction, setPendingPanelAction] = useState(null)
  const panelTrigger = useRef(null)
  useEffect(() => {setEditorDirty(false)},[editor])
  useEffect(() => { setDetails(null) },[view])
  const [categories, setCategories] = useState([])
  const [taskCategories, setTaskCategories] = useState([])
  const [creationMenu, setCreationMenu] = useState(false)
  const [creationType, setCreationType] = useState('event')
  const [calendarManage, setCalendarManage] = useState(null)
  const [recovery, setRecovery] = useState(null)
  const recoveryEpoch = useRef(0)
  useEffect(() => { recoveryEpoch.current++; setRecovery(null) }, [view])
  const [reloadKey, setReloadKey] = useState(0)
  const kind = kindFor[view]
  const current = applicationViews.find(item => item.id === view)
  async function unwrap(promise) {
    const result = await promise
    if (!result.ok) throw new Error(result.error?.message || '操作结果未确认，请先核对当前状态。')
    return result.value
  }
  async function refresh() {
    try { const [value, groups, lists] = await Promise.all([unwrap(api.read({})), unwrap(api.business({ domain: 'calendar', method: 'categories' })), unwrap(api.business({ domain: 'tasks', method: 'categories' }))]); setData(value); setCategories(groups); setTaskCategories(lists); setLoading(false); setReloadKey(previous => previous + 1) }
    catch { setLoading(false); setError('连接中断或业务服务不可用，请刷新重试。') }
  }
  useEffect(() => {
    let disposed = false
    const load = async () => {
      try { const [value, groups, lists] = await Promise.all([unwrap(api.read({})), unwrap(api.business({ domain: 'calendar', method: 'categories' })), unwrap(api.business({ domain: 'tasks', method: 'categories' }))]); if (!disposed) { setData(value); setCategories(groups); setTaskCategories(lists); setLoading(false) } }
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
  async function openRecoveries() {
    const domain = kind === 'task' ? 'tasks' : 'calendar'
    const epoch = ++recoveryEpoch.current
    setError('')
    try {
      const rows = await unwrap(api.listInboundRecoveries({ domain }))
      if (epoch === recoveryEpoch.current) setRecovery({ domain, rows })
    } catch (failure) { if (epoch === recoveryEpoch.current) setError(failure.message || '无法读取恢复记录。') }
  }
  async function restoreRecovery(record) {
    if (!recovery || busy) return
    const { domain } = recovery
    const epoch = recoveryEpoch.current
    const result = await run(() => api.restoreInboundRecovery({ domain, recoveryId: record.id }))
    if (!result || epoch !== recoveryEpoch.current) return
    const warnings = (result.conflicts || []).map(item => ({ category_renamed:'分类重名，已另存', category_unavailable:'原分类不可用', parent_unavailable:'原父事务未关联', reminder_paused:'提醒已暂停', external_dependency_not_relinked:'外部依赖未重连', handoff_not_relinked:'简报关联未重连', apple_disconnected:'Apple 同步已断开' })[item.kind] || '有信息需要核对')
    setNotice(`已恢复本地副本，不会自动同步到 Apple。${warnings.length ? ' ' + [...new Set(warnings)].join('；') + '。' : ''}`)
    await openRecoveries()
  }
  function edit(record) { setEditor(record.kind === 'event' && !record.allDay ? { ...record, start: localTime(record.start), end: localTime(record.end) } : record) }
  function requestPanel(action) {
    if (busy) return
    if (!document.activeElement?.closest('.alcor-calendar-record-panel')) panelTrigger.current=document.activeElement
    if (['event','task'].includes(kind) && editor !== null && editorDirty) {setPendingPanelAction({apply:action});return}
    action()
  }
  function create(day, hour = 9, type = kind) { requestPanel(() => {setDetails(null); setCreationType(type); setCreationMenu(false); const start = day ? new Date(`${day}T${String(hour).padStart(2, '0')}:00`) : null; setEditor(start ? { title: '', details: '', start: localTime(start), end: localTime(new Date(start.getTime() + 3600000)), allDay: false } : {}) }) }
  const mutate = (domain, method, arguments_, success) => run(() => api.business({ domain, method, arguments: arguments_, requestId: crypto.randomUUID() }), success)
  const commitDelete = async ticket => { const result = await run(() => api.commitPageDelete({ ticket }), '已删除'); if (result?.status === 'needs_review') setNotice(result.message || '删除结果尚未确认，请先核对。'); else if (result?.pending) setNotice('删除已确认，等待同步；可在 Apple 同步中取消此次操作。'); else if (result?.apple_unchanged) setNotice('已停止导入并删除本地分类，Apple 内容未改动。'); return result }
  function inspect(record, remove, viewTree, trigger) {requestPanel(() => {if (trigger?.isConnected) panelTrigger.current=trigger.matches('button,[tabindex]') ? trigger : trigger.querySelector('button');setEditor(null);setError('');setDetails({record,remove,viewTree})})}
  function editRecord(record, trigger) {requestPanel(() => {if (trigger?.isConnected) panelTrigger.current=trigger.matches('button,[tabindex]') ? trigger : trigger.querySelector('button');if (kind !== 'task' || !details || recordId(details.record) !== recordId(record)) setDetails(null);void loadEditor(record)})}
  async function loadEditor(record) {
    if (record.kind==='task' && !data.records.some(item => item.kind==='task' && item.id===recordId(record))) {
      try {
        const raw=await unwrap(api.business({domain:'tasks',method:'get_todo',arguments:{todo_id:recordId(record)}}))
        edit({...record,id:raw.id,title:raw.title,details:raw.notes,due:raw.due_on,business:raw})
      } catch (failure) { setError(failure.message) }
    } else edit(recordEditTarget(record,data.records))
  }
  async function copyRecord(record) {
    setError(''); setNotice('')
    try { await navigator.clipboard.writeText(recordClipboardText(record)); setNotice('已拷贝文本') }
    catch { setError('拷贝未完成，请检查剪贴板访问后重试。') }
  }
  function moveRecord(record, category_id) {
    if (record.kind==='event' && recordRepeats(record)) {
      const selected=recordEditTarget(record,data.records)
      requestPanel(() => {setDetails(null);edit({...selected,business:{...selected.business,category_id}})})
      return
    }
    return record.kind==='task' ? mutate('tasks','set_todo_category',{todo_id:recordId(record),category_id},'清单已更新') : mutate('calendar','set_event_category',{id:recordId(record),category_id},'日历已更新')
  }
  const completeRecord = record => mutate('tasks','complete_todo',taskCompletion(record.kind==='task' && record.id.startsWith('task:') ? {...record,id:record.business.id} : record),'事务状态已更新')

  function toggleArchive(record) { return run(() => api.archive({ id: record.id, expectedRevision: record.revision, archived: !record.archived }), record.archived ? '已恢复' : '已归档') }
  function download(content, name, type) {
    const link = document.createElement('a'); const url = URL.createObjectURL(new Blob([content], { type }))
    link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url)
  }
  const primary = kind === 'task' ? '新建事务' : null
  const editorKind = editor?.id ? editor.kind : kind === 'event' ? creationType : kind
  function newItem() {
    if (kind === 'task' || kind === 'event') create()

  }
  const editorContent = <>
      {kind === 'event' && editor !== null && !editor.id && <SegmentedControl id="calendar-create-type" label="创建类型" value={creationType} onChange={setCreationType} options={[{value:'event',label:'日程'}, {value:'task',label:'事务'}]} />}
      {editor !== null && <>
        {(kind === 'task' || kind === 'event' && (!editor.id || editor.kind === 'task')) && <div hidden={editorKind !== 'task'}><TaskEditor key={editor.id ?? 'new-task'} record={editor.id ? editor : undefined} draft={editor} calendar={kind === 'event'} categories={taskCategories} busy={busy} onDirty={() => setEditorDirty(true)} cancel={() => requestPanel(() => setEditor(null))} save={async payload => { if (await mutate('tasks', editor.id ? 'update_todo' : 'create_todo', editor.id ? { todo_id: editor.id, payload } : { payload })) setEditor(null) }} /></div>}
        {kind === 'event' && (!editor.id || editor.kind === 'event') && <div hidden={editorKind !== 'event'}><CalendarEditor key={editor.id ?? 'new-event'} record={editor.id ? editor : undefined} draft={editor} categories={categories} busy={busy} onDirty={() => setEditorDirty(true)} cancel={() => requestPanel(() => setEditor(null))} save={async payload => { if (await mutate('calendar', editor.id ? 'update_event' : 'create_event', editor.id ? { id: editor.id, patch: payload } : payload)) setEditor(null) }} /></div>}
        {kind === 'bill' && <BillEditor key={editor.id ?? 'new'} record={editor.id ? editor : undefined} busy={busy} cancel={() => requestPanel(() => setEditor(null))} save={async payload => { if (await mutate('bills', editor.id ? 'update_ledger' : 'create_ledger', editor.id ? { id: editor.id, patch: payload, expectedRevision: editor.business.revision } : payload)) setEditor(null) }} remove={async () => { if (billDeleteInFlight.current) return; billDeleteInFlight.current = true; const epoch = billDeleteEpoch.current; try { const result = await run(async () => {
          const preview = await unwrap(api.preparePageDelete({ domain: 'bills', kind: 'item', id: editor.id, expectedRevision: editor.business.revision }))
          if (epoch !== billDeleteEpoch.current) { void api.cancelPageDelete({ ticket: preview.ticket }).catch(() => {}); throw new Error('账单已切换，请重新确认。') }
          pendingBillDelete.current = preview.ticket
          try { return await api.commitPageDelete({ ticket: preview.ticket }) }
          finally { if (pendingBillDelete.current === preview.ticket) pendingBillDelete.current = null }
        }, '已删除'); if (result) setEditor(null) } finally { billDeleteInFlight.current = false } }} archive={async () => { if (await toggleArchive(editor)) setEditor(null) }} />}
      </>}
      {error && <Notice tone="error">{error}</Notice>}
  </>
  const panelOpen = ['event','task'].includes(kind) && (details !== null || editor !== null)
  const taskPageProps = {commitDelete,records,categories:taskCategories,select:(record,remove,trigger) => record ? inspect(record,remove,undefined,trigger) : requestPanel(() => {setEditor(null);setDetails(null)}),copy:copyRecord,move:moveRecord,edit:editRecord,toggle:completeRecord,mutate:(method,args) => mutate('tasks',method,args,method === 'delete_todo' ? '已删除' : '已保存'),api,reloadKey,busy,loading,quickChat,error}
  const taskDetail = details?.record.kind === 'task' ? data.records.find(item => item.kind === 'task' && item.id === recordId(details.record)) ?? details.record : null
  const panelTitle = editor !== null ? `${editor.id ? '编辑' : '新增'}${titles[editorKind]}` : details?.record.kind === 'task' ? '事务详情' : '日程详情'
  return <div className={`alcor-business${kind === 'bill' ? ' alcor-bills-page' : kind === 'task' ? ' alcor-task-business' : ''}`}>
    <header className="alcor-app-header"><div><h1>{current.label}</h1></div><div className="alcor-actions">
      <Button variant="outline" aria-label="刷新" title="刷新" onClick={() => { setError(''); void refresh() }}><IconRefreshOutlineRegular size={16} /></Button>
      {['event','task'].includes(kind) && <Button variant="outline" disabled={busy} onClick={openRecoveries}>恢复同步移除记录</Button>}
      {kind === 'event' && <><Button variant="outline" disabled={busy} onClick={() => setCalendarManage('categories')}>分类管理</Button></>}
      {kind === 'task' && <Button variant="outline" disabled={busy} onClick={() => setCalendarManage('task-categories')}>清单管理</Button>}
      {kind === 'event' && <Menu open={creationMenu} onClose={() => setCreationMenu(false)} portal autoFocus anchor={<Button variant="primary" aria-label="新建" aria-haspopup="menu" aria-expanded={creationMenu} onClick={() => setCreationMenu(!creationMenu)}><IconPlusOutlineRegular size={20} /></Button>}><MenuItemButton onSelect={() => create(undefined, 9, 'event')}>新建日程</MenuItemButton><MenuItemButton onSelect={() => create(undefined, 9, 'task')}>新建事务</MenuItemButton></Menu>}
      {primary && <Button variant="primary" icon={<IconPlusOutlineRegular size={16} />} onClick={newItem}>{primary}</Button>}
    </div></header>
    <div className="alcor-page-feedback">{loading && <Notice role="status">正在读取已保存记录…</Notice>}{error && <Notice tone="error" role="alert" className="alcor-error">{error}</Notice>}{notice && <Notice tone="success" role="status">{notice}</Notice>}</div>
    {['event','task'].includes(kind) && <div className={`alcor-calendar-split${kind === 'task' ? ' alcor-task-split' : ''}`} data-panel-open={panelOpen || undefined}>
      {kind === 'task' && <div className="alcor-task-workspace"><TasksPage {...taskPageProps} selectedRecord={editor ?? details?.record} /></div>}
      {kind === 'event' && <CalendarPage commitDelete={commitDelete} selectedRecord={editor ?? details?.record} archive={toggleArchive} archived={false} templates={data.records.filter(item => ['event', 'task'].includes(item.kind))} taskCategories={taskCategories} toggle={completeRecord} edit={editRecord} inspect={inspect} copy={copyRecord} move={moveRecord} taskMutate={(method,args) => mutate('tasks',method,args)} create={create} busy={busy} loading={loading} quickChat={quickChat} api={api} mutate={(method, args) => mutate('calendar', method, args)} categories={categories} reloadKey={reloadKey} manage={calendarManage} setManage={setCalendarManage} />}
      {panelOpen && <OrganizerRecordPanel title={panelTitle} focusKey={`${editor ? 'edit' : 'view'}:${editor?.id ?? details?.record.id ?? 'new'}`} trigger={panelTrigger} editing={editor !== null} busy={busy} close={() => requestPanel(() => {setEditor(null);setDetails(null)})}>
        {editor !== null ? <div className="alcor-calendar-panel-editor">{editorContent}</div> : kind === 'task' ? <TasksPage {...taskPageProps} detailRecord={taskDetail} /> : <OrganizerDetails inline value={details} groups={details?.record.kind === 'task' ? taskCategories : categories} busy={busy} close={() => setDetails(null)} edit={editRecord} complete={completeRecord} error={error} />}
      </OrganizerRecordPanel>}
    </div>}
    {kind === 'bill' && <BillsPage records={data.records.filter(item => item.kind === 'bill')} api={api} reloadKey={reloadKey} edit={edit} create={() => create()} archive={toggleArchive} download={download} busy={busy} loading={loading} icon={<BillIcon size={32} />} />}
    <Modal open={!!recovery} title="恢复同步移除记录" closeLabel="关闭恢复记录" onClose={() => { if (!busy) { recoveryEpoch.current++; setRecovery(null) } }} className="alcor-editor-dialog">
      <Notice>恢复为新的本地副本，不会自动同步到 Apple。提醒默认暂停；外部依赖和简报关联可能需要重新设置。</Notice>
      {!recovery?.rows.length && <p>没有可恢复记录。</p>}
      <ul>{recovery?.rows.map(row => <li key={row.id}><strong>{row.title || '未命名记录'}</strong> <span>{row.created_at}</span> {row.status === 'available' ? <Button variant="outline" disabled={busy} onClick={() => restoreRecovery(row)}>恢复本地副本</Button> : <span>已恢复</span>}</li>)}</ul>
    </Modal>
    <Modal open={calendarManage === 'task-categories'} title="清单管理" closeLabel="关闭清单管理" className="alcor-category-dialog" onClose={() => {if (!busy) setCalendarManage(null)}}><Categories commitDelete={commitDelete} failure={error} api={api} categories={taskCategories} counts={Object.fromEntries(taskCategories.map(group => [group.id, data.records.filter(item => item.kind === 'task' && item.business.category_id === group.id).length]))} task busy={busy} mutate={(method, args) => mutate('tasks', method, method === 'cancel_apple_operation' ? args : method === 'create_category' ? {payload: args} : method === 'update_category' ? {category_id: args.id, payload: args.patch} : {category_id: args.id})} /></Modal>
    {!['event','task'].includes(kind) && <OrganizerDetails value={details} groups={details?.record.kind==='task' ? taskCategories : categories} busy={busy} close={() => setDetails(null)} edit={editRecord} complete={completeRecord} error={error} />}
    <Modal open={!['event','task'].includes(kind) && editor !== null} headless title={`${editor?.id ? '编辑' : '新增'}${titles[editorKind] ?? ''}`} onClose={() => { if (!busy) setEditor(null) }} className="alcor-editor-dialog">
      {editorContent}
    </Modal>
    <Modal open={!!pendingPanelAction} title="放弃未保存的修改？" onClose={() => setPendingPanelAction(null)}><Notice tone="warning">当前修改尚未保存。</Notice><div className="alcor-actions"><Button variant="outline" onClick={() => setPendingPanelAction(null)}>继续编辑</Button><Button variant="primary" onClick={() => {const action=pendingPanelAction.apply;setPendingPanelAction(null);setEditorDirty(false);action()}}>放弃修改</Button></div></Modal>
  </div>
}
