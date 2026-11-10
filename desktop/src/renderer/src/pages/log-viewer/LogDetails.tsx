import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import type { LogEntry } from './logTypes'
import { levelNames, levelStyle, sourceName } from './logTypes'

export default function LogDetails({ entry, en, onClose }: { entry: LogEntry; en: boolean; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    closeButton.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  // The window title bar is a native drag strip. A full-viewport dialog placed
  // its close control inside that strip, so the click never reached the button.
  return createPortal(
    <aside role="dialog" aria-modal="false" aria-label={en ? 'Log details' : '日志详情'}
      className="titlebar-no-drag fixed bottom-0 right-0 top-11 z-40 flex w-full max-w-lg flex-col border-l border-default bg-surface text-content shadow-lg">
      <header className="titlebar-no-drag flex shrink-0 items-center justify-between border-b border-default px-5 py-4">
        <h2 className="text-base font-semibold text-content">{en ? 'Log details' : '日志详情'}</h2>
        <button ref={closeButton} type="button" aria-label={en ? 'Close details' : '关闭详情'} onClick={onClose}
          className="titlebar-no-drag rounded-btn p-2 text-content-secondary hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent"><X size={18} /></button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5">
        <span className={`inline-flex rounded px-2 py-1 text-xs font-medium ${levelStyle(entry.level)}`}>{levelNames[entry.level]?.[en ? 1 : 0] || entry.level}</span>
        <p className="mt-4 break-words text-base font-medium leading-relaxed text-content">{entry.message}</p>
        <dl className="my-6 grid grid-cols-[80px_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm">
          {[[en ? 'Time' : '时间', entry.timestamp || '—'], [en ? 'Feature' : '功能', sourceName(entry.source, en)],
            [en ? 'Process' : '来源', entry.component], [en ? 'Location' : '位置', entry.location || '—']].map(([name, value]) => <div key={name} className="contents"><dt className="text-content-tertiary">{name}</dt><dd className="break-all text-content-secondary">{value}</dd></div>)}
        </dl>
        <h3 className="mb-3 text-sm font-semibold">{en ? 'Diagnostic details' : '诊断详情'}</h3>
        <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-card border border-default bg-inset p-4 font-mono text-xs leading-6 text-content-secondary">{entry.detail}</pre>
      </div>
    </aside>,
    document.body,
  )
}
