import { Notice } from './notice.jsx'
import { repeatLabel } from './task-frequency.js'
import React, { useEffect, useState } from 'react'
import { Button, Input, Modal, SegmentedControl, IconSearchOutlineRegular, IconCheckOutlineRegular, IconAlarmClockOutlineRegular, IconChevronDownOutlineRegular, IconPlusOutlineRegular, IconChecklistOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { OrganizerContextMenu, OrganizerDetails, useRecordContextMenu } from './organizer-details.jsx'
import { priorityLabels, recordId, recordPermissions, taskDeleteMessage } from './organizer-actions.js'
import { focusedTaskRecords } from './task-focus.js'
import { arrayValue } from './organizer-forms.js'
import { AgentStrip, Empty } from './business-pages.jsx'
import { taskMatches, taskGroup, localDate, addDays, categorySources } from './presentation.js'

export const taskStatuses = { todo: '未完成', in_progress: '未完成', waiting: '未完成', blocked: '未完成', completed: '已完成', cancelled: '已取消' }
const views = [['all', '全部事务'], ['pending', '待办'], ['upcoming', '即将到期'], ['overdue', '逾期'], ['completed', '已完成']]
const groups = { overdue: '逾期', today: '今天到期', upcoming: '即将到期', later: '后续', noDate: '无日期', completed: '已完成', cancelled: '已取消' }

export function TasksPage({ records, edit, toggle, mutate, api, reloadKey, busy, loading, quickChat, categories = [], select, detailRecord, selectedRecord, copy, move, error }) {
  const [removing, setRemoving] = useState(null)
  const [removeError, setRemoveError] = useState('')
  const [view, setView] = useState('all')
  const context = useRecordContextMenu()
  const [search, setSearch] = useState('')
  const [list, setList] = useState('all')
  const [tag, setTag] = useState('all')
  const [sort, setSort] = useState('date')
  const [childTitle, setChildTitle] = useState('')
  const [quickTitle, setQuickTitle] = useState('')
  const [occurrenceDay, setOccurrenceDay] = useState(localDate())
  const [occurrences, setOccurrences] = useState([])
  const [occurrenceError, setOccurrenceError] = useState('')
  const now = new Date()
  const current = detailRecord
  useEffect(() => {setChildTitle('')},[current?.id])
  useEffect(() => {
    let disposed = false
    setOccurrences([]); setOccurrenceError('')
    if (current?.business?.repeat_frequency && current.business.repeat_frequency !== 'none' && current.business.source_type !== 'apple_reminder') {
      api.business({ domain: 'tasks', method: 'list_todos', arguments: { start: occurrenceDay, end: addDays(occurrenceDay, 31) } }).then(result => {
        if (disposed) return
        if (!result.ok) throw new Error(result.error?.message || '重复事务读取失败。')
        setOccurrences(result.value.filter(item => item.series_id === current.id))
      }).catch(error => { if (!disposed) setOccurrenceError(error.message) })
    }
    return () => { disposed = true }
  }, [api, current?.id, current?.updatedAt, current?.business?.repeat_frequency, occurrenceDay, reloadKey])
  const tags = item => arrayValue(item.business?.tags)
  const focused = focusedTaskRecords(records)
  const selectedList = categories.find(group => group.id === list)
  const listBlocked = selectedList?.apple?.can_modify === false || !!selectedList?.apple_pending?.length
  const inList = focused.filter(item => list === 'all' || item.business?.category_id === list)
  useEffect(() => { if (list !== 'all' && !selectedList) setList('all') }, [list, selectedList])
  const filtered = inList.filter(item => taskMatches(item, view, now) && (tag === 'all' || tags(item).includes(tag)) && `${item.title} ${item.details}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).sort((left, right) => sort === 'title' ? left.title.localeCompare(right.title, 'zh-CN') : sort === 'created' ? right.createdAt.localeCompare(left.createdAt) : (left.due || '9999').localeCompare(right.due || '9999') || left.createdAt.localeCompare(right.createdAt))
  const visible = filtered.filter(item => search || list !== 'all' || tag !== 'all' || !filtered.some(parent => parent.id === item.business?.parent_id))
  function open(id, trigger = document.activeElement) {
    const item = records.find(record => record.id === id)
    select(item, item ? () => requestDelete(item) : undefined, trigger)
    setChildTitle('')
  }
  const removeDialog = <Modal open={!!removing} title="删除事务" closeLabel="关闭" onClose={() => { if (!busy) setRemoving(null) }} className="stella-editor-dialog"><div className="stella-task-delete-confirm"><p>确定删除「{removing?.title}」？</p><Notice tone="warning">{removing && taskDeleteMessage(removing)}</Notice>{removeError && <Notice tone="error">{removeError}</Notice>}<div className="stella-actions"><Button variant="outline" disabled={busy} onClick={() => setRemoving(null)}>取消</Button><Button variant="primary" disabled={busy} onClick={async () => { if (await mutate('delete_todo', { todo_id: removing.id })) { setRemoving(null); if (current?.id === removing.id || selectedRecord && recordId(selectedRecord) === removing.id) open(null) } else setRemoveError('删除未完成，请关闭后核对错误或刷新重试。') }}>确认删除</Button></div></div></Modal>
  function requestDelete(item) { setRemoveError(''); setRemoving({...item,id:recordId(item)}) }
  const contextMenu = <OrganizerContextMenu context={context} groups={categories} busy={busy} inspect={item => open(recordId(item),context.target?.element)} edit={item => edit(item,context.target?.element)} complete={toggle} move={move} copy={copy} remove={requestDelete} />
  function row(item) {
    return <li key={item.business?.occurrence_id ?? item.id} className="stella-task-card" {...context.trigger(item)} data-task-id={item.id} data-record-selected={selectedRecord && recordId(selectedRecord) === recordId(item) || undefined} data-priority={item.business?.priority} data-group={taskGroup(item, now)} data-completed={item.status === 'done' || undefined}>
      <button type="button" className="stella-task-open" aria-label={item.title} onClick={() => open(item.id)} />
      <Button className="stella-task-check" variant="ghost" aria-label={item.business?.repeat_frequency !== 'none' ? item.status === 'done' ? '重新打开本次' : '完成本次' : item.status === 'done' ? '重新打开' : '完成事务'} aria-pressed={item.status === 'done'} disabled={busy || item.archived || item.business?.occurrence_unavailable || item.business?.apple?.can_modify === false || !!item.business?.apple_pending?.length || item.business?.apple_history} onClick={() => toggle(item)}>{item.status === 'done' && <IconCheckOutlineRegular size={16} />}</Button>
      <div className="stella-task-copy"><h3 className={item.status === 'done' ? 'stella-completed' : undefined}>{item.title}</h3><p className="stella-task-note">{item.details}</p><TaskMetadata item={item} categories={categories} hideList={list === item.business?.category_id} />{item.business?.subtask_count > 0 && <small>子事务 {item.business.progress_completed}/{item.business.progress_total}</small>}</div>
      <Button variant="ghost" size="sm" className="stella-task-more" aria-label={`更多操作：${item.title}`} aria-haspopup="menu" aria-expanded={(context.target?.record.business?.occurrence_id ?? context.target?.record.id) === (item.business?.occurrence_id ?? item.id)} disabled={busy || item.archived} onClick={event => context.open(event,item,true)}><IconChevronDownOutlineRegular size={16} /></Button>
    </li>
  }

  if (current) {
    const currentAction = focusedTaskRecords([current]).find(item => item.status !== 'done') ?? focusedTaskRecords([current])[0]
    const ancestors = []
    const seen = new Set([current.id])
    let parent = records.find(item => item.id === current.business?.parent_id)
    while (parent && !seen.has(parent.id)) { ancestors.unshift(parent); seen.add(parent.id); parent = records.find(item => item.id === parent.business?.parent_id) }
    const children = records.filter(item => item.business?.parent_id === current.id).sort((left, right) => (left.business?.sort_order ?? 0) - (right.business?.sort_order ?? 0))
    return <>
      <OrganizerDetails inline closeOnEdit={false} closeOnComplete={false} closeOnDelete={false} value={{record:currentAction ?? current,remove:() => requestDelete(current)}} groups={categories} busy={busy} close={() => open(null)} edit={edit} complete={toggle} error={error}>
      <div className="stella-task-detail">
      <nav aria-label="事务路径" className="stella-task-breadcrumbs"><Button variant="ghost" onClick={() => open(null)}>全部事务</Button>{ancestors.map(item => <React.Fragment key={item.id}><span aria-hidden="true">›</span><Button variant="ghost" onClick={() => open(item.id)}>{item.title}</Button></React.Fragment>)}<span aria-hidden="true">›</span><span aria-current="page">{current.title}</span></nav>
      {current.business?.subtask_count > 0 && <div className="stella-task-progress"><span>子事务进度 {current.business.progress_completed}/{current.business.progress_total}</span><progress aria-label="子事务进度" value={current.business.progress_completed} max={current.business.progress_total || 1} /></div>}

      {current.business?.repeat_frequency !== 'none' && current.business?.source_type !== 'apple_reminder' && !current.business?.native_managed && <section className="stella-task-group"><h2>重复事务</h2><FieldDate value={occurrenceDay} change={setOccurrenceDay} /><Notice>显示所选日期起 31 天，按每次发生分别完成。</Notice>{occurrenceError && <Notice tone="error">{occurrenceError}</Notice>}{occurrences.map(item => <div className="stella-task-dependency" key={item.id}><span>{item.due_on} · {taskStatuses[item.status]}</span><Button variant="outline" disabled={!recordPermissions(current,busy).complete} onClick={() => void mutate('complete_todo', { todo_id: item.id, completed: item.status !== 'completed' })}>{item.status === 'completed' ? '重新打开本次' : '完成本次'}</Button></div>)}</section>}
      <section className="stella-task-group"><div className="stella-calendar-period"><h2>子事务 <small>{children.length}</small></h2></div><div className="stella-task-children" data-notice-only={current.business?.repeat_frequency !== 'none' && !children.length || undefined}><ul className="stella-task-list">{children.map(row)}</ul>{current.business?.repeat_frequency === 'none' && <form className="stella-task-add" onSubmit={async event => { event.preventDefault(); const result = await mutate('add_subtask', { parent_id: current.id, payload: { title: childTitle.trim() } }); if (result) setChildTitle('') }}><Input aria-label="子事务标题" placeholder="添加下一步…" value={childTitle} onChange={event => setChildTitle(event.target.value)} disabled={!recordPermissions(current,busy).edit || current.business?.repeat_frequency !== 'none'} /><Button type="submit" variant="primary" disabled={!recordPermissions(current,busy).edit || !childTitle.trim() || current.business?.repeat_frequency !== 'none'}>添加子事务</Button></form>}{current.business?.repeat_frequency !== 'none' && <Notice>重复事务按日期完成，不支持子事务。</Notice>}</div></section>

      </div>
      </OrganizerDetails>
      {contextMenu}{removeDialog}
    </>
  }
  return <div className="stella-tasks-page stella-page-content">
    <aside className="stella-task-sidebar stella-organizer-sidebar"><nav aria-label="事务清单">
      <Button variant="ghost" className="stella-task-all-lists" aria-label="全部清单" aria-pressed={list === 'all'} onClick={() => setList('all')}><IconChecklistOutlineRegular size={16} /><span>全部清单</span><small aria-hidden="true">{focused.length}</small></Button>
      {categorySources(categories).map(source => <section className="stella-organizer-source" key={source.id} role="group" aria-label={source.name}><h3>{source.name}</h3><ul>{source.items.map(group => <li className="stella-organizer-source-row" key={group.id}><Button variant="ghost" aria-label={group.display_name ?? group.name} title={`${group.display_name ?? group.name} · ${source.name}`} aria-pressed={list === group.id} onClick={() => setList(group.id)}><span className="stella-task-list-icon" style={{background:group.color}}><IconChecklistOutlineRegular size={12} /></span><span>{group.display_name ?? group.name}</span><small aria-hidden="true">{focused.filter(item => item.business?.category_id === group.id).length}</small></Button></li>)}</ul></section>)}
    </nav></aside>
    <div className="stella-task-main">
      <div className="stella-task-list-heading"><h2>{selectedList?.display_name ?? selectedList?.name ?? '全部事务'}</h2><span>{inList.length} 项</span></div>
      <SegmentedControl className="stella-task-tabs" id="task-view" label="事务视图" value={view} onChange={setView} options={views.map(([value, label]) => ({ value, label: <span className="stella-tab-label">{label}<span className="stella-tab-count">{inList.filter(item => taskMatches(item, value, now)).length}</span></span> }))} />
      <div className="stella-task-filters"><Input icon={<IconSearchOutlineRegular size={16} />} aria-label="搜索事务" placeholder="搜索事务" value={search} onChange={event => setSearch(event.target.value)} /><select aria-label="标签筛选" value={tag} onChange={event => setTag(event.target.value)}><option value="all">全部标签</option>{[...new Set(records.flatMap(tags))].map(name => <option key={name}>{name}</option>)}</select><select aria-label="事务排序" value={sort} onChange={event => setSort(event.target.value)}><option value="date">按日期</option><option value="title">按名称</option><option value="created">按创建时间</option></select></div>
      <form className="stella-task-quick-add" aria-label="快速添加事务" onSubmit={async event => {event.preventDefault(); if (!quickTitle.trim() || busy || listBlocked) return; const saved = await mutate('create_todo',{payload:{title:quickTitle.trim(),...(selectedList ? {category_id:selectedList.id} : {})}}); if (saved) setQuickTitle('')}}><Input icon={<IconPlusOutlineRegular size={17} />} aria-label="快速添加事务" placeholder={listBlocked ? selectedList?.apple_pending?.length ? '清单等待 Apple 确认' : '此清单只读，请在 Apple 中编辑' : '添加事务…'} value={quickTitle} onChange={event => setQuickTitle(event.target.value)} disabled={busy || listBlocked} /><Button type="submit" variant="ghost" size="sm" disabled={busy || !quickTitle.trim() || listBlocked}>添加事务</Button></form>
      <div id={`task-view-${view}-panel`} role="tabpanel" aria-labelledby={`task-view-${view}`}>{Object.entries(groups).map(([group, label]) => { const rows = visible.filter(item => taskGroup(item, now) === group); return rows.length > 0 && <section className="stella-task-group" data-group={group} key={group}><h2>{label} <small>{rows.length}</small></h2><ul className="stella-task-list">{rows.map(row)}</ul></section> })}{!loading && !visible.length && <Empty icon={<IconCheckOutlineRegular size={26} />} title={records.length ? view === 'pending' && !search && list === 'all' && tag === 'all' ? '暂无待办' : '没有符合条件的事务' : '还没有事务'} />}</div>
      <AgentStrip kind="task" quickChat={quickChat} />
    </div>
    {contextMenu}{removeDialog}
  </div>
}

function FieldDate({ value, change }) { return <label className="stella-field"><span>重复事务开始日期</span><Input type="date" required value={value} onChange={event => { if (event.target.value) change(event.target.value) }} /></label> }

function TaskMetadata({ item, showStatus = true, categories = [], hideList = false }) {
  const status = item.business?.status ?? (item.status === 'done' ? 'completed' : 'todo')
  return <div className="stella-task-metadata">{showStatus && status === 'cancelled' && <span className="stella-task-badge" data-status={status}>{taskStatuses[status]}</span>}{item.business?.repeat_frequency && item.business.repeat_frequency !== 'none' && <span className="stella-task-badge">{repeatLabel(item.business)}</span>}{item.due && <span className="stella-task-badge" data-kind="due"><IconAlarmClockOutlineRegular size={13} /><time aria-label="截止时间" dateTime={item.business?.due_time ? `${item.due}T${item.business.due_time}` : item.due}>{item.due}{item.business?.due_time && ` ${item.business.due_time}`}</time></span>}{!hideList && item.business?.list_name && <span className="stella-task-badge" data-kind="list" aria-label={`清单：${item.business.list_name}`}><i className="stella-task-list-dot" style={{background:categories.find(group => group.id === item.business?.category_id)?.color}} />{item.business.list_name}</span>}{item.business?.priority > 0 && <span className="stella-task-badge" data-priority={item.business.priority}>{priorityLabels[item.business.priority]}优先级</span>}</div>
}
