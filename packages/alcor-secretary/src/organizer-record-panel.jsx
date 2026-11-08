import React, { useEffect, useLayoutEffect, useRef } from 'react'
import { Button, IconCloseOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

export function OrganizerRecordPanel({ title, focusKey, busy, close, trigger, editing, children }) {
  const panel = useRef(null)
  useLayoutEffect(() => {
    const element = panel.current
    const desk = element?.closest('.alcor-desk')
    if (!desk) return
    let frame = 0
    const update = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        if (getComputedStyle(element).position !== 'sticky') { element.style.removeProperty('--alcor-panel-height'); return }
        const bottom = Math.min(window.innerHeight, desk.getBoundingClientRect().bottom)
        element.style.setProperty('--alcor-panel-height', `${Math.max(0, bottom - element.getBoundingClientRect().top - 12)}px`)
      })
    }
    const observer = new ResizeObserver(update)
    observer.observe(desk)
    element.closest('.alcor-business')?.querySelectorAll(':scope > .alcor-app-header, :scope > .alcor-page-feedback').forEach(item => observer.observe(item))
    desk.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    update()
    return () => { cancelAnimationFrame(frame); observer.disconnect(); desk.removeEventListener('scroll', update); window.removeEventListener('resize', update) }
  }, [])
  useEffect(() => {
    return () => { const opener=trigger.current; if (opener?.isConnected) opener.focus({preventScroll:true}) }
  }, [])
  useEffect(() => {
    const target = panel.current?.querySelector('[data-modal-autofocus]:not(:disabled)') ?? panel.current?.querySelector('[aria-label="关闭详情"]')
    target?.focus({preventScroll:true})
  }, [focusKey])
  return <aside ref={panel} className={`alcor-calendar-record-panel${editing ? ' alcor-editor-dialog' : ''}`} role="region" aria-label={title} onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented) { event.stopPropagation(); if (!busy) close() } }}>
    <header className="alcor-calendar-panel-header"><h2>{title}</h2><Button variant="ghost" size="sm" aria-label="关闭详情" disabled={busy} onClick={close}><IconCloseOutlineRegular size={18} /></Button></header>
    {children}
  </aside>
}
