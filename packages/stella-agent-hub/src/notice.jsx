import React from 'react'

export function Notice({ children, tone = 'info', role, className = '' }) {
  return <div className={`stella-notice ${className}`} data-tone={tone} role={role ?? (tone === 'error' ? 'alert' : undefined)}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">{tone === 'success' ? <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></> : tone === 'warning' ? <><path d="m12 3 10 18H2zM12 9v5m0 3h.01" /></> : <><circle cx="12" cy="12" r="9" /><path d={tone === 'error' ? 'M12 7v6m0 4h.01' : 'M12 11v6m0-10h.01'} /></>}</svg>
    <div className="stella-notice-content">{children}</div>
  </div>
}
