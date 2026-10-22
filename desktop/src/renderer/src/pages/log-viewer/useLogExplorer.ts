import { useCallback, useEffect, useRef, useState } from 'react'
import apiClient from '../../api/client'
import type { LogPage, LogQuery } from './logTypes'

const empty: LogPage = { entries: [], next_cursor: null, truncated: false, sources: {}, scanned_bytes: 0 }

export function useLogExplorer(baseUrl: string, query: string, levels: string, source: string, range: string) {
  const [page, setPage] = useState<LogPage>(empty)
  const [live, setLive] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<Date | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [search, setSearch] = useState(query)
  const request = useRef<AbortController | null>(null)
  const latest = useRef(0)
  const lastFilter = useRef('')
  const lastRefresh = useRef(-1)
  const loadMoreRef = useRef<() => void>(() => {})
  const pendingOlder = useRef(false)
  const running = useRef(false)
  const pageRef = useRef(page)
  const snapshotSince = useRef<string | undefined>(undefined)
  pageRef.current = page

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query), 250)
    return () => clearTimeout(timer)
  }, [query])

  useEffect(() => {
    apiClient.setBaseUrl(baseUrl)
    const filter = JSON.stringify([baseUrl, search, levels, source, range])
    const changed = lastFilter.current !== filter || lastRefresh.current !== refreshKey
    if (lastFilter.current !== filter) { setPage(empty); setUpdated(null) }
    lastFilter.current = filter
    lastRefresh.current = refreshKey
    let active = true
    const parameters: LogQuery = { q: search, levels, source, limit: '100' }

    const load = async (older = false) => {
      request.current?.abort()
      const controller = new AbortController()
      request.current = controller
      const serial = ++latest.current
      running.current = true
      setBusy(true)
      try {
        const since = older ? snapshotSince.current : range ? new Date(Date.now() - Number(range) * 60_000).toISOString() : undefined
        const result = await apiClient.queryLogs({ ...parameters,
          since, cursor: older ? pageRef.current.next_cursor || undefined : undefined }, controller.signal)
        if (!active || serial !== latest.current) return
        if (!older) snapshotSince.current = since
        setPage(previous => older ? { ...result, entries: [...previous.entries,
          ...result.entries.filter(row => !previous.entries.some(old => old.id === row.id))].slice(0, 1000),
        sources: { ...previous.sources, ...result.sources } } : result)
        setError('')
        setUpdated(new Date())
      } catch (cause) {
        if (active && serial === latest.current && !controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      } finally { if (active && serial === latest.current) { setBusy(false); running.current = false } }
    }
    loadMoreRef.current = () => { pendingOlder.current = true; setLive(false); setRefreshKey(value => value + 1) }
    const older = pendingOlder.current
    pendingOlder.current = false
    if (changed || live) void load(older)
    const timer = live ? setInterval(() => { if (!running.current) void load() }, 3000) : undefined
    return () => { active = false; request.current?.abort(); running.current = false; setBusy(false); if (timer) clearInterval(timer) }
    // busy is intentionally not a dependency: each query owns its own timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, search, levels, source, range, live, refreshKey])

  const loadMore = useCallback(() => loadMoreRef.current(), [])
  const refresh = useCallback(() => setRefreshKey(value => value + 1), [])
  return { page, live, setLive, busy, error, updated, refresh, loadMore }
}
