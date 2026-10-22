import { useEffect, useRef, useState } from 'react'
import { Check, Copy, X } from 'lucide-react'
import type { LogEntry } from './logTypes'
import { levelNames, levelStyle, sourceName } from './logTypes'

export default function LogDetails({ entry, en, onClose }: { entry: LogEntry; en: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { dialog.current?.showModal() }, [])
  const copy = async () => {
    try { await navigator.clipboard.writeText(entry.detail); setCopied(true); setError('') }
    catch { setError(en ? 'Unable to copy. Select the text below.' : '复制失败，可以选中下方文本手动复制。') }
  }
  return <dialog ref={dialog} aria-label={en ? 'Log details' : '日志详情'} onCancel={onClose} onClose={onClose}
    className="log-detail m-0 ml-auto h-dvh max-h-none w-full max-w-lg border-l border-default bg-surface p-0 text-content shadow-lg backdrop:bg-black/35">
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-default px-5 py-4">
        <h2 className="text-base font-semibold text-content">{en ? 'Log details' : '日志详情'}</h2>
        <button type="button" autoFocus aria-label={en ? 'Close details' : '关闭详情'} onClick={onClose} className="rounded-btn p-2 text-content-secondary hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent"><X size={18} /></button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5">
        <span className={`inline-flex rounded px-2 py-1 text-xs font-medium ${levelStyle(entry.level)}`}>{levelNames[entry.level]?.[en ? 1 : 0] || entry.level}</span>
        <p className="mt-4 break-words text-base font-medium leading-relaxed text-content">{entry.message}</p>
        <dl className="my-6 grid grid-cols-[80px_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm">
          {[[en ? 'Time' : '时间', entry.timestamp || '—'], [en ? 'Feature' : '功能', sourceName(entry.source, en)],
            [en ? 'Process' : '来源', entry.component], [en ? 'Location' : '位置', entry.location || '—']].map(([name, value]) => <div key={name} className="contents"><dt className="text-content-tertiary">{name}</dt><dd className="break-all text-content-secondary">{value}</dd></div>)}
        </dl>
        <div className="mb-3 flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">{en ? 'Diagnostic details' : '诊断详情'}</h3>
          <button type="button" onClick={copy} className="inline-flex min-h-9 items-center gap-1.5 rounded-btn px-2 text-sm text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-accent">{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? (en ? 'Copied' : '已复制') : (en ? 'Copy details' : '复制详情')}</button>
        </div>
        {error && <p role="alert" className="mb-2 text-sm text-danger">{error}</p>}
        <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-card border border-default bg-inset p-4 font-mono text-xs leading-6 text-content-secondary">{entry.detail}</pre>
      </div>
    </div>
  </dialog>
}
