import React, { useEffect, useState } from 'react'
import { Button, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { BriefingToggle } from './briefing-toggle.jsx'
import { Notice } from './notice.jsx'

const options = [
  ['announcement', '公告', 'include', '完整收录公告正文、图片引用及附件'],
  ['assignment', '作业', 'pending', '尚未提交且未过截止时间'],
  ['quiz', '测验', 'pending', '尚未完成且未过截止时间'],
  ['grade', '单项成绩', 'published', '已公布的作业、测验与考试成绩，含改分和零分'],
  ['course_grade', '课程总评', 'include', '课程总分、加权总评与评分组小计'],
  ['file', '课程文件', 'grouped', '将课程文件更新汇总为一张卡片'],
]

export function BriefingPolicy({ api }) {
  const [policy, setPolicy] = useState(null), [saved, setSaved] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('')
  async function call(method, args = {}) {
    const result = await api.business({ domain: 'briefing', method, arguments: args, ...(method === 'update_preferences' ? { requestId: crypto.randomUUID() } : {}) })
    if (!result.ok) throw new Error(result.error?.message || '收录设置操作失败。')
    return result.value
  }
  async function read() {
    const value = await call('get_preferences')
    setPolicy(value.elearning_policy || {}); setSaved(value.elearning_policy || {})
  }
  useEffect(() => { read().catch(error => setError(error.message)) }, [api])
  const changed = policy && Object.fromEntries(Object.entries(policy).filter(([key, value]) => value !== saved[key]))
  return <section className="alcor-briefing-policy" aria-labelledby="alcor-briefing-policy-heading">
    <div className="alcor-briefing-policy-heading"><div><h2 id="alcor-briefing-policy-heading">eLearning</h2><p className="alcor-muted">选择简报要收录的内容。也可以在对话中让 Agent 调整。</p></div><Button variant="ghost" aria-label="刷新内容偏好" disabled={busy || !!Object.keys(changed || {}).length} onClick={() => { setError(''); read().catch(error => setError(error.message)) }}><IconRefreshOutlineRegular size={16} /></Button></div>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {!policy ? <Button variant="ghost" disabled={busy} onClick={() => { setError(''); read().catch(error => setError(error.message)) }}>{error ? '重新读取设置' : '正在读取…'}</Button> : <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(''); setMessage('')
      try {
        const value = await call('update_preferences', { payload: { elearning_policy: changed } })
        setPolicy(value.elearning_policy); setSaved(value.elearning_policy); setMessage('已保存')
      } catch (error) { setError(error.message) } finally { setBusy(false) }
    }}>
      <div className="alcor-briefing-policy-list">{options.map(([key, label, mode, description]) => {
        const value = policy[key] || 'model', enabled = value !== 'exclude'
        return <div className="alcor-briefing-policy-row" key={key}>
          <div><strong>{label}</strong><p>{description}</p></div>
          {enabled && <select aria-label={`${label}收录方式`} disabled={busy} value={value} onChange={event => { setPolicy({ ...policy, [key]: event.target.value }); setMessage('') }}>
            <option value={mode}>按上述条件收录</option><option value="model">按正文和补充要求判断</option>
          </select>}
          <BriefingToggle label={`收录${label}`} checked={enabled} disabled={busy} onChange={enabled => { setPolicy({ ...policy, [key]: enabled ? mode : 'exclude' }); setMessage('') }} />
        </div>
      })}</div>
      <div className="alcor-briefing-policy-footer"><span role="status">{message}</span><Button type="submit" variant="primary" disabled={busy || !Object.keys(changed).length}>{busy ? '正在保存…' : '保存偏好'}</Button></div>
    </form>}
  </section>
}
