import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import RunPolicyEditor from '../components/RunPolicyEditor'

export function mountRunPolicyTest() {
  const host = document.createElement('main')
  host.style.cssText = 'position:absolute;inset:0;z-index:99999;background:var(--color-surface);overflow:auto;padding:16px'
  document.body.replaceChildren(host)
  createRoot(host).render(<MemoryRouter><div className="mx-auto grid max-w-5xl gap-8 md:grid-cols-2">
    <section data-testid="policy-one"><RunPolicyEditor kind="session" subject="one" /></section>
    <section data-testid="policy-two"><RunPolicyEditor kind="session" subject="two" /></section>
  </div></MemoryRouter>)
}
