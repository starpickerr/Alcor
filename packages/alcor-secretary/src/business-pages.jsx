import { BearLogo } from './bear-logo.jsx'
import mascotImage from './assets/alcor-mascot.svg'
import React, { useState } from 'react'
import { Button, IconChevronDownOutlineRegular, IconCheckOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { localDate, monthDays, eventOnDay } from './presentation.js'

export const showTime = value => new Date(value).toLocaleString('zh-CN')
export function Field({ label, children }) { return <label className="alcor-field"><span>{label}</span>{['select', 'textarea'].includes(children?.type) ? React.cloneElement(children, { 'aria-label': label }) : children}</label> }
export function Empty({ title, description, icon, mascot = false }) { return <div className="alcor-empty">{mascot ? <img className="alcor-empty-mascot" src={mascotImage} width="124" height="121" alt="" aria-hidden="true" draggable={false} /> : icon && <div className="alcor-empty-icon" aria-hidden="true">{icon}</div>}<h2>{title}</h2>{description && <p>{description}</p>}</div> }
export function AgentStrip({ kind, quickChat }) {
  const [expanded, setExpanded] = useState(false)
  const [opened, setOpened] = useState(false)
  return <section className="alcor-agent-strip">
    <div className="alcor-agent-identity"><span className="alcor-agent-avatar" aria-hidden="true"><BearLogo size={30} /></span><div><h2>Alcor Agent</h2></div></div>
    <div className="alcor-actions"><Button variant="ghost" aria-expanded={expanded} onClick={() => { setOpened(true); setExpanded(!expanded) }}><IconChevronDownOutlineRegular size={16} />{expanded ? '收起' : '展开'}</Button></div>
    {opened && <div className="alcor-agent-expanded" hidden={!expanded}>{quickChat(kind)}</div>}
  </section>
}


const weekdayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
export function MonthGrid({ day, selected, records, select, compact = false, categories = {}, open, recordTrigger, recordSelected }) {
  const month = day.slice(0, 7)
  return <div className={`alcor-month-grid${compact ? ' alcor-month-compact' : ''}`}>
    {weekdayNames.map(label => <div className="alcor-weekday" key={label}>{compact ? label.slice(1) : label}</div>)}
    {monthDays(day).map(date => {
      const events = records.filter(item => eventOnDay(item, date))
      const state = {'data-outside':date.slice(0,7)!==month || undefined,'data-today':date===localDate() || undefined}
      const number = <span className="alcor-day-number">{Number(date.slice(-2))}</span>
      if (compact) return <button type="button" key={date} {...state} aria-label={date} aria-pressed={date===selected} onClick={() => select(date)}>{number}{!!events.length && <span className="alcor-calendar-dots" aria-label={`${events.length} 项日程`}>{[...new Set(events.map(event => categories[event.groupId ?? event.business?.category_id]?.color ?? 'var(--dsw-alias-label-secondary)'))].slice(0,3).map(color => <i key={color} style={{background:color}} />)}</span>}</button>
      return <div key={date} {...state} data-selected={date===selected || undefined} className="alcor-month-day" onClick={() => select(date)}>
        <button type="button" className="alcor-day-select" aria-label={date} aria-pressed={date===selected} onClick={event => {event.stopPropagation(); select(date)}}>{number}</button>
        {events.slice(0,3).map(event => <button type="button" className="alcor-calendar-chip" key={event.id} data-kind={event.kind} data-record-selected={recordSelected?.(event) || undefined} data-completed={event.status === 'done' || undefined} data-record-id={event.business?.id} {...recordTrigger?.(event)} style={{'--alcor-event-color':categories[event.groupId ?? event.business?.category_id]?.color ?? 'var(--dsw-alias-label-secondary)'}} title={event.title} onClick={click => {click.stopPropagation(); open?.(event)}}>{!event.allDay && <time>{new Date(event.start).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})} </time>}{event.kind==='task' && <span className="alcor-calendar-task-marker" aria-hidden="true">{event.status==='done' && <IconCheckOutlineRegular size={10} />}</span>}{event.title}</button>)}
        {events.length>3 && <button type="button" className="alcor-calendar-more" aria-label={`${date} 查看更多日程`} onClick={event => {event.stopPropagation();select(date)}}>+{events.length-3} 项</button>}
      </div>
    })}
  </div>
}
