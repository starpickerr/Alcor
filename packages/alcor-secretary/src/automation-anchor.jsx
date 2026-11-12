import { useId, useLayoutEffect } from 'react'

// Follow the official CSS-anchor migration without depending on removed primitive exports.
export function useAutomationAnchor({ open, anchorRef, align = 'start', gap = 4, margin = 12 }) {
  const name = '--alcor-automation-' + useId().replace(/[^a-zA-Z0-9_-]/g, '')
  useLayoutEffect(() => {
    const anchor = anchorRef.current
    if (!open || !anchor) return
    const previous = anchor.style.getPropertyValue('anchor-name')
    anchor.style.setProperty('anchor-name', name)
    return () => {
      if (anchor.style.getPropertyValue('anchor-name') === name) {
        if (previous) anchor.style.setProperty('anchor-name', previous)
        else anchor.style.removeProperty('anchor-name')
      }
    }
  }, [open, anchorRef, name])
  return open ? {
    position: 'fixed', positionAnchor: name, top: `calc(anchor(bottom) + ${gap}px)`,
    ...(align === 'end' ? { right: 'anchor(right)' } : { left: 'anchor(left)' }),
    positionTryFallbacks: 'flip-block, flip-inline',
    maxWidth: `calc(100vw - ${margin * 2}px)`, maxHeight: `calc(100vh - ${margin * 2}px)`,
  } : null
}
