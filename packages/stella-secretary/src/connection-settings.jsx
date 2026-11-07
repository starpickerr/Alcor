import React, { useEffect, useState } from 'react'
import { Button, Input } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'
import { checkMessage } from './elearning-status.js'

const unwrap = async promise => { const result = await promise; if (!result.ok) throw new Error(result.error?.message || '操作失败，请重试。'); return result.value }

export function SchoolConnection({ api }) {
  const [account, setAccount] = useState(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [removing, setRemoving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const load = async () => { const value = await unwrap(api.elearningAccount({})); setAccount(value); setUsername(value.username || '') }
  async function run(work) { setBusy(true); setError(''); setNotice(''); try { await work() } catch (failure) { setError(failure.message) } finally { setBusy(false) } }
  useEffect(() => { void run(load) }, [api])
  if (account && !account.installed) return null
  const savedPassword = account?.configured && username === (account.username || '')
  return <section className="stella-connection-settings stella-school-connection" aria-label="学校连接">
    <span className="stella-school-account-state">{!account ? '正在读取…' : account.configured ? '账号已保存' : '未配置账号'}</span>
    <form autoComplete="off" onSubmit={event => { event.preventDefault(); const input = { username, password }; void run(async () => { await unwrap(api.saveElearningAccount(input)); setPassword(''); await load(); setNotice('账号已保存。') }) }}>
      <Field label="学工号"><Input required maxLength={160} autoComplete="off" disabled={busy || !account?.writable} value={username} onChange={event => { setUsername(event.target.value); setNotice('') }} /></Field>
      <Field label="eLearning 密码"><Input required type="password" maxLength={4096} autoComplete="new-password" disabled={busy || !account?.writable} placeholder={savedPassword ? '********' : ''} aria-description={savedPassword ? '密码已保存，输入新密码以替换。' : undefined} value={password} onChange={event => { setPassword(event.target.value); setNotice('') }} /></Field>
      <div className="stella-actions"><Button type="submit" variant="primary" disabled={busy || !account?.writable || !username.trim() || !password}>保存账号</Button>{account?.configured && <Button type="button" variant="ghost" disabled={busy} onClick={() => setRemoving(true)}>移除账号</Button>}</div>
    </form>
    {removing && <div className="stella-school-remove"><p>移除账号会停止后续访问，历史记录保留。</p><div className="stella-actions"><Button variant="outline" disabled={busy} onClick={() => run(async () => { await unwrap(api.removeElearningAccount({})); setPassword(''); await load(); setRemoving(false); setNotice('账号已移除，历史记录已保留。') })}>确认移除账号</Button><Button variant="ghost" disabled={busy} onClick={() => setRemoving(false)}>取消</Button></div></div>}
    <div className="stella-school-test"><Button variant="outline" disabled={busy || !savedPassword || !!password} onClick={() => run(async () => { const result = await unwrap(api.checkElearning({})); setNotice(result.status === 'ready' ? `连接有效，可访问 ${result.courseCount} 门课程。` : checkMessage(result)) })}>测试连接</Button></div>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
