import React, { useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Empty, Field, showTime } from './business-pages.jsx'
import { localDate } from './presentation.js'
import { BriefingReport } from './briefing-report.jsx'
import { BriefingSources } from './briefing-sources.jsx'

async function unwrap(promise) { const result = await promise; if (!result.ok) throw new Error(result.error?.message || '操作未完成，请重试。'); return result.value }
const call = (api, method, args = {}, write = false) => unwrap(api.business({ domain: 'briefing', method, arguments: args, ...(write ? { requestId: crypto.randomUUID() } : {}) }))
const active = progress => ['collecting', 'model'].includes(progress?.phase)
const statusNames = { completed: '已生成', partial: '部分来源失败', failed: '生成失败', running: '生成中' }

function BriefingRules({ api }) {
  const [rules, setRules] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = () => call(api, 'list_rules').then(setRules)
  useEffect(() => { load().catch(error => setError(error.message)) }, [api])
  async function run(method, args) { setBusy(true); setError(''); try { await call(api, method, args, true); await load(); return true } catch (error) { setError(error.message) } finally { setBusy(false) } }
  return <section className="stella-section"><h2>筛选与写作规则</h2>{rules.map(rule => <div className="stella-briefing-source" key={rule.id}><Checkbox label={rule.text} checked={rule.enabled} disabled={busy} onChange={enabled => run('update_rule', { rule_id: rule.id, payload: { enabled } })} /><Button variant="ghost" size="sm" disabled={busy} onClick={() => run('delete_rule', { rule_id: rule.id })}>删除规则</Button></div>)}<form onSubmit={async event => { event.preventDefault(); if (await run('create_rule', { payload: { text, enabled: true, source: '*' } })) setText('') }}><Field label="新增规则"><textarea required maxLength={4000} rows={3} value={text} onChange={event => setText(event.target.value)} /></Field><Button type="submit" disabled={busy || !text.trim()}>保存规则</Button></form>{error && <p role="alert">{error}</p>}</section>
}

function BriefingTimeline({ revisions, selected, onSelect }) {
  const ordered = [...revisions].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.number - a.number)
  return <aside className="stella-briefing-revisions" aria-label="更新历史"><h2>更新历史 <small>{revisions.length}</small></h2>
    <ol className="stella-briefing-timeline" aria-label="简报更新历史">
      <li data-status="current"><button type="button" aria-current={!selected ? 'true' : undefined} onClick={() => onSelect(0)}><strong>当前版本</strong><span>最新内容与事项状态</span></button></li>
      {ordered.map(row => <li data-status={row.status} key={row.number}><button type="button" data-revision={row.number} aria-current={selected === row.number ? 'true' : undefined} onClick={() => onSelect(row.number)}>
        <span className="stella-revision-heading"><time dateTime={row.created_at}>{new Date(row.created_at).toLocaleTimeString('zh-CN', { hour12: false })}</time><small>#{row.number}</small></span>
        <span>{new Date(row.created_at).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })} · {row.trigger === 'scheduled' ? '自动生成／更新' : '手动生成／更新'}</span>
        <span className="stella-revision-status">{statusNames[row.status]}</span>
      </button></li>)}
    </ol>
  </aside>
}

