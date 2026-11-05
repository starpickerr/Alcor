import React, { useEffect, useState } from 'react'
import { Button, Input, Modal, SegmentedControl, IconSearchOutlineRegular, IconCheckOutlineRegular, IconTrashOutlineRegular, IconAlarmClockOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { AgentStrip, Empty } from './business-pages.jsx'
import { taskMatches, taskGroup, localDate, addDays } from './presentation.js'

export const taskStatuses = { todo: '待处理', in_progress: '进行中', waiting: '等待中', blocked: '已阻塞', completed: '已完成', cancelled: '已取消' }
const views = [['all', '全部事务'], ['upcoming', '即将到期'], ['overdue', '逾期'], ['incomplete', '未完成'], ['completed', '已完成']]
const groups = { overdue: '逾期', today: '今天到期', upcoming: '即将到期', later: '后续', noDate: '无日期', completed: '已完成', cancelled: '已取消' }

export function TasksPage({ records, edit, toggle, mutate, api, reloadKey, busy, loading, quickChat }) {
  const [removing, setRemoving] = useState(null)
  const [removeError, setRemoveError] = useState('')
  const [view, setView] = useState('all')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [list, setList] = useState('all')
  const [tag, setTag] = useState('all')
  const [sort, setSort] = useState('date')
  const [selected, select] = useState(null)
  const [childTitle, setChildTitle] = useState('')
  const [occurrenceDay, setOccurrenceDay] = useState(localDate())
  const [occurrences, setOccurrences] = useState([])
  const [occurrenceError, setOccurrenceError] = useState('')
  const now = new Date()
  const current = records.find(item => item.id === selected)
  useEffect(() => {
    let disposed = false
    setOccurrences([]); setOccurrenceError('')
    if (current?.business?.repeat_frequency && current.business.repeat_frequency !== 'none') {
      api.business({ domain: 'tasks', method: 'list_todos', arguments: { start: occurrenceDay, end: addDays(occurrenceDay, 31) } }).then(result => {
        if (disposed) return
        if (!result.ok) throw new Error(result.error?.message || '重复事务读取失败。')
        setOccurrences(result.value.filter(item => item.series_id === current.id))
      }).catch(error => { if (!disposed) setOccurrenceError(error.message) })
    }
    return () => { disposed = true }
  }, [api, current?.id, current?.updatedAt, current?.business?.repeat_frequency, occurrenceDay, reloadKey])
  const nativeStatus = item => item.business?.status ?? (item.status === 'done' ? 'completed' : 'todo')
  const tags = item => Array.isArray(item.business?.tags) ? item.business.tags : JSON.parse(item.business?.tags || '[]')
  const filtered = records.filter(item => taskMatches(item, view, now) && (status === 'all' || nativeStatus(item) === status) && (list === 'all' || item.business?.list_name === list) && (tag === 'all' || tags(item).includes(tag)) && `${item.title} ${item.details}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).sort((left, right) => sort === 'title' ? left.title.localeCompare(right.title, 'zh-CN') : sort === 'created' ? right.createdAt.localeCompare(left.createdAt) : (left.due || '9999').localeCompare(right.due || '9999') || left.createdAt.localeCompare(right.createdAt))
  const visible = filtered.filter(item => search || status !== 'all' || list !== 'all' || tag !== 'all' || view !== 'all' || !records.some(parent => parent.id === item.business?.parent_id))
  function open(id) { select(id); setChildTitle('') }
  const removeDialog = <Modal open={!!removing} title="删除事务" closeLabel="关闭" onClose={() => { if (!busy) setRemoving(null) }} className="stella-editor-dialog"><div className="stella-task-delete-confirm"><p>确定删除「{removing?.title}」？</p><p className="stella-muted">{removing?.business?.repeat_frequency !== 'none' ? '将删除整个重复系列及其记录，无法撤销。' : '将同时删除其子事务，无法撤销。'}</p>{removeError && <p role="alert">{removeError}</p>}<div className="stella-actions"><Button variant="outline" disabled={busy} onClick={() => setRemoving(null)}>取消</Button><Button variant="primary" disabled={busy} onClick={async () => { if (await mutate('delete_todo', { todo_id: removing.id })) { setRemoving(null); if (selected === removing.id) open(null) } else setRemoveError('删除未完成，请关闭后核对错误或刷新重试。') }}>确认删除</Button></div></div></Modal>
  function row(item) {
    return <li key={item.id} className="stella-task-card" data-task-id={item.id}>
      <button type="button" className="stella-task-open" aria-label={item.title} onClick={() => open(item.id)} />
      <Button className="stella-task-check" variant="ghost" aria-label={item.business?.repeat_frequency !== 'none' ? '查看重复事务' : item.status === 'done' ? '重新打开' : '完成事务'} aria-pressed={item.status === 'done'} disabled={busy || item.archived} onClick={() => item.business?.repeat_frequency !== 'none' ? open(item.id) : toggle(item)}>{item.status === 'done' && <IconCheckOutlineRegular size={16} />}</Button>
      <div className="stella-task-copy"><h3 className={item.status === 'done' ? 'stella-completed' : undefined}>{item.title}</h3><TaskMetadata item={item} />{item.business?.subtask_count > 0 && <small>子事务 {item.business.progress_completed}/{item.business.progress_total}</small>}</div>
      <Button variant="ghost" size="sm" className="stella-task-delete" aria-label={`删除事务：${item.title}`} title="删除事务" disabled={busy || item.archived} onClick={() => { setRemoveError(''); setRemoving(item) }}><IconTrashOutlineRegular size={16} /></Button>
    </li>
  }

  if (current) {
    const ancestors = []
    const seen = new Set([current.id])
    let parent = records.find(item => item.id === current.business?.parent_id)
    while (parent && !seen.has(parent.id)) { ancestors.unshift(parent); seen.add(parent.id); parent = records.find(item => item.id === parent.business?.parent_id) }
    const children = records.filter(item => item.business?.parent_id === current.id).sort((left, right) => (left.business?.sort_order ?? 0) - (right.business?.sort_order ?? 0))
    return <div className="stella-task-detail stella-page-content">
      <nav aria-label="事务路径" className="stella-task-breadcrumbs"><Button variant="ghost" onClick={() => open(null)}>事务树</Button>{ancestors.map(item => <React.Fragment key={item.id}><span aria-hidden="true">›</span><Button variant="ghost" onClick={() => open(item.id)}>{item.title}</Button></React.Fragment>)}<span aria-hidden="true">›</span><span aria-current="page">{current.title}</span></nav>
      <div className="stella-calendar-period"><div><h2>{current.title}</h2></div><Button variant="outline" onClick={() => open(null)}>返回事务树</Button></div>
      <section className="stella-task-detail-summary"><div className="stella-task-detail-toolbar"><select className="stella-task-status" data-status={nativeStatus(current)} aria-label="当前事务状态" value={nativeStatus(current)} disabled={busy || current.archived || current.business?.repeat_frequency !== 'none'} onChange={event => void mutate('change_todo_status', { todo_id: current.id, status: event.target.value })}>{Object.entries(taskStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><TaskMetadata item={current} showStatus={false} /><Button variant="outline" className="stella-task-detail-edit" disabled={busy || current.archived} onClick={() => edit(current)}>编辑详情</Button></div>{current.details && <p className="stella-task-description">{current.details}</p>}{current.business?.subtask_count > 0 && <div className="stella-task-progress"><span>子事务进度 {current.business.progress_completed}/{current.business.progress_total}</span><progress aria-label="子事务进度" value={current.business.progress_completed} max={current.business.progress_total || 1} /></div>}</section>
      {current.business?.repeat_frequency !== 'none' && <section className="stella-task-group"><h2>重复事务</h2><FieldDate value={occurrenceDay} change={setOccurrenceDay} /><p className="stella-muted">显示所选日期起 31 天，按每次发生分别完成。</p>{occurrenceError && <p role="alert">{occurrenceError}</p>}{occurrences.map(item => <div className="stella-task-dependency" key={item.id}><span>{item.due_on} · {taskStatuses[item.status]}</span><Button variant="outline" disabled={busy || current.archived} onClick={() => void mutate('complete_todo', { todo_id: item.id, completed: item.status !== 'completed' })}>{item.status === 'completed' ? '重新打开本次' : '完成本次'}</Button></div>)}</section>}
      <section className="stella-task-group"><div className="stella-calendar-period"><h2>子事务 <small>{children.length}</small></h2></div><div className="stella-task-children"><ul className="stella-task-list">{children.map(row)}</ul>{!children.length && <Empty title="还没有子事务"  />}<form className="stella-task-add" onSubmit={async event => { event.preventDefault(); const result = await mutate('add_subtask', { parent_id: current.id, payload: { title: childTitle.trim() } }); if (result) setChildTitle('') }}><Input aria-label="子事务标题" placeholder="添加下一步…" value={childTitle} onChange={event => setChildTitle(event.target.value)} disabled={busy || current.archived || current.business?.repeat_frequency !== 'none'} /><Button type="submit" variant="primary" disabled={busy || current.archived || !childTitle.trim() || current.business?.repeat_frequency !== 'none'}>添加子事务</Button></form>{current.business?.repeat_frequency !== 'none' && <p className="stella-muted">重复事务按日期完成，不支持子事务。</p>}</div></section>

      {removeDialog}
    </div>
  }
  return <div className="stella-tasks-page stella-page-content">
    <SegmentedControl className="stella-task-tabs" id="task-view" label="事务视图" value={view} onChange={setView} options={views.map(([value, label]) => ({ value, label: `${label} ${records.filter(item => taskMatches(item, value, now)).length}` }))} />
    <AgentStrip kind="task" quickChat={quickChat} />
    <div className="stella-task-filters"><Input icon={<IconSearchOutlineRegular size={16} />} aria-label="搜索事务" placeholder="搜索事务" value={search} onChange={event => setSearch(event.target.value)} /><select aria-label="清单筛选" value={list} onChange={event => setList(event.target.value)}><option value="all">全部清单</option>{[...new Set(records.map(item => item.business?.list_name).filter(Boolean))].map(name => <option key={name}>{name}</option>)}</select><select aria-label="标签筛选" value={tag} onChange={event => setTag(event.target.value)}><option value="all">全部标签</option>{[...new Set(records.flatMap(tags))].map(name => <option key={name}>{name}</option>)}</select><select aria-label="状态筛选" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option>{Object.entries(taskStatuses).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><select aria-label="事务排序" value={sort} onChange={event => setSort(event.target.value)}><option value="date">日期</option><option value="title">名称</option><option value="created">创建时间</option></select></div>
    <div id={`task-view-${view}-panel`} role="tabpanel" aria-labelledby={`task-view-${view}`}>{Object.entries(groups).map(([group, label]) => { const rows = visible.filter(item => taskGroup(item, now) === group); return rows.length > 0 && <section className="stella-task-group" key={group}><h2>{label} <small>{rows.length}</small></h2><ul className="stella-task-list">{rows.map(row)}</ul></section> })}{!loading && !visible.length && <Empty title={records.length ? '没有符合条件的事务' : '还没有事务'}  />}</div>
    {removeDialog}
  </div>
}

function FieldDate({ value, change }) { return <label className="stella-field"><span>重复事务开始日期</span><Input type="date" required value={value} onChange={event => { if (event.target.value) change(event.target.value) }} /></label> }

function TaskMetadata({ item, showStatus = true }) {
  const status = item.business?.status ?? (item.status === 'done' ? 'completed' : 'todo')
  return <div className="stella-task-metadata">{showStatus && <span className="stella-task-badge" data-status={status}>{taskStatuses[status]}</span>}{item.due && <span className="stella-task-badge"><IconAlarmClockOutlineRegular size={13} /><time aria-label="截止时间" dateTime={item.business?.due_time ? `${item.due}T${item.business.due_time}` : item.due}>{item.due}{item.business?.due_time && ` ${item.business.due_time}`}</time></span>}{item.business?.list_name && <span className="stella-task-badge" aria-label={`清单：${item.business.list_name}`}>{item.business.list_name}</span>}{item.business?.priority > 0 && <span className="stella-task-badge" data-priority={item.business.priority}>{['', '高', '中', '低'][item.business.priority]}优先级</span>}</div>
}
