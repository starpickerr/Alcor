import { useState } from 'react'
import { Activity, AlertCircle, ArrowDown, ChevronRight, Download, FileSearch, Layers, Pause, Play, RefreshCw, Search, X } from 'lucide-react'
import { getLang } from '../i18n'
import PageHeader from '../components/PageHeader'
import LogDetails from './log-viewer/LogDetails'
import { levelNames, levelStyle, sourceName, sourceNames } from './log-viewer/logTypes'
import type { LogEntry } from './log-viewer/logTypes'
import { useLogExplorer } from './log-viewer/useLogExplorer'

const control = 'min-h-10 rounded-btn border border-default bg-surface px-3 text-sm text-content hover:border-strong focus-visible:outline-2 focus-visible:outline-accent'
const button = `${control} inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed`
const time = (value: string, en: boolean) => {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleTimeString(en ? 'en-GB' : 'zh-CN', { hour12: false })
}

export default function LogsPage({ baseUrl }: { baseUrl: string }) {
  const en = getLang() === 'en'
  const c = (zh: string, english: string) => en ? english : zh
  const [query, setQuery] = useState('')
  const [level, setLevel] = useState('')
  const [source, setSource] = useState('')
  const [range, setRange] = useState('')
  const [selected, setSelected] = useState<LogEntry | null>(null)
  const { page, live, setLive, busy, error, updated, refresh, loadMore } = useLogExplorer(baseUrl, query, level, source, range)
  const sources = [...new Set([...Object.keys(sourceNames), ...Object.keys(page.sources)])]
  const errors = page.entries.filter(row => ['ERROR', 'CRITICAL'].includes(row.level)).length
  const warnings = page.entries.filter(row => row.level === 'WARNING').length
  const filtered = Boolean(query || level || source || range)
  const clear = () => { setQuery(''); setLevel(''); setSource(''); setRange('') }
  const exportRows = () => {
    const blob = new Blob([page.entries.map(row => JSON.stringify(row)).join('\n') + '\n'], { type: 'application/x-ndjson' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `stella-logs-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.jsonl`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <div className="flex min-h-0 flex-1 flex-col bg-base">
    <PageHeader className="flex flex-wrap items-center justify-between gap-4">
      <div><h1 className="text-xl font-semibold tracking-tight text-content">{c('日志', 'Logs')}</h1>
        <p className="mt-1 text-sm text-content-tertiary">{c('追踪运行过程，定位每一次异常。', 'Follow activity. Find what went wrong.')}</p></div>
      <div className="flex items-center gap-2">
        <button type="button" className={button} onClick={refresh} disabled={busy} aria-label={c('刷新日志', 'Refresh logs')}><RefreshCw size={15} className={busy ? 'animate-spin motion-reduce:animate-none' : ''} /><span>{c('刷新', 'Refresh')}</span></button>
        <button type="button" className={button} onClick={exportRows} disabled={!page.entries.length} aria-label={c('导出当前结果', 'Export current results')}><Download size={15} /><span>{c('导出结果', 'Export')}</span></button>
      </div>
    </PageHeader>

    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6">
      <div className="flex min-h-0 flex-1 flex-col rounded-card border border-default bg-surface shadow-sm">
        <div className="flex flex-wrap items-center gap-3 border-b border-default p-4">
          <div className="relative min-w-44 flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-3 text-content-tertiary" />
            <input type="search" name="log-search" autoComplete="off" spellCheck={false} aria-label={c('搜索日志', 'Search logs')} value={query} onChange={event => setQuery(event.target.value)} placeholder={c('搜索消息、错误或请求标识…', 'Search messages, errors or request IDs…')} maxLength={500}
              className={`${control} w-full pl-10 pr-9`} />
            {query && <button type="button" className="absolute right-1 top-1 rounded-btn p-2 text-content-tertiary hover:bg-surface-2" aria-label={c('清除搜索', 'Clear search')} onClick={() => setQuery('')}><X size={14} /></button>}
          </div>
          <select className={control} aria-label={c('日志级别', 'Log level')} value={level} onChange={event => setLevel(event.target.value)}>
            <option value="">{c('全部级别', 'All levels')}</option><option value="ERROR,CRITICAL">{c('错误与严重', 'Errors & critical')}</option>
            <option value="WARNING">{c('警告', 'Warning')}</option><option value="INFO">{c('信息', 'Info')}</option><option value="DEBUG">{c('调试', 'Debug')}</option><option value="UNKNOWN">{c('未识别', 'Unknown')}</option>
          </select>
          <select className={control} aria-label={c('时间范围', 'Time range')} value={range} onChange={event => setRange(event.target.value)}>
            <option value="">{c('所有保留时间', 'All retained time')}</option><option value="15">{c('最近 15 分钟', 'Last 15 minutes')}</option><option value="60">{c('最近 1 小时', 'Last hour')}</option><option value="1440">{c('最近 24 小时', 'Last 24 hours')}</option>
          </select>
          <button type="button" className={`${button} ${live && !error ? 'border-accent text-accent' : ''}`} onClick={() => setLive(value => !value)} aria-label={live ? c('暂停实时更新', 'Pause live updates') : c('恢复实时更新', 'Resume live updates')}>
            {live ? <Pause size={14} /> : <Play size={14} />}{live ? c('实时', 'Live') : c('已暂停', 'Paused')}
          </button>
          <select className={`${control} w-full md:hidden`} aria-label={c('功能来源', 'Feature source')} value={source} onChange={event => setSource(event.target.value)}>
            <option value="">{c('全部功能', 'All features')}</option>{sources.map(value => <option key={value} value={value}>{sourceName(value, en)}</option>)}
          </select>
        </div>

        <div className="flex min-h-0 flex-1">
          <aside className="hidden w-44 shrink-0 overflow-y-auto border-r border-default p-3 md:block" aria-label={c('日志功能', 'Log features')}>
            <p className="px-3 pb-3 pt-2 text-xs font-medium text-content-tertiary">{c('功能来源', 'Features')}</p>
            <button type="button" onClick={() => setSource('')} aria-pressed={!source} className={`mb-1 flex min-h-10 w-full items-center gap-2 rounded-btn px-3 text-sm ${!source ? 'bg-accent-soft font-medium text-accent' : 'text-content-secondary hover:bg-surface-2'}`}><Layers size={15} />{c('全部功能', 'All features')}</button>
            {sources.map(value => <button key={value} type="button" onClick={() => setSource(value)} aria-pressed={source === value}
              className={`flex min-h-9 w-full items-center gap-2 rounded-btn px-3 text-left text-sm focus-visible:outline-2 focus-visible:outline-accent ${source === value ? 'bg-accent-soft font-medium text-accent' : 'text-content-secondary hover:bg-surface-2'}`}>
              <span className={`size-1.5 shrink-0 rounded-full ${source === value ? 'bg-accent' : 'bg-content-tertiary opacity-40'}`} /><span className="truncate">{sourceName(value, en)}</span>
            </button>)}
          </aside>
          <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label={c('日志结果', 'Log results')}>
            <div className="flex min-h-12 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-default px-4 py-3">
              <div className="flex flex-wrap items-center gap-3 text-xs text-content-secondary"><span className="font-medium text-content">{c('当前结果', 'Loaded results')} <span className="ml-1 tabular-nums">{page.entries.length}</span></span>
                {errors > 0 && <span className="text-red-700 dark:text-red-300">{errors} {c('条错误', 'errors')}</span>}{warnings > 0 && <span className="text-amber-800 dark:text-amber-300">{warnings} {c('条警告', 'warnings')}</span>}
                {filtered && <button type="button" className="text-accent hover:underline" onClick={clear}>{c('重置筛选', 'Reset filters')}</button>}
              </div>
              <span role="status" className="flex items-center gap-1.5 text-xs text-content-tertiary"><Activity size={12} />{error ? c('连接异常', 'Connection issue') : !updated ? c('正在连接', 'Connecting') : !live ? c('实时更新已暂停', 'Live updates paused') : c('每 3 秒更新', 'Updates every 3 seconds')}</span>
            </div>
            {error && <div role="alert" className="flex items-start gap-2 border-b border-default bg-danger-soft px-4 py-3 text-sm text-danger"><AlertCircle size={16} className="mt-0.5 shrink-0" /><span className="min-w-0 break-words">{error} <button type="button" onClick={refresh} className="ml-2 font-medium underline">{c('重试', 'Retry')}</button></span></div>}
            <div className="grid grid-cols-[minmax(0,1fr)_64px_18px] gap-3 border-b border-default bg-surface-2 px-4 py-2.5 text-xs text-content-tertiary sm:grid-cols-[82px_72px_104px_minmax(0,1fr)_18px]" aria-hidden="true">
              <span>{c('时间', 'Time')}<span className="sm:hidden"> / {c('消息', 'Message')}</span></span><span>{c('级别', 'Level')}</span><span className="hidden sm:block">{c('功能', 'Feature')}</span><span className="hidden sm:block">{c('消息', 'Message')}</span><span />
            </div>
            <div className="min-h-48 flex-1 overflow-y-auto" aria-busy={busy}>
              {page.entries.length ? <ol>{page.entries.map(row => <li key={row.id} className="border-b border-default last:border-b-0" style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 56px' }}>
                <button type="button" aria-label={c('查看日志详情', 'Inspect log')} aria-describedby={`log-${row.id}`} onClick={() => { setSelected(row); setLive(false) }}
                  className="group grid min-h-14 w-full grid-cols-[minmax(0,1fr)_64px_18px] items-center gap-x-3 gap-y-2 px-4 py-3 text-left hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent sm:grid-cols-[82px_72px_104px_minmax(0,1fr)_18px]">
                  <time className="col-start-1 row-start-1 text-xs tabular-nums text-content-tertiary sm:col-auto sm:row-auto" dateTime={row.timestamp || undefined} title={row.timestamp}>{time(row.timestamp, en)}</time>
                  <span className={`col-start-2 row-start-1 w-fit rounded px-1.5 py-1 text-xs font-medium sm:col-auto sm:row-auto ${levelStyle(row.level)}`}>{levelNames[row.level]?.[en ? 1 : 0] || row.level}</span>
                  <span className="hidden truncate text-xs text-content-secondary sm:block">{sourceName(row.source, en)}</span>
                  <span id={`log-${row.id}`} className="col-span-2 col-start-1 row-start-2 min-w-0 sm:col-auto sm:row-auto sm:col-span-1"><span className="block truncate text-sm text-content">{row.message || c('空消息', 'Empty message')}</span><span className="mt-1 block text-xs text-content-tertiary sm:hidden">{sourceName(row.source, en)}</span></span>
                  <ChevronRight size={15} className="col-start-3 row-start-1 text-content-tertiary group-hover:text-accent sm:col-auto sm:row-auto" />
                </button>
              </li>)}</ol> : <div className="flex min-h-64 flex-col items-center justify-center gap-3 px-6 text-center"><FileSearch size={30} className="text-content-tertiary" /><p className="text-sm font-medium text-content">{busy ? c('正在读取日志…', 'Reading logs…') : error ? c('暂时无法读取日志', 'Logs are unavailable') : filtered ? c('没有符合条件的日志', 'No matching logs') : c('还没有运行日志', 'No logs yet')}</p><p className="max-w-sm text-xs leading-relaxed text-content-tertiary">{filtered ? c('换个关键词或放宽筛选条件，也可以继续加载更早记录。', 'Try another search, broaden your filters or load older entries.') : c('运行功能后，这里会显示可用的诊断记录。', 'Available diagnostics appear here as features run.')}</p>{filtered && <button type="button" className="text-sm text-accent hover:underline" onClick={clear}>{c('重置筛选', 'Reset filters')}</button>}</div>}
              {page.next_cursor && page.entries.length < 1000 && <div className="flex justify-center border-t border-default px-4 py-4"><button type="button" className={button} disabled={busy} onClick={loadMore}><ArrowDown size={14} />{c('加载更早日志', 'Load older logs')}</button></div>}
            </div>
            <footer className="flex shrink-0 flex-wrap justify-between gap-2 border-t border-default px-4 py-3 text-xs text-content-tertiary">
              <span>{c('最新记录在前', 'Newest first')}{page.truncated ? c('；已达单次扫描上限，可继续加载', '; scan limit reached, load older logs to continue') : ''}{page.entries.length >= 1000 ? c('；已达显示上限，请缩小筛选范围', '; display limit reached, narrow your filters') : ''}</span>
              <span>{updated ? c('上次更新 ', 'Updated ') + updated.toLocaleTimeString() : c('等待连接', 'Waiting for connection')}</span>
            </footer>
          </section>
        </div>
      </div>
    </div>
    {selected && <LogDetails entry={selected} en={en} onClose={() => setSelected(null)} />}
  </div>
}
