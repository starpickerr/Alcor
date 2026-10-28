import { useCallback, useEffect, useRef, useState } from 'react'
import apiClient, { type McpAuthorizationState } from '../../api/client'
import { c } from './presentation'

type Action = 'status' | 'authorize' | 'cancel' | 'remove'
const button = 'min-h-10 rounded-btn border border-default px-3 py-2 text-sm hover:bg-surface-2 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent'

export default function McpOAuthSettings({ name }: { name: string }) {
  const [data, setData] = useState<McpAuthorizationState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const request = useRef<AbortController | null>(null)
  const saving = useRef(false)
  const perform = useCallback(async (action: Action, silent = false) => {
    if (silent ? request.current !== null : saving.current) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    if (!silent) { saving.current = true; setBusy(true); setError(''); setNotice('') }
    try {
      const result = await apiClient.manageMcpAuthorization(name, action, controller.signal)
      if (request.current !== controller || controller.signal.aborted) return
      setData(result)
      setError('')
      if (action === 'cancel') setNotice(c('授权流程已取消，已保存凭据保持不变。', 'Authorization cancelled. Saved credentials are unchanged.'))
      if (action === 'remove') setNotice(c('已删除授权，服务配置、业务数据和工具开关保留。', 'Authorization removed. Service settings, business data and tool switches are preserved.'))
    } catch (cause) {
      if (request.current === controller && !controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (request.current === controller) { request.current = null; saving.current = false; setBusy(false) }
    }
  }, [name])
  useEffect(() => {
    void perform('status')
    return () => { request.current?.abort(); request.current = null; saving.current = false }
  }, [perform])
  useEffect(() => {
    if (!data?.authorization_url && !data?.authorizing) return
    const timer = setInterval(() => void perform('status', true), 1000)
    return () => clearInterval(timer)
  }, [data?.authorization_url, data?.authorizing, perform])
  const expired = Boolean(data?.expires_at && data.expires_at * 1000 <= Date.now())
  const pending = Boolean(data?.authorization_url && !expired)
  const label = data ? ({ configured: c('已配置', 'Configured'), 'not-configured': c('未配置', 'Not configured'),
    unavailable: c('暂不可用', 'Unavailable'), 'repair-required': c('需要修复', 'Needs repair') })[data.state] : ''
  return <section className="min-w-0 space-y-4 rounded-card border border-default p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-base font-semibold text-content">{c('OAuth 授权', 'OAuth authorization')}</h2>
      <button type="button" className={button} disabled={busy} onClick={() => void perform('status')}>{c('刷新授权状态', 'Refresh authorization')}</button></div>
    {error && <p role="alert" className="break-words text-sm text-danger">{error}</p>}
    {notice && <p role="status" className="text-sm leading-6 text-accent">{notice}</p>}
    {busy && <p className="text-sm text-content-secondary">{c('正在更新…', 'Updating…')}</p>}
    {data && <>
      <p role="status" className="text-sm font-medium">{data.authorizing ? c('正在完成授权', 'Completing authorization') : pending ? c('等待完成浏览器授权', 'Waiting for browser authorization') : expired ? c('授权链接已过期', 'Authorization link expired') : label}</p>
      {data.reason && <p className="break-words text-sm leading-6 text-content-secondary">{data.reason}</p>}
      {!data.enabled && <p className="text-sm text-content-secondary">{c('启用此 HTTP MCP 后可开始授权。', 'Enable this HTTP MCP to start authorization.')}</p>}
      {pending && <p><a href={data.authorization_url!} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center text-sm text-accent">{c('前往授权页面', 'Open authorization page')}</a></p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} disabled={busy || !data.can_authorize || data.authorizing} onClick={() => void perform('authorize')}>{data.saved ? c('重新授权', 'Authorize again') : c('开始授权', 'Start authorization')}</button>
        {(pending || data.authorizing) && <button type="button" className={button} disabled={busy} onClick={() => void perform('cancel')}>{c('取消授权流程', 'Cancel authorization')}</button>}
        {data.saved && <button type="button" className={button} disabled={busy} onClick={() => void perform('remove')}>{c('删除已保存授权', 'Remove saved authorization')}</button>}
      </div>
    </>}
    <p className="text-xs leading-6 text-content-secondary">{c('钥匙串不可用时可先解锁并刷新。凭据丢失时可删除授权后重新授权。删除保留服务配置、业务数据和工具开关。', 'If Keychain is unavailable, unlock it and refresh. Missing credentials can be removed and authorized again. Removal preserves service settings, business data and tool switches.')}</p>
  </section>
}
