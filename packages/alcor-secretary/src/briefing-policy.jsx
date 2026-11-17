import React, { useEffect, useState } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'
import { Notice } from './notice.jsx'

const options = [
  ['assignment', '作业待办', 'pending', '仅未提交且未过截止时间'],
  ['grade', '单项成绩', 'published', '收录已公布成绩及改分'],
  ['course_grade', '课程总评与分组合计', 'include', '收录'],
]

export function BriefingPolicy({ api }) {
  const [policy, setPolicy] = useState(null), [saved, setSaved] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function call(method, args = {}) {
    const result = await api.business({ domain: 'briefing', method, arguments: args, ...(method === 'update_preferences' ? { requestId: crypto.randomUUID() } : {}) })
    if (!result.ok) throw new Error(result.error?.message || '收录设置操作失败。')
    return result.value
  }
  useEffect(() => {
    let active = true
    call('get_preferences').then(value => { if (active) { setPolicy(value.elearning_policy || {}); setSaved(value.elearning_policy || {}) } }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [api])
  return <section className="alcor-briefing-policy">
    <h2>eLearning 收录内容</h2>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {policy && <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('')
      try { const value = await call('update_preferences', { payload: { elearning_policy: policy } }); setSaved(value.elearning_policy) }
      catch (error) { setError(error.message) } finally { setBusy(false) }
    }}>
      {options.map(([key, label, mode, text]) => <Field key={key} label={label}><select aria-label={label} disabled={busy} value={policy[key] || 'model'} onChange={event => setPolicy({ ...policy, [key]: event.target.value })}>
        <option value={mode}>{text}</option><option value="exclude">略去</option><option value="model">按正文和自定义规则判断</option>
      </select></Field>)}
      <p>收录选项优先决定对应类型是否入选。需要按正文设置例外时，选择「按正文和自定义规则判断」。</p>
      <Button type="submit" variant="primary" disabled={busy || JSON.stringify(policy) === JSON.stringify(saved)}>{busy ? '正在保存…' : '保存收录设置'}</Button>
    </form>}
  </section>
}
