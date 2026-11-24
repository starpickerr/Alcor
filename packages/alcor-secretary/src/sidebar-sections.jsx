import React, { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

export function useSidebarSection(section) {
  const key = 'alcor.sidebar.collapsed.' + section
  const [expanded, setExpanded] = useState(() => {
    try { return globalThis.window?.localStorage.getItem(key) !== 'true' } catch { return true }
  })
  function toggle() {
    setExpanded(!expanded)
    try { globalThis.window?.localStorage.setItem(key, String(expanded)) } catch { /* Keep the choice in this window. */ }
  }
  return { expanded, toggle }
}

export function SectionToggle({ label, section }) {
  return <button type="button" className="alcor-section-toggle" aria-expanded={section.expanded}
    title={`${section.expanded ? '收起' : '展开'}${label}`} onClick={section.toggle}>
    <IconChevronDownOutlineRegular size={12} aria-hidden="true" /><span>{label}</span>
  </button>
}

export function WorkspaceToggle({ nav }) {
  const section = useSidebarSection('workspaces')
  const [header, setHeader] = useState(null)
  useEffect(() => {
    // The native workspace owns its toolbar and can remount it when the sidebar changes layout.
    const root = nav.current?.closest('.alcor-sidebar') ?? nav.current?.closest('[class$="_newSessionRow"]')?.parentElement
    if (!root) return
    let current
    const bind = () => {
      if (current && root.contains(current)) return
      current = root.querySelector('[data-sidebar-workspace-header], [class*="_sectionHeader"]:has(> [class*="_sectionLabel"])')
      setHeader(current)
    }
    bind()
    const observer = new MutationObserver(bind)
    observer.observe(root, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [nav])
  return header && createPortal(<SectionToggle label="工作区" section={section} />, header)
}
