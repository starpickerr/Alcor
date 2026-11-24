import React from 'react'

export function Field({ label, children }) {
  return <label className="alcor-field"><span>{label}</span>{['select', 'textarea'].includes(children?.type) ? React.cloneElement(children, { 'aria-label': label }) : children}</label>
}
