import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { Button, Input, Menu, MenuItemButton, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'
import { Notice } from './notice.jsx'
import { briefingPrompt } from './briefing-task.js'

function TaskOption({ label, value, options, onChange, disabled, pill }) {
  const [open, setOpen] = useState(false)
  const current = options.find(option => option.value === value)?.label || '工作区不可用，请重新选择'
  return <div className="alcor-briefing-task-option"><span>{label}</span><Menu open={open} onClose={() => setOpen(false)} portal autoFocus align="end" className="alcor-briefing-task-picker" listClassName={options.some(option => option.description) ? 'alcor-briefing-target-menu' : undefined}
    anchor={<Button type="button" variant="ghost" aria-label={label} aria-haspopup="menu" aria-expanded={open} disabled={disabled} data-pill={pill || undefined} title={current} onClick={() => setOpen(value => !value)}><span>{current}</span><IconChevronDownOutlineRegular size={14} /></Button>}>
    {options.map(option => <MenuItemButton key={option.value} current={value === option.value} selected={value === option.value} onSelect={() => { onChange(option.value); setOpen(false) }}><span className="alcor-briefing-target-choice"><span>{option.label}</span>{option.description && <span className="alcor-briefing-target-description">{option.description}</span>}</span></MenuItemButton>)}
  </Menu></div>
}

export function BriefingTaskSettings({ api, workspaces, currentSession }) {
  const list = useSyncExternalStore(listener => workspaces.subscribe(listener), () => workspaces.getSnapshot())
  const [saved, setSaved] = useState(null), [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function read() {
    const result = await api.business({ domain: 'briefing', method: 'get_preferences', arguments: {} })
    if (!result.ok) throw new Error(result.error?.message || '任务设置读取失败。')
    const value = { task_prompt: result.value.task_prompt || briefingPrompt, task_target: result.value.task_target, task_workspace_id: result.value.task_workspace_id }
    setSaved(value); setDraft(value)
  }
  useEffect(() => { read().catch(error => setError(error.message)) }, [api])
  const dirty = draft && JSON.stringify(draft) !== JSON.stringify(saved)
  const currentWorkspace = list.items.find(item => item.sessionIds.includes(currentSession()))
  return <section className="alcor-briefing-task-form">
    <h2>生成方式</h2>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {!draft ? <><Notice role="status">{error ? '任务设置暂不可用' : '正在读取任务设置…'}</Notice>{error && <Button variant="outline" onClick={() => { setError(''); read().catch(error => setError(error.message)) }}>重试</Button>}</> : <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('')
      try {
        const result = await api.business({ domain: 'briefing', method: 'update_preferences', arguments: { payload: draft }, requestId: crypto.randomUUID() })
        if (!result.ok) throw new Error(result.error?.message || '任务设置保存失败。')
        setSaved(draft)
      } catch (error) { setError(error.message) } finally { setBusy(false) }
    }}>
      <Field label="任务名称"><Input readOnly value="生成/更新简报" /></Field>
      <Field label="任务内容"><textarea required rows={6} maxLength={12000} disabled={busy} value={draft.task_prompt} onChange={event => setDraft({ ...draft, task_prompt: event.target.value })} /></Field>
      <div className="alcor-briefing-task-options">
        <TaskOption label="执行位置" pill disabled={busy} value={draft.task_target} options={[
          { value: 'new_once', label: '新建会话并复用', description: '首次运行时创建，之后沿用这个会话' },
          { value: 'new', label: '每次新建会话', description: '每次运行都创建独立会话' },
          ...(draft.task_target === 'current' ? [{ value: 'current', label: '当前对话', description: '沿用原有执行位置设置' }] : []),
        ]} onChange={task_target => setDraft({ ...draft, task_target })} />
        <TaskOption label="关联工作区" pill disabled={busy || draft.task_target === 'current'} value={draft.task_target === 'current' ? '' : draft.task_workspace_id}
          options={draft.task_target === 'current' ? [{ value: '', label: currentWorkspace?.title || '当前对话所在工作区' }] : [{ value: '', label: '当前工作区' }, ...list.items.map(item => ({ value: item.workspaceId, label: item.title }))]}
          onChange={task_workspace_id => setDraft({ ...draft, task_workspace_id })} />
      </div>
      <div className="alcor-briefing-task-footer"><Button type="button" variant="ghost" disabled={busy || !dirty} onClick={() => { setDraft(saved); setError('') }}>放弃</Button><Button type="submit" variant="primary" disabled={busy || !dirty || !draft.task_prompt.trim()}>{busy ? '正在保存…' : '保存修改'}</Button></div>
    </form>}
  </section>
}
