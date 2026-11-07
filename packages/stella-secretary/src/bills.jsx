import { Notice } from './notice.jsx'
import React, { useEffect, useId, useState } from 'react'
import { Button, Input, SegmentedControl, IconPlusOutlineRegular, IconChevronRightOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { currencies, minorUnits } from '../../stella-organizer/src/records.js'
import { Field, Empty } from './business-pages.jsx'
import { localDate, periodBills, ledgerCsv } from './presentation.js'
import { money, periodRange, periodTitle, periodStatus, shareText, donutArc, billValues, billPayload } from './bill-view.js'

const palette = ['#f87171', '#fb923c', '#fbbf24', '#a78bfa', '#38bdf8', '#2dd4bf', '#f472b6', '#a3e635']
const incomePalette = ['#34d399', '#2dd4bf', '#a3e635', '#38bdf8', '#86efac', '#22d3ee', '#4ade80', '#bef264']

function Totals({ total, cards = false }) {
  return <dl className={`stella-bill-totals${cards ? ' stella-bill-totals-cards' : ''}`}>
    {['income', 'expense', 'net'].map((key, index) => <div key={key}><dt>{['收入', '支出', '结余'][index]}</dt><dd data-direction={key}>{money(total[key + '_minor'], total.currency)}</dd></div>)}
  </dl>
}

function Composition({ rows, total, direction, currency }) {
  const [hovered, setHovered] = useState(null)
  const [focused, setFocused] = useState(null)
  const tooltipId = useId()
  const active = rows.find(row => row.category === (hovered ?? focused))
  const label = direction === 'income' ? '收入' : '支出'
  const colors = direction === 'income' ? incomePalette : palette
  let offset = 0
  return <section className="stella-bill-composition" aria-label={direction === 'income' ? '收入构成' : '支出构成'}>
    <h3>{label}构成</h3><strong data-direction={direction}>{money(total, currency)}</strong>
    {rows.length ? <>
      <div className="stella-bill-chart">
      <svg className="stella-bill-donut" viewBox="0 0 160 160" role="img" aria-label={`${direction === 'income' ? '收入' : '支出'}分类占比，合计${money(total, currency)}`}>
        <circle className="stella-bill-ring-track" cx="80" cy="80" r="58" fill="none" strokeWidth="22" />
        {rows.map((row, index) => {
          const share = Number(BigInt(row.amount_minor) * 1000000n / BigInt(total)) / 10000
          const start = offset; offset += share
          return <path key={row.category} className="stella-bill-segment" data-category={row.category} data-active={active?.category === row.category || undefined} d={donutArc(start, share)} fill="none" stroke={colors[index % colors.length]} strokeWidth="22" onPointerEnter={() => setHovered(row.category)} onPointerLeave={() => setHovered(null)} />
        })}
      </svg>
      {active && <div className="stella-bill-chart-tooltip" id={tooltipId} role="tooltip"><strong>{active.category}</strong><span><span data-direction={direction}>{label} {money(active.amount_minor, currency)}</span><span>占比 {shareText(active.share_basis_points)}</span></span></div>}
      </div>
      <ul className="stella-bill-legend">{rows.map((row, index) => <li key={row.category}><button type="button" data-active={active?.category === row.category || undefined} aria-label={`${row.category}，${label} ${money(row.amount_minor, currency)}，占比 ${shareText(row.share_basis_points)}`} aria-describedby={active?.category === row.category ? tooltipId : undefined} onPointerEnter={() => setHovered(row.category)} onPointerLeave={() => setHovered(null)} onFocus={() => setFocused(row.category)} onBlur={() => setFocused(null)} onKeyDown={event => { if (event.key === 'Escape') { setHovered(null); event.currentTarget.blur() } }}><span className="stella-bill-dot" style={{ background: colors[index % colors.length] }} /><span>{row.category}</span><span data-direction={direction}>{money(row.amount_minor, currency)}</span><span>{shareText(row.share_basis_points)}</span></button></li>)}</ul>
    </> : <div className="stella-bill-chart-empty">暂无{direction === 'income' ? '收入' : '支出'}</div>}
  </section>
}

function Statement({ report }) {
  const [direction, setDirection] = useState('expense')
  const range = periodRange(String(report.year), report.month ? String(report.month).padStart(2, '0') : '')
  return <div className="stella-bill-report">
    <section className="stella-bill-card"><Totals total={report} /><div className="stella-bill-period-meta"><span>周期 · {range.start} – {range.end}</span><time dateTime={report.generated_at}>汇总于 · {new Date(report.generated_at).toLocaleString('zh-CN')}</time></div></section>
    <section className="stella-bill-card"><h2>分类构成</h2><div className="stella-bill-compositions">{['expense', 'income'].map(type => <Composition key={type} rows={report.categories[type]} total={report[type + '_minor']} direction={type} currency={report.currency} />)}</div></section>
    <section className="stella-bill-card"><div className="stella-bill-ranking-header"><h2>明细排行</h2><SegmentedControl id="bill-ranking" label="排行收支" value={direction} onChange={setDirection} options={[{ value: 'expense', label: '支出' }, { value: 'income', label: '收入' }]} /></div>
      {report.rankings[direction].length ? <ol className="stella-bill-ranking">{report.rankings[direction].map((row, index) => <li key={row.id}><span>{index + 1}</span><div><strong>{row.name}</strong><p>{row.category} · {row.entry_count} 笔 · {shareText(row.share_basis_points)}</p></div><strong data-direction={direction}>{direction === 'income' ? '+' : '−'}{money(row.amount_minor, report.currency)}</strong></li>)}</ol> : <p className="stella-muted">暂无{direction === 'income' ? '收入' : '支出'}</p>}
    </section>
  </div>
}

export function BillsPage({ records, api, reloadKey, edit, create, download, busy, loading, icon }) {
  const [view, setView] = useState('ledger')
  const [year, setYear] = useState(String(new Date().getFullYear()))
  const [month, setMonth] = useState(localDate().slice(5, 7))
  const [currency, setCurrency] = useState('CNY')
  const [selected, setSelected] = useState(null)
  const [data, setData] = useState({ reports: [], ledger: { totals: [], days: [] } })
  const [pending, setPending] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const signature = records.map(row => `${row.id}:${row.revision}`).join(',')
  useEffect(() => {
    let active = true
    setPending(true); setError('')
    const call = async (method, args) => {
      const result = await api.business({ domain: 'bills', method, arguments: args })
      if (!result.ok) throw new Error(result.error?.message || '账单读取失败。')
      return result.value
    }
    Promise.all([call('statement_reports', { period_type: view === 'year' ? 'yearly' : 'monthly', year: Number(year) }), view === 'ledger' ? call('ledger_report', periodRange(year, month)) : null])
      .then(([reports, ledger]) => { if (active) setData({ reports, ledger: ledger ?? { totals: [], days: [] } }) })
      .catch(failure => { if (active) setError(failure.message) })
      .finally(() => { if (active) setPending(false) })
    return () => { active = false }
  }, [api, view, year, month, signature, reloadKey, retry])
  const years = [...new Set([String(new Date().getFullYear()), ...records.map(item => item.date.slice(0, 4))])].sort().reverse()
  const available = [...new Set((view === 'ledger' ? data.ledger.totals : data.reports).map(row => row.currency))].sort()
  const currentCurrency = available.includes(currency) ? currency : available[0] ?? currency
  const reports = data.reports.filter(row => row.currency === currentCurrency).sort((a, b) => (b.month ?? 0) - (a.month ?? 0))
  const report = reports.find(row => row.id === selected)
  const selectedMonth = view === 'ledger' ? month : report?.month ? String(report.month).padStart(2, '0') : ''
  const rows = periodBills(records, year, selectedMonth).filter(row => row.currency === currentCurrency)
  const rowMap = new Map(rows.map(row => [row.id, row]))
  const total = data.ledger.totals.find(row => row.currency === currentCurrency)
  const days = data.ledger.days.map(day => ({ ...day, rows: day.entry_ids.map(id => rowMap.get(id)).filter(Boolean) })).filter(day => day.rows.length)
  return <div className="stella-bills">
    <div className="stella-bill-tabs"><SegmentedControl id="bill-view" label="账单视图" value={view} onChange={next => { setView(next); setSelected(null) }} options={[{ value: 'ledger', label: '明细' }, { value: 'month', label: '月账单' }, { value: 'year', label: '年账单' }]} /></div>
    <div className="stella-page-content stella-bills-content">
      <div className="stella-bill-toolbar"><div className="stella-actions">{report ? <><Button variant="outline" onClick={() => setSelected(null)}>← 返回</Button><h2>{periodTitle(report)}</h2><span className="stella-bill-status" data-current={periodStatus(report) === '进行中' || undefined}>{periodStatus(report)}</span></> : <>
        <select aria-label="账单年份" value={year} onChange={event => { setYear(event.target.value); setSelected(null) }}>{years.map(value => <option key={value}>{value}</option>)}</select>
        {view === 'ledger' && <select aria-label="账单月份" value={month} onChange={event => setMonth(event.target.value)}>{Array.from({ length: 12 }, (_, index) => <option value={String(index + 1).padStart(2, '0')} key={index}>{index + 1} 月</option>)}</select>}
      </>}{available.length > 1 && <select aria-label="账单币种" value={currentCurrency} onChange={event => { setCurrency(event.target.value); setSelected(null) }}>{available.map(value => <option key={value}>{value}</option>)}</select>}</div>
        <div className="stella-actions"><Button variant="primary" disabled={busy} icon={<IconPlusOutlineRegular size={16} />} onClick={create}>记账</Button>{(view === 'ledger' || report) && <Button variant="outline" disabled={pending || !rows.length} onClick={() => download(ledgerCsv(rows), `stella-ledger-${year}${selectedMonth ? '-' + selectedMonth : ''}-${currentCurrency}.csv`, 'text/csv;charset=utf-8')}>导出 CSV</Button>}</div>
      </div>
      <div id={`bill-view-${view}-panel`} role="tabpanel" aria-labelledby={`bill-view-${view}`} aria-busy={loading || pending}>
        {error ? <Notice tone="error" role="alert" className="stella-bill-load-error">{error}<Button variant="outline" onClick={() => setRetry(value => value + 1)}>重试</Button></Notice> : pending || loading ? <Notice role="status" className="stella-muted">正在读取账单…</Notice> : view === 'ledger' ? <>
          {total && <Totals total={total} cards />}
          {!days.length ? <Empty icon={icon} title="这个月还没有流水" /> : days.map(day => {
            const dayTotal = day.totals.find(row => row.currency === currentCurrency)
            return <section className="stella-bill-day" key={day.date}><header><h2>{new Date(`${day.date}T12:00`).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' })}</h2><div><span data-direction="expense">支出 {money(dayTotal.expense_minor, currentCurrency)}</span><span data-direction="income">收入 {money(dayTotal.income_minor, currentCurrency)}</span></div></header>
              <ul className="stella-ledger-list">{day.rows.map(item => <li key={item.id}><button type="button" className="stella-bill-row" aria-label={`编辑账单：${item.title}`} onClick={() => edit(item)}><span className="stella-bill-row-copy"><strong>{item.title}</strong><span>{item.business?.category || '未分类'} · {item.business?.occurred_at?.slice(11, 16) || '—'}{item.status !== 'paid' && <span className="stella-bill-entry-status"> · {item.status === 'reversed' ? '已冲正' : '待处理'}</span>}{item.archived && ' · 已归档'}</span></span><strong className="stella-ledger-amount" data-direction={item.direction} data-inactive={item.status !== 'paid' || undefined}>{item.direction === 'income' ? '+' : '−'}{money(minorUnits(item.amount, item.currency), item.currency)}</strong><IconChevronRightOutlineRegular size={16} /></button></li>)}</ul>
            </section>
          })}
        </> : report ? <Statement report={report} /> : reports.length ? <div className="stella-bill-period-list">{reports.map(item => <button type="button" className="stella-bill-card" key={item.id} aria-label={`查看${periodTitle(item)}账单`} onClick={() => setSelected(item.id)}><div className="stella-bill-period-heading"><h2>{periodTitle(item)}</h2><span className="stella-bill-status" data-current={periodStatus(item) === '进行中' || undefined}>{periodStatus(item)}</span><IconChevronRightOutlineRegular size={16} /></div><Totals total={item} /></button>)}</div> : <Empty icon={icon} title={view === 'year' ? '这一年还没有已记账流水' : '这一年还没有月账单'} />}
      </div>
    </div>
  </div>
}

export function BillEditor({ record, save, remove, archive, cancel, busy }) {
  const [value, setValue] = useState(() => billValues(record))
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const set = (key, next) => setValue(previous => ({ ...previous, [key]: next }))
  const options = [...new Set([...Object.keys(currencies), value.currency])]
  return <form className="stella-editor stella-bill-editor" aria-label={record ? '编辑账单' : '新增账单'} onSubmit={async event => { event.preventDefault(); setError(''); try { await save(billPayload(value, record)) } catch (failure) { setError(failure.message) } }}>
    <h2>{record ? '编辑账单' : '新增账单'}</h2>
    <Field label="标题"><Input data-modal-autofocus required value={value.name} onChange={event => set('name', event.target.value)} /></Field>
    <div className="stella-form-grid"><Field label="金额"><Input required inputMode="decimal" value={value.amount} onChange={event => set('amount', event.target.value)} /></Field><Field label="币种"><select value={value.currency} onChange={event => set('currency', event.target.value)}>{options.map(item => <option key={item}>{item}</option>)}</select></Field></div>
    <div className="stella-form-grid"><Field label="收支"><select value={value.entry_type} onChange={event => set('entry_type', event.target.value)}><option value="expense">支出</option><option value="income">收入</option></select></Field><Field label="分类"><Input value={value.category} onChange={event => set('category', event.target.value)} /></Field></div>
    <Field label="发生时间"><Input required type="datetime-local" step="1" value={value.occurred_at} onChange={event => set('occurred_at', event.target.value)} /></Field>
    <Field label="账单状态"><select value={value.status} onChange={event => set('status', event.target.value)}><option value="posted">已记账</option><option value="pending">待处理</option><option value="reversed">已冲正</option></select></Field>
    <Field label="备注"><textarea aria-label="备注" rows={3} value={value.note} onChange={event => set('note', event.target.value)} /></Field>
    {error && <Notice tone="error" role="alert" className="stella-error">{error}</Notice>}
    <div className="stella-actions"><Button type="submit" variant="primary" disabled={busy}>保存账单</Button><Button type="button" variant="outline" disabled={busy} onClick={cancel}>取消编辑</Button>{record && <><Button type="button" variant="ghost" disabled={busy} onClick={archive}>{record.archived ? '恢复' : '归档'}</Button><Button type="button" variant="ghost" className="stella-bill-delete" disabled={busy} onClick={() => setDeleting(true)}>删除记录</Button></>}</div>
    {deleting && <Notice tone="warning" role="alert" className="stella-bill-delete-confirm"><p>删除这笔记录？收支统计将同步更新。</p><div className="stella-actions"><Button type="button" variant="outline" disabled={busy} onClick={remove}>确认删除</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => setDeleting(false)}>取消删除</Button></div></Notice>}
  </form>
}