export function BriefingWorkspace({ api, withBriefingSession, openPlugins }) {
  const [history, setHistory] = useState([])
  const [selected, setSelected] = useState('')
  const [revision, setRevision] = useState(0)
  const [detail, setDetail] = useState(null)
  const [sources, setSources] = useState([])
  const [progress, setProgress] = useState({ phase: 'idle' })
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [settings, setSettings] = useState(false)
  const [sourceSettings, setSourceSettings] = useState(false)
  const [decision, setDecision] = useState(null)
  const [targets, setTargets] = useState([])
  useEffect(() => {
    let disposed = false
    setTargets([])
    if (decision) call(api, 'get_action_targets', { kind: decision.kind }).then(value => { if (!disposed) setTargets(value) }).catch(error => { if (!disposed) setError(error.message) })
    return () => { disposed = true }
  }, [api, decision?.item.id, decision?.kind])
  const running = active(progress) || busy
  const currentRevision = useRef(0)
  currentRevision.current = revision
  const selection = useRef('')
  selection.current = selected
  async function refresh() {
    const [rows, configured] = await Promise.all([call(api, 'list_briefings'), call(api, 'list_sources')])
    setHistory(rows); setSources(configured)
    if (!selection.current && rows.length) setSelected(rows[0].id)
    if (selection.current) {
      const id = selection.current; const number = currentRevision.current
      const value = await call(api, number ? 'get_revision' : 'get_briefing', { briefing_id: id, ...(number ? { number } : {}) })
      if (selection.current === id && currentRevision.current === number) setDetail(value)
    }
  }
  useEffect(() => {
    let disposed = false
    let polling = false
    let lastRun = ''
    refresh().catch(error => setError(error.message)).finally(() => setLoading(false))
    async function poll() {
      if (polling) return
      polling = true
      try {
        const value = await call(api, 'generation_status')
        if (disposed) return
        setProgress(value)
        const key = `${value.requestId}:${value.phase}`
        if (key !== lastRun && ['completed', 'failed', 'cancelled'].includes(value.phase)) {
          await refresh()
          if (value.phase === 'completed' && value.briefingId && !currentRevision.current) { setSelected(value.briefingId); setRevision(0); setDetail(await call(api, 'get_briefing', { briefing_id: value.briefingId })) }
        }
        lastRun = key
      } catch (error) { if (!disposed) setError(error.message) } finally { polling = false }
    }
    void poll(); const timer = setInterval(poll, 1500)
    return () => { disposed = true; clearInterval(timer) }
  }, [api])
  useEffect(() => {
    let disposed = false
    setDetail(null)
    if (selected) call(api, revision ? 'get_revision' : 'get_briefing', { briefing_id: selected, ...(revision ? { number: revision } : {}) }).then(value => { if (!disposed) setDetail(value) }).catch(error => { if (!disposed) setError(error.message) })
    return () => { disposed = true }
  }, [api, selected, revision])
  async function generate() {
    setBusy(true); setError('')
    try {
      const record = await withBriefingSession(parentSessionId => unwrap(api.generateBriefing({ date: localDate(), notes: '', parentSessionId, requestId: crypto.randomUUID() })))
      setProgress({ phase: 'completed' })
      await refresh(); setRevision(0); setSelected(record.business.briefingId); setDetail(await call(api, 'get_briefing', { briefing_id: record.business.briefingId }))
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  async function mutate(method, args) {
    setBusy(true); setError('')
    try { await call(api, method, args, true); setDetail(await call(api, 'get_briefing', { briefing_id: selected })); setRevision(0); return true }
    catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  const today = history.some(row => row.id === `brf-${localDate()}`)
  const report = detail?.briefing
  return <div className="stella-business"><header className="stella-app-header"><div><h1>简报</h1></div><div className="stella-actions"><Button variant="ghost" aria-label="刷新简报" title="刷新简报" onClick={() => refresh().catch(error => setError(error.message))}><IconRefreshOutlineRegular size={16} /></Button><Button variant="outline" onClick={() => setSourceSettings(true)}>信息来源</Button><Button variant="outline" onClick={() => setSettings(true)}>简报设置</Button><Button variant="primary" disabled={running || loading || !sources.length} onClick={generate}>{today ? '更新今日简报' : '生成今日简报'}</Button></div></header>
    {(error || active(progress) || progress.phase === 'failed' || progress.phase === 'cancelled') && <div className="stella-page-feedback">{error && <p role="alert" className="stella-error">{error}</p>}{active(progress) && <p role="status">{progress.message}<Button variant="ghost" onClick={() => call(api, 'cancel_generation').catch(error => setError(error.message))}>取消生成</Button></p>}</div>}
    <div className="stella-briefing-layout"><aside className="stella-briefing-history"><h2>历史简报</h2>{history.map(row => <button type="button" key={row.id} aria-current={row.id === selected ? 'true' : undefined} onClick={() => { setSelected(row.id); setRevision(0) }}><strong>{row.title}</strong><small>{statusNames[row.status]} · {row.kept_count} 条精选信息 · {row.id.slice(4)}</small></button>)}</aside>
      <article className="stella-briefing-detail">{report ? <><div className="stella-briefing-title"><h2>{report.title}</h2><span>{statusNames[report.status]}</span></div><p className="stella-muted">简报条目：{detail.items.length}{revision ? ` · 历史版本 ${revision}` : ''}</p><p className="stella-muted">{showTime(report.generated_at)}</p>{report.error_message && <p role="status">{report.error_message}</p>}
        <BriefingReport detail={detail} revision={revision} busy={running} createTask={async item => {
          const receipt = detail.actions?.[item.id]?.todo
          if (['detached', 'deleted'].includes(receipt?.state) || receipt?.result?.operation === 'matching_required') throw new Error('请打开条目，选择并关联已有事务。')
          const result = await call(api, 'update_action', { item_id: item.id, payload: { kind: 'todo', operation: 'create', title: item.title } }, true)
          const updated = await call(api, 'get_briefing', { briefing_id: selected })
          setDetail(updated)
          if (!updated.actions?.[item.id]?.todo?.business && !updated.actions?.[item.id]?.todo?.target_id) throw new Error('事务尚未建立，请打开条目核对关联建议。')
          return result
        }} renderActions={item => <><div className="stella-actions"><select aria-label={`处理状态：${item.title}`} disabled={running || !!revision} value={item.status} onChange={event => mutate('update_item_status', { item_id: item.id, status: event.target.value })}>{Object.entries({ undo: '待处理', done: '已完成', ignored: '已忽略', follow_up: '继续关注' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{['todo', 'calendar'].map(kind => { const action = detail.actions?.[item.id]?.[kind]; return <Button key={kind} variant="outline" disabled={running || !!revision} onClick={() => setDecision({ item, kind, operation: action?.target_id ? 'confirm_update' : 'create', start_at: '', end_at: '', target_id: '' })}>{action?.target_id ? '管理已关联' : '转为'}{kind === 'todo' ? '事务' : '日程'}</Button> })}</div>{Object.entries(detail.actions?.[item.id] || {}).filter(([kind, action]) => ['todo', 'calendar'].includes(kind) && action).map(([kind, action]) => <p className="stella-muted" key={kind}>{kind === 'todo' ? '事务' : '日程'}：{({ succeeded: '已关联', ignored: '已忽略', deferred: '已推迟', suggested: '待确认', failed: '未成功', deleted: '原记录已删除', briefing_only: '仅在简报保留' })[action.state] || action.state}{action.error && ` · ${action.error}`}</p>)}</>} />
      </> : <Empty title={loading ? '正在读取简报…' : '还没有简报'}  />}</article>
      <BriefingTimeline revisions={detail?.revisions || []} selected={revision} onSelect={setRevision} /></div>
    <Modal open={sourceSettings} title="简报信息来源" closeLabel="关闭" onClose={() => setSourceSettings(false)} className="stella-editor-dialog">{sourceSettings && <BriefingSources api={api} onSaved={setSources} openPlugins={name => { setSourceSettings(false); openPlugins(name) }} />}</Modal>
    <Modal open={settings} title="简报设置" closeLabel="关闭" onClose={() => setSettings(false)} className="stella-editor-dialog"><BriefingRules api={api} /></Modal>
    <Modal open={!!decision} title="处理简报事项" closeLabel="关闭" onClose={() => { if (!busy) setDecision(null) }} className="stella-editor-dialog">{decision && <form onSubmit={async event => { event.preventDefault(); const options = decision.kind === 'calendar' && decision.operation === 'create' ? { start_at: new Date(decision.start_at).toISOString(), end_at: new Date(decision.end_at).toISOString() } : {}; if (await mutate('update_action', { item_id: decision.item.id, payload: { kind: decision.kind, operation: decision.operation, ...(decision.operation === 'link' ? { target_id: decision.target_id } : {}), ...options } })) setDecision(null) }}><h3>{decision.item.title}</h3><Field label="处理方式"><select value={decision.operation} onChange={event => setDecision({ ...decision, operation: event.target.value })}>{Object.entries({ create: '创建新记录', link: '关联已有记录', confirm_update: '按最新来源更新关联记录', keep_current: '保留关联记录现状', ignore: '忽略建议', defer: '稍后处理', keep: '仅在简报保留', unlink: '解除关联' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>{decision.operation === 'link' && <Field label="关联记录"><select required value={decision.target_id} onChange={event => setDecision({ ...decision, target_id: event.target.value })}><option value="">选择已有记录</option>{targets.map(target => <option key={target.id} value={target.id}>{target.title}</option>)}</select></Field>}{decision.kind === 'calendar' && decision.operation === 'create' && <><Field label="开始时间"><Input type="datetime-local" required value={decision.start_at} onChange={event => setDecision({ ...decision, start_at: event.target.value })} /></Field><Field label="结束时间"><Input type="datetime-local" required value={decision.end_at} onChange={event => setDecision({ ...decision, end_at: event.target.value })} /></Field></>}<Button type="submit" variant="primary" disabled={busy}>确认处理</Button>{error && <p role="alert">{error}</p>}</form>}</Modal>
  </div>
}
