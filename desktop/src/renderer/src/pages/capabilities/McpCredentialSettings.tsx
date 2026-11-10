import { useCallback, useEffect, useRef, useState } from 'react'
import apiClient, { type McpCredentialState } from '../../api/client'
import { c } from './presentation'
import { TextInput } from '../settings/primitives'

type Credential = McpCredentialState['fields'][number]
const button = 'min-h-10 rounded-btn border border-default px-3 py-2 text-sm text-content hover:bg-surface-2 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent'
const fieldId = (field: Credential) => `${field.area}/${field.key}`

export default function McpCredentialSettings({ name }: { name: string }) {
  const [data, setData] = useState<McpCredentialState | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const request = useRef<AbortController | null>(null)
  const working = useRef(false)
  const perform = useCallback(async (change?: Parameters<typeof apiClient.manageMcpCredentials>[1]) => {
    if (working.current) return
    const controller = new AbortController()
    request.current = controller
    working.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const result = await apiClient.manageMcpCredentials(name, change, controller.signal)
      if (controller.signal.aborted || request.current !== controller) return
      setData(result)
      if (change) {
        setDrafts(previous => ({ ...previous, [`${change.area}/${change.key}`]: '' }))
        setNotice(change.value ? c('此凭据已保存。', 'Credential saved.') : c('此凭据已删除。', 'Credential removed.'))
      }
    } catch (cause) {
      if (!controller.signal.aborted && request.current === controller) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (request.current === controller) { request.current = null; working.current = false; setBusy(false) }
    }
  }, [name])
  useEffect(() => {
    setData(null); setDrafts({})
    void perform()
    return () => { request.current?.abort(); request.current = null; working.current = false }
  }, [perform])
  const fields: Credential[] = [...(data?.fields || [])]
  if (data && data.transport !== 'stdio' && !fields.some(field => field.area === 'headers' && field.key.toLowerCase() === 'authorization')) {
    fields.push({ area: 'headers', key: 'Authorization', state: 'not_configured' })
  }
  const stateLabel = (field: Credential) => ({ configured: c('已配置', 'Configured'), not_configured: c('未配置', 'Not configured'),
    unavailable: c('暂不可用', 'Unavailable'), repair_required: c('需要修复', 'Needs repair') })[field.state]
  const save = (field: Credential, value: string) => {
    if (data) void perform({ area: field.area, key: field.key, value, expectedRevision: data.revision })
  }
  return <section aria-label={c('认证凭据', 'Authentication credentials')} className="min-w-0 space-y-4 rounded-card border border-default p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-base font-semibold text-content">{c('认证凭据', 'Authentication credentials')}</h2>
      <button type="button" className={button} disabled={busy} onClick={() => void perform()}>{c('刷新凭据状态', 'Refresh credentials')}</button>
    </div>
    {error && <p role="alert" className="break-words text-sm text-danger">{error}</p>}
    {notice && <p role="status" className="text-sm text-accent">{notice}</p>}
    <p className="text-xs leading-6 text-content-secondary">{c('凭据保存在此服务的原配置中，已保存值不会回填。每次只保存或删除一个字段；刷新状态会保留未保存输入。',
      'Credentials use this service’s existing configuration. Saved values are never filled in. Each save or removal affects one field; refreshing keeps unsaved input.')}</p>
    {data?.transport === 'stdio' && fields.length > 0 && <p className="text-sm leading-6 text-content-secondary">{c('本地 MCP 仅接收此服务专用凭据；保存后，在运行能力中明确允许注入。进程可读取这些值，请使用受限账号。',
      'Local MCP receives only its dedicated credentials after explicit runtime permission. The process can read these values; use a limited account.')}</p>}
    {fields.map(field => {
      const id = fieldId(field)
      const value = drafts[id] || ''
      return <div key={id} className="min-w-0 space-y-2 border-t border-default pt-4">
        <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-2">
          <label htmlFor={`mcp-credential-${id}`} className="break-all text-sm font-medium text-content">{field.key}</label>
          <span className="text-xs text-content-secondary">{field.area} · <span>{stateLabel(field)}</span></span>
        </div>
        <TextInput id={`mcp-credential-${id}`} type="password" autoComplete="new-password" disabled={busy}
          placeholder={c('输入替换值', 'Enter a replacement')} value={value}
          onChange={event => setDrafts(previous => ({ ...previous, [id]: event.target.value }))} />
        <div className="flex flex-wrap gap-2">
          <button type="button" className={button} disabled={busy || !value} onClick={() => save(field, value)}>{c(`保存${field.key}`, `Save ${field.key}`)}</button>
          {field.state !== 'not_configured' && <button type="button" className={button} disabled={busy} onClick={() => save(field, '')}>{c(`删除${field.key}`, `Remove ${field.key}`)}</button>}
        </div>
      </div>
    })}
    {data?.transport !== 'stdio' && <p className="text-xs leading-6 text-content-secondary">{c('配置 Authorization 时优先使用该字段认证；OAuth 授权保持独立。',
      'A configured Authorization header takes precedence; OAuth authorization remains separate.')}</p>}
  </section>
}
