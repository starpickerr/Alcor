import { useEffect, useRef, useState } from 'react'
import { ExternalLink, X } from 'lucide-react'
import apiClient from '../../api/client'
import { SKILL_HUB_URL } from '../settings/links'
import { button, c, panel } from './presentation'

export default function AddCapability({ onClose, onInstalled }: { onClose: () => void; onInstalled: (id: string) => void }) {
  const [id, setId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef<HTMLDivElement>(null)
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.querySelector('input')?.focus()
    return () => { active.current = false; previous?.focus() }
  }, [])
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="add-capability-title" className={`${panel} w-full max-w-md p-6 space-y-5 shadow-xl`}
      onKeyDown={event => {
        if (event.key === 'Escape' && !busy) onClose()
        if (event.key !== 'Tab') return
        const nodes = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href]')
        if (!nodes?.length) return
        if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes[nodes.length - 1].focus() }
        else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0].focus() }
      }}>
      <header className="flex items-center justify-between"><h2 id="add-capability-title" className="text-lg font-semibold">{c('添加能力', 'Add capability')}</h2>
        <button type="button" aria-label={c('关闭', 'Close')} disabled={busy} onClick={onClose} className="rounded-btn p-2 hover:bg-surface-2"><X size={18} /></button></header>
      <form className="space-y-3" onSubmit={event => {
        event.preventDefault()
        if (busy || !id.trim()) return
        setBusy(true); setError('')
        void apiClient.pluginRequest('manage', { action: 'install', pluginId: id.trim() }).then(() => {
          if (active.current) onInstalled(id.trim())
        }).catch(cause => { if (active.current) setError(String(cause)) }).finally(() => { if (active.current) setBusy(false) })
      }}>
        <label className="block text-sm font-medium" htmlFor="capability-install-id">{c('从插件目录安装', 'Install from the plugin catalog')}</label>
        <input id="capability-install-id" value={id} disabled={busy} onChange={event => setId(event.target.value)} placeholder="stella.example"
          className="w-full rounded-btn border border-default bg-surface px-3 py-2 text-sm" />
        {error && <p role="alert" className="text-sm text-danger break-words">{error}</p>}
        <button className={`${button} bg-accent text-accent-contrast`} disabled={busy || !id.trim()}>{busy ? c('安装中…', 'Installing…') : c('安装插件', 'Install plugin')}</button>
      </form>
      <a className="inline-flex items-center gap-2 text-sm text-accent" href={SKILL_HUB_URL} target="_blank" rel="noopener noreferrer">{c('浏览 Skill 市场', 'Browse the Skill marketplace')}<ExternalLink size={14} /></a>
    </div>
  </div>
}
