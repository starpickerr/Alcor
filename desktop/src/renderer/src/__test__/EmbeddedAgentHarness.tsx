import React from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import EmbeddedAgent from '../components/EmbeddedAgent'

const mount = document.createElement('div')
mount.id = 'embedded-test-root'
document.body.appendChild(mount)
const root = createRoot(mount)

declare global {
  interface Window {
    mountEmbedded: (context: 'calendar' | 'todos', presetRequest?: { id: string; text: string } | null) => void
    detachEmbedded: () => void
  }
}

window.mountEmbedded = (context, presetRequest) => root.render(
  <HashRouter><EmbeddedAgent key={context} context={context} presetRequest={presetRequest || undefined} /></HashRouter>)
window.detachEmbedded = () => root.render(null)
