import { Notice } from './notice.jsx'
import React, { useState } from 'react'
import { Button, Menu, Modal, IconCloseOutlineRegular, IconChevronRightOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { arrayValue } from './organizer-forms.js'
import { groupName, priorityLabels, recordAlarms, recordMenuEntries, recordPermissions, recordRepeatLabel } from './organizer-actions.js'

export function useRecordContextMenu() {
  const [target, setTarget] = useState(null)
  const open = (event, record, anchored = false) => {
    event.preventDefault(); event.stopPropagation()
    const element = event.target.closest('button, [tabindex]') ?? event.currentTarget
    const rect = element.getBoundingClientRect()
    const keyboard = anchored || event.type === 'keydown' || (!event.clientX && !event.clientY)
    setTarget({ record, element, x: keyboard ? rect.left : event.clientX, y: keyboard ? rect.bottom : event.clientY })
  }
  return { target, open, showGroups: () => setTarget(value => ({...value,groups:true})), back: () => setTarget(value => ({...value,groups:false})), close: () => setTarget(null), trigger: record => ({
    onContextMenu: event => open(event, record),
    onKeyDown: event => { if (event.key === 'ContextMenu' || event.key === 'F10' && event.shiftKey) open(event, record) },
  }) }
}

export function OrganizerContextMenu({ context, groups, busy, inspect, edit, complete, move, copy, remove }) {
  const target = context.target
  const record = target?.record
  const entries = record ? recordMenuEntries(record,groups,busy) : []
  const choices = entries.find(item => item.id==='group')?.submenu ?? []
  const items = target?.groups ? [{id:'back',label:'返回'},{type:'label',id:'group-title',text:record.kind==='task' ? '选择清单' : '选择日历'},...choices.map(item => ({...item,icon:<span className="stella-category-dot" style={{background:groups.find(group => `group:${group.id}`===item.id)?.color}} />}))] : entries.map(({submenu,...item}) => submenu ? {...item,label:<span className="stella-record-menu-group">{item.label}<IconChevronRightOutlineRegular size={14} /></span>} : item)
  return <Menu key={target?.groups ? 'groups' : 'actions'} open={!!target} contextual portal autoFocus anchor={null} onClose={context.close}
    className="stella-record-context-menu" listClassName="stella-record-context-list"
    getAnchorRect={() => target ? new DOMRect(target.x, target.y, 0, 0) : null} returnFocus={() => target?.element ?? null}
    items={items}
    selectedId={record ? `group:${record.business?.category_id}` : undefined}
    onSelect={id => {
      if (id==='group') {context.showGroups(); return}
      if (id==='back') {context.back(); return}
      context.close()
      if (id === 'details') inspect(record)
      else if (id === 'edit') edit(record)
      else if (id === 'complete') complete(record)
      else if (id === 'copy') copy(record)
      else if (id === 'delete') remove(record)
      else if (id.startsWith('group:')) move(record, id.slice(6))
    }} />
}

const paths = {
  date: 'M5 5h14v15H5z M8 3v4m8-4v4M5 10h14M8 13h2m4 0h2m-8 4h2',
  time: 'M12 8v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  repeat: 'm16 3 4 4-4 4M4 11V9a2 2 0 0 1 2-2h14M8 21l-4-4 4-4m12 0v2a2 2 0 0 1-2 2H4',
  alarm: 'M6 17V10a6 6 0 0 1 12 0v7l2 2H4zM10 22h4M12 2v2',
  list: 'M9 6h12M9 12h12M9 18h12M3 6h1m-1 6h1m-1 6h1',
  tag: 'M3 3h8l10 10-8 8L3 11zM7 7h.01',
  flag: 'M5 22V3c4-3 8 3 14 0v11c-6 3-10-3-14 0',
  priority: 'M12 3v12m0 5h.01',
  location: 'M12 22s8-8 8-14A8 8 0 0 0 4 8c0 6 8 14 8 14zM15 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
}
function DetailRow({ label, icon, children }) {
  return <div className="stella-inspector-row"><dt><svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={paths[icon]} /></svg><span>{label}</span></dt><dd>{children}</dd></div>
}
const dateTitle = day => day ? new Date(`${day.slice(0, 10)}T12:00`).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }) : '未设置日期'
const timeTitle = value => new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
function alarmTitle(alarm) {
  if (alarm.location) return `位置提醒 · ${alarm.location.title || '已设置位置'}`
  if (alarm.absolute) return timeTitle(alarm.absolute)
  const minutes = Math.abs(alarm.offset ?? 0) / 60
  return minutes ? `${alarm.offset < 0 ? '提前' : '开始后'} ${minutes} 分钟` : '开始时'
}

