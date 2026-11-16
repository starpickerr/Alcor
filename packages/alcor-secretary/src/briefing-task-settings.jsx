import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { Button, Input } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'
import { Notice } from './notice.jsx'
import { briefingPrompt } from './briefing-task.js'

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
    <h2>任务设置</h2>
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
        <Field label="执行位置"><select disabled={busy} value={draft.task_target} onChange={event => setDraft({ ...draft, task_target: event.target.value })}><option value="new">每次新建会话</option><option value="current">当前对话</option></select></Field>
        <Field label="关联工作区">{draft.task_target === 'current' ? <Input readOnly value={currentWorkspace?.title || '当前对话所在工作区'} /> : <select disabled={busy} value={draft.task_workspace_id} onChange={event => setDraft({ ...draft, task_workspace_id: event.target.value })}>
          <option value="">当前工作区</option>{list.items.map(item => <option value={item.workspaceId} key={item.workspaceId}>{item.title}</option>)}
          {draft.task_workspace_id && !list.items.some(item => item.workspaceId === draft.task_workspace_id) && <option value={draft.task_workspace_id}>工作区不可用，请重新选择</option>}
        </select>}</Field>
      </div>
      <div className="alcor-briefing-task-footer"><Button type="button" variant="ghost" disabled={busy || !dirty} onClick={() => { setDraft(saved); setError('') }}>放弃</Button><Button type="submit" variant="primary" disabled={busy || !dirty || !draft.task_prompt.trim()}>{busy ? '正在保存…' : '保存修改'}</Button></div>
    </form>}
  </section>
}
