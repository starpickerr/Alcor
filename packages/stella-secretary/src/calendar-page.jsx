import React, { useEffect, useRef, useState } from 'react'
import { Button, Input, Checkbox, Modal, SegmentedControl, Pill, IconPlusOutlineRegular, IconChevronLeftOutlineRegular, IconChevronRightOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { AgentStrip, Empty, Field, MonthGrid, showTime } from './business-pages.jsx'
import { addDays, localDate, monthDays, eventOnDay, calendarFocusMinute } from './presentation.js'
import { calendarRecord, eventLanes } from './organizer-forms.js'

const dateTitle = day => new Date(`${day}T12:00`).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
const clock = value => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
const eventTime = item => item.allDay ? item.end > addDays(item.start, 1) ? `${item.start}–${addDays(item.end, -1)} 全天` : '全天' : `${clock(item.start)}–${localDate(new Date(item.start)) !== localDate(new Date(item.end)) ? new Date(item.end).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' ' : ''}${clock(item.end)}`
const lunar = day => new Intl.DateTimeFormat('zh-CN-u-ca-chinese', { month: 'long', day: 'numeric' }).format(new Date(`${day}T12:00`))
function range(day, view) {
  const date = new Date(`${day}T12:00`)
  if (view === 'year' || view === 'list') return [`${date.getFullYear()}-01-01`, `${date.getFullYear() + 1}-01-01`]
  if (view === 'week') { const start = addDays(day, -date.getDay()); return [start, addDays(start, 7)] }
  const days = monthDays(day)
  return [days[0], addDays(days.at(-1), 1)]
}

export function CalendarPage({ templates, edit, create, archive, archived, busy, loading, quickChat, api, mutate, categories, reloadKey, manage, setManage }) {
  const [view, setView] = useState('month')
  const timeViewport = useRef(null)
  const [selected, select] = useState(localDate())
  const [category, setCategory] = useState('all')
  const [records, setRecords] = useState([])
  const [fetching, setFetching] = useState(true)
  const [error, setError] = useState('')
  const [remove, setRemove] = useState(null)
  const chosen = new Date(`${selected}T12:00`)
  const [first, last] = range(selected, view)
  const version = templates.map(item => `${item.id}:${item.updatedAt}:${item.archived}`).join('|')
  useEffect(() => {
    let disposed = false
    setFetching(true); setRecords([]); setError('')
    api.business({ domain: 'calendar', method: 'list_events', arguments: { start: new Date(`${first}T00:00`).toISOString(), end: new Date(`${last}T00:00`).toISOString() } }).then(result => {
      if (disposed) return
      if (!result.ok) throw new Error(result.error?.message || '日程读取失败，请重试。')
      const allowed = new Set(templates.filter(item => item.archived === archived).map(item => item.id))
      setRecords(result.value.filter(row => allowed.has(row.series_id ?? row.id)).map(calendarRecord)); setFetching(false)
    }).catch(failure => { if (!disposed) { setError(failure.message); setFetching(false) } })
    return () => { disposed = true }
  }, [api, first, last, version, reloadKey, archived])
  const filtered = records.filter(item => category === 'all' || (category === 'none' ? !item.business.category_id : item.business.category_id === category))
  const daily = filtered.filter(item => eventOnDay(item, selected))
  const categoriesById = Object.fromEntries(categories.map(item => [item.id, item]))
  function shift(direction) {
    if (view === 'year' || view === 'list') select(localDate(new Date(chosen.getFullYear() + direction, chosen.getMonth(), 1, 12)))
    else if (view === 'month') select(localDate(new Date(chosen.getFullYear(), chosen.getMonth() + direction, 1, 12)))
    else select(addDays(selected, direction * (view === 'week' ? 7 : 1)))
  }
  function open(item) { edit(templates.find(template => template.id === (item.business.series_id ?? item.id)) ?? item) }
  function entry(item) {
    const group = categoriesById[item.business.category_id]
    return <li key={item.id} style={{ '--stella-event-color': group?.color ?? 'var(--dsw-alias-label-secondary)' }}><Button variant="ghost" className="stella-agenda-entry" onClick={() => open(item)}><time>{eventTime(item)}</time><span><h3>{item.title}</h3><small>{group?.name ?? '未分类'}{item.business.location ? ` · ${item.business.location}` : ''}{item.business.repeat_frequency !== 'none' ? ' · 重复日程' : ''}</small></span><IconChevronRightOutlineRegular size={16} /></Button><div className="stella-actions"><Button variant="ghost" size="sm" disabled={busy} onClick={() => archive(templates.find(template => template.id === (item.business.series_id ?? item.id)))}>{archived ? '恢复' : '归档'}</Button>{!archived && item.business.series_id && <Button variant="ghost" size="sm" disabled={busy} onClick={() => setRemove({ item, occurrence: true })}>跳过本次</Button>}<Button variant="ghost" size="sm" disabled={busy || archived} onClick={() => setRemove({ item })}>{item.business.repeat_frequency !== 'none' ? '删除系列' : '删除日程'}</Button></div></li>
  }
  const period = view === 'day' ? dateTitle(selected) : view === 'week' ? `${dateTitle(first)} — ${dateTitle(addDays(last, -1))}` : view === 'year' || view === 'list' ? `${chosen.getFullYear()}年` : `${chosen.getFullYear()}年${chosen.getMonth() + 1}月`
  const days = Array.from({ length: view === 'week' ? 7 : 1 }, (_, index) => view === 'week' ? addDays(first, index) : selected)
  const focusKey = `${view}:${days.join(',')}:${selected}:${category}`
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
  return <div className="stella-calendar-page">
    <div className="stella-page-content"><AgentStrip kind="event" quickChat={quickChat} /></div>
    <div className="stella-calendar-toolbar"><div className="stella-actions"><Pill active={category === 'all'} onClick={() => setCategory('all')}>全部</Pill>{categories.map(item => <Pill key={item.id} active={category === item.id} onClick={() => setCategory(item.id)}><span className="stella-category-dot" style={{ background: item.color }} />{item.name}</Pill>)}<Pill active={category === 'none'} onClick={() => setCategory('none')}>未分类</Pill></div><SegmentedControl id="calendar-view" label="日历视图" value={view} onChange={setView} options={[['day', '日'], ['week', '周'], ['month', '月'], ['year', '年'], ['list', '列表']].map(([value, label]) => ({ value, label }))} /></div>
    <div className="stella-page-content"><div className="stella-calendar-period"><h2>{period}</h2><div className="stella-actions"><Button variant="ghost" aria-label="上一期" onClick={() => shift(-1)}><IconChevronLeftOutlineRegular size={18} /></Button><Button variant="ghost" onClick={() => select(localDate())}>{({ week: '本周', month: '本月', year: '今年', list: '今年' })[view] ?? '今天'}</Button><Button variant="ghost" aria-label="下一期" onClick={() => shift(1)}><IconChevronRightOutlineRegular size={18} /></Button></div></div>{(loading || fetching) && <p role="status">正在读取日程…</p>}{error && <p role="alert">{error}</p>}
      <div id={`calendar-view-${view}-panel`} role="tabpanel" aria-labelledby={`calendar-view-${view}`} className={view === 'day' ? 'stella-calendar-layout' : ''}>
        <div className="stella-calendar-surface">
          {view === 'month' && <MonthGrid day={selected} selected={selected} records={filtered} categories={categoriesById} select={day => { select(day); setView('day') }} />}
          {view === 'year' && <div className="stella-year-grid">{Array.from({ length: 12 }, (_, index) => <section key={index}><h3>{index + 1}月</h3><MonthGrid compact day={`${chosen.getFullYear()}-${String(index + 1).padStart(2, '0')}-01`} selected={selected} records={filtered} categories={categoriesById} select={day => { select(day); setView('day') }} /></section>)}</div>}
          {['day', 'week'].includes(view) && <div className="stella-time-scroll"><div className="stella-time-grid" style={{ '--stella-day-count': days.length }}><div className="stella-time-head"><span>日期</span>{days.map(day => <Button key={day} variant="ghost" onClick={() => select(day)} aria-pressed={day === selected} data-today={day === localDate() || undefined}><strong>{new Date(`${day}T12:00`).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' })}</strong><small>{lunar(day)}</small></Button>)}</div><div className="stella-time-all-day"><span>全天</span>{days.map(day => <div key={day}>{filtered.filter(item => item.allDay && eventOnDay(item, day)).map(item => <button className="stella-timed-event" key={item.id} style={{ '--stella-event-color': categoriesById[item.business.category_id]?.color ?? 'var(--dsw-alias-label-secondary)' }} onClick={() => open(item)}>{item.title}</button>)}</div>)}</div><div className="stella-time-viewport" ref={timeViewport}><div className="stella-time-body"><div className="stella-time-labels">{Array.from({ length: 24 }, (_, hour) => <span key={hour}>{String(hour).padStart(2, '0')}:00</span>)}</div>{days.map(day => { const beginning = new Date(`${day}T00:00`).getTime(); const next = new Date(`${addDays(day, 1)}T00:00`).getTime(); const lanes = eventLanes(filtered.filter(item => !item.allDay && eventOnDay(item, day))); return <div className="stella-time-column" key={day}>{Array.from({ length: 24 }, (_, hour) => <button className="stella-hour-slot" key={hour} aria-label={`${day} ${String(hour).padStart(2, '0')}:00 新建日程`} onClick={() => create(day, hour)} />)}{filtered.filter(item => !item.allDay && eventOnDay(item, day)).map(item => { const start = Math.max(beginning, Date.parse(item.start)); const end = Math.min(next, Date.parse(item.end)); const top = (start - beginning) / (next - beginning) * 100; const height = (end - start) / (next - beginning) * 100; return <button className="stella-timed-event" key={item.id} style={{ left: `calc(${lanes.get(item.id).left}% + 3px)`, width: `calc(${lanes.get(item.id).width}% - 6px)`, top: `${top}%`, height: `max(28px, ${height}%)`, '--stella-event-color': categoriesById[item.business.category_id]?.color ?? 'var(--dsw-alias-label-secondary)' }} onClick={() => open(item)} title={`${item.title} · ${showTime(item.start)} — ${showTime(item.end)}`}><strong>{item.title}</strong><small>{eventTime(item)}</small>{item.business.location && <small>{item.business.location}</small>}</button> })}</div> })}</div></div></div></div>}
          {view === 'list' && <>{[...new Set(filtered.map(item => item.allDay ? item.start : localDate(new Date(item.start))))].sort().map(day => <section className="stella-agenda-date" key={day}><h3>{dateTitle(day)}</h3><ul className="stella-agenda-list">{filtered.filter(item => (item.allDay ? item.start : localDate(new Date(item.start))) === day).map(entry)}</ul></section>)}{!filtered.length && !fetching && <Empty title="本年暂无日程"  />}</>}
        </div>
        {view === 'day' && <aside className="stella-day-detail"><div className="stella-calendar-period"><Button variant="ghost" aria-label="小日历上个月" onClick={() => select(localDate(new Date(chosen.getFullYear(), chosen.getMonth() - 1, 1, 12)))}>‹</Button><span>{chosen.getFullYear()}年{chosen.getMonth() + 1}月</span><Button variant="ghost" aria-label="小日历下个月" onClick={() => select(localDate(new Date(chosen.getFullYear(), chosen.getMonth() + 1, 1, 12)))}>›</Button></div><MonthGrid compact day={selected} selected={selected} records={filtered} categories={categoriesById} select={select} /><div className="stella-calendar-period"><h2>{dateTitle(selected)}</h2><Button variant="ghost" size="sm" aria-label="为选中日期新建日程" onClick={() => create(selected)}><IconPlusOutlineRegular size={18} /></Button></div><p className="stella-muted">{lunar(selected)}</p><ul className="stella-agenda-list">{daily.map(entry)}</ul>{!daily.length && !fetching && <Empty title="当天暂无日程"  />}</aside>}
      </div>
    </div>
    <Modal open={!!remove} title={remove?.occurrence ? '跳过本次日程' : '删除日程'} onClose={() => { if (!busy) setRemove(null) }}><p>{remove?.occurrence ? '仅跳过选中的这一天，保留重复系列的其他日程。' : '此操作删除整条日程；重复日程会删除整个系列，无法撤销。'}</p><p>{remove?.item.title}</p><div className="stella-actions"><Button variant="primary" disabled={busy} onClick={async () => { const item = remove.item; const result = await mutate(remove.occurrence ? 'exclude_occurrence' : 'delete_event', remove.occurrence ? { series_id: item.business.series_id, occurrence_on: item.id.split('@').at(-1) } : { id: item.business.series_id ?? item.id }); if (result) setRemove(null) }}>确认{remove?.occurrence ? '跳过' : '删除'}</Button><Button variant="outline" disabled={busy} onClick={() => setRemove(null)}>取消</Button></div></Modal>
    <Modal open={!!manage} title={manage === 'categories' ? '分类管理' : '导入日期'} onClose={() => { if (!busy) setManage(null) }} className="stella-editor-dialog">{manage === 'categories' ? <Categories categories={categories} mutate={mutate} busy={busy} /> : manage === 'import' ? <DateImport mutate={mutate} busy={busy} /> : null}</Modal>
  </div>
}

function Categories({ categories, mutate, busy }) {
  const [name, setName] = useState('')
  const [color, setColor] = useState('#6B8AF7')
  const [deleting, setDeleting] = useState(null)
  const [error, setError] = useState('')
  async function perform(method, args) { const value = await mutate(method, args); setError(value ? '' : '操作失败，请查看页面提示并重试。'); return value }
  return <div>{categories.map(item => <form className="stella-category-editor" key={`${item.id}:${item.name}:${item.color}`} onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void perform('update_category', { id: item.id, patch: { name: form.get('name'), color: form.get('color') } }) }}><Input name="name" aria-label={`${item.name}名称`} defaultValue={item.name} required maxLength={40} readOnly={item.id === 'china_holidays'} /><input name="color" aria-label={`${item.name}颜色`} type="color" defaultValue={item.color} /><Button type="submit" variant="outline" disabled={busy}>保存</Button>{item.id !== 'china_holidays' && <Button type="button" variant="ghost" disabled={busy} onClick={() => setDeleting(item)}>删除分类</Button>}</form>)}<form className="stella-category-editor" onSubmit={async event => { event.preventDefault(); if (await perform('create_category', { name, color })) setName('') }}><Input aria-label="新分类名称" required maxLength={40} value={name} onChange={event => setName(event.target.value)} /><input aria-label="新分类颜色" type="color" value={color} onChange={event => setColor(event.target.value)} /><Button type="submit" variant="primary" disabled={busy || !name.trim()}>添加分类</Button></form>{deleting && <div role="alert"><p>删除“{deleting.name}”后，其日程会归为未分类，日程本身保留。</p><Button variant="outline" disabled={busy} onClick={async () => { if (await perform('delete_category', { id: deleting.id })) setDeleting(null) }}>确认删除分类</Button><Button variant="ghost" disabled={busy} onClick={() => setDeleting(null)}>取消</Button></div>}{error && <p role="alert">{error}</p>}</div>
}

function DateImport({ mutate, busy }) {
  const [year, setYear] = useState('2026')
  const [format, setFormat] = useState('csv')
  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [date, setDate] = useState(localDate())
  const [annual, setAnnual] = useState(false)
  const [message, setMessage] = useState('')
  async function perform(method, args) { const result = await mutate(method, args); setMessage(result ? result.added === undefined ? '特殊日期已保存。' : `新增 ${result.added} 项，共扫描 ${result.total} 项；重复来源不会再次新增。` : '操作失败，请查看页面提示。') }
  return <div className="stella-date-import"><h3>中国大陆节假日</h3><p className="stella-muted">支持 2025、2026 年。</p><form className="stella-actions" onSubmit={event => { event.preventDefault(); void perform('import_china_holidays', { year: Number(year) }) }}><select aria-label="节假日年份" value={year} onChange={event => setYear(event.target.value)}><option>2025</option><option>2026</option></select><Button type="submit" variant="outline" disabled={busy}>导入节假日</Button></form><h3>特殊日期</h3><form onSubmit={event => { event.preventDefault(); void perform('create_special_date', { title, date, annual, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }) }}><Field label="特殊日期名称"><Input required value={title} onChange={event => setTitle(event.target.value)} /></Field><Field label="日期"><Input required type="date" value={date} onChange={event => setDate(event.target.value)} /></Field><Checkbox label="每年重复" checked={annual} onChange={setAnnual} /><Button type="submit" variant="outline" disabled={busy}>添加特殊日期</Button></form><h3>文件导入</h3><p className="stella-muted">CSV 需包含 title,date，可选 annual,description,uid。ICS 只支持全天日期及简单年度重复；最多 1 MB、500 项。</p><Field label="导入格式"><select value={format} onChange={event => setFormat(event.target.value)}><option value="csv">CSV</option><option value="ics">ICS</option></select></Field><input type="file" accept=".csv,.ics,text/csv,text/calendar" aria-label="选择日期文件" disabled={busy} onChange={async event => { const file = event.target.files?.[0]; if (!file) return; if (file.size > 1024 * 1024) { setMessage('文件不能超过 1 MB。'); return } try { setContent(await file.text()); setFormat(file.name.toLowerCase().endsWith('.ics') ? 'ics' : 'csv') } catch { setMessage('文件读取失败，请重新选择。') } }} /><form onSubmit={event => { event.preventDefault(); void perform('import_special_dates', { content, format, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }) }}><Field label="日期文件内容"><textarea rows={5} required value={content} onChange={event => setContent(event.target.value)} /></Field><Button type="submit" variant="primary" disabled={busy || !content.trim()}>导入日期文件</Button></form><p role="status">{message}</p></div>
}