export function OrganizerDetails({ value, groups, busy, close, edit, complete, error, inline = false, children, closeOnComplete = true, closeOnDelete = true, closeOnEdit = true }) {
  const record = value?.record
  const raw = record?.business ?? {}
  const task = record?.kind === 'task'
  const group = groups.find(item => item.id === raw.category_id)
  const rights = record ? recordPermissions(record, busy) : {}
  const alarms = recordAlarms(raw)
  const tags = arrayValue(raw.tags)
  const url = raw.url || ''
  const Container = inline ? 'div' : Modal
  const containerProps = inline ? {} : {open:!!record,headless:true,title:task ? '事务简介' : '日程简介',onClose:() => {if (!busy) close()}}
  return <Container {...containerProps} className="stella-record-inspector">
    {record && <>
      {!inline && <header className="stella-inspector-header"><Button variant="ghost" size="sm" aria-label="关闭简介" disabled={busy} onClick={close}><IconCloseOutlineRegular size={16} /></Button><h2>简介</h2><span /></header>}
      <div className="stella-inspector-body">
        {error && <Notice tone="error">{error}</Notice>}
        <section className="stella-inspector-card stella-inspector-title" style={{ '--stella-event-color': group?.color ?? 'var(--stella-accent)' }}><h3>{record.title}</h3>{record.details && <p className={task ? 'stella-task-description' : undefined}>{record.details}</p>}{url && (/^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noreferrer">{url}</a> : <p>{url}</p>)}</section>
        {(raw.apple_history || raw.apple_pending?.length > 0 || raw.apple?.can_modify === false) && <Notice role="status">{raw.apple_history ? '已完成的历史记录' : raw.apple_pending?.length ? '等待 Apple 确认，内容已保留' : 'Apple 来源内容只读'}{raw.apple_pending?.map(item => item.error).filter(Boolean).map(error => <span key={error}>{error}</span>)}</Notice>}
        {children}
        <section aria-label="日期与时间"><h3 className="stella-inspector-section-title">日期与时间</h3><dl className="stella-inspector-card">
          {task ? <><DetailRow label="日期" icon="date">{dateTitle(record.due ?? raw.due_on)}</DetailRow><DetailRow label="时间" icon="time">{raw.due_time || '未设置时间'}</DetailRow></> : <><DetailRow label="开始" icon="date">{record.allDay ? `${dateTitle(record.start)} · 全天` : timeTitle(record.start)}</DetailRow><DetailRow label={record.allDay ? '结束（不含当天）' : '结束'} icon="time">{record.allDay ? dateTitle(record.end) : timeTitle(record.end)}</DetailRow></>}
          {raw.floating && <DetailRow label="时区" icon="time">浮动时间</DetailRow>}
        </dl></section>
        <dl className="stella-inspector-card"><DetailRow label="重复" icon="repeat">{recordRepeatLabel(raw)}</DetailRow><DetailRow label="提醒" icon="alarm">{alarms.length ? alarms.map((alarm, index) => <span key={index}>{alarmTitle(alarm)}</span>) : '无'}</DetailRow></dl>
        <section aria-label="组织"><h3 className="stella-inspector-section-title">组织</h3><dl className="stella-inspector-card"><DetailRow label={task ? '清单' : '日历'} icon="list"><span className="stella-inspector-group"><i style={{ background: group?.color ?? 'var(--stella-accent)' }} />{groupName(group) || raw.list_name || '正在读取归属'}</span>{group?.apple?.source_name && <small>{group.apple.source_name}</small>}</DetailRow>
          {task && <><DetailRow label="标签" icon="tag">{tags.length ? <span className="stella-inspector-tags">{tags.map(tag => <span key={tag}>#{tag}</span>)}</span> : '无'}</DetailRow><DetailRow label="旗标" icon="flag">{raw.flagged ? '已标记' : '未标记'}</DetailRow><DetailRow label="优先级" icon="priority">{priorityLabels[raw.priority ?? 0]}</DetailRow></>}
        </dl></section>
        {raw.location && <dl className="stella-inspector-card"><DetailRow label="位置" icon="location">{raw.location}</DetailRow></dl>}
        {raw.attendees?.length > 0 && <section aria-label="受邀人"><h3 className="stella-inspector-section-title">受邀人</h3><ul className="stella-inspector-card stella-inspector-attendees">{raw.attendees.map((person, index) => <li key={index}>{person.name || person.url}</li>)}</ul></section>}
        {raw.apple?.issues?.map(issue => <Notice key={issue}>{issue}</Notice>)}
        {value.viewTree && <Button variant="ghost" onClick={() => { close(); value.viewTree() }}>查看事务树{raw.subtask_count ? ` · ${raw.subtask_count} 个子事务` : ''}</Button>}
      </div>
      <footer className="stella-inspector-actions"><Button variant="ghost" className="stella-inspector-delete" disabled={!rights.remove} onClick={() => { if (closeOnDelete) close(); value.remove() }}>删除</Button><span />{task && <Button variant="outline" disabled={!rights.complete} onClick={async () => { if (await complete(record) && closeOnComplete) close() }}>{record.status === 'done' ? '标为未完成' : '标为已完成'}</Button>}<Button variant="primary" disabled={!rights.edit} onClick={() => { if (closeOnEdit) close(); edit(record) }}>编辑详情</Button></footer>
    </>}
  </Container>
}
