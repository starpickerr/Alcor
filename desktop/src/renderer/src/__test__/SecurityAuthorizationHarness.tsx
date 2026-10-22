import React from 'react'
import { createRoot } from 'react-dom/client'
import { ResourceAuthorizationPanel, RuntimeAuthorizationPanel } from '../components/SecurityAuthorizationPanels'

export function mountSecurityAuthorizationTest() {
  const host = document.createElement('main')
  host.style.cssText = 'position:absolute;inset:0;z-index:100001;overflow:auto;background:var(--color-surface);padding:20px'
  document.body.replaceChildren(host)
  createRoot(host).render(<div className="mx-auto max-w-4xl space-y-8"><ResourceAuthorizationPanel /><RuntimeAuthorizationPanel /></div>)
}
