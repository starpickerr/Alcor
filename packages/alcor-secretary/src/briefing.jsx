import { Notice } from './notice.jsx'
import { BriefingToggle } from './briefing-toggle.jsx'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal, IconRefreshOutlineRegular, IconSettingsOutlineMedium, IconChevronLeftOutlineRegular, IconChevronRightOutlineRegular, IconPlusOutlineRegular, MarkdownText } from '@deepseek-ai/dsh-client-ui-primitives'
import { Empty, Field } from './business-pages.jsx'
import { localDate } from './presentation.js'
import { BriefingReport, markdownLabels } from './briefing-report.jsx'
import { BriefingSources } from './briefing-sources.jsx'

async function unwrap(promise) { const result = await promise; if (!result.ok) throw new Error(result.error?.message || '操作未完成，请重试。'); return result.value }
const call = (api, method, args = {}, write = false) => unwrap(api.business({ domain: 'briefing', method, arguments: args, ...(write ? { requestId: crypto.randomUUID() } : {}) }))
const active = progress => ['starting', 'collecting', 'model'].includes(progress?.phase)
const statusNames = { completed: '已生成', partial: '部分来源失败', failed: '生成失败', running: '生成中' }

function BriefingRules({ api }) {
  const [rules, setRules] = useState([])
  const [selected, setSelected] = useState(null)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = () => call(api, 'list_rules').then(setRules)
  useEffect(() => { load().catch(error => setError(error.message)).finally(() => setLoading(false)) }, [api])
  const summary = value => value.replace(/```[\s\S]*?```/g, '').replace(/<[^>]*>/g, '').replace(/[#*`>~]/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/(^|\n)\s*(?:[-+]\s+|\d+\.\s+)/g, ' ').replace(/\s+/g, ' ').trim()
  const title = rule => summary(rule.text.split('\n').find(line => line.trim()) || '筛选规则').slice(0, 64)
  function open(rule) { setSelected(rule); setText(rule.text); setEnabled(rule.enabled); setEditing(!rule.id); setDeleting(false); setError('') }
  async function run(method, args) {
    setBusy(true); setError('')
    try { const value = await call(api, method, args, true); await load(); return value }
    catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  async function save(event) {
    event.preventDefault()
    const value = await run(selected.id ? 'update_rule' : 'create_rule', { ...(selected.id ? { rule_id: selected.id } : {}), payload: { text, enabled, ...(selected.id ? {} : { source: '*' }) } })
    if (value) { setSelected(value); setEditing(false) }
  }
  return <section className="alcor-rule-section">
    <div className="alcor-rule-heading"><h2>筛选与写作规则</h2></div>
    <div className="alcor-rule-toolbar"><Button variant="ghost" aria-label="刷新规则" title="刷新规则" disabled={loading || busy} onClick={async () => { setLoading(true); setError(''); try { await load() } catch (error) { setError(error.message) } finally { setLoading(false) } }}><IconRefreshOutlineRegular size={16} /></Button><Button variant="outline" size="sm" onClick={() => open({ text: '', enabled: true })}><IconPlusOutlineRegular size={16} />新增规则</Button></div>
    {loading ? <Notice role="status">正在读取规则…</Notice> : rules.length ? <div className="alcor-rule-cards">{rules.map(rule => { const description = summary(rule.text.split('\n').slice(1).join('\n')); return <div className="alcor-rule-card" key={rule.id}><button type="button" className="alcor-rule-card-open" aria-label={`查看规则：${title(rule)}`} onClick={() => open(rule)}>
      <div className="alcor-rule-card-top"><strong>{title(rule)}</strong></div>
      {description && <p>{description}</p>}<IconChevronRightOutlineRegular className="alcor-rule-chevron" size={16} />
    </button><BriefingToggle label={`启用规则：${title(rule)}`} checked={rule.enabled} disabled={busy} onChange={enabled => run('update_rule', { rule_id: rule.id, payload: { enabled } })} /></div> })}</div> : <div className="alcor-rule-empty">还没有自定义规则</div>}
    {error && !selected && <Notice tone="error" role="alert">{error}</Notice>}
    <Modal open={!!selected} title={selected?.id ? '规则详情' : '新增规则'} closeLabel="关闭" onClose={() => { if (!busy) setSelected(null) }} className="alcor-editor-dialog alcor-rule-dialog">{selected && <div className="alcor-rule-detail">
      {editing ? <form onSubmit={save}><Field label="规则内容"><textarea required autoFocus maxLength={4000} rows={12} value={text} onChange={event => setText(event.target.value)} placeholder="使用 Markdown 编写筛选和写作要求" /></Field><div className="alcor-rule-enabled"><span>启用规则</span><BriefingToggle label="启用规则" checked={enabled} disabled={busy} onChange={setEnabled} /></div><div className="alcor-actions"><Button type="submit" variant="primary" disabled={busy || !text.trim()}>{busy ? '正在保存…' : '保存规则'}</Button>{selected.id && <Button type="button" variant="ghost" disabled={busy} onClick={() => { setEditing(false); setText(selected.text); setEnabled(selected.enabled) }}>取消编辑</Button>}</div></form> : <>
        <div className="alcor-rule-detail-toolbar"><div className="alcor-rule-enabled"><span>启用规则</span><BriefingToggle label="启用规则" checked={selected.enabled} disabled={busy} onChange={async enabled => { const previous = selected; setSelected({ ...selected, enabled }); const value = await run('update_rule', { rule_id: selected.id, payload: { enabled } }); setSelected(value || previous) }} /></div><Button variant="outline" disabled={busy} onClick={() => { setText(selected.text); setEnabled(selected.enabled); setEditing(true); setDeleting(false) }}>编辑规则</Button></div>
        <div className="alcor-rule-preview"><MarkdownText text={selected.text} streaming={false} labels={markdownLabels} /></div>
        <div className="alcor-rule-delete">{deleting ? <><Notice tone="warning">删除这条规则？</Notice><Button variant="outline" disabled={busy} onClick={async () => { if (await run('delete_rule', { rule_id: selected.id }) !== undefined) setSelected(null) }}>确认删除</Button><Button variant="ghost" disabled={busy} onClick={() => setDeleting(false)}>取消</Button></> : <Button variant="ghost" disabled={busy} onClick={() => setDeleting(true)}>删除规则</Button>}</div>
      </>}{error && <Notice tone="error" role="alert">{error}</Notice>}
    </div>}</Modal>
  </section>
}

function BriefingTimeline({ revisions, selected, onSelect, timeZone }) {
  const ordered = [...revisions].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.number - a.number)
  return <aside className="alcor-briefing-revisions" aria-label="更新历史"><h2>更新历史 <small>{revisions.length}</small></h2>
    <ol className="alcor-briefing-timeline" aria-label="简报更新历史">
      <li data-status="current"><button type="button" aria-current={!selected ? 'true' : undefined} onClick={() => onSelect(0)}><strong>当前版本</strong><span>最新简报内容</span></button></li>
      {ordered.map(row => <li data-status={row.status} key={row.number}><button type="button" data-revision={row.number} aria-current={selected === row.number ? 'true' : undefined} onClick={() => onSelect(row.number)}>
        <span className="alcor-revision-heading"><time dateTime={row.created_at}>{new Date(row.created_at).toLocaleTimeString('zh-CN', { hour12: false, timeZone })}</time><small>#{row.number}</small></span>
        <span>{new Date(row.created_at).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', timeZone })} · {row.trigger === 'scheduled' ? '自动生成／更新' : '手动生成／更新'}</span>
        <span className="alcor-revision-status">{statusNames[row.status]}</span>
      </button></li>)}
    </ol>
  </aside>
}

export function BriefingWorkspace({ api, briefingContext, openSession, openPlugins, initialView }) {
  const [history, setHistory] = useState([])
  const [selected, setSelected] = useState(initialView?.selected ?? '')
  const [revision, setRevision] = useState(initialView?.revision ?? 0)
  const [detail, setDetail] = useState(null)
  const [sources, setSources] = useState([])
  const [progress, setProgress] = useState({ phase: 'idle' })
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [settings, setSettings] = useState(initialView?.settings ?? false)
  const [zone, setZone] = useState(''), [zoneInput, setZoneInput] = useState(initialView?.zoneInput ?? '')
  const [zoneSaving, setZoneSaving] = useState(false), [zoneError, setZoneError] = useState('')
  const day = zone ? new Date().toLocaleDateString('sv-SE',{timeZone:zone}) : localDate()
  const settingsButton = useRef(null)
  const settingsHeading = useRef(null)
  useEffect(() => { if (settings) settingsHeading.current?.focus() }, [settings])
  function closeSettings() { setSettings(false); requestAnimationFrame(() => settingsButton.current?.focus()) }
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
    const [rows, configured, preferences] = await Promise.all([call(api, 'list_briefings'), call(api, 'list_sources'), call(api, 'get_preferences')])
    setZone(preferences.time_zone || '')
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
        if (key !== lastRun && ['completed', 'failed', 'cancelled', 'interrupted'].includes(value.phase)) {
          await refresh()
          if (value.phase === 'completed' && value.briefingId && !currentRevision.current && (!initialView || lastRun)) { setSelected(value.briefingId); setRevision(0); setDetail(await call(api, 'get_briefing', { briefing_id: value.briefingId })) }
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
      const run = unwrap(await api.startBriefing({ date: day, notes: '', ...briefingContext(), requestId: crypto.randomUUID() }))
      setProgress(run)
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  async function resume() {
    setBusy(true); setError('')
    try { setProgress(unwrap(await api.resumeBriefing({ requestId: progress.requestId }))) }
    catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  async function mutate(method, args) {
    setBusy(true); setError('')
    try { await call(api, method, args, true); setDetail(await call(api, 'get_briefing', { briefing_id: selected })); setRevision(0); return true }
    catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  const today = history.some(row => row.id.slice(4,14) === day)
  const report = detail?.briefing
  if (settings) return <div className="alcor-business" data-briefing-settings>
    <header className="alcor-app-header"><div className="alcor-actions"><Button variant="ghost" aria-label="返回简报" title="返回简报" onClick={closeSettings}><IconChevronLeftOutlineRegular size={18} /></Button><h1 ref={settingsHeading} tabIndex={-1}>简报设置</h1></div></header>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    <div className="alcor-page-content alcor-briefing-settings">
      <div className="alcor-briefing-settings-column">
        <section aria-labelledby="alcor-briefing-sources-heading"><h2 id="alcor-briefing-sources-heading">信息来源</h2><BriefingSources api={api} openPlugins={name => openPlugins(name, {settings:true, selected, revision, zoneInput})} /></section>
        <section aria-labelledby="alcor-briefing-timezone-heading">
          <h2 id="alcor-briefing-timezone-heading">简报时区</h2>
          <form className="alcor-briefing-timezone" onSubmit={async event => {
            event.preventDefault()
            setZoneSaving(true); setZoneError('')
            try {
              const value = zoneInput.trim()
              if (value) new Intl.DateTimeFormat('zh-CN',{timeZone:value})
              const preferences = await call(api,'update_preferences',{payload:{time_zone:value}},true)
              setZone(preferences.time_zone); setZoneInput(preferences.time_zone)
            } catch { setZoneError('时区未保存，请使用有效的时区名称。') }
            finally { setZoneSaving(false) }
          }}>
            <Input aria-label="简报时区" value={zoneInput} disabled={zoneSaving} placeholder={Intl.DateTimeFormat().resolvedOptions().timeZone}
              onChange={event => { setZoneInput(event.target.value); setZoneError('') }} />
            <div className="alcor-briefing-timezone-actions">
              <Button variant="ghost" size="sm" disabled={zoneSaving || !zoneInput.trim()} onClick={() => { setZoneInput(''); setZoneError('') }}>跟随系统</Button>
              <Button type="submit" variant="outline" size="sm" disabled={zoneSaving || zoneInput.trim()===zone}>{zoneSaving ? '正在保存…' : '保存时区'}</Button>
            </div>
            {zoneError && <Notice tone="error" role="alert">{zoneError}</Notice>}
          </form>
        </section>
      </div>
      <BriefingRules api={api} />
    </div>
  </div>
  return <div className="alcor-business"><header className="alcor-app-header"><div><h1>简报</h1></div><div className="alcor-actions"><Button variant="ghost" aria-label="刷新简报" title="刷新简报" onClick={() => refresh().catch(error => setError(error.message))}><IconRefreshOutlineRegular size={16} /></Button><Button ref={settingsButton} variant="ghost" aria-label="简报设置" title="简报设置" onClick={() => { setZoneInput(zone); setSettings(true) }}><IconSettingsOutlineMedium size={18} /></Button><Button variant="primary" disabled={running || loading || !sources.length} onClick={generate}>{today ? '更新今日简报' : '生成今日简报'}</Button></div></header>
    {(error || progress.phase !== 'idle') && <div className="alcor-page-feedback">
      {error && <Notice tone="error" role="alert" className="alcor-error">{error}</Notice>}
      {active(progress) && <Notice role="status">{progress.message}<Button variant="ghost" disabled={busy} onClick={() => call(api, 'cancel_generation', { requestId: progress.requestId }).then(setProgress).catch(error => setError(error.message))}>取消生成</Button></Notice>}
      {['failed', 'cancelled', 'interrupted'].includes(progress.phase) && <Notice role="status">{progress.message}<Button variant="outline" disabled={running} onClick={resume}>{progress.phase === 'failed' ? '重试' : '继续生成'}</Button></Notice>}
      {progress.sessionId && <Button variant="ghost" size="sm" onClick={() => openSession(progress.sessionId)}>执行详情</Button>}
    </div>}

    <div className="alcor-briefing-layout"><aside className="alcor-briefing-history"><h2>历史简报</h2>{history.map(row => <button type="button" key={row.id} aria-current={row.id === selected ? 'true' : undefined} onClick={() => { setSelected(row.id); setRevision(0) }}><strong>{row.title}</strong><small>{statusNames[row.status]} · {row.id === selected && !revision && detail?.briefing.id === row.id ? detail.briefing.kept_count : row.kept_count} 条精选信息 · {row.id.slice(4)}</small></button>)}</aside>
      <article className="alcor-briefing-detail">{report ? <><div className="alcor-briefing-title"><h2>{report.title}</h2><span>{statusNames[report.status]}</span></div><p className="alcor-muted">简报条目：{detail.items.length}{revision ? ` · 历史版本 ${revision}` : ''}</p><p className="alcor-muted">生成于 {new Date(report.generated_at).toLocaleString('zh-CN',{timeZone:zone || undefined})}</p>{report.error_message && <Notice role="status">{report.error_message}</Notice>}
        <BriefingReport timeZone={zone || undefined} sources={sources} detail={detail} revision={revision} busy={running} createTask={async item => {
          const receipt = detail.actions?.[item.id]?.todo
          if (['detached', 'deleted'].includes(receipt?.state) || receipt?.result?.operation === 'matching_required') throw new Error('请打开条目，选择并关联已有事务。')
          const result = await call(api, 'update_action', { item_id: item.id, payload: { kind: 'todo', operation: 'create', title: item.title } }, true)
          const updated = await call(api, 'get_briefing', { briefing_id: selected })
          setDetail(updated)
          if (!updated.actions?.[item.id]?.todo?.business && !updated.actions?.[item.id]?.todo?.target_id) throw new Error('事务尚未建立，请打开条目核对关联建议。')
          return result
        }} renderActions={item => <><div className="alcor-actions">{['todo', 'calendar'].map(kind => { const action = detail.actions?.[item.id]?.[kind]; return <Button key={kind} variant="outline" disabled={running || !!revision} onClick={() => setDecision({ item, kind, operation: action?.target_id ? 'confirm_update' : 'create', start_at: '', end_at: '', target_id: '' })}>{action?.target_id ? '管理已关联' : '转为'}{kind === 'todo' ? '事务' : '日程'}</Button> })}</div>{Object.entries(detail.actions?.[item.id] || {}).filter(([kind, action]) => ['todo', 'calendar'].includes(kind) && action).map(([kind, action]) => <p className="alcor-muted" key={kind}>{kind === 'todo' ? '事务' : '日程'}：{({ succeeded: '已关联', ignored: '已忽略', deferred: '已推迟', suggested: '待确认', failed: '未成功', deleted: '原记录已删除', briefing_only: '仅在简报保留' })[action.state] || action.state}{action.error && ` · ${action.error}`}</p>)}</>} />
      </> : <Empty mascot={!loading && !error} title={loading ? '正在读取简报…' : '还没有简报'}  />}</article>
      <BriefingTimeline timeZone={zone || undefined} revisions={detail?.revisions || []} selected={revision} onSelect={setRevision} /></div>
    <Modal open={!!decision} title="处理简报事项" closeLabel="关闭" onClose={() => { if (!busy) setDecision(null) }} className="alcor-editor-dialog">{decision && <form onSubmit={async event => { event.preventDefault(); const options = decision.kind === 'calendar' && decision.operation === 'create' ? { start_at: new Date(decision.start_at).toISOString(), end_at: new Date(decision.end_at).toISOString() } : {}; if (await mutate('update_action', { item_id: decision.item.id, payload: { kind: decision.kind, operation: decision.operation, ...(decision.operation === 'link' ? { target_id: decision.target_id } : {}), ...options } })) setDecision(null) }}><h3>{decision.item.title}</h3><Field label="处理方式"><select value={decision.operation} onChange={event => setDecision({ ...decision, operation: event.target.value })}>{Object.entries({ create: '创建新记录', link: '关联已有记录', confirm_update: '按最新来源更新关联记录', keep_current: '保留关联记录现状', ignore: '忽略建议', defer: '稍后处理', keep: '仅在简报保留', unlink: '解除关联' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>{decision.operation === 'link' && <Field label="关联记录"><select required value={decision.target_id} onChange={event => setDecision({ ...decision, target_id: event.target.value })}><option value="">选择已有记录</option>{targets.map(target => <option key={target.id} value={target.id}>{target.title}</option>)}</select></Field>}{decision.kind === 'calendar' && decision.operation === 'create' && <><Field label="开始时间"><Input type="datetime-local" required value={decision.start_at} onChange={event => setDecision({ ...decision, start_at: event.target.value })} /></Field><Field label="结束时间"><Input type="datetime-local" required value={decision.end_at} onChange={event => setDecision({ ...decision, end_at: event.target.value })} /></Field></>}<Button type="submit" variant="primary" disabled={busy}>确认处理</Button>{error && <Notice tone="error" role="alert">{error}</Notice>}</form>}</Modal>
  </div>
}
