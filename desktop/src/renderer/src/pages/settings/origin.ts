import { useEffect, type MouseEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export interface SettingsOrigin {
  from: string
  scroll: number
}

export function currentSettingsOrigin(pathname: string, search: string): SettingsOrigin {
  const scroller = document.querySelector('[data-stella-scroll]')
  return {
    from: `${pathname}${search}`,
    scroll: scroller instanceof HTMLElement ? scroller.scrollTop : 0,
  }
}

export function useRestoreScroll(ready = true) {
  const location = useLocation()
  useEffect(() => {
    if (!ready) return
    const scroll = (location.state as { restoreScroll?: number } | null)?.restoreScroll
    if (typeof scroll !== 'number') return
    const scroller = document.querySelector('[data-stella-scroll]')
    if (scroller instanceof HTMLElement) scroller.scrollTop = scroll
  }, [location.state, ready])
}

// Capture the origin when the link is used, after the user has scrolled.
export function useSettingsLink() {
  const location = useLocation()
  const navigate = useNavigate()
  return (event: MouseEvent<HTMLAnchorElement>, to: string) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(to, { state: currentSettingsOrigin(location.pathname, location.search) })
  }
}
