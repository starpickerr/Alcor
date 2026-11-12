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
    '--alcor-anchor-margin': `${margin}px`, '--alcor-anchor-gap': `${gap}px`,
    '--alcor-anchor-top': `max(${margin}px, var(--dsh-frame-overlay-top, 0px))`,
    '--dsh-anchored-max-height': `calc(100dvh - var(--alcor-anchor-top) - ${margin}px)`,
    '--dsh-anchored-max-width': `calc(100vw - ${margin * 2}px)`,
    position: 'fixed', positionAnchor: name, margin: 0,
    top: `max(var(--alcor-anchor-top), calc(anchor(bottom) + ${gap}px))`, bottom: margin,
    left: align === 'end' ? margin : `max(${margin}px, anchor(left))`,
    right: align === 'end' ? `max(${margin}px, anchor(right))` : margin,
    alignSelf: 'start', justifySelf: align === 'end' ? 'end' : 'start',
    positionTryFallbacks: '--alcor-automation-above, --alcor-automation-window, --alcor-automation-wide-window',
  } : null
}
