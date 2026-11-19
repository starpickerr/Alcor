import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Button, IconRefreshOutlineRegular, IconChevronRightOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Notice } from './notice.jsx'
import { logCategory, logCategoryLabels, logFilterCategories, logSources } from './log-contract.js'

const clock = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

function ColumnFilter({ label, value, activeLabel, onChange, children }) {
  return <div className="alcor-log-filter" data-active={Boolean(activeLabel)} title={activeLabel || label}>
    <span aria-hidden="true">{activeLabel || label}</span><IconChevronRightOutlineRegular size={12} aria-hidden="true" />
    <select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>{children}</select>
  </div>
}

function LogDetail({ row, copy }) {
  const diagnostic = row.diagnostic
  return <div className="alcor-log-detail">
    <header><strong>{diagnostic ? '故障详情' : '日志详情'}</strong><Button variant="ghost" size="sm" onClick={() => void copy([row])}>复制此条</Button></header>
    {diagnostic?.reason && <p className="alcor-log-reason">{diagnostic.reason}</p>}
    <dl>
      {diagnostic && <div><dt>错误类型 / 代码</dt><dd><code>{diagnostic.code}</code></dd></div>}
      {diagnostic?.businessCode && <div><dt>业务错误</dt><dd><code>{diagnostic.businessCode}</code></dd></div>}
      {diagnostic?.systemCode && <div><dt>系统错误</dt><dd><code>{diagnostic.systemCode}</code></dd></div>}
      {row.operation && <div><dt>操作</dt><dd><code>{row.operation}</code></dd></div>}
      <div><dt>来源</dt><dd>{row.source}</dd></div><div><dt>日志级别</dt><dd>{logCategoryLabels[row.level]}</dd></div>
      <div><dt>发生时间</dt><dd><time dateTime={row.time}>{row.time}</time></dd></div>
      {row.durationMs != null && <div><dt>耗时</dt><dd>{row.durationMs} ms</dd></div>}
      {row.requestId && <div><dt>请求编号</dt><dd><code>{row.requestId}</code></dd></div>}
    </dl>
    {diagnostic?.locations.length > 0 && <section><h3>调用位置</h3><pre>{diagnostic.locations.join('\n')}</pre></section>}
    {diagnostic ? <details className="alcor-log-raw"><summary>原始记录</summary><pre>{row.message}</pre></details> : <pre>{row.message}</pre>}
  </div>
}

