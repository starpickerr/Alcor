import React, { useEffect, useState } from 'react'
import { Button, Checkbox, Input } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'

const unwrap = async promise => { const result = await promise; if (!result.ok) throw new Error(result.error?.message || '操作失败，请重试。'); return result.value }
const label = name => ({ list_courses: '查看课程列表', fetch: '读取课程信息', fetch_source: '读取更新', fetch_messages: '读取邮件', list_sources: '查看来源列表', list_accounts: '查看账号列表', list_files: '查看文件目录', sync_files: '下载文件' })[name.split('__').at(-1)] || name.split('__').at(-1).replaceAll('_', ' ')

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
    <div className="stella-school-test"><Button variant="outline" disabled={busy || !savedPassword || !!password} onClick={() => run(async () => { const result = await unwrap(api.checkElearning({})); setNotice(result.status === 'ready' ? `连接有效，可访问 ${result.courseCount} 门课程。` : '测试未通过，请核对账号和网络。') })}>测试连接</Button></div>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}

export function ConnectionSettings({ api, openPlugins }) {
  const [grants, setGrants] = useState(null)
  const [allowed, setAllowed] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function load() { const value = await unwrap(api.mcpState({})); setGrants(value); setAllowed(value.allowedTools) }
  async function run(work) { setBusy(true); setError(''); setNotice(''); try { await work() } catch (failure) { setError(failure.message) } finally { setBusy(false) } }
  useEffect(() => { void run(load) }, [api])
  const tools = [...new Set([...(grants?.availableTools || []), ...(grants?.allowedTools || [])])]
  return <section className="stella-connection-settings"><h3>连接与对话权限</h3><p className="stella-muted">仅授权 Stella 主对话；简报来源单独设置。</p><Button variant="outline" onClick={openPlugins}>管理 DSH 插件</Button>
    {grants && <details><summary>Stella 对话工具</summary><p className="stella-muted">所选工具的查询结果会发送给模型。</p>{tools.length ? tools.map(name => <div className="stella-connection-tool" key={name}><Checkbox label={`${name.split('__')[1]} · ${label(name)}`} checked={allowed.includes(name)} disabled={busy || (!grants.availableTools.includes(name) && !allowed.includes(name))} onChange={checked => setAllowed(checked ? [...allowed, name] : allowed.filter(value => value !== name))} />{!grants.availableTools.includes(name) && <small>连接不可用</small>}</div>) : <p className="stella-muted">尚无已连接的工具。</p>}<Button variant="outline" disabled={busy} onClick={() => run(async () => { await unwrap(api.setMcpTools({ expectedRevision: grants.revision, allowedTools: allowed })); await load(); setNotice('对话权限已保存。') })}>保存对话权限</Button></details>}
    <SchoolConnection api={api} />
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
