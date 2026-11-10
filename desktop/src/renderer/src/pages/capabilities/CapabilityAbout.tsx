import { useEffect, useRef, useState } from 'react'
import apiClient from '../../api/client'
import type { CapabilityDetail } from '../../api/capabilityTypes'
import { checkPluginUpdates } from '../settings/pluginUpdatePlan'
import { button, c, sourceLabel } from './presentation'

export default function CapabilityAbout({ item }: { item: CapabilityDetail }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const active = useRef(true)
  const checking = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  const supported = item.kind === 'plugins' && !item.retained
  const check = async () => {
    if (!supported || checking.current) return
    checking.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const plan = await checkPluginUpdates(apiClient)
      if (!active.current) return
      const update = plan.updates.find(row => row.pluginId === item.id)
      const blocked = plan.blocked.find(row => row.pluginId === item.id)
      setMessage(blocked ? blocked.reason : update ? c(`发现新版本：${update.toVersion}`, `New version available: ${update.toVersion}`) : c('未发现可用更新。', 'No available update was found.'))
    } catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { checking.current = false; if (active.current) setBusy(false) }
  }
  return <div className="space-y-5 text-sm">
    <dl className="space-y-4"><div><dt className="text-content-secondary">{c('版本', 'Version')}</dt><dd className="mt-1 break-words">{item.version || (item.kind === 'mcp' ? c('提供者未报告版本', 'The provider has not reported a version') : c('暂无安装版本', 'No installed version'))}</dd></div>
      <div><dt className="text-content-secondary">{c('来源', 'Source')}</dt><dd className="mt-1">{sourceLabel(item.source)}</dd></div></dl>
    <button type="button" className={`${button} bg-surface-2`} disabled={!supported || busy} onClick={() => void check()}>{busy ? c('正在检查…', 'Checking…') : c('检查更新', 'Check for updates')}</button>
    {!supported && <p className="text-content-secondary">{item.retained ? c('提供者已移除，无法检查更新。', 'This provider has been removed and cannot be checked for updates.') : c('此 MCP 未提供更新检查机制，请通过其安装来源管理更新。', 'This MCP has no update check mechanism. Manage updates through its installation source.')}</p>}
    {message && <p role="status" className="break-words text-content-secondary">{message}</p>}
    {error && <p role="alert" className="break-words text-danger">{error}</p>}
  </div>
}
