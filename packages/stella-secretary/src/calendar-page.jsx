import { Notice } from './notice.jsx'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Modal, SegmentedControl, IconChevronLeftOutlineRegular, IconChevronRightOutlineRegular, IconCheckOutlineRegular, IconChevronDownOutlineRegular, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Categories } from './category-manager.jsx'
import { OrganizerContextMenu, useRecordContextMenu } from './organizer-details.jsx'
import { recordId, recordPermissions, taskDeleteMessage } from './organizer-actions.js'
import { AgentStrip, Empty, Field, MonthGrid } from './business-pages.jsx'
import { addDays, localDate, monthDays, eventOnDay, calendarFocusMinute, calendarOrder, calendarSources, calendarWeekLabel } from './presentation.js'
import { calendarRecord, calendarTaskRecord, eventLanes } from './organizer-forms.js'

const dateTitle = day => new Date(`${day}T12:00`).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
const clock = value => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
const eventTime = item => item.kind === 'task' ? item.allDay ? '全天' : clock(item.start) : item.allDay ? item.end > addDays(item.start, 1) ? `${item.start}–${addDays(item.end, -1)} 全天` : '全天' : `${clock(item.start)}–${localDate(new Date(item.start)) !== localDate(new Date(item.end)) ? new Date(item.end).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' ' : ''}${clock(item.end)}`
const lunar = day => new Intl.DateTimeFormat('zh-CN-u-ca-chinese', { month: 'long', day: 'numeric' }).format(new Date(`${day}T12:00`))
function range(day, view) {
  const date = new Date(`${day}T12:00`)
  if (view === 'year' || view === 'list') return [`${date.getFullYear()}-01-01`, `${date.getFullYear() + 1}-01-01`]
  if (view === 'week') { const start = addDays(day, -date.getDay()); return [start, addDays(start, 7)] }
  const days = monthDays(day)
  return [days[0], addDays(days.at(-1), 1)]
}

