import noticeStyle from '../../alcor-secretary/src/notice.css'
import { Notice } from '../../alcor-secretary/src/notice.jsx'
import { checkMessage } from './check-status.js'
import React, { useEffect, useState } from 'react'
import { Button, Checkbox, Input, Modal, IconPlusOutlineRegular, IconSearchOutlineRegular, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { remote } from './remote.js'
import style from '../../alcor-elearning/src/settings.css'
import mailStyle from './style.css'
import connectionStyle from '../../alcor-secretary/src/connection-settings.css'
import { Field } from '../../alcor-secretary/src/business-pages.jsx'

const checkLabel = status => ({ ready: '连接正常', query_failed: '连接失败', disconnected: '未连接', checking: '正在测试' })[status] || '未验证'

const empty = () => ({ host: '', user: '', password: '', port: 993, folder: 'INBOX', enabled: true })

function MailAccount({ account, revision, writable, busy, connected, onSave, onCheck, onRemove, onCancel, onToggle, active, checkState }) {
  const [draft, setDraft] = useState({ ...account, password: '' })
  const [expectedRevision, setExpectedRevision] = useState(revision)
  const [edited, setEdited] = useState(false)
  const reset = () => { setDraft({ ...account, password: '' }); setExpectedRevision(revision); setEdited(false) }
  // Another mailbox's save must not discard an unfinished edit or silently refresh its revision.
  useEffect(() => { if (!edited) { setDraft({ ...account, password: '' }); setExpectedRevision(revision) } }, [account, revision, edited])
  const set = (key, value) => { setEdited(true); setDraft(previous => ({ ...previous, [key]: value })) }
  const disabled = busy || !writable
  return <section hidden={!active} className="alcor-source-account alcor-mail-connection" data-mail-account={account.id || 'new'} aria-label={account.id ? `邮箱：${account.user}` : '添加邮箱'}>
    <header className="alcor-mail-editor-heading"><h3>{account.id ? account.user : '添加邮箱'}</h3>{account.id && <span className="alcor-mail-state" data-state={checkState}>{!account.enabled ? '已停用' : checkLabel(checkState)}</span>}</header>
    <form autoComplete="off" onSubmit={async event => { event.preventDefault(); if (await onSave(draft, expectedRevision)) { setDraft(previous => ({ ...previous, password: '' })); setEdited(false) } }}>
      <Field label="邮箱账号"><Input required type="email" autoComplete="off" maxLength={320} disabled={disabled} value={draft.user} onChange={event => set('user', event.target.value)} /></Field>
      <Field label="邮箱授权码"><Input required={!account.id} type="password" autoComplete="new-password" maxLength={4096} disabled={disabled} placeholder={account.id ? '********' : ''} aria-description={account.id ? '授权码已保存，留空保留，输入新授权码以替换。' : undefined} value={draft.password} onChange={event => set('password', event.target.value)} /></Field>
      <div className="alcor-source-grid"><Field label="IMAP 服务器"><Input required maxLength={253} placeholder="imap.example.com" disabled={disabled} value={draft.host} onChange={event => set('host', event.target.value)} /></Field><Field label="SSL 端口"><Input required type="number" min={1} max={65535} disabled={disabled} value={draft.port} onChange={event => set('port', Number(event.target.value))} /></Field></div>
      <Field label="邮件文件夹"><Input required maxLength={512} disabled={disabled} value={draft.folder} onChange={event => set('folder', event.target.value)} /></Field>
      {!account.id && <Checkbox label="保存后启用邮箱" checked={draft.enabled} disabled={disabled} onChange={value => set('enabled', value)} />}
      <div className="alcor-actions"><Button type="submit" variant="primary" disabled={disabled || !edited}>保存邮箱</Button>{account.id ? <><Button type="button" variant="ghost" disabled={busy} onClick={onRemove}>移除邮箱</Button>{edited && <Button type="button" variant="ghost" disabled={busy} onClick={reset}>撤销修改</Button>}</> : onCancel && <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>取消添加</Button>}</div>
    </form>
    <div className="alcor-mail-test">{account.id && <Button variant="outline" disabled={disabled || edited} onClick={onToggle}>{account.enabled ? '停用邮箱' : '开启邮箱'}</Button>}<Button variant="outline" disabled={busy || !connected || !account.id || !account.enabled || edited} aria-busy={checkState === 'checking'} onClick={onCheck}>{checkState === 'checking' ? '正在测试…' : '测试连接'}</Button></div>
  </section>
}

function Settings({ api, view }) {
  const [state, setState] = useState(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState(null)
  const [selected, setSelected] = useState(null)
  const [query, setQuery] = useState('')
  const [checks, setChecks] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const unwrap = async result => { const value = await result; if (!value.ok) throw new Error(value.error?.message || '操作未完成。'); return value.value }
  async function load() { const next = await unwrap(api.state({})); setState(next); setSelected(current => current === 'new' || next.accounts.some(row => row.id === current) ? current : next.accounts[0]?.id ?? 'new'); return next }
  async function run(work) { setBusy(true); setError(''); setNotice(''); try { return await work() } catch (failure) { setError(failure.message) } finally { setBusy(false) } }
  useEffect(() => { if (view !== 'summary') void run(load) }, [api, view])
  if (view === 'summary') return '连接 IMAP 邮箱，供对话和简报按授权读取。'
  const save = (account, expectedRevision) => run(async () => { const next = await unwrap(api.save({ account, expectedRevision })); setState(next); setAdding(false); setSelected(account.id ?? next.accounts.find(row => row.user === account.user && row.host === account.host && row.folder === account.folder)?.id ?? next.accounts[0]?.id); setNotice('已保存'); return next })
  const props = { revision: state?.revision, writable: state?.writable, busy, connected: state?.connected, onSave: save }
  const checkState = row => !row.enabled ? 'disabled' : checks[row.id]?.revision === state?.revision ? checks[row.id].status : 'unchecked'
  const check = row => run(async () => {
    setChecks(previous => ({ ...previous, [row.id]: { revision: state.revision, status: 'checking' } }))
    try {
      const result = await unwrap(api.check({ id: row.id }))
      setChecks(previous => ({ ...previous, [row.id]: { revision: state.revision, status: result.status } }))
      if (result.status !== 'ready') setError(checkMessage(result))
    } catch (failure) {
      setChecks(previous => ({ ...previous, [row.id]: { revision: state.revision, status: 'query_failed' } }))
      throw failure
    }
  })
  const accounts = state?.accounts ?? []
  const visible = accounts.filter(row => `${row.user} ${row.host} ${row.folder}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
  return <section className="alcor-source-settings alcor-mail-settings"><header className="alcor-mail-header"><h2>邮箱 <small>{accounts.length || ''}</small></h2><div className="alcor-actions"><Button variant="outline" aria-label="刷新状态" title="刷新状态" disabled={busy} onClick={() => run(load)}><IconRefreshOutlineRegular size={16} /></Button><Button variant="primary" disabled={busy || !state?.writable || adding || accounts.length >= 20} title={accounts.length >= 20 ? '最多添加 20 个邮箱' : undefined} onClick={() => { setAdding(true); setSelected('new'); setError(''); setNotice('') }}><IconPlusOutlineRegular size={16} />添加邮箱</Button></div></header>
    {!state ? <Notice role="status">正在读取…</Notice> : <div className="alcor-mail-layout"><aside className="alcor-mail-sidebar" aria-label="邮箱列表"><Input aria-label="搜索邮箱" icon={<IconSearchOutlineRegular size={16} />} placeholder="搜索邮箱" value={query} onChange={event => setQuery(event.target.value)} /><div className="alcor-mail-accounts">{visible.map(row => <button key={row.id} type="button" className="alcor-mail-account-row" aria-label={`选择邮箱：${row.user}`} aria-current={selected === row.id ? 'true' : undefined} disabled={busy} onClick={() => { setSelected(row.id); setError(''); setNotice('') }}><span><strong>{row.user}</strong><small>{row.host}</small></span><span className="alcor-mail-state-dot" data-state={checkState(row)} title={!row.enabled ? '已停用' : checkLabel(checkState(row))} /></button>)}{(adding || !accounts.length) && <button type="button" className="alcor-mail-account-row" aria-current={selected === 'new' ? 'true' : undefined} onClick={() => setSelected('new')}>新邮箱</button>}{!!accounts.length && !visible.length && <p className="alcor-mail-empty">未找到邮箱</p>}</div></aside>
      <div className="alcor-mail-editor">{accounts.map(row => <MailAccount key={row.id} account={row} active={selected === row.id} checkState={checkState(row)} {...props} onRemove={() => setRemoving(row)} onToggle={() => run(async () => { await unwrap(api.save({ account: { ...row, enabled: !row.enabled, password: '' }, expectedRevision: state.revision })); await load(); setNotice(row.enabled ? '邮箱已停用' : '邮箱已开启') })} onCheck={() => check(row)} />)}
      {(adding || !accounts.length) && <MailAccount account={empty()} active={selected === 'new'} {...props} onCancel={accounts.length ? () => { setAdding(false); setSelected(accounts[0].id) } : undefined} />}
      {state.configured && !state.connected && <Notice tone="error" role="alert">邮件服务未就绪，请刷新后重试。</Notice>}
      {error && <Notice tone="error" role="alert">{error}</Notice>}{notice && <Notice tone="success" role="status">{notice}</Notice>}</div>
    </div>}
    <Modal open={!!removing} title="移除邮箱" onClose={() => { if (!busy) setRemoving(null) }}><Notice tone="warning">移除 {removing?.user}？已生成的简报保留。</Notice><div className="alcor-actions"><Button variant="outline" disabled={busy} onClick={() => setRemoving(null)}>取消移除</Button><Button variant="primary" disabled={busy} onClick={() => run(async () => { await unwrap(api.removeAccount({ id: removing.id, expectedRevision: state.revision })); await load(); setRemoving(null); setNotice('邮箱已移除') })}>确认移除邮箱</Button></div></Modal>
  </section>

}
export const inject = ['slots', 'remote']
export async function apply(ctx) {
  try {
    ctx.effect(() => { const element = document.createElement('style'); element.textContent = style + connectionStyle + mailStyle + noticeStyle; document.head.append(element); return () => element.remove() })
    await ctx.remote.$mount(remote)
    ctx.inject(['remote.alcorMail'], inner => inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@alcor/mail', inject: () => ({ api: inner.remote.alcorMail }) }, Settings)))
  } catch (failure) { console.error('邮件插件界面加载失败：', failure); throw failure }
}
