import { BearLogo } from './bear-logo.jsx'
import React, { useState } from 'react'
import { Button, IconChevronDownOutlineRegular, IconCheckOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { localDate, monthDays, eventOnDay } from './presentation.js'

export const showTime = value => new Date(value).toLocaleString('zh-CN')
export function Field({ label, children }) { return <label className="stella-field"><span>{label}</span>{['select', 'textarea'].includes(children?.type) ? React.cloneElement(children, { 'aria-label': label }) : children}</label> }
export function Empty({ title, description, icon }) { return <div className="stella-empty">{icon && <div className="stella-empty-icon" aria-hidden="true">{icon}</div>}<h2>{title}</h2>{description && <p>{description}</p>}</div> }
export function AgentStrip({ kind, quickChat }) {
  const [expanded, setExpanded] = useState(false)
  const [opened, setOpened] = useState(false)
  return <section className="stella-agent-strip">
    <div className="stella-agent-identity"><span className="stella-agent-avatar" aria-hidden="true"><BearLogo size={20} /></span><div><h2>Stella Agent</h2></div></div>
    <div className="stella-actions"><Button variant="ghost" aria-expanded={expanded} onClick={() => { setOpened(true); setExpanded(!expanded) }}><IconChevronDownOutlineRegular size={16} />{expanded ? '收起' : '展开'}</Button></div>
    {opened && <div className="stella-agent-expanded" hidden={!expanded}>{quickChat(kind)}</div>}
  </section>
}


const weekdayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
export function MonthGrid({ day, selected, records, select, compact = false, categories = {}, open, recordTrigger, recordSelected }) {
  const month = day.slice(0, 7)
  return <div className={`stella-month-grid${compact ? ' stella-month-compact' : ''}`}>
    {weekdayNames.map(label => <div className="stella-weekday" key={label}>{compact ? label.slice(1) : label}</div>)}
    {monthDays(day).map(date => {
      const events = records.filter(item => eventOnDay(item, date))
      const state = {'data-outside':date.slice(0,7)!==month || undefined,'data-today':date===localDate() || undefined}
      const number = <span className="stella-day-number">{Number(date.slice(-2))}</span>
      if (compact) return <button type="button" key={date} {...state} aria-label={date} aria-pressed={date===selected} onClick={() => select(date)}>{number}{!!events.length && <span className="stella-calendar-dots" aria-label={`${events.length} 项日程`}>{[...new Set(events.map(event => categories[event.groupId ?? event.business?.category_id]?.color ?? 'var(--dsw-alias-label-secondary)'))].slice(0,3).map(color => <i key={color} style={{background:color}} />)}</span>}</button>
      return <div key={date} {...state} data-selected={date===selected || undefined} className="stella-month-day" onClick={() => select(date)}>
        <button type="button" className="stella-day-select" aria-label={date} aria-pressed={date===selected} onClick={event => {event.stopPropagation(); select(date)}}>{number}</button>
        {events.slice(0,3).map(event => <button type="button" className="stella-calendar-chip" key={event.id} data-kind={event.kind} data-record-selected={recordSelected?.(event) || undefined} data-completed={event.status === 'done' || undefined} data-record-id={event.business?.id} {...recordTrigger?.(event)} style={{'--stella-event-color':categories[event.groupId ?? event.business?.category_id]?.color ?? 'var(--dsw-alias-label-secondary)'}} title={event.title} onClick={click => {click.stopPropagation(); open?.(event)}}>{!event.allDay && <time>{new Date(event.start).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})} </time>}{event.kind==='task' && <span className="stella-calendar-task-marker" aria-hidden="true">{event.status==='done' && <IconCheckOutlineRegular size={10} />}</span>}{event.title}</button>)}
        {events.length>3 && <button type="button" className="stella-calendar-more" aria-label={`${date} 查看更多日程`} onClick={event => {event.stopPropagation();select(date)}}>+{events.length-3} 项</button>}
      </div>
    })}
  </div>
}
