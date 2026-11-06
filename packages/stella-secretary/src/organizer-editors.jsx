import { repeatDraft, repeatPayload, repeatOptions } from './task-frequency.js'
import React, { useState } from 'react'
import { Button, Input, Checkbox } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'
import { arrayValue, localTime, midnight } from './organizer-forms.js'
import { localDate } from './presentation.js'

const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
function TaskRepetition({ value, set, disabled }) {
  const frequency = value.repeat_mode === 'custom' ? value.repeat_frequency : value.repeat_mode
  return <fieldset className="stella-task-repetition" disabled={disabled}><Field label="频率"><select value={value.repeat_mode} onChange={event => set('repeat_mode', event.target.value)}>{Object.entries(repeatOptions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
    {value.repeat_mode === 'custom' && <div className="stella-repeat-interval"><span>每</span><Input aria-label="重复间隔" required type="number" min={1} max={99} value={value.repeat_interval} onChange={event => set('repeat_interval', event.target.value)} /><select aria-label="重复单位" value={value.repeat_frequency} onChange={event => set('repeat_frequency', event.target.value)}>{Object.entries({ daily: '天', weekly: '周', monthly: '月', yearly: '年' }).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>}
    {frequency === 'weekly' && <div className="stella-repeat-weekdays" role="group" aria-label="重复星期">{['一','二','三','四','五','六','日'].map((day, index) => <Checkbox key={day} label={`周${day}`} checked={value.repeat_days.includes(index)} onChange={checked => set('repeat_days', checked ? [...value.repeat_days, index].sort() : value.repeat_days.filter(item => item !== index))} />)}</div>}
    {value.repeat_mode !== 'none' && <Field label="结束日期"><Input type="date" min={value.due_on || undefined} value={value.repeat_until} onChange={event => set('repeat_until', event.target.value)} /></Field>}
  </fieldset>
}

const repeats = { none: '不重复', daily: '每天', weekly: '每周', monthly: '每月', yearly: '每年' }
function Repetition({ value, set }) {
  return <><Field label="重复频率"><select value={value.repeat_frequency} onChange={event => set('repeat_frequency', event.target.value)}>{Object.entries(repeats).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>{value.repeat_frequency !== 'none' && <><Field label="重复截止日期"><Input type="date" value={value.repeat_until ?? ''} onChange={event => set('repeat_until', event.target.value)} /></Field>{value.repeat_frequency === 'weekly' && <fieldset className="stella-repeat-days"><legend>每周重复日（留空沿用开始日期）</legend>{['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((label, index) => <Checkbox key={label} label={label} checked={value.repeat_days.includes(index)} onChange={checked => set('repeat_days', checked ? [...value.repeat_days, index] : value.repeat_days.filter(day => day !== index))} />)}</fieldset>}</> }</>
}

export function TaskEditor({ record, save, cancel, busy }) {
  const raw = record?.business ?? {}
  const [value, setValue] = useState({ title: record?.title ?? '', notes: record?.details ?? '', due_on: record?.due ?? '', due_time: raw.due_time ?? '', priority: raw.priority ?? 0, tags: arrayValue(raw.tags).join(', '), ...repeatDraft(raw) })
  const set = (key, next) => setValue(previous => ({ ...previous, [key]: next }))
  return <form className="stella-editor stella-task-editor" aria-label={record ? '编辑事务' : '新增事务'} onSubmit={event => { event.preventDefault(); const { repeat_mode, ...fields } = value; void save({ ...fields, ...repeatPayload(value), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, due_on: value.due_on || null, due_time: value.due_time || null, tags: value.tags.split(/[,，]/).map(tag => tag.trim()).filter(Boolean) }) }}>
    <h2>{record ? '编辑事务' : '新增事务'}</h2>
    <Field label="标题"><Input data-modal-autofocus required value={value.title} onChange={event => set('title', event.target.value)} /></Field>
    <Field label="事务详情"><textarea rows={4} value={value.notes} onChange={event => set('notes', event.target.value)} /></Field>
    <div className="stella-form-grid"><Field label="截止日期"><Input required={value.repeat_mode !== 'none'} type="date" value={value.due_on} onChange={event => set('due_on', event.target.value)} /></Field><Field label="截止时间"><Input type="time" value={value.due_time} onChange={event => set('due_time', event.target.value)} /></Field></div>
    <div className="stella-form-grid"><Field label="优先级"><select value={value.priority} onChange={event => set('priority', Number(event.target.value))}><option value="0">无</option><option value="1">高</option><option value="2">中</option><option value="3">低</option></select></Field><Field label="标签"><Input placeholder="客户, 方案" value={value.tags} onChange={event => set('tags', event.target.value)} /></Field></div>
    <TaskRepetition value={value} set={set} disabled={busy || !!raw.parent_id || raw.subtask_count > 0} />
    <div className="stella-actions"><Button type="submit" variant="primary" disabled={busy}>保存事务</Button><Button type="button" variant="outline" disabled={busy} onClick={cancel}>取消编辑</Button></div>
  </form>
}

export function CalendarEditor({ record, draft, categories, save, cancel, busy }) {
  const raw = record?.business ?? {}
  const [value, setValue] = useState({ title: record?.title ?? '', description: record?.details ?? '', start: record ? (record.allDay ? record.start : localTime(raw.start_at)) : draft?.start ?? `${localDate()}T09:00`, end: record ? (record.allDay ? record.end : localTime(raw.end_at)) : draft?.end ?? `${localDate()}T10:00`, all_day: record?.allDay ?? false, location: raw.location ?? '', category_id: raw.category_id ?? '', repeat_frequency: raw.repeat_frequency ?? 'none', repeat_days: arrayValue(raw.repeat_days), repeat_until: raw.repeat_until ?? '', repeat_timezone: raw.repeat_timezone || zone })
  const [error, setError] = useState('')
  const set = (key, next) => setValue(previous => ({ ...previous, [key]: next }))
  return <form className="stella-editor" aria-label={record ? '编辑日程' : '新增日程'} onSubmit={async event => { event.preventDefault(); setError(''); try { await save({ title: value.title, description: value.description, location: value.location, category_id: value.category_id || null, all_day: value.all_day, start_at: value.all_day ? midnight(value.start, value.repeat_timezone) : new Date(value.start).toISOString(), end_at: value.all_day ? midnight(value.end, value.repeat_timezone) : new Date(value.end).toISOString(), repeat_frequency: value.repeat_frequency, repeat_days: value.repeat_days, repeat_until: value.repeat_until || null, repeat_timezone: value.repeat_timezone }) } catch (failure) { setError(failure.message) } }}>
    <h2>{record ? '编辑日程' : '新增日程'}</h2>{record?.business?.repeat_frequency !== 'none' && record && <p className="stella-muted">编辑会修改整个重复系列；跳过某一天请在日历中使用“跳过本次”。</p>}<Field label="标题"><Input data-modal-autofocus required value={value.title} onChange={event => set('title', event.target.value)} /></Field><Checkbox label="全天日程" checked={value.all_day} onChange={checked => setValue(previous => ({ ...previous, all_day: checked, start: checked ? previous.start.slice(0, 10) : `${previous.start}T09:00`, end: checked ? previous.end.slice(0, 10) : `${previous.end}T10:00` }))} />
    <div className="stella-form-grid"><Field label="开始"><Input required type={value.all_day ? 'date' : 'datetime-local'} value={value.start} onChange={event => set('start', event.target.value)} /></Field><Field label={value.all_day ? '结束日期（不含当天）' : '结束'}><Input required type={value.all_day ? 'date' : 'datetime-local'} value={value.end} onChange={event => set('end', event.target.value)} /></Field></div><p className="stella-muted">本机时区：{zone}；全天与重复日程使用日程时区。</p><Field label="日程分类"><select value={value.category_id} onChange={event => set('category_id', event.target.value)}><option value="">未分类</option>{categories.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="地点"><Input value={value.location} onChange={event => set('location', event.target.value)} /></Field><Field label="日程时区"><Input required value={value.repeat_timezone} onChange={event => set('repeat_timezone', event.target.value)} /></Field><Repetition value={value} set={set} /><Field label="备注"><textarea rows={3} value={value.description} onChange={event => set('description', event.target.value)} /></Field>{error && <p role="alert">{error}</p>}<div className="stella-actions"><Button type="submit" variant="primary" disabled={busy}>保存日程</Button><Button type="button" variant="outline" disabled={busy} onClick={cancel}>取消编辑</Button></div>
  </form>
}
