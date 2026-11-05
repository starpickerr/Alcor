import { BearLogo } from './bear-logo.jsx'
import React, { useState } from 'react'
import { Button, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { localDate, monthDays, eventOnDay } from './presentation.js'

export const showTime = value => new Date(value).toLocaleString('zh-CN')
export function Field({ label, children }) { return <label className="stella-field"><span>{label}</span>{['select', 'textarea'].includes(children?.type) ? React.cloneElement(children, { 'aria-label': label }) : children}</label> }
export function Empty({ title, description, icon }) { return <div className="stella-empty">{icon && <div className="stella-empty-icon" aria-hidden="true">{icon}</div>}<h2>{title}</h2>{description && <p>{description}</p>}</div> }
export function AgentStrip({ kind, quickChat }) {
  const [expanded, setExpanded] = useState(false)
  const [opened, setOpened] = useState(false)
  return <section className="stella-agent-strip">
    <div className="stella-agent-identity"><BearLogo size={22} /><div><h2>Stella Agent</h2></div></div>
    <div className="stella-actions"><Button variant="ghost" aria-expanded={expanded} onClick={() => { setOpened(true); setExpanded(!expanded) }}><IconChevronDownOutlineRegular size={16} />{expanded ? '收起' : '展开'}</Button></div>
    {opened && <div className="stella-agent-expanded" hidden={!expanded}>{quickChat(kind)}</div>}
  </section>
}


const weekdayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
export function MonthGrid({ day, selected, records, select, compact = false }) {
  const month = day.slice(0, 7)
  return <div className={`stella-month-grid${compact ? ' stella-month-compact' : ''}`}>
    {weekdayNames.map(label => <div className="stella-weekday" key={label}>{compact ? label.slice(1) : label}</div>)}
    {monthDays(day).map(date => { const events = records.filter(item => eventOnDay(item, date)); return <button type="button" key={date} aria-label={date} aria-pressed={date === selected} data-outside={date.slice(0, 7) !== month || undefined} data-today={date === localDate() || undefined} onClick={() => select(date)}>
      <span className="stella-day-number">{Number(date.slice(-2))}</span>{!compact && events.slice(0, 2).map(event => <span className="stella-calendar-chip" key={event.id}>{event.title}</span>)}{events.length > (compact ? 0 : 2) && <small>{compact ? '•' : `+${events.length - 2}`}</small>}
    </button> })}
  </div>
}