export function RuntimeLogsPage({ api }) {
  const [query, setQuery] = useState(''), [category, setCategory] = useState('normal'), [source, setSource] = useState(''), [range, setRange] = useState('all')
  const [page, setPage] = useState(null), [busy, setBusy] = useState(true), [error, setError] = useState('')
  const [automatic, setAutomatic] = useState(true), [feedback, setFeedback] = useState('')
  const request = useRef(0)
  const load = useCallback(async (before, background = false) => {
    const generation = ++request.current
    if (!background) setBusy(true)
    try {
      const result = await api.logs({ query, source, level: category === 'normal' ? 'normal' : 'all',
        ...(category !== 'all' && category !== 'normal' ? { category } : {}), ...(before ? { before } : {}),
        ...(range !== 'all' ? { since: new Date(Date.now() - Number(range) * 3600000).toISOString() } : {}) })
      if (!result.ok) throw new Error('日志服务暂不可用，请重试。')
      if (generation !== request.current) return
      setPage(previous => ({ ...result.value, entries: before ? [...(previous?.entries ?? []), ...result.value.entries] : result.value.entries }))
      setError('')
    } catch {
      if (generation === request.current) setError('无法读取运行日志，请检查插件连接后重试。')
    } finally { if (generation === request.current) setBusy(false) }
  }, [api, query, category, source, range])

  useEffect(() => {
    request.current++
    setPage(null); setBusy(true); setError(''); setFeedback('')
    const timer = setTimeout(() => void load(), 250)
    return () => { clearTimeout(timer); request.current++ }
  }, [load])
  useEffect(() => {
    if (!automatic) return
    const timer = setInterval(() => { if (!document.hidden) void load(undefined, true) }, 3000)
    return () => clearInterval(timer)
  }, [automatic, load])

  async function copy(entries) {
    try {
      await navigator.clipboard.writeText(entries.map(row => `${row.time} ${row.level.toUpperCase()} [${row.source}] ${row.message}`).join('\n'))
      setFeedback('已复制')
    } catch { setFeedback('复制失败，请展开详情后手动选择文本。') }
  }
  const filtered = query || category !== 'normal' || source || range !== 'all'
  const reset = () => { setQuery(''); setCategory('normal'); setSource(''); setRange('all') }
  return <section className="alcor-desk alcor-logs" aria-label="运行日志">
    <header className="alcor-page-heading"><h1>运行日志</h1><div className="alcor-log-actions">
      <Button className="alcor-icon-action" variant="ghost" aria-label="刷新" title="刷新" onClick={() => { setAutomatic(true); void load() }} disabled={busy}><IconRefreshOutlineRegular size={16} /></Button>
      <Button variant="outline" disabled={!page?.entries.length} onClick={() => void copy(page.entries)}>复制当前结果</Button>
    </div></header>
    <div className="alcor-log-primary" role="search" aria-label="筛选运行日志">
      <label className="alcor-log-search"><span>搜索</span><input type="search" value={query} maxLength={200} placeholder="搜索日志内容" onChange={event => setQuery(event.target.value)} /></label>
    </div>
    {error && <Notice tone="error" role="alert">{error}<Button variant="ghost" onClick={() => void load()}>重试</Button></Notice>}
    {page?.storage === 'failed' && <Notice tone="warning" role="alert">日志保存失败，当前内存中的记录仍可查看；重启后可能丢失。</Notice>}
    <div className="alcor-log-status"><span>{busy ? '正在读取…' : automatic ? '自动刷新中' : '历史记录'}{page ? ` · 显示 ${page.entries.length} / ${page.matched} 条` : ''}</span><span role="status">{feedback}</span></div>
    <div className="alcor-log-results">
      <div className="alcor-log-columns" role="group" aria-label="日志表头">
        <span></span>
        <ColumnFilter label="时间" value={range} activeLabel={range !== 'all' && `近 ${range} 小时`} onChange={setRange}>
          <option value="all">全部时间</option><option value="24">最近 24 小时</option><option value="1">最近 1 小时</option>
        </ColumnFilter>
        <ColumnFilter label="类别" value={category} activeLabel={category === 'all' ? '全部' : logCategoryLabels[category]} onChange={setCategory}>
          <option value="normal">常用（不含调试）</option><option value="all">全部（含调试）</option>
          {logFilterCategories.map(value => <option key={value} value={value}>{logCategoryLabels[value]}</option>)}
        </ColumnFilter>
        <ColumnFilter label="来源" value={source} activeLabel={source} onChange={setSource}>
          <option value="">全部来源</option>{(page?.sources ?? logSources).map(value => <option key={value}>{value}</option>)}
        </ColumnFilter>
        <div className="alcor-log-heading-end">
          <span className="alcor-log-message-heading">操作 / 消息</span>
          {filtered && <Button variant="ghost" size="sm" aria-label="重置筛选" title="重置筛选" onClick={reset}>重置<span className="alcor-log-reset-suffix">筛选</span></Button>}
        </div>
      </div>
      <div aria-busy={busy}>
      {page?.entries.length ? <ol aria-label="日志记录">{page.entries.map(row => <li key={row.id}>
        <details><summary><IconChevronRightOutlineRegular className="alcor-log-chevron" size={14} /><time dateTime={row.time} title={row.time}>{clock.format(new Date(row.time))}</time>
          <span className="alcor-log-category" data-category={logCategory(row)}>{logCategoryLabels[logCategory(row)]}</span>
          <span className="alcor-log-source" title={row.source}>{row.source}</span>
          <span className="alcor-log-message">{row.operation ?? (row.message.split('\n')[0] || '空消息')}</span></summary>
          <LogDetail row={row} copy={copy} />
        </details>
      </li>)}</ol> : <div className="alcor-log-empty" role="status">{busy ? '正在读取日志…' : error ? '日志暂不可用' : filtered ? '没有符合条件的日志' : '暂无运行日志'}
        {!busy && filtered && <p>试试其他关键词或重置筛选。</p>}</div>}
      {page?.nextBefore && <div className="alcor-log-more"><Button variant="outline" disabled={busy} onClick={() => { setAutomatic(false); void load(page.nextBefore) }}>加载更早日志</Button></div>}
      </div>
    </div>
  </section>
}
