import { Notice } from './notice.jsx'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Checkbox, MarkdownText } from '@deepseek-ai/dsh-client-ui-primitives'
import { visibleBriefingDetail } from './briefing-source-selection.js'

export const markdownLabels = { code: { copyLabel: '复制', copiedLabel: '已复制', toolbarLabels: { codeLabel: '代码', wrapLabel: '自动换行', unwrapLabel: '取消换行' } }, footnotes: '脚注' }
const preview = text => <MarkdownText text={text || ''} streaming={false} labels={markdownLabels} />

const sourceName = source => ({ email: '邮件', elearning: 'eLearning', calendar_agenda: 'Alcor 日程', alcor_tasks: 'Alcor 事务', alcor_bills: 'Alcor 账单', stella_tasks: 'Alcor 事务', stella_bills: 'Alcor 账单' })[source] || source
export function BriefingReport({ detail: storedDetail, sources, timeZone, revision, busy, renderActions, createTask }) {
  const detail = visibleBriefingDetail(storedDetail, sources, !!revision)
  const [filter, setFilter] = useState(null)
  const [focused, setFocused] = useState(null)
  const [checked, setChecked] = useState([])
  const [batchBusy, setBatchBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { setFilter(null); setFocused(null); setChecked([]); setError('') }, [detail.briefing.id, revision])
  const heading = useRef()
  useEffect(() => { if (focused) heading.current?.focus() }, [focused])
  const showTime = value => new Date(value).toLocaleString('zh-CN',{timeZone})
  const sourceTime = row => {
    const meta = row.payload || {}
    return [meta.time_kind === 'observed' ? '来源观测时间 ' + showTime(row.occurred_at) : '来源时间 ' + showTime(meta.published_at || row.occurred_at),
      meta.source_updated_at && meta.source_updated_at !== (meta.published_at || row.occurred_at) && '更新 ' + showTime(meta.source_updated_at),
      meta.observed_change_at && '本次发现 ' + showTime(meta.observed_change_at)].filter(Boolean).join(' · ')
  }
  const report = detail.briefing
  const snapshots = detail.mail || []
  const types = [...new Set([...snapshots.map(row => row.source), ...Object.values(report.source_windows || {}).map(row => row.source).filter(Boolean)])]
  const items = [...detail.items].sort((a, b) => (b.score || 0) - (a.score || 0) || a.sort_order - b.sort_order)
  const item = items.find(row => row.id === focused)
  const eligible = items.filter(row => checked.includes(row.id) && !row.payload?._preview_only && !detail.actions?.[row.id]?.todo?.target_id && !detail.actions?.[row.id]?.todo?.business)
  const disabled = busy || !!revision || batchBusy
  async function batch() {
    setBatchBusy(true); setError('')
    const failures = []
    for (const row of eligible) {
      try { await createTask(row); setChecked(current => current.filter(id => id !== row.id)) }
      catch (error) { failures.push(`${row.title}：${error.message}`) }
    }
    setError(failures.join('；')); setBatchBusy(false)
  }
  return <>
    {!!types.length && <details className="alcor-source-summary"><summary>来源与时间窗口</summary>
      {types.map(source => { const rows = snapshots.filter(row => row.source === source); return <section key={source} className="alcor-briefing-source-group"><div className="alcor-briefing-source-window"><h3>{sourceName(source)}</h3><div>{Object.entries(report.source_windows || {}).filter(([key, window]) => window.source === source || key === source || key.startsWith(source + ':')).map(([key, window]) => <p key={key}>{window.name && window.name !== source && window.name !== sourceName(source) && <span>{window.name} · </span>}<time>{showTime(window.start)}</time><span aria-hidden="true"> → </span><time>{showTime(window.end)}</time></p>)}</div></div><div className="alcor-briefing-counts">{[['candidate', '候选信息', rows.length], ['selected', '精选信息', rows.filter(row => row.category === 'selected').length], ['filtered', '过滤信息', rows.filter(row => row.category === 'filtered').length]].map(([category, label, count]) => <button type="button" key={category} aria-label={`${sourceName(source)} · ${label}`} aria-pressed={filter?.source === source && filter.category === category} onClick={() => { setFilter(filter?.source === source && filter.category === category ? null : { source, category }); setFocused(null) }}><strong>{report.mail_snapshot_available ? count : '—'}</strong><span>{label}</span></button>)}</div></section> })}
    </details>}
    {filter ? <section className="alcor-briefing-sources"><div className="alcor-briefing-report-heading"><h3>{sourceName(filter.source)} · {({ candidate: '候选信息', selected: '精选信息', filtered: '过滤信息' })[filter.category]}</h3><Button variant="ghost" onClick={() => setFilter(null)}>返回 AI 报告</Button></div>{report.mail_snapshot_available ? snapshots.filter(row => row.source === filter.source && (filter.category === 'candidate' || row.category === filter.category)).map(row => <details className="alcor-briefing-source" key={row.id}><summary>{row.title}</summary><p>{row.snippet}</p><p>{row.filter_reason || row.decision?.reason}</p>{preview(row.body || row.payload?.body)}</details>) : <Notice>此历史版本没有保留来源快照。</Notice>}</section> : <section className="alcor-briefing-sources" aria-label="AI 报告">
      <div className="alcor-briefing-report-heading"><div><h2>AI 报告</h2><p className="alcor-muted">{items.length} 条信息</p></div><div className="alcor-actions"><Checkbox label="全选" checked={items.length > 0 && checked.length === items.length} disabled={disabled || !items.length} onChange={all => setChecked(all ? items.filter(row => !row.payload?._preview_only).map(row => row.id) : [])} /><Button variant="primary" disabled={disabled || !eligible.length} onClick={batch}>建立事务{eligible.length ? ` (${eligible.length})` : ''}</Button></div></div>
      {error && <Notice tone="error" role="alert">{error}</Notice>}
      {!item && items.length > 0 && report.body && <div className="alcor-report">{preview(report.body)}</div>}
      {item ? <article className="alcor-briefing-item-detail"><Button variant="ghost" onClick={() => setFocused(null)}>返回报告列表</Button><h3 ref={heading} tabIndex={-1}>{item.title}</h3><small>{item.sender} · {sourceTime(item)}{item.payload?.collected_at && ` · 采集于 ${showTime(item.payload.collected_at)}`}</small><h4>条目内容</h4><div className="alcor-report">{preview(item.payload?._briefing_report || item.payload?.body || item.snippet)}</div><h4>判断依据</h4><p>{item.payload?._briefing_reason || snapshots.find(row => row.id === item.source_item_id)?.filter_reason || '详见来源筛选记录。'}</p>{/^https?:\/\//i.test(item.url) && <a href={item.url} target="_blank" rel="noreferrer">查看原文</a>}{(item.payload?._briefing_members?.length || 0) > 1 && <details><summary>关联来源</summary><ul>{item.payload._briefing_members.map(row => <li key={row.id}>{/^https?:\/\//i.test(row.url) ? <a href={row.url} target="_blank" rel="noreferrer">{row.title}</a> : row.title} · {showTime(row.occurred_at)}</li>)}</ul></details>}{item.payload?._preview_only ? <Notice>更新今日简报后可关联这个条目。</Notice> : renderActions(item)}</article> : items.length ? <div className="alcor-briefing-item-list">{items.map(row => { const linked = detail.actions?.[row.id]?.todo; return <div className="alcor-briefing-item-row" key={row.id}><span className="alcor-briefing-item-check"><Checkbox label={`选择条目：${row.title}`} checked={checked.includes(row.id)} disabled={disabled || row.payload?._preview_only} onChange={value => setChecked(current => value ? [...current, row.id] : current.filter(id => id !== row.id))} /></span><button type="button" aria-label={`查看条目：${row.title}`} onClick={() => setFocused(row.id)}>{(linked?.target_id || linked?.business) && <small>已关联事务</small>}<strong>{row.title}</strong><p>{row.snippet}</p><span>{row.sender} · {sourceTime(row)}</span><span aria-hidden="true" className="alcor-briefing-item-chevron">›</span></button></div> })}</div> : <div className="alcor-report">{preview(report.body || (detail.scopeChanged ? '来源已更改，请更新简报。' : '暂无需要处理的信息。'))}</div>}
    </section>}
  </>
}
