import { Notice } from './notice.jsx'
import { useDeletePreview, DeletePreview } from './delete-preview.jsx'
import { appleOperationMessage } from './category-manager.jsx'
import { repeatLabel } from './task-frequency.js'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Input, Modal, SegmentedControl, IconSearchOutlineRegular, IconCheckOutlineRegular, IconAlarmClockOutlineRegular, IconChevronDownOutlineRegular, IconChevronRightOutlineRegular, IconPlusOutlineRegular, IconChecklistOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { OrganizerContextMenu, OrganizerDetails, useRecordContextMenu } from './organizer-details.jsx'
import { priorityLabels, recordId, recordPermissions, recordRepeatLabel, recordRepeats, taskDeleteMessage } from './organizer-actions.js'
import { focusedTaskRecords } from './task-focus.js'
import { taskKey, taskTree } from './task-tree.js'
import { arrayValue } from './organizer-forms.js'
import { Empty } from './business-pages.jsx'
import { taskMatches, taskGroup, taskDate, taskDeadline, taskToday, localDate, addDays, categorySources } from './presentation.js'

export const taskStatuses = { todo: '未完成', in_progress: '未完成', waiting: '未完成', blocked: '未完成', completed: '已完成', cancelled: '已取消' }
const views = [['today', '今天'], ['plan', '计划'], ['all', '全部事务'], ['completed', '已完成']]
const groups = { overdue: '含逾期', today: '含今天事务', upcoming: '近期', later: '稍后', noDate: '无日期', completed: '已完成', cancelled: '已取消' }
const dateLabel = day => new Date(`${day}T12:00`).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', ...(day.slice(0, 4) !== localDate().slice(0, 4) ? { year: 'numeric' } : {}) })
const relativeDay = (day, today) => day === today ? '今天' : day === addDays(today, 1) ? '明天' : dateLabel(day)

