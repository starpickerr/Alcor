import React from 'react'
export function BriefingToggle({ label, checked, disabled, onChange }) {
  return <button type="button" className="alcor-briefing-toggle" role="switch" aria-label={label} aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}><span /></button>
}
