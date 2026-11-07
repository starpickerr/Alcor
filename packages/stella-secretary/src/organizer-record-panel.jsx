import React, { useEffect, useRef } from 'react'
import { Button, IconCloseOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

export function OrganizerRecordPanel({ title, focusKey, busy, close, trigger, editing, children }) {
  const panel = useRef(null)
  useEffect(() => {
    return () => { const opener=trigger.current; if (opener?.isConnected) opener.focus({preventScroll:true}) }
  }, [])
  useEffect(() => {
    const target = panel.current?.querySelector('[data-modal-autofocus]:not(:disabled)') ?? panel.current?.querySelector('[aria-label="关闭详情"]')
    target?.focus({preventScroll:true})
  }, [focusKey])
  return <aside ref={panel} className={`stella-calendar-record-panel${editing ? ' stella-editor-dialog' : ''}`} role="region" aria-label={title} onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented) { event.stopPropagation(); if (!busy) close() } }}>
    <header className="stella-calendar-panel-header"><h2>{title}</h2><Button variant="ghost" size="sm" aria-label="关闭详情" disabled={busy} onClick={close}><IconCloseOutlineRegular size={18} /></Button></header>
    {children}
  </aside>
}