export function TasksPage({ commitDelete, records, edit, toggle, mutate, api, reloadKey, busy, loading, categories = [], select, detailRecord, selectedRecord, copy, move, error, view, setView }) {
  const [removing, setRemoving] = useState(null)
  const [removeError, setRemoveError] = useState('')
  const deletePreview = useDeletePreview(api, 'tasks', 'item', removing?.id)
  const context = useRecordContextMenu()
  const [search, setSearch] = useState('')
  const [list, setList] = useState('all')
  const [tag, setTag] = useState('all')
  const [sort, setSort] = useState('date')
  const [childTitle, setChildTitle] = useState('')
  const [quickTitle, setQuickTitle] = useState('')
  const [expansion, setExpansion] = useState({ scope: '', values: {} })
  const [addingChild, setAddingChild] = useState(false)
  const addChildButton = useRef(null)
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
  const ordered = [...focused].sort((left, right) => sort === 'title' ? left.title.localeCompare(right.title, 'zh-CN') : sort === 'created' ? right.createdAt.localeCompare(left.createdAt) : (taskDate(left) || '9999').localeCompare(taskDate(right) || '9999') || left.createdAt.localeCompare(right.createdAt))
  const queryMatches = item => (tag === 'all' || tags(item).includes(tag)) && `${item.title} ${item.details}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
  const filtered = inList.filter(item => taskMatches(item, view, now) && queryMatches(item))
  let sections
  if (view === 'today') {
    sections = Object.entries({ overdue: '已逾期', today: '今天待办', completed: '今天已完成' }).map(([group, label]) => ({
      group, label, rows: taskTree(ordered, filtered.filter(item => taskGroup(item, now) === group), now),
    }))
  } else if (view === 'plan') {
    sections = [...new Set(filtered.map(taskDate))].sort().map(day => ({
      group: day, label: relativeDay(day, localDate(now)), rows: taskTree(ordered, filtered.filter(item => taskDate(item) === day), now),
    }))
  } else {
    const tree = taskTree(ordered, filtered, now)
    sections = Object.entries(groups).map(([group, label]) => ({ group, label, rows: tree.filter(node => node.group === group) }))
  }
  const filtering = !!search.trim() || tag !== 'all' || view === 'completed'
  const scope = JSON.stringify([view, list, tag, search.trim()])
  const expanded = (node, group) => expansion.scope === scope && Object.hasOwn(expansion.values, `${group}:${node.key}`) ? expansion.values[`${group}:${node.key}`] : filtering || !node.matched
  function setExpanded(values) { setExpansion(state => ({ scope, values: { ...(state.scope === scope ? state.values : {}), ...values } })) }
  function open(target, trigger = document.activeElement) {
    const item = typeof target === 'string' ? records.find(record => record.id === target) : target
    select(item, item ? () => requestDelete(item) : undefined, trigger)
    setChildTitle('')
  }
  async function quickAdd(event) {
    event.preventDefault()
    if (!quickTitle.trim() || busy || listBlocked) return
    const planned = view === 'today' && !selectedList?.apple
    const saved = await mutate('create_todo', { payload: {
      title: quickTitle.trim(),
      ...(selectedList ? { category_id: selectedList.id } : {}),
      ...(planned ? { planned_on: localDate(now), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } : {}),
    } })
    if (saved) {
      setQuickTitle('')
      if (!planned || !saved.planned_on) setView('all')
    }
  }
  const removeDialog = <Modal open={!!removing} title="删除事务" closeLabel="关闭" onClose={() => { if (!busy) setRemoving(null) }} className="alcor-editor-dialog"><div className="alcor-task-delete-confirm"><p>确定删除「{removing?.title}」？</p><Notice tone="warning">{removing && taskDeleteMessage(removing)}</Notice><DeletePreview state={deletePreview} />{removeError && <Notice tone="error">{removeError}</Notice>}<div className="alcor-actions"><Button variant="outline" disabled={busy} onClick={() => setRemoving(null)}>取消</Button><Button variant="primary" disabled={busy || !deletePreview.plan} onClick={async () => { if (await commitDelete(deletePreview.plan.ticket)) { setRemoving(null); if (current?.id === removing.id || selectedRecord && recordId(selectedRecord) === removing.id) open(null) } else setRemoveError('删除未完成，请先核对 Apple 和本地状态，勿重复提交。') }}>继续确认</Button></div></div></Modal>
  function requestDelete(item) { setRemoveError(''); setRemoving({...item,id:recordId(item)}) }
  const contextMenu = <OrganizerContextMenu context={context} groups={categories} busy={busy} inspect={item => open(item,context.target?.element)} edit={item => edit(item,context.target?.element)} complete={toggle} move={move} copy={copy} remove={requestDelete} />
  function revealUrgent(node, group, section) {
    const values = {}
    let target
    function reveal(branch) {
      values[`${section}:${branch.key}`] = true
      if (!target && branch !== node && branch.matched && taskGroup(branch.record, now) === group) target = branch.key
      branch.children.filter(child => child.urgency[group] || taskGroup(child.record, now) === group).forEach(reveal)
    }
    reveal(node)
    setExpanded(values)
    if (target) requestAnimationFrame(() => document.querySelector(`.alcor-task-workspace [data-tree-key="${CSS.escape(target)}"] .alcor-task-open`)?.focus())
  }
  function row(item, { node, depth = 0, content, section, parentLink = false } = {}) {
    const contextOnly = node && !node.matched
    const actionable = !contextOnly && !parentLink
    const selected = selectedRecord && (selectedRecord.business?.occurrence_id ? taskKey(selectedRecord) === taskKey(item) : recordId(selectedRecord) === recordId(item))
    return <li key={taskKey(item)} className="alcor-task-node"><div className="alcor-task-card" style={{'--task-indent': `${Math.min(depth, 4) * 16}px`}} {...(actionable ? context.trigger(item) : {})} data-task-id={item.id} data-tree-key={node?.key} data-context-only={contextOnly || undefined} data-depth={depth} data-record-selected={selected || undefined} data-priority={item.business?.priority} data-group={taskGroup(item, now)} data-completed={item.status === 'done' || undefined}>
      <button type="button" className="alcor-task-open" aria-label={item.title} onClick={() => open(item)} />
      {node && <span className="alcor-task-disclosure">{node.children.length > 0 && <Button variant="ghost" size="sm" aria-label={`${expanded(node, section) ? '收起' : '展开'}子事务：${item.title}`} aria-expanded={expanded(node, section)} onClick={() => setExpanded({ [`${section}:${node.key}`]: !expanded(node, section) })}><IconChevronDownOutlineRegular size={20} /></Button>}</span>}
      {parentLink && <IconChecklistOutlineRegular className="alcor-task-parent-icon" size={18} aria-hidden="true" />}
      {actionable && <Button className="alcor-task-check" variant="ghost" aria-label={recordRepeats(item) ? item.status === 'done' ? '重新打开本次' : '完成本次' : item.status === 'done' ? '重新打开' : '完成事务'} aria-pressed={item.status === 'done'} disabled={!recordPermissions(item, busy).complete} onClick={() => toggle(item)}>{item.status === 'done' && <IconCheckOutlineRegular size={16} />}</Button>}
      <div className="alcor-task-copy"><h3>{item.title}</h3>
        {!contextOnly && <TaskMetadata item={item} now={now} view={view} categories={categories} hideList={list === item.business?.category_id} />}
        {!contextOnly && item.business?.subtask_count > 0 && <small>已完成 {item.business.progress_completed}/{item.business.progress_total}</small>}
        {node && <div className="alcor-task-urgency">{['overdue', 'today'].map(group => node.urgency[group] > 0 && <Button key={group} variant="ghost" size="sm" data-urgency={group} aria-label={`${item.title}：${node.urgency[group]} 项子事务${group === 'overdue' ? '逾期' : '今天'}`} onClick={() => revealUrgent(node, group, section)}>{node.urgency[group]} 项{group === 'overdue' ? '逾期' : '今天'}</Button>)}</div>}
      </div>
      {parentLink && <IconChevronRightOutlineRegular className="alcor-task-parent-arrow" size={16} aria-hidden="true" />}
    </div>{content}</li>
  }
  function treeRow(node, section, depth = 0) {
    return row(node.record, { node, section, depth, content: expanded(node, section) && node.children.length > 0 && <div className="alcor-task-branch"><ul className="alcor-task-list">{node.children.map(child => treeRow(child, section, depth + 1))}</ul></div> })
  }

  if (current) {
    const options = focusedTaskRecords([current])
    const currentAction = options.find(item => item.business?.occurrence_id && taskKey(item) === taskKey(selectedRecord ?? current)) ?? options.find(item => item.status !== 'done') ?? options[0]
    const parent = records.find(item => item.id === current.business?.parent_id && item.id !== current.id)
    const children = records.filter(item => item.business?.parent_id === current.id).sort((left, right) => (left.business?.sort_order ?? 0) - (right.business?.sort_order ?? 0))
    const repeating = recordRepeats(current)
    const canAddChild = recordPermissions(current, busy).edit
    function cancelAddChild() {
      setChildTitle('')
      setAddingChild(false)
      requestAnimationFrame(() => addChildButton.current?.focus())
    }
    async function saveChild(event) {
      event.preventDefault()
      if (!childTitle.trim() || !canAddChild) return
      const result = await mutate('add_subtask', { parent_id: current.id, payload: { title: childTitle.trim() } })
      if (result) setChildTitle('')
    }
    const addChild = addingChild ? <form className="alcor-task-add" onSubmit={saveChild}>
      <Input autoFocus className="alcor-task-add-input" aria-label="子事务标题" placeholder="添加下一步…" value={childTitle} onChange={event => setChildTitle(event.target.value)} disabled={!canAddChild} />
      <div className="alcor-task-add-actions">
        <Button type="button" variant="ghost" disabled={busy} onClick={cancelAddChild}>取消</Button>
        <Button type="submit" variant="primary" disabled={!canAddChild || !childTitle.trim()}>添加子事务</Button>
      </div>
    </form> : <Button ref={addChildButton} className="alcor-task-add-entry" variant="ghost" icon={<IconPlusOutlineRegular size={15} />} disabled={!canAddChild} onClick={() => setAddingChild(true)}>添加子事务</Button>
    const childCard = <div className="alcor-task-children" data-notice-only={repeating && !children.length || undefined}>
      {children.length > 0 && <ul className="alcor-task-list">{children.map(item => row(item))}</ul>}
      {repeating ? <Notice>重复事务按日期完成，不支持子事务。</Notice> : addChild}
    </div>
    return <>
      <OrganizerDetails inline closeOnEdit={false} closeOnComplete={false} closeOnDelete={false} value={{record:currentAction ?? current,remove:() => requestDelete(current)}} groups={categories} busy={busy} close={() => open(null)} edit={edit} complete={toggle} error={error}>
      <div className="alcor-task-detail">
      {current.business?.repeat_frequency !== 'none' && current.business?.source_type !== 'apple_reminder' && !current.business?.native_managed && <section className="alcor-task-group"><h2>重复事务</h2><FieldDate value={occurrenceDay} change={setOccurrenceDay} /><Notice>显示所选日期起 31 天，按每次发生分别完成。</Notice>{occurrenceError && <Notice tone="error">{occurrenceError}</Notice>}{occurrences.map(item => <div className="alcor-task-dependency" key={item.id}><span>{item.due_on} · {taskStatuses[item.status]}</span><Button variant="outline" disabled={!recordPermissions(current,busy).complete} onClick={() => void mutate('complete_todo', { todo_id: item.id, completed: item.status !== 'completed' })}>{item.status === 'completed' ? '重新打开本次' : '完成本次'}</Button></div>)}</section>}
      {parent && <nav className="alcor-task-group" aria-label="事务路径">
        <div className="alcor-calendar-period"><h2>父事务</h2></div>
        <div className="alcor-task-parent"><ul className="alcor-task-list">{row(parent, { parentLink: true })}</ul></div>
      </nav>}
      {(children.length > 0 || addingChild || repeating) ? <section className="alcor-task-group">
        <div className="alcor-calendar-period"><h2>子事务 <small>{children.length}</small></h2></div>
        {current.business?.subtask_count > 0 && <div className="alcor-task-progress"><span>子事务进度 {current.business.progress_completed}/{current.business.progress_total}</span><progress aria-label="子事务进度" value={current.business.progress_completed} max={current.business.progress_total || 1} /></div>}
        {childCard}
      </section> : childCard}

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
      <div className="alcor-task-list-heading"><h2>{selectedList?.display_name ?? selectedList?.name ?? views.find(([key]) => key === view)?.[1]}</h2>{view === 'today' && <time dateTime={localDate(now)}>{now.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })} · {now.toLocaleDateString('zh-CN', { weekday: 'short' })}</time>}{view !== 'today' && <span>{filtered.length} 项</span>}</div>
      <SegmentedControl className="alcor-task-tabs" id="task-view" label="事务视图" value={view} onChange={setView} options={views.map(([value, label]) => ({ value, label }))} />
      <div className="alcor-task-filters"><Input icon={<IconSearchOutlineRegular size={16} />} aria-label="搜索事务" placeholder="搜索事务" value={search} onChange={event => setSearch(event.target.value)} /><select aria-label="标签筛选" value={tag} onChange={event => setTag(event.target.value)}><option value="all">全部标签</option>{[...new Set(records.flatMap(tags))].map(name => <option key={name}>{name}</option>)}</select><select aria-label="事务排序" value={sort} onChange={event => setSort(event.target.value)}><option value="date">按日期</option><option value="title">按名称</option><option value="created">按创建时间</option></select></div>
      <form className="alcor-task-quick-add" aria-label="快速添加事务" onSubmit={quickAdd}><Input icon={<IconPlusOutlineRegular size={17} />} aria-label="快速添加事务" placeholder={listBlocked ? selectedList?.apple_pending?.length ? '清单有待处理操作' : '此清单只读，请在 Apple 中编辑' : '添加事务…'} value={quickTitle} onChange={event => setQuickTitle(event.target.value)} disabled={busy || listBlocked} /><Button type="submit" variant="ghost" size="sm" disabled={busy || !quickTitle.trim() || listBlocked}>添加事务</Button></form>
      <div id={`task-view-${view}-panel`} role="tabpanel" aria-labelledby={`task-view-${view}`}>
        {sections.map(({ group, label, rows }) => rows.length > 0 && <section className="alcor-task-group" data-group={group} key={group}><h2>{label} <small>{view === 'all' ? `${rows.length} 组` : `${rows.reduce((count, node) => count + node.matchCount, 0)} 项`}</small></h2><ul className="alcor-task-list">{rows.map(node => treeRow(node, group))}</ul></section>)}
        {!loading && !filtered.length && <Empty mascot={!error && !records.length} icon={<IconCheckOutlineRegular size={26} />} title={!records.length ? '还没有事务' : search.trim() || tag !== 'all' ? '没有符合条件的事务' : ({ today: '今天暂无事务', plan: '暂无后续计划', completed: '暂无已完成事务' }[view] || '此清单暂无事务')} />}
      </div>
    </div>
    {contextMenu}{removeDialog}
  </div>
}

function FieldDate({ value, change }) { return <label className="alcor-field"><span>重复事务开始日期</span><Input type="date" required value={value} onChange={event => { if (event.target.value) change(event.target.value) }} /></label> }

function TaskMetadata({ item, now, view, categories, hideList }) {
  const raw = item.business ?? {}
  const done = raw.status === 'completed'
  const repeating = recordRepeats(item)
  const today = taskToday(item, now)
  const deadline = taskDeadline(item)
  const day = taskDate(item)
  const next = raw.current_occurrence?.due_on
  const showDate = !done || view !== 'today'
  const repeat = raw.native_recurrence?.frequency && raw.native_recurrence.frequency !== 'none' ? recordRepeatLabel(raw) : `${repeatLabel(raw) ?? ''}重复`
  return <div className="alcor-task-metadata">
    {raw.status === 'cancelled' && <span className="alcor-task-badge" data-status="cancelled">已取消</span>}
    {done && <span className="alcor-task-badge" data-status="completed">{repeating ? '本次已完成' : '已完成'}</span>}
    {repeating && <span className="alcor-task-badge">{repeat}</span>}
    {done && repeating && next && next > today && <span className="alcor-task-badge">下次{relativeDay(next, today)}</span>}
    {showDate && day && day !== deadline && <time className="alcor-task-badge" dateTime={day}>{day < today ? '原计划' : '安排在'}{relativeDay(day, today)}</time>}
    {showDate && deadline && <span className="alcor-task-badge" data-kind="due"><IconAlarmClockOutlineRegular size={13} /><time aria-label="截止时间" dateTime={raw.due_time ? `${deadline}T${raw.due_time}` : deadline}>{relativeDay(deadline, today)}{raw.due_time && ` ${raw.due_time}`} 截止</time></span>}
    {!!raw.apple_pending?.length && <span className="alcor-task-badge" role="status">{appleOperationMessage(raw.apple_pending[0])}</span>}
    {!hideList && raw.list_name && <span className="alcor-task-badge" data-kind="list" aria-label={`清单：${raw.list_name}`}><i className="alcor-task-list-dot" style={{background:categories.find(group => group.id === raw.category_id)?.color}} />{raw.list_name}</span>}
    {raw.priority > 0 && <span className="alcor-task-badge" data-priority={raw.priority}>{priorityLabels[raw.priority]}优先级</span>}
  </div>
}
