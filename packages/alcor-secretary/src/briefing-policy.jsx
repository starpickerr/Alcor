import React, { useEffect, useState } from 'react'
import { Button, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { BriefingToggle } from './briefing-toggle.jsx'
import { Notice } from './notice.jsx'

const options = [
  ['announcement', '公告', 'include', '全部新公告，保留正文、图片和附件'],
  ['assignment', '作业', 'pending', '仅未提交且未过期的作业'],
  ['quiz', '测验', 'pending', '仅未完成且未过期的测验'],
  ['grade', '单项成绩', 'published', '全部已公布成绩，包括零分和改分'],
  ['course_grade', '课程总评', 'include', '课程总分、加权总评与评分组小计'],
  ['file', '课程文件', 'grouped', '所有课程文件更新汇总为一张卡片'],
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
    <div className="alcor-briefing-policy-heading"><div><h2 id="alcor-briefing-policy-heading">eLearning</h2><p className="alcor-muted">打开想看的内容。需要调整条件时，可以直接在对话中说明。</p></div><Button variant="ghost" aria-label="刷新内容偏好" disabled={busy || !!Object.keys(changed || {}).length} onClick={() => { setError(''); read().catch(error => setError(error.message)) }}><IconRefreshOutlineRegular size={16} /></Button></div>
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
          <div><strong>{label}</strong>{value === 'model' ? <p>按补充要求筛选 · <a href="#alcor-briefing-extra-requirements" aria-label={`查看${label}补充要求`}>查看要求</a></p> : <p>{description}</p>}</div>
          {enabled && value === 'model' && <Button variant="ghost" size="sm" aria-label={`恢复${label}默认条件`} disabled={busy} onClick={() => { setPolicy({ ...policy, [key]: mode }); setMessage('') }}>恢复默认</Button>}
          <BriefingToggle label={`收录${label}`} checked={enabled} disabled={busy} onChange={enabled => { setPolicy({ ...policy, [key]: enabled ? mode : 'exclude' }); setMessage('') }} />
        </div>
      })}</div>
      <div className="alcor-briefing-policy-footer"><span role="status">{message}</span><Button type="submit" variant="primary" disabled={busy || !Object.keys(changed).length}>{busy ? '正在保存…' : '保存偏好'}</Button></div>
    </form>}
  </section>
}
