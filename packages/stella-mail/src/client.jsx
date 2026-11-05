import React, { useEffect, useState } from 'react'
import { Button, Checkbox, Input, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { remote } from './remote.js'
import style from '../../stella-elearning/src/settings.css'
import connectionStyle from '../../stella-secretary/src/connection-settings.css'
import { Field } from '../../stella-secretary/src/business-pages.jsx'

const empty = () => ({ host: '', user: '', password: '', port: 993, folder: 'INBOX', enabled: true })

function MailAccount({ account, revision, writable, busy, connected, onSave, onCheck, onRemove, onCancel }) {
  const [draft, setDraft] = useState({ ...account, password: '' })
  const [expectedRevision, setExpectedRevision] = useState(revision)
  const [edited, setEdited] = useState(false)
  const reset = () => { setDraft({ ...account, password: '' }); setExpectedRevision(revision); setEdited(false) }
  // Another mailbox's save must not discard an unfinished edit or silently refresh its revision.
  useEffect(() => { if (!edited) { setDraft({ ...account, password: '' }); setExpectedRevision(revision) } }, [account, revision, edited])
  const set = (key, value) => { setEdited(true); setDraft(previous => ({ ...previous, [key]: value })) }
  const disabled = busy || !writable
  return <section className="stella-source-account stella-mail-connection" data-mail-account={account.id || 'new'} aria-label={account.id ? `邮箱：${account.user}` : '添加邮箱'}>
    <h3>{account.id ? account.user : '添加邮箱'}</h3>
    {account.id && <span className="stella-mail-account-state">账号已保存</span>}
    <form autoComplete="off" onSubmit={async event => { event.preventDefault(); if (await onSave(draft, expectedRevision)) { setDraft(previous => ({ ...previous, password: '' })); setEdited(false) } }}>
      <Field label="邮箱账号"><Input required type="email" autoComplete="off" maxLength={320} disabled={disabled} value={draft.user} onChange={event => set('user', event.target.value)} /></Field>
      <Field label="邮箱授权码"><Input required={!account.id} type="password" autoComplete="new-password" maxLength={4096} disabled={disabled} placeholder={account.id ? '********' : ''} aria-description={account.id ? '授权码已保存，留空保留，输入新授权码以替换。' : undefined} value={draft.password} onChange={event => set('password', event.target.value)} /></Field>
      <div className="stella-source-grid"><Field label="IMAP 服务器"><Input required maxLength={253} placeholder="imap.example.com" disabled={disabled} value={draft.host} onChange={event => set('host', event.target.value)} /></Field><Field label="SSL 端口"><Input required type="number" min={1} max={65535} disabled={disabled} value={draft.port} onChange={event => set('port', Number(event.target.value))} /></Field></div>
      <Field label="邮件文件夹"><Input required maxLength={512} disabled={disabled} value={draft.folder} onChange={event => set('folder', event.target.value)} /></Field>
      <Checkbox label="启用邮箱" checked={draft.enabled} disabled={disabled} onChange={value => set('enabled', value)} />
      <div className="stella-actions"><Button type="submit" variant="primary" disabled={disabled || !edited}>保存邮箱</Button>{account.id ? <><Button type="button" variant="ghost" disabled={busy} onClick={onRemove}>移除邮箱</Button>{edited && <Button type="button" variant="ghost" disabled={busy} onClick={reset}>撤销修改</Button>}</> : onCancel && <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>取消添加</Button>}</div>
    </form>
    <div className="stella-mail-test"><Button variant="outline" disabled={busy || !connected || !account.id || !account.enabled || edited} onClick={onCheck}>测试连接</Button></div>
  </section>
}

function Settings({ api, view }) {
  const [state, setState] = useState(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const unwrap = async result => { const value = await result; if (!value.ok) throw new Error(value.error?.message || '操作未完成。'); return value.value }
  async function load() { const next = await unwrap(api.state({})); setState(next); return next }
  async function run(work) { setBusy(true); setError(''); setNotice(''); try { return await work() } catch (failure) { setError(failure.message) } finally { setBusy(false) } }
  useEffect(() => { if (view !== 'summary') void run(load) }, [api, view])
  if (view === 'summary') return '连接 IMAP 邮箱，供对话和简报按授权读取。'
  const save = (account, expectedRevision) => run(async () => { const next = await unwrap(api.save({ account, expectedRevision })); setState(next); setAdding(false); setNotice('账号已保存。'); return next })
  const props = { revision: state?.revision, writable: state?.writable, busy, connected: state?.connected, onSave: save }
  return <section className="stella-source-settings"><h2>邮件</h2>
    {!state && <p>正在读取…</p>}{state?.configured && !state.connected && <p>连接未就绪，请检查账号设置。</p>}
    <div className="stella-actions">{!!state?.accounts.length && <Button variant="primary" disabled={busy || !state.writable || adding || state.accounts.length >= 20} onClick={() => { setAdding(true); setError(''); setNotice('') }}>添加邮箱</Button>}<Button variant="outline" aria-label="刷新状态" title="刷新状态" disabled={busy} onClick={() => run(load)}><IconRefreshOutlineRegular size={16} /></Button></div>
    {state?.accounts.map(row => <MailAccount key={row.id} account={row} {...props} onRemove={() => setRemoving(row)} onCheck={() => run(async () => { const result = await unwrap(api.check({ id: row.id })); setNotice(result.status === 'ready' ? '连接有效，文件夹可读。' : '测试未通过，请核对授权码、IMAP 权限和网络。') })} />)}
    {state && (adding || !state.accounts.length) && <MailAccount account={empty()} {...props} onCancel={state.accounts.length ? () => setAdding(false) : undefined} />}
    {removing && <div role="alert"><p>移除后停止访问此邮箱，已生成的简报保留。</p><div className="stella-actions"><Button variant="outline" disabled={busy} onClick={() => run(async () => { await unwrap(api.removeAccount({ id: removing.id, expectedRevision: state.revision })); await load(); setRemoving(null); setNotice('邮箱已移除。') })}>确认移除邮箱</Button><Button variant="ghost" disabled={busy} onClick={() => setRemoving(null)}>取消移除</Button></div></div>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
export const inject = ['slots', 'remote']
export async function apply(ctx) {
  try {
    ctx.effect(() => { const element = document.createElement('style'); element.textContent = style + connectionStyle; document.head.append(element); return () => element.remove() })
    await ctx.remote.$mount(remote)
    ctx.inject(['remote.stellaMail'], inner => inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@stella/mail', inject: () => ({ api: inner.remote.stellaMail }) }, Settings)))
  } catch (failure) { console.error('邮件插件界面加载失败：', failure); throw failure }
}
