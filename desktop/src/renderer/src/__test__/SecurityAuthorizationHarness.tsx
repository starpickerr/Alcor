import React from 'react'
import apiClient from '../api/client'
import type { FeatureAccessContext } from '../api/client'
import FeatureAccessEditor from '../components/FeatureAccessEditor'
import { createRoot } from 'react-dom/client'
import { RuntimeAuthorizationPanel } from '../components/SecurityAuthorizationPanels'

export function mountSecurityAuthorizationTest(context: FeatureAccessContext = { module: 'sessions', id: 'chat', agent: 'default' }) {
  apiClient.setBaseUrl('http://127.0.0.1:9876')
  const host = document.createElement('main')
  host.style.cssText = 'position:absolute;inset:0;z-index:100001;overflow:auto;background:var(--color-surface);padding:20px'
  document.body.replaceChildren(host)
  createRoot(host).render(<div className="mx-auto max-w-4xl space-y-8"><FeatureAccessEditor context={context} /><RuntimeAuthorizationPanel /></div>)
}
