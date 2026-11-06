import React, { useEffect, useState } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import { remote } from './remote.js'
import { connectionStatus, formatSyncTime } from './presentation.js'
import style from './style.css'

export const inject = ['slots', 'remote']
const authorizations = { not_determined: '尚未授权', denied: '系统访问已拒绝，请在 macOS 系统设置中允许访问。', restricted: '系统限制访问', write_only: '仅有写入权限，需要完整访问', unsupported: '仅支持 macOS', helper_missing: '系统桥未安装', full_access: '已获系统授权' }

function Connection({ kind, api }) {
  const title = kind === 'calendar' ? 'Apple 日历' : 'Apple 提醒事项'
  const [state, setState] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const call = async (method, args = {}) => {
    const result = await api.invoke({ method, arguments: { kind, ...args } })
    if (!result.ok) throw new Error(result.error?.message ?? '操作未成功，请刷新核对。')
    return result.value
  }
  const accept = setState
  async function run(operation) {
    setBusy(true); setMessage('')
    try { await operation() }
    catch (error) { setMessage(error.message) }
    finally { setBusy(false) }
  }
  useEffect(() => {
    let disposed = false
    const refresh = () => call('status').then(value => { if (!disposed) accept(value) }).catch(error => { if (!disposed) setMessage(error.message) })
    void refresh()
    const timer = setInterval(refresh, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [api, kind])
  const options = state?.calendars ?? state?.lists ?? []
  const active = state?.connected && state?.auto_enabled
  async function sync() {
    if (state?.authorization !== 'full_access') {
      const authorized = await call('request_access')
      accept(authorized)
      if (authorized.authorization !== 'full_access') throw new Error(authorized.authorization === 'not_determined' ? '系统未能完成授权请求，请重试同步。' : authorizations[authorized.authorization] ?? '需要系统完整访问授权。')
    }
    const { state: saved, result } = await call('start_sync')
    accept(saved)
    if (result.status !== 'success' && !saved.error) setMessage('部分项目尚未同步，请重试。')
  }
  const status = connectionStatus(state, busy, Boolean(message))
  const lastTime = formatSyncTime(state?.last_success)
  return <section className="apple-sync-connection" aria-label={title}>
    <div className="apple-sync-heading">
      <h3>{title}</h3>
      <div className="apple-sync-indicators">
        {options.some(item => !item.writable) && <span className="apple-sync-badge" title="只读来源仅支持导入，无法将 Stella 的更改写回 Apple。">含只读来源</span>}
        <span className="apple-sync-badge" data-tone={status.tone} role="status"><span className="apple-sync-dot" aria-hidden="true" />{status.label}</span>
      </div>
    </div>
    {lastTime && <div className="apple-sync-metadata"><div className="apple-sync-last"><span>上次成功同步</span><time dateTime={state.last_success}>{lastTime}</time></div></div>}
    {state?.error && <p role="alert" className="apple-sync-error">{state.error}</p>}
    <div className="apple-sync-actions">
      <Button variant="primary" disabled={busy || !state?.supported} onClick={() => void run(sync)}>{busy ? '处理中…' : '同步'}</Button>
      <Button variant="outline" disabled={busy || !active} onClick={() => void run(async () => accept(await call('disconnect')))}>暂停同步</Button>
    </div>
    {!!state?.conflicts?.length && <div><h4>待处理冲突</h4>{state.conflicts.map(item => <div className="apple-sync-conflict" key={item.stella_id ?? item.todo_id}><p>{item.reason}</p><div className="apple-sync-actions">{[['keep_stella', '保留 Stella'], ['keep_apple', '采用 Apple']].map(([choice, label]) => <Button key={choice} variant="outline" disabled={busy} onClick={() => void run(async () => accept(await call('resolve_conflict', { [kind === 'calendar' ? 'stella_id' : 'todo_id']: item.stella_id ?? item.todo_id, choice })))}>{label}</Button>)}</div></div>)}</div>}
    {message && message !== state?.error && <p role="alert" className="apple-sync-error">{message}</p>}
  </section>
}

function Settings({ api, view }) {
  if (view === 'summary') return '双向同步 Apple 日历与提醒事项，保留分类和清单。'
  return <div className="apple-sync-settings"><h2>Apple 同步</h2><Connection kind="calendar" api={api} /><Connection kind="reminders" api={api} /></div>
}

export async function apply(ctx) {
  ctx.effect(() => { const element = document.createElement('style'); element.textContent = style; document.head.append(element); return () => element.remove() })
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.stellaAppleSync'], inner => {
    inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@stella/apple-sync', inject: () => ({ api: inner.remote.stellaAppleSync }) }, Settings))
  })
}