export function CalendarPage({ selectedRecord, templates, edit, create, archive, archived, busy, loading, quickChat, api, mutate, categories, reloadKey, manage, setManage, taskCategories = [], toggle, inspect, copy, move, taskMutate }) {
  const [view, setView] = useState('month')
  const context = useRecordContextMenu()
  const timeViewport = useRef(null)
  const sidebarToggle = useRef(null)
  const [selected, select] = useState(localDate())
  const [miniMonth, setMiniMonth] = useState(localDate())
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {const timer=setInterval(() => setNow(new Date()),60000);return () => clearInterval(timer)},[])
  useEffect(() => setMiniMonth(selected),[selected])
  const [hiddenGroups, setHiddenGroups] = useState([])
  const [records, setRecords] = useState([])
  const [fetching, setFetching] = useState(true)
  const [error, setError] = useState('')
  const [coverage, setCoverage] = useState(null)
  const [remove, setRemove] = useState(null)
  const [removeError,setRemoveError] = useState('')
  const chosen = new Date(`${selected}T12:00`)
  const [first, last] = range(selected, view)
  const version = templates.map(item => `${item.id}:${item.updatedAt}:${item.archived}`).join('|')
  useEffect(() => {
    let disposed = false
    setFetching(true); setRecords([]); setError('')
    Promise.all([
      api.business({ domain: 'calendar', method: 'list_events', arguments: { start: new Date(`${addDays(first,-2)}T00:00`).toISOString(), end: new Date(`${addDays(last,2)}T00:00`).toISOString() } }),
      api.business({ domain: 'tasks', method: 'list_todos', arguments: { start: addDays(first, -1), end: addDays(last, 1) } }),
    ]).then(([events, tasks]) => {
      if (disposed) return
      for (const result of [events, tasks]) if (!result.ok) throw new Error(result.error?.message || '日程读取失败，请重试。')
      const allowed = new Set(templates.filter(item => item.archived === archived).map(item => `${item.kind}:${item.id}`))
      const known = new Set(templates.map(item => `${item.kind}:${item.id}`))
      const visible = (rows, kind) => rows.filter(row => allowed.has(`${kind}:${row.series_id ?? row.id}`) || (!archived && !known.has(`${kind}:${row.series_id ?? row.id}`)))
      void api.business({domain:'apple',method:'status',arguments:{kind:'calendar'}}).then(result => { if (!disposed && result.ok) setCoverage(result.value.range_state) }).catch(() => {})
      setRecords([...visible(events.value,'event').map(calendarRecord).filter(item => item.allDay ? item.start < last && item.end > first : new Date(item.start)<new Date(`${last}T00:00`) && new Date(item.end)>=new Date(`${first}T00:00`)), ...visible(tasks.value, 'task').filter(row => row.status !== 'cancelled').map(calendarTaskRecord).filter(Boolean)]); setFetching(false)
    }).catch(failure => { if (!disposed) { setError(failure.message); setFetching(false) } })
    return () => { disposed = true }
  }, [api, first, last, version, reloadKey, archived])
  const groups = [...categories, ...taskCategories.map(item => ({ ...item, id: `task:${item.id}`, name: `${item.name} · 事务` }))]
  const groupId = item => item.groupId ?? item.business.category_id
  const filtered = records.filter(item => !hiddenGroups.includes(item.kind === 'task' ? '__tasks__' : groupId(item))).sort(calendarOrder)
  const categoriesById = Object.fromEntries(groups.map(item => [item.id, item]))
  function shift(direction) {
    if (view === 'year' || view === 'list') select(localDate(new Date(chosen.getFullYear() + direction, chosen.getMonth(), 1, 12)))
    else if (view === 'month') select(localDate(new Date(chosen.getFullYear(), chosen.getMonth() + direction, 1, 12)))
    else select(addDays(selected, direction * (view === 'week' ? 7 : 1)))
  }
  function requestDelete(item) { setRemoveError(''); setRemove({item,task:item.kind==='task',scope:'this'}) }
  function open(item) { inspect(item,() => requestDelete(item),undefined,context.target?.element ?? document.activeElement) }
  function completion(item) { return item.kind === 'task' && <button type="button" className="stella-calendar-task-check" aria-label={`${item.status === 'done' ? '重新打开' : '完成事务'}：${item.title}`} aria-pressed={item.status === 'done'} disabled={busy || item.business.apple?.can_modify === false || !!item.business.apple_pending?.length || item.business.apple_history} onClick={() => toggle({...item, id: item.business.id})}>{item.status === 'done' && <IconCheckOutlineRegular size={12} />}</button> }
  const isSelected = item => !!selectedRecord && (item.id === selectedRecord.id || item.business.id === selectedRecord.business?.id)
  function card(item, style) {
    return <div className={`stella-timed-event${item.kind === 'task' ? ' stella-calendar-task' : ''}`} key={item.id} data-record-id={item.business.id} data-record-selected={isSelected(item) || undefined} {...context.trigger(item)} data-completed={item.status === 'done' || undefined} data-readonly={item.business.apple?.can_modify === false || undefined} style={{...style, '--stella-event-color': categoriesById[groupId(item)]?.color ?? 'var(--dsw-alias-label-secondary)'}}>
      {completion(item)}<button type="button" className="stella-calendar-open" title={`${item.title} · ${eventTime(item)}`} onClick={() => open(item)}><span className="stella-calendar-title-line"><strong>{item.title}</strong>{item.business.repeat_frequency && item.business.repeat_frequency !== 'none' && <IconRefreshOutlineRegular size={12} />}</span>{item.kind !== 'task' && !item.allDay && <small>{eventTime(item)}</small>}{item.business.apple?.can_modify === false && <span className="stella-calendar-event-state">只读</span>}</button>
    </div>
  }
  function entry(item) {
    const group = categoriesById[groupId(item)]
    return <li key={item.id} data-record-id={item.business.id} data-record-selected={isSelected(item) || undefined} {...context.trigger(item)} style={{ '--stella-event-color': group?.color ?? 'var(--dsw-alias-label-secondary)' }}>
      {completion(item)}<Button variant="ghost" className="stella-agenda-entry" onClick={() => open(item)}><time>{eventTime(item)}</time><span><h3>{item.title}</h3><small>{group?.display_name ?? group?.name ?? (item.kind === 'task' ? '事务' : '日程')}{item.business.location ? ` · ${item.business.location}` : ''}{item.business.repeat_frequency && item.business.repeat_frequency !== 'none' ? ' · 重复' : ''}</small></span><IconChevronRightOutlineRegular size={16} /></Button>
      {item.kind !== 'task' && <div className="stella-actions"><Button variant="ghost" size="sm" disabled={busy} onClick={() => archive(templates.find(template => template.id === (item.business.series_id ?? item.id)))}>{archived ? '恢复' : '归档'}</Button>{!archived && item.business.series_id && <Button variant="ghost" size="sm" disabled={busy} onClick={() => setRemove({ item, occurrence: true })}>跳过本次</Button>}<Button variant="ghost" size="sm" disabled={!recordPermissions(item,busy).remove || archived} onClick={() => requestDelete(item)}>删除日程</Button></div>}
    </li>
  }
  const period = view === 'day' ? dateTitle(selected) : view === 'week' ? calendarWeekLabel(first,addDays(last,-1)) : view === 'year' || view === 'list' ? `${chosen.getFullYear()}年` : `${chosen.getFullYear()}年${chosen.getMonth() + 1}月`
  const days = Array.from({ length: view === 'week' ? 7 : 1 }, (_, index) => view === 'week' ? addDays(first, index) : selected)
  const focusKey = `${view}:${days.join(',')}:${selected}:${hiddenGroups.join('|')}`
  useEffect(() => {
    const viewport = timeViewport.current
    if (!viewport || fetching) return
    const hourHeight = viewport.querySelector('.stella-hour-slot')?.offsetHeight ?? 64
    const scroller = viewport.closest('.stella-time-scroll')
    const reveal = () => {
      viewport.scrollTop = calendarFocusMinute(filtered, days) / 60 * hourHeight
      const column = viewport.querySelectorAll('.stella-time-column')[days.indexOf(selected)]
      if (column) scroller.scrollLeft = column.offsetLeft - 64
    }
    reveal()
    const observer = new ResizeObserver(reveal)
    observer.observe(scroller)
    return () => observer.disconnect()
  }, [focusKey, fetching])
  const mini = new Date(`${miniMonth}T12:00`)
  const selectDay = day => { select(day); if (sidebarOpen) sidebarToggle.current?.focus(); setSidebarOpen(false) }
  const sources = calendarSources(categories)
  const groupIds = sources.flatMap(source => source.items.map(item => item.id))
  const hiddenCount = groupIds.filter(id => hiddenGroups.includes(id)).length
  const toggleGroup = (id,shown) => setHiddenGroups(previous => shown ? previous.filter(value => value !== id) : [...previous,id])
  const today = localDate(now)
  const todayStart = new Date(`${today}T00:00`).getTime()
  const tomorrowStart = new Date(`${addDays(today,1)}T00:00`).getTime()
  return <div className="stella-calendar-page">
    <div className="stella-calendar-workspace">
      <div className="stella-calendar-sidebar-frame">
        <Button ref={sidebarToggle} className="stella-calendar-sidebar-toggle" variant="outline" aria-controls="calendar-sidebar" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}>日历与日期{hiddenCount>0 && ` · 已隐藏 ${hiddenCount} 项`}<IconChevronDownOutlineRegular size={15} /></Button>
        <aside id="calendar-sidebar" className="stella-calendar-sidebar stella-organizer-sidebar" data-open={sidebarOpen || undefined}>
          <nav className="stella-calendar-filters" aria-label="日历筛选">
            <div className="stella-calendar-filter-tools"><Button variant="ghost" size="sm" aria-label="显示全部日历" onClick={() => setHiddenGroups([])}>全选</Button><Button variant="ghost" size="sm" aria-label="隐藏全部日历" onClick={() => setHiddenGroups(groupIds)}>清空</Button></div>
            {sources.map(source => <section className="stella-organizer-source" key={source.id} role="group" aria-label={source.name}><h3>{source.name}</h3><ul>{source.items.map(item => <li key={item.id} className="stella-organizer-source-row" style={{'--calendar-color':item.color}}><Checkbox className="stella-calendar-source-checkbox" label={item.display_name ?? item.name} title={item.id === '__tasks__' ? '显示或隐藏事务' : '显示或隐藏该日历'} checked={!hiddenGroups.includes(item.id)} onChange={shown => toggleGroup(item.id,shown)} />{item.apple?.can_modify === false && <span>只读</span>}</li>)}</ul></section>)}
          </nav>
          <section className="stella-calendar-mini" aria-label="日期导航"><div className="stella-calendar-mini-heading"><Button variant="ghost" size="sm" aria-label="小日历上个月" onClick={() => setMiniMonth(localDate(new Date(mini.getFullYear(),mini.getMonth()-1,1,12)))}><IconChevronLeftOutlineRegular size={14} /></Button><h3>{mini.getFullYear()}年{mini.getMonth()+1}月</h3><Button variant="ghost" size="sm" aria-label="小日历下个月" onClick={() => setMiniMonth(localDate(new Date(mini.getFullYear(),mini.getMonth()+1,1,12)))}><IconChevronRightOutlineRegular size={14} /></Button></div><MonthGrid compact day={miniMonth} selected={selected} records={filtered} categories={categoriesById} select={selectDay} /></section>
        </aside>
      </div>
      <div className="stella-calendar-main">
        <div className="stella-calendar-toolbar"><div className="stella-calendar-navigation"><h2>{period}</h2><div className="stella-actions"><Button variant="ghost" size="sm" aria-label="上一期" onClick={() => shift(-1)}><IconChevronLeftOutlineRegular size={16} /></Button><Button variant="outline" size="sm" onClick={() => selectDay(localDate())}>{({ week:'本周',month:'本月',year:'今年',list:'今年' })[view] ?? '今天'}</Button><Button variant="ghost" size="sm" aria-label="下一期" onClick={() => shift(1)}><IconChevronRightOutlineRegular size={16} /></Button></div></div><SegmentedControl id="calendar-view" label="日历视图" value={view} onChange={setView} options={[[ 'day','日' ],['week','周'],['month','月'],['year','年'],['list','列表']].map(([value,label]) => ({value,label}))} /></div>
        {coverage && coverage.status !== 'success' && <Notice role="status" className="stella-calendar-coverage">{coverage.reason || '此范围尚未完整同步；显示本地已保存日程。'}</Notice>}
        {(loading || fetching) && <Notice role="status" className="stella-calendar-loading">正在读取日程…</Notice>}{error && <Notice tone="error" role="alert" className="stella-error">{error}</Notice>}
        <div id={`calendar-view-${view}-panel`} role="tabpanel" aria-labelledby={`calendar-view-${view}`}>
          <div className="stella-calendar-surface">
            {view === 'month' && <MonthGrid day={selected} selected={selected} records={filtered} categories={categoriesById} open={open} recordSelected={isSelected} recordTrigger={context.trigger} select={day => {selectDay(day);setView('day')}} />}
            {view === 'year' && <div className="stella-year-grid">{Array.from({length:12},(_,index) => <section key={index}><h3>{index+1}月</h3><MonthGrid compact day={`${chosen.getFullYear()}-${String(index+1).padStart(2,'0')}-01`} selected={selected} records={filtered} categories={categoriesById} select={day => {selectDay(day);setView('day')}} /></section>)}</div>}
            {['day','week'].includes(view) && <div className="stella-time-scroll"><div className="stella-time-grid" style={{'--stella-day-count':days.length}}>
              <div className="stella-time-head"><span>时间</span>{days.map(day => <Button key={day} variant="ghost" onClick={() => selectDay(day)} aria-pressed={day === selected} data-today={day === today || undefined}><small>{new Date(`${day}T12:00`).toLocaleDateString('zh-CN',{weekday:'short'})}</small><strong>{Number(day.slice(-2))}</strong><small>{lunar(day)}</small></Button>)}</div>
              <div className="stella-time-all-day"><span>全天</span>{days.map(day => <div key={day}>{filtered.filter(item => item.allDay && eventOnDay(item,day)).map(item => card(item))}</div>)}</div>
              <div className="stella-time-viewport" ref={timeViewport}><div className="stella-time-body"><div className="stella-time-labels">{Array.from({length:24},(_,hour) => <span key={hour} data-current={days.includes(today) && Math.abs(hour*60-now.getHours()*60-now.getMinutes())<12 || undefined}>{String(hour).padStart(2,'0')}:00</span>)}</div>
                {days.map(day => {
                  const beginning = new Date(`${day}T00:00`).getTime()
                  const next = new Date(`${addDays(day,1)}T00:00`).getTime()
                  const items = filtered.filter(item => !item.allDay && eventOnDay(item,day))
                  const lanes = eventLanes(items)
                  return <div className="stella-time-column" key={day}>{Array.from({length:24},(_,hour) => <button className="stella-hour-slot" key={hour} aria-label={`${day} ${String(hour).padStart(2,'0')}:00 新建日程`} onClick={() => create(day,hour)} />)}{items.map(item => {
                    const start = Math.max(beginning,Date.parse(item.start))
                    const end = Math.min(next,Date.parse(item.end))
                    return card(item,{left:`calc(${lanes.get(item.id).left}% + 3px)`,width:`calc(${lanes.get(item.id).width}% - 6px)`,top:`${(start-beginning)/(next-beginning)*100}%`,height:item.kind === 'task' ? '30px' : `max(28px, ${(end-start)/(next-beginning)*100}%)`})
                  })}</div>
                })}
                {days.includes(today) && <div className="stella-current-time" style={{top:`${(now.getTime()-todayStart)/(tomorrowStart-todayStart)*100}%`}}><time dateTime={now.toISOString()}>{clock(now)}</time></div>}
              </div></div>
            </div></div>}
            {view === 'list' && <>{[...new Set(filtered.map(item => item.allDay ? item.start : localDate(new Date(item.start))))].sort().map(day => <section className="stella-agenda-date" key={day}><h3>{dateTitle(day)}</h3><ul className="stella-agenda-list">{filtered.filter(item => (item.allDay ? item.start : localDate(new Date(item.start))) === day).map(item => entry(item))}</ul></section>)}{!filtered.length && !fetching && <Empty title="本年暂无日程" />}</>}
          </div>
        </div>
        <AgentStrip kind="event" quickChat={quickChat} />
      </div>
    </div>
    <OrganizerContextMenu context={context} groups={context.target?.record.kind==='task' ? taskCategories : categories} busy={busy} inspect={open} edit={edit} complete={toggle} move={move} copy={copy} remove={requestDelete} />
    <Modal open={!!remove} title={remove?.task ? '删除事务' : remove?.occurrence ? '跳过本次日程' : '删除日程'} onClose={() => { if (!busy) setRemove(null) }}><Notice tone="warning">{remove?.task ? taskDeleteMessage(remove.item) : remove?.occurrence ? '仅跳过选中的这一天，保留重复系列的其他日程。' : '删除会同步至 Apple。已连接的日程等待 Apple 确认后才删除本地记录。'}</Notice><p>{remove?.item.title}</p>{remove && !remove.task && !remove.occurrence && (remove.item.business.series_id || remove.item.business.native_recurrence?.frequency && remove.item.business.native_recurrence.frequency !== 'none') && <Field label="删除范围"><select value={remove.scope} onChange={event => setRemove({...remove,scope:event.target.value})}><option value="this">仅本次日程</option><option value="future">本次及将来所有日程</option></select></Field>}{removeError && <Notice tone="error" role="alert">{removeError}</Notice>}<div className="stella-actions"><Button variant="primary" disabled={busy} onClick={async () => { const item = remove.item; const result = remove.task ? await taskMutate('delete_todo',{todo_id:recordId(item)}) : await mutate(remove.occurrence ? 'exclude_occurrence' : 'delete_event', remove.occurrence ? { series_id: item.business.series_id, occurrence_on: item.id.split('@').at(-1) } : { id: item.business.series_id ?? item.id,scope:remove.scope ?? 'this',...(item.business.series_id ? {occurrence_on:item.id.split('@').at(-1)} : {}) }); if (result) setRemove(null); else setRemoveError('删除未完成，记录已保留，请核对后重试。') }}>确认{remove?.occurrence ? '跳过' : '删除'}</Button><Button variant="outline" disabled={busy} onClick={() => setRemove(null)}>取消</Button></div></Modal>
    <Modal open={manage === 'categories'} title="分类管理" closeLabel="关闭分类管理" onClose={() => { if (!busy) setManage(null) }} className="stella-category-dialog"><Categories api={api} categories={categories} counts={Object.fromEntries(categories.map(group => [group.id, templates.filter(item => item.kind === 'event' && item.business.category_id === group.id).length]))} mutate={mutate} busy={busy} /></Modal>
  </div>
}

export { Categories }
