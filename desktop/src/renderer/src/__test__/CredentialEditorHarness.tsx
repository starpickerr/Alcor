import React from 'react'
import { createRoot, type Root } from 'react-dom/client'
import CredentialEditor from '../components/CredentialEditor'

let root: Root | undefined
export function mountCredentialEditor(name: string) {
  if (!root) {
    const host = document.createElement('div')
    host.id = 'credential-test-root'
    host.style.cssText = 'position:fixed;inset:10px;background:white;z-index:100000;overflow:auto'
    document.body.append(host)
    root = createRoot(host)
  }
  root.render(<CredentialEditor name={name} />)
}
