import React, { useEffect, useState } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import { remote } from './remote.js'
import { connectionStatus, formatSyncTime } from './presentation.js'
import style from './style.css'

export const inject = ['slots', 'remote']
const authorizations = { not_determined: '尚未授权', denied: '系统访问已拒绝，请在 macOS 系统设置中允许访问。', restricted: '系统限制访问', write_only: '仅有写入权限，需要完整访问', unsupported: '仅支持 macOS', helper_missing: '系统桥未安装', full_access: '已获系统授权' }
const summary = value => Object.entries({ new: '新增', imported: '导入', exported: '导出', updated: '更新', skipped: '跳过', conflicts: '冲突', deleted_remote: 'Apple 删除', deleted_local: 'Stella 删除', pending_deletions: '待删除 Apple 记录', pending_local_deletions: '待删除 Stella 记录' }).filter(([key]) => typeof value[key] === 'number').map(([key, label]) => `${label} ${value[key]}`).join('，') + ((value.failed_calendar_ids ?? value.failed_list_ids ?? []).length ? '；部分来源读取失败，请重试同步。' : '')

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
  async function run(operation, notice) {
    setBusy(true); setMessage('')
    try { await operation(); if (notice) setMessage(notice) }
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
      if (authorized.authorization !== 'full_access') throw new Error(authorizations[authorized.authorization] ?? '需要系统完整访问授权。')
    }
    const { state: saved, result } = await call('start_sync')
    accept(saved)
    setMessage((result.status === 'success' ? '同步已开启。' : '同步已开启，部分项目需要处理。') + '同步结果：' + summary(result))
  }
  const status = connectionStatus(state, busy, Boolean(message))
  const lastTime = formatSyncTime(state?.last_success)
  return <section className="apple-sync-connection" aria-label={title}>
    <div className="apple-sync-heading">
      <h3>{title}</h3>
      <span className="apple-sync-badge" data-tone={status.tone} role="status"><span className="apple-sync-dot" aria-hidden="true" />{status.label}</span>
    </div>
    <div className="apple-sync-metadata">
      <span className="apple-sync-authorization">{state ? authorizations[state.authorization] ?? state.authorization : '正在读取系统授权…'}</span>
      <div className="apple-sync-last"><span>上次成功同步</span>{lastTime ? <time dateTime={state.last_success}>{lastTime}</time> : <span>尚无成功同步记录</span>}</div>
    </div>
    {state?.error && <p role="alert">{state.error}</p>}
    <div className="apple-sync-actions">
      <Button variant="primary" disabled={busy || !state?.supported} onClick={() => void run(sync)}>{busy ? '处理中…' : '同步'}</Button>
      <Button variant="outline" disabled={busy || !active} onClick={() => void run(async () => accept(await call('disconnect')), '同步已暂停，分类和记录保留。')}>暂停同步</Button>
    </div>
    <p>同步全部{kind === 'calendar' ? '日历' : '提醒事项'}并保留分类；双方增删改自动同步，需保持客户端运行。</p>
    {options.some(item => !item.writable) && <p>只读来源仅导入，无法将 Stella 的更改写回 Apple。</p>}
    {!!state?.links?.length && <p className="apple-sync-saved">分类：{state.links.filter(link => link.enabled).map(link => link.stella_name).join('、')}</p>}
    {!!state?.conflicts?.length && <div><h4>待处理冲突</h4>{state.conflicts.map(item => <div className="apple-sync-conflict" key={item.stella_id ?? item.todo_id}><p>{item.reason}</p><div className="apple-sync-actions">{[['keep_stella', '保留 Stella'], ['keep_apple', '采用 Apple']].map(([choice, label]) => <Button key={choice} variant="outline" disabled={busy} onClick={() => void run(async () => accept(await call('resolve_conflict', { [kind === 'calendar' ? 'stella_id' : 'todo_id']: item.stella_id ?? item.todo_id, choice })), '冲突已处理。')}>{label}</Button>)}</div></div>)}</div>}
    <p role="status" className="apple-sync-message">{message}</p>
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
