import { Notice } from './notice.jsx'
import { useDeletePreview, DeletePreview } from './delete-preview.jsx'
import { repeatLabel } from './task-frequency.js'
import React, { useEffect, useState } from 'react'
import { Button, Input, Modal, SegmentedControl, IconSearchOutlineRegular, IconCheckOutlineRegular, IconAlarmClockOutlineRegular, IconChevronDownOutlineRegular, IconPlusOutlineRegular, IconChecklistOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { OrganizerContextMenu, OrganizerDetails, useRecordContextMenu } from './organizer-details.jsx'
import { priorityLabels, recordId, recordPermissions, taskDeleteMessage } from './organizer-actions.js'
import { focusedTaskRecords } from './task-focus.js'
import { taskKey, taskTree } from './task-tree.js'
import { arrayValue } from './organizer-forms.js'
import { Empty } from './business-pages.jsx'
import { taskMatches, taskGroup, localDate, addDays, categorySources } from './presentation.js'

export const taskStatuses = { todo: '未完成', in_progress: '未完成', waiting: '未完成', blocked: '未完成', completed: '已完成', cancelled: '已取消' }
const views = [['all', '全部事务'], ['pending', '待办'], ['upcoming', '即将到期'], ['overdue', '逾期'], ['completed', '已完成']]
const groups = { overdue: '含逾期事务', today: '含今天到期事务', upcoming: '含即将到期事务', later: '含后续事务', noDate: '无日期', completed: '已完成', cancelled: '已取消' }

export function TasksPage({ commitDelete, records, edit, toggle, mutate, api, reloadKey, busy, loading, categories = [], select, detailRecord, selectedRecord, copy, move, error }) {
  const [removing, setRemoving] = useState(null)
  const [removeError, setRemoveError] = useState('')
  const deletePreview = useDeletePreview(api, 'tasks', 'item', removing?.id)
  const [view, setView] = useState('all')
  const context = useRecordContextMenu()
  const [search, setSearch] = useState('')
  const [list, setList] = useState('all')
  const [tag, setTag] = useState('all')
  const [sort, setSort] = useState('date')
  const [childTitle, setChildTitle] = useState('')
  const [quickTitle, setQuickTitle] = useState('')
  const [expansion, setExpansion] = useState({ scope: '', values: {} })
  const [addingChild, setAddingChild] = useState(false)
  const [occurrenceDay, setOccurrenceDay] = useState(localDate())
  const [occurrences, setOccurrences] = useState([])
  const [occurrenceError, setOccurrenceError] = useState('')
  const now = new Date()
  const current = detailRecord
  useEffect(() => {setChildTitle(''); setAddingChild(false)},[current?.id])
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
  const ordered = [...focused].sort((left, right) => sort === 'title' ? left.title.localeCompare(right.title, 'zh-CN') : sort === 'created' ? right.createdAt.localeCompare(left.createdAt) : (left.due || '9999').localeCompare(right.due || '9999') || left.createdAt.localeCompare(right.createdAt))
  const queryMatches = item => (tag === 'all' || tags(item).includes(tag)) && `${item.title} ${item.details}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
  const filtered = inList.filter(item => taskMatches(item, view, now) && queryMatches(item))
  const tree = taskTree(ordered, filtered, now)
  const scope = JSON.stringify([view, list, tag, search.trim()])
  const filtering = !!search.trim() || tag !== 'all' || ['upcoming', 'overdue', 'completed'].includes(view)
  const expanded = node => expansion.scope === scope && Object.hasOwn(expansion.values, node.key) ? expansion.values[node.key] : filtering || !node.matched
  function setExpanded(key, value) { setExpansion(state => ({ scope, values: { ...(state.scope === scope ? state.values : {}), [key]: value } })) }
  function open(id, trigger = document.activeElement) {
    const item = records.find(record => record.id === id)
    select(item, item ? () => requestDelete(item) : undefined, trigger)
    setChildTitle('')
  }
  const removeDialog = <Modal open={!!removing} title="删除事务" closeLabel="关闭" onClose={() => { if (!busy) setRemoving(null) }} className="alcor-editor-dialog"><div className="alcor-task-delete-confirm"><p>确定删除「{removing?.title}」？</p><Notice tone="warning">{removing && taskDeleteMessage(removing)}</Notice><DeletePreview state={deletePreview} />{removeError && <Notice tone="error">{removeError}</Notice>}<div className="alcor-actions"><Button variant="outline" disabled={busy} onClick={() => setRemoving(null)}>取消</Button><Button variant="primary" disabled={busy || !deletePreview.plan} onClick={async () => { if (await commitDelete(deletePreview.plan.ticket)) { setRemoving(null); if (current?.id === removing.id || selectedRecord && recordId(selectedRecord) === removing.id) open(null) } else setRemoveError('删除未完成，请先核对 Apple 和本地状态，勿重复提交。') }}>继续确认</Button></div></div></Modal>
  function requestDelete(item) { setRemoveError(''); setRemoving({...item,id:recordId(item)}) }
  const contextMenu = <OrganizerContextMenu context={context} groups={categories} busy={busy} inspect={item => open(recordId(item),context.target?.element)} edit={item => edit(item,context.target?.element)} complete={toggle} move={move} copy={copy} remove={requestDelete} />
  function revealUrgent(node, group) {
    const values = {}
    let target
    function reveal(branch) {
      values[branch.key] = true
      if (!target && branch !== node && branch.matched && taskGroup(branch.record, now) === group) target = branch.key
      branch.children.filter(child => child.urgency[group] || taskGroup(child.record, now) === group).forEach(reveal)
    }
    reveal(node)
    setExpansion(state => ({ scope, values: { ...(state.scope === scope ? state.values : {}), ...values } }))
    if (target) requestAnimationFrame(() => document.querySelector(`.alcor-task-workspace [data-tree-key="${CSS.escape(target)}"] .alcor-task-open`)?.focus())
  }
  function row(item, { node, depth = 0, content } = {}) {
    const contextOnly = node && !node.matched
    return <li key={taskKey(item)} className="alcor-task-node"><div className="alcor-task-card" style={{'--task-indent': `${Math.min(depth, 4) * 16}px`}} {...(!contextOnly ? context.trigger(item) : {})} data-task-id={item.id} data-tree-key={node?.key} data-context-only={contextOnly || undefined} data-depth={depth} data-record-selected={selectedRecord && recordId(selectedRecord) === recordId(item) || undefined} data-priority={item.business?.priority} data-group={taskGroup(item, now)} data-completed={item.status === 'done' || undefined}>
      <button type="button" className="alcor-task-open" aria-label={item.title} onClick={() => open(item.id)} />
      {node && <span className="alcor-task-disclosure">{node.children.length > 0 && <Button variant="ghost" size="sm" aria-label={`${expanded(node) ? '收起' : '展开'}子事务：${item.title}`} aria-expanded={expanded(node)} onClick={() => setExpanded(node.key, !expanded(node))}><IconChevronDownOutlineRegular size={20} /></Button>}</span>}
      {!contextOnly && <Button className="alcor-task-check" variant="ghost" aria-label={item.business?.repeat_frequency !== 'none' ? item.status === 'done' ? '重新打开本次' : '完成本次' : item.status === 'done' ? '重新打开' : '完成事务'} aria-pressed={item.status === 'done'} disabled={busy || item.archived || item.business?.occurrence_unavailable || item.business?.apple?.can_modify === false || !!item.business?.apple_pending?.length || item.business?.apple_history} onClick={() => toggle(item)}>{item.status === 'done' && <IconCheckOutlineRegular size={16} />}</Button>}
      <div className="alcor-task-copy"><h3 className={item.status === 'done' ? 'alcor-completed' : undefined}>{item.title}</h3>
        {!contextOnly && <TaskMetadata item={item} categories={categories} hideList={list === item.business?.category_id} />}
        {!contextOnly && item.business?.subtask_count > 0 && <small>已完成 {item.business.progress_completed}/{item.business.progress_total}</small>}
        {node && <div className="alcor-task-urgency">{['overdue', 'today'].map(group => node.urgency[group] > 0 && <Button key={group} variant="ghost" size="sm" data-urgency={group} aria-label={`${item.title}：${node.urgency[group]} 项子事务${group === 'overdue' ? '逾期' : '今天到期'}`} onClick={() => revealUrgent(node, group)}>{node.urgency[group]} 项{group === 'overdue' ? '逾期' : '今天到期'}</Button>)}</div>}
      </div>
      {!contextOnly && <Button variant="ghost" size="sm" className="alcor-task-more" aria-label={`更多操作：${item.title}`} aria-haspopup="menu" aria-expanded={(context.target?.record.business?.occurrence_id ?? context.target?.record.id) === (item.business?.occurrence_id ?? item.id)} disabled={busy || item.archived} onClick={event => context.open(event,item,true)}><IconChevronDownOutlineRegular size={16} /></Button>}
    </div>{content}</li>
  }
  function treeRow(node, depth = 0) {
    return row(node.record, { node, depth, content: expanded(node) && node.children.length > 0 && <div className="alcor-task-branch"><ul className="alcor-task-list">{node.children.map(child => treeRow(child, depth + 1))}</ul></div> })
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
      <div className="alcor-task-detail">
      <nav aria-label="事务路径" className="alcor-task-breadcrumbs"><Button variant="ghost" onClick={() => open(null)}>全部事务</Button>{ancestors.map(item => <React.Fragment key={item.id}><span aria-hidden="true">›</span><Button variant="ghost" onClick={() => open(item.id)}>{item.title}</Button></React.Fragment>)}<span aria-hidden="true">›</span><span aria-current="page">{current.title}</span></nav>
      {current.business?.subtask_count > 0 && <div className="alcor-task-progress"><span>子事务进度 {current.business.progress_completed}/{current.business.progress_total}</span><progress aria-label="子事务进度" value={current.business.progress_completed} max={current.business.progress_total || 1} /></div>}

      {current.business?.repeat_frequency !== 'none' && current.business?.source_type !== 'apple_reminder' && !current.business?.native_managed && <section className="alcor-task-group"><h2>重复事务</h2><FieldDate value={occurrenceDay} change={setOccurrenceDay} /><Notice>显示所选日期起 31 天，按每次发生分别完成。</Notice>{occurrenceError && <Notice tone="error">{occurrenceError}</Notice>}{occurrences.map(item => <div className="alcor-task-dependency" key={item.id}><span>{item.due_on} · {taskStatuses[item.status]}</span><Button variant="outline" disabled={!recordPermissions(current,busy).complete} onClick={() => void mutate('complete_todo', { todo_id: item.id, completed: item.status !== 'completed' })}>{item.status === 'completed' ? '重新打开本次' : '完成本次'}</Button></div>)}</section>}
      {(children.length > 0 || addingChild || current.business?.repeat_frequency !== 'none') ? <section className="alcor-task-group"><div className="alcor-calendar-period"><h2>子事务 <small>{children.length}</small></h2></div><div className="alcor-task-children" data-notice-only={current.business?.repeat_frequency !== 'none' && !children.length || undefined}><ul className="alcor-task-list">{children.map(item => row(item))}</ul>{current.business?.repeat_frequency === 'none' && <form className="alcor-task-add" onSubmit={async event => { event.preventDefault(); if (!childTitle.trim() || !recordPermissions(current,busy).edit) return; const result = await mutate('add_subtask', { parent_id: current.id, payload: { title: childTitle.trim() } }); if (result) setChildTitle('') }}><Input autoFocus={addingChild} aria-label="子事务标题" placeholder="添加下一步…" value={childTitle} onChange={event => setChildTitle(event.target.value)} disabled={!recordPermissions(current,busy).edit} /><Button type="submit" variant="primary" disabled={!recordPermissions(current,busy).edit || !childTitle.trim()}>添加子事务</Button></form>}{current.business?.repeat_frequency !== 'none' && <Notice>重复事务按日期完成，不支持子事务。</Notice>}</div></section> : <Button className="alcor-task-add-entry" variant="ghost" icon={<IconPlusOutlineRegular size={15} />} disabled={!recordPermissions(current,busy).edit} onClick={() => setAddingChild(true)}>添加子事务</Button>}

      </div>
      </OrganizerDetails>
      {contextMenu}{removeDialog}
    </>
  }
  return <div className="alcor-tasks-page alcor-page-content">
    <aside className="alcor-task-sidebar alcor-organizer-sidebar"><nav aria-label="事务清单">
      <Button variant="ghost" className="alcor-task-all-lists" aria-label="全部清单" aria-pressed={list === 'all'} onClick={() => setList('all')}><IconChecklistOutlineRegular size={16} /><span>全部清单</span><small aria-hidden="true">{focused.length}</small></Button>
      {categorySources(categories).map(source => <section className="alcor-organizer-source" key={source.id} role="group" aria-label={source.name}><h3>{source.name}</h3><ul>{source.items.map(group => <li className="alcor-organizer-source-row" key={group.id}><Button variant="ghost" aria-label={group.display_name ?? group.name} title={`${group.display_name ?? group.name} · ${source.name}`} aria-pressed={list === group.id} onClick={() => setList(group.id)}><span className="alcor-task-list-icon" style={{background:group.color}}><IconChecklistOutlineRegular size={12} /></span><span>{group.display_name ?? group.name}</span><small aria-hidden="true">{focused.filter(item => item.business?.category_id === group.id).length}</small></Button></li>)}</ul></section>)}
    </nav></aside>
    <div className="alcor-task-main">
      <div className="alcor-task-list-heading"><h2>{selectedList?.display_name ?? selectedList?.name ?? '全部事务'}</h2><span>{filtered.length} 项</span></div>
      <SegmentedControl className="alcor-task-tabs" id="task-view" label="事务视图" value={view} onChange={setView} options={views.map(([value, label]) => ({ value, label: <span className="alcor-tab-label">{label}<span className="alcor-tab-count">{inList.filter(item => taskMatches(item, value, now) && queryMatches(item)).length}</span></span> }))} />
      <div className="alcor-task-filters"><Input icon={<IconSearchOutlineRegular size={16} />} aria-label="搜索事务" placeholder="搜索事务" value={search} onChange={event => setSearch(event.target.value)} /><select aria-label="标签筛选" value={tag} onChange={event => setTag(event.target.value)}><option value="all">全部标签</option>{[...new Set(records.flatMap(tags))].map(name => <option key={name}>{name}</option>)}</select><select aria-label="事务排序" value={sort} onChange={event => setSort(event.target.value)}><option value="date">按日期</option><option value="title">按名称</option><option value="created">按创建时间</option></select></div>
      <form className="alcor-task-quick-add" aria-label="快速添加事务" onSubmit={async event => {event.preventDefault(); if (!quickTitle.trim() || busy || listBlocked) return; const saved = await mutate('create_todo',{payload:{title:quickTitle.trim(),...(selectedList ? {category_id:selectedList.id} : {})}}); if (saved) setQuickTitle('')}}><Input icon={<IconPlusOutlineRegular size={17} />} aria-label="快速添加事务" placeholder={listBlocked ? selectedList?.apple_pending?.length ? '清单有待处理操作' : '此清单只读，请在 Apple 中编辑' : '添加事务…'} value={quickTitle} onChange={event => setQuickTitle(event.target.value)} disabled={busy || listBlocked} /><Button type="submit" variant="ghost" size="sm" disabled={busy || !quickTitle.trim() || listBlocked}>添加事务</Button></form>
      <div id={`task-view-${view}-panel`} role="tabpanel" aria-labelledby={`task-view-${view}`}>{Object.entries(groups).map(([group, label]) => { const rows = tree.filter(node => node.group === group); return rows.length > 0 && <section className="alcor-task-group" data-group={group} key={group}><h2>{label} <small>{rows.length} 组</small></h2><ul className="alcor-task-list">{rows.map(node => treeRow(node))}</ul></section> })}{!loading && !filtered.length && <Empty mascot={!error && !records.length} icon={<IconCheckOutlineRegular size={26} />} title={records.length ? view === 'pending' && !search && list === 'all' && tag === 'all' ? '暂无待办' : '没有符合条件的事务' : '还没有事务'} />}</div>
    </div>
    {contextMenu}{removeDialog}
  </div>
}

function FieldDate({ value, change }) { return <label className="alcor-field"><span>重复事务开始日期</span><Input type="date" required value={value} onChange={event => { if (event.target.value) change(event.target.value) }} /></label> }

function TaskMetadata({ item, showStatus = true, categories = [], hideList = false }) {
  const status = item.business?.status ?? (item.status === 'done' ? 'completed' : 'todo')
  return <div className="alcor-task-metadata">{showStatus && status === 'cancelled' && <span className="alcor-task-badge" data-status={status}>{taskStatuses[status]}</span>}{item.business?.repeat_frequency && item.business.repeat_frequency !== 'none' && <span className="alcor-task-badge">{repeatLabel(item.business)}</span>}{item.due && <span className="alcor-task-badge" data-kind="due"><IconAlarmClockOutlineRegular size={13} /><time aria-label="截止时间" dateTime={item.business?.due_time ? `${item.due}T${item.business.due_time}` : item.due}>{item.due}{item.business?.due_time && ` ${item.business.due_time}`}</time></span>}{!hideList && item.business?.list_name && <span className="alcor-task-badge" data-kind="list" aria-label={`清单：${item.business.list_name}`}><i className="alcor-task-list-dot" style={{background:categories.find(group => group.id === item.business?.category_id)?.color}} />{item.business.list_name}</span>}{item.business?.priority > 0 && <span className="alcor-task-badge" data-priority={item.business.priority}>{priorityLabels[item.business.priority]}优先级</span>}</div>
}
