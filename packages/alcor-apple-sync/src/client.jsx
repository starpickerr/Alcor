import noticeStyle from '../../alcor-secretary/src/notice.css'
import { Notice } from '../../alcor-secretary/src/notice.jsx'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Modal, IconCheckOutlineRegular, IconChecklistOutlineRegular, IconClockOutlineRegular, IconWarningOutlineRegular, IconChevronRightOutlineRegular, IconCloseOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { remote } from './remote.js'
import { connectionStatus, formatSyncTime, reviewItems, resolutionRequest, deletionPhaseLabel, canRestoreLocalItem, boundConflictPending } from './presentation.js'
import style from './style.css'
import { createCategoryRebuildPage } from './category-rebuild-page.js'

export const inject = ['slots', 'remote']
const authorizations = { not_determined: '同步前需要允许访问 Apple 日历或提醒事项。', denied: '请在 macOS 系统设置 → 隐私与安全性中允许完整访问。', restricted: '系统限制了访问权限。', write_only: '当前仅有写入权限，请在系统设置中允许完整访问。', unsupported: 'Apple 同步仅支持 macOS。', helper_missing: '系统连接组件未安装，请重新安装 Apple 同步插件。' }

function Choice({ value, selected, change, title, children, danger, disabled }) {
  return <label className="apple-sync-choice" data-selected={selected === value} data-danger={danger || undefined} data-disabled={disabled || undefined}>
    <input type="radio" name="apple-resolution" value={value} checked={selected === value} onChange={() => change(value)} disabled={disabled} />
    <span><strong>{title}</strong><span>{children}</span></span>
  </label>
}

function Review({ item, kind, state, busy: outsideBusy, error: outsideError, close, resolve, api, finishRebuild }) {
  const boundPending = boundConflictPending(item)
  const [choice, setChoice] = useState(boundPending?.choice ?? '')
  const [rebuildPlan, setRebuildPlan] = useState(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildError, setRebuildError] = useState('')
  const mounted = useRef(true)
  const rebuildPage = useRef(null)
  if (!rebuildPage.current) rebuildPage.current = createCategoryRebuildPage({ api, kind, appleId:item.apple_id })
  useEffect(() => { mounted.current = true; rebuildPage.current.activate(); return () => { mounted.current = false; rebuildPage.current.dispose() } }, [])
  const busy = outsideBusy || rebuilding, error = rebuildError || outsideError
  const [source, setSource] = useState(state.default_source_id || '')
  const [confirm, setConfirm] = useState(false)
  const backButton = useRef(null)
  useEffect(() => { if (confirm) backButton.current?.focus() }, [confirm])
  const missing = item.kind === 'missing_source'
  const localRestore = canRestoreLocalItem(item), restorePending = item.local_restore_pending === true
  const destructive = missing && choice === 'keep_apple'
  const groupLabel = kind === 'calendar' ? '日历' : '清单'
  const uncertain = item.type === 'operation' && ['sending', 'uncertain'].includes(item.state)
  const pendingDelete = item.type === 'operation' && ['delete', 'truncate', 'exclude'].includes(item.action)
  const retained = ['identity_candidate','scan_claim','category_create','category_retained','marker_conflict'].includes(item.type)
  const single = item.type === 'operation' || item.kind === 'legacy_retained'
  const actionable = !uncertain && (single || item.type === 'record' || ['name', 'color', 'missing_source'].includes(item.kind))
  const title = rebuildPlan ? `确认创建空 Apple ${groupLabel}？` : retained ? '查看待核对状态' : confirm ? `删除 Alcor ${groupLabel}？` : uncertain ? item.state === 'sending' ? '正在向 Apple 提交' : '请核对 Apple 操作结果' : item.type === 'operation' ? pendingDelete ? deletionPhaseLabel(item) : '等待同步' : '处理同步冲突'
  const apply = async () => {
    if (missing && choice === 'keep_alcor') {
      setRebuilding(true); setRebuildError('')
      try {
        if (!rebuildPlan) { const plan = await rebuildPage.current.preview(source); if (mounted.current) setRebuildPlan(plan) }
        else { const value = await rebuildPage.current.confirm(rebuildPlan); if (mounted.current) finishRebuild(value) }
      } catch (e) { if (mounted.current) { setRebuildPlan(null); setRebuildError(e.message) } }
      finally { if (mounted.current) setRebuilding(false) }
      return
    }
    if (destructive && !confirm) setConfirm(true); else resolve(single ? item.type === 'operation' ? 'cancel' : 'acknowledge' : choice, source) }
  return <Modal open title={title} headless className="apple-sync-review" onClose={() => { if (!busy) close() }}>
    <header className="apple-sync-review-header"><h2>{title}</h2><Button variant="ghost" size="sm" aria-label="关闭处理窗口" disabled={busy} onClick={close}><IconCloseOutlineRegular size={18} /></Button></header>
    <div className="apple-sync-review-body">
      <div className="apple-sync-subject"><span className="apple-sync-eyebrow">{item.label}</span><h3>{item.title}</h3>{item.source && <p>{item.source}</p>}{localRestore && <p>Alcor ID：{item.alcor_id ?? item.todo_id}</p>}</div>
      {rebuildPlan ? <Notice tone="warning" className="apple-sync-notice"><strong>在 {rebuildPlan.source.title || rebuildPlan.source.name || rebuildPlan.source_id} 创建空{groupLabel}「{rebuildPlan.title}」</strong><p>只创建容器，不上传原成员。以下 {rebuildPlan.retained_members.length} 项及旧关联证据保留在本地，等待重新关联。</p><ul>{rebuildPlan.retained_members.map(member => <li key={member.id}>{member.title}</li>)}</ul></Notice> : confirm ? <Notice tone="warning" className="apple-sync-danger-note"><strong>将删除此{groupLabel}及其中全部{kind === 'calendar' ? '日程' : '事务'}。</strong><p>只有确认 Apple 中的来源已被删除时，才选择此操作。Alcor 中的删除无法撤销。</p></Notice> : <>
        {retained ? <Notice tone="warning" className="apple-sync-notice"><strong>{item.label}</strong><p>{item.type === 'marker_conflict' ? '旧关联来源或当前内容尚未核实。两侧内容和核对证据已保留，此项目不会自动重新关联或发送，其他正常项目继续同步。' : ['category_create','category_retained'].includes(item.type) ? item.reason : item.type === 'identity_candidate' ? '这是先前观察到的候选。原项目和候选证据已保留，请在 Apple 中核对来源及内容；此处不会自动合并、删除或新建本地项目。' : item.state === 'uncertain' ? '已有操作的结果尚未确认，相关来源暂不重复执行。提交阶段和证据已保留；请先核对本地及 Apple 内容，此处不会重发或清除占用。' : '此来源仍被同步操作占用，请稍后刷新。不会从这里取消或重复执行。'}</p><p>{['category_create','category_retained'].includes(item.type) ? `本地分类：${item.local_id} · 阶段：${item.state}` : item.type === 'identity_candidate' ? `候选清单：${item.native?.list_id || ''}` : `来源容器：${item.group_id}`}{formatSyncTime(item.observed_at || item.started_at) && ` · 记录于 ${formatSyncTime(item.observed_at || item.started_at)}`}</p></Notice> : item.type === 'operation' ? uncertain ? <Notice tone="warning" className="apple-sync-notice"><strong>{item.state === 'sending' ? '正在向 Apple 提交' : 'Apple 操作结果未知'}</strong><p>{item.state === 'sending' ? '请等待本机操作结果；暂不能取消此请求。' : '本地内容和证据已保留，请核对；此操作不会自动重试，不能当作尚未发送。'}</p></Notice> : <>
          <Notice className="apple-sync-notice"><strong>本地内容仍然保留</strong><p>{pendingDelete ? item.document?._confirmed_intent ? '本次具体删除已确认。断线或重启后，目标、内容、范围与权限不变且明确尚未发送时会继续；可取消此次操作或暂停同步。' : 'Alcor 将请求 Apple 删除，并在本机操作完成后处理本地记录。等待本次具体确认，可取消此请求。' : '此操作尚未提交，下次同步会继续处理，也可以取消此次操作。'}</p></Notice>
          <ol className="apple-sync-progress"><li data-done><IconCheckOutlineRegular size={15} /><span>已记录操作</span></li><li aria-current="step"><IconClockOutlineRegular size={15} /><span>{pendingDelete ? deletionPhaseLabel(item) : '等待同步'}</span></li><li><span className="apple-sync-progress-dot" /><span>完成同步</span></li></ol>
          <Notice className="apple-sync-support">取消仅撤回这次待处理操作，保留现有内容。</Notice>
        </> : item.type === 'legacy_deletion' || item.kind === 'legacy_delete' ? <Notice tone="warning" className="apple-sync-notice"><strong>旧版删除需要重新核对</strong><p>Apple 内容和原有关联已保留，不会自动删除。暂不能在 Alcor 重新执行；你可以在 Apple 中核对并自行处理，或暂时保留此记录。</p></Notice> : item.kind === 'legacy_retained' ? <Notice className="apple-sync-support">旧版遗留内容已保留。确认后会移除此提示，不会删除内容。</Notice> : <>
          <Notice className="apple-sync-instruction">{boundPending ? boundPending.phase === 'native_saved' ? 'Apple 已有成功更新回执，但 Alcor 本地提交尚未确认。可在此核对原操作回执，不再次发送；仍未确认时请在 Apple 核对内容，保留两侧，勿重复操作。' : '先前的冲突处理结果待核对。这里只核对原操作回执，不重新写入，也不改用另一侧覆盖。' : restorePending ? '先前的本地恢复结果待核对。这里只核对已有回执，不重复创建项目，Apple 保持不变。' : localRestore ? 'Alcor 项目已缺失。可从已关联的 Apple 来源恢复到本地，Apple 保持不变。' : missing ? `来源可能暂时不可用，或账户中的${groupLabel}已发生变化。请选择如何继续。` : `选择要保留的一侧，确认后会更新另一侧。`}{item.type === 'record' && !localRestore && !boundPending && '仅处理双方都存在且关联明确的项目，不从此入口删除内容。'}</Notice>
          <fieldset className="apple-sync-choices" disabled={busy}><legend className="apple-sync-sr-only">选择处理方式</legend>
            {missing ? <>
              {(item.candidates ?? []).map(candidate => <Choice key={candidate.id} value={`rebind:${candidate.id}`} selected={choice} change={setChoice} title={`重新关联到「${candidate.title}」`}>{candidate.source || 'Apple'} · 保留本地内容并恢复关联</Choice>)}
              <Choice value="keep_alcor" selected={choice} change={setChoice} title={`保留 Alcor，重建 Apple ${groupLabel}`} disabled={!state.sources?.length}>保留本地内容，并在所选账户中创建同名{groupLabel}。</Choice>
              {choice === 'keep_alcor' && <label className="apple-sync-account">Apple 账户<select value={source} onChange={event => setSource(event.target.value)}><option value="">选择账户</option>{(state.sources ?? []).map(account => <option key={account.id} value={account.id}>{account.title || account.name}</option>)}</select></label>}
              <Choice value="keep_apple" selected={choice} change={setChoice} title="确认 Apple 已删除此来源" danger>同步删除 Alcor 对应{groupLabel}及其中全部内容。下一步需再次确认。</Choice>
            </> : ['keep_alcor', 'keep_apple'].map(value => {
              const local = value === 'keep_alcor'
              const version = local ? item.local : item.native
              const detail = item.kind === 'name' ? version?.display_name || version?.name || version?.title : item.kind === 'color' ? version?.color : null
              const blocked = boundPending ? value !== boundPending.choice : local && (localRestore || item.native && item.local?.apple?.can_rename === false)
              return <Choice key={value} value={value} selected={choice} change={setChoice} title={local ? '保留 Alcor' : restorePending ? '核对本地恢复结果' : localRestore ? '仅恢复 Alcor 本地' : '采用 Apple'} disabled={blocked}>{detail && <span className="apple-sync-version">{item.kind === 'color' && <i style={{ background: detail }} />}{detail}</span>}{boundPending ? value === boundPending.choice ? '仅核对原处理结果，不重复写入。' : '原操作结果未核对，不能改用另一侧覆盖。' : localRestore ? local ? '此处不会删除或更改 Apple 内容。' : restorePending ? '只核对先前恢复的回执，不再次写入本地项目。' : '恢复缺失项目，不覆盖现有项目，不向 Apple 写入。' : blocked ? '此 Apple 来源不允许修改，请采用 Apple。' : local ? '用 Alcor 当前内容更新 Apple。' : '用 Apple 当前内容更新 Alcor。'}</Choice>
            })}
          </fieldset>
        </>}
        {(item.reason || item.error) && <details className="apple-sync-diagnostic"><summary>查看具体原因</summary><Notice tone="warning">{item.reason || item.error}</Notice></details>}
      </>}
      {error && <Notice tone="error" role="alert" className="apple-sync-error">{error}</Notice>}
    </div>
    <footer className="apple-sync-review-footer"><span>{rebuildPlan ? '仅创建空容器，不上传原成员' : confirm ? '此操作无法撤销' : '确认前不会更改内容'}</span><div><Button ref={backButton} variant="outline" disabled={busy} onClick={() => { if (rebuildPlan) { rebuildPage.current.invalidate(); setRebuildPlan(null) } else if (confirm) setConfirm(false); else close() }}>{confirm || rebuildPlan ? '返回' : '暂不处理'}</Button>{actionable && <Button variant="primary" className={confirm ? 'apple-sync-destructive' : ''} disabled={busy || (!single && !choice) || (missing && choice === 'keep_alcor' && !source)} onClick={apply}>{busy ? '处理中…' : rebuildPlan ? `确认创建空${groupLabel}` : missing && choice === 'keep_alcor' ? '查看创建预览' : confirm ? `删除 Alcor ${groupLabel}` : destructive ? '继续…' : item.type === 'operation' ? '取消此次操作' : item.kind === 'legacy_retained' ? '确认保留' : boundPending ? '核对处理结果' : restorePending ? '核对恢复结果' : localRestore ? '恢复到 Alcor' : '确认并同步'}</Button>}</div></footer>
  </Modal>
}

function Connection({ kind, api }) {
  const title = kind === 'calendar' ? 'Apple 日历' : 'Apple 提醒事项'
  const [state, setState] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [readError, setReadError] = useState('')
  const [selected, setSelected] = useState(null)
  const revision = useRef(0)
  const working = useRef(false)
  const call = async (method, args = {}) => {
    const result = await api.invoke({ method, arguments: { kind, ...args } })
    if (!result.ok) throw new Error(result.error?.message ?? '操作未成功，请重试。')
    return result.value
  }
  async function run(operation) {
    revision.current++
    working.current = true
    setBusy(true); setMessage(''); setNotice('')
    try { await operation() }
    catch (error) { setMessage(error.message) }
    finally { revision.current++; working.current = false; setBusy(false) }
  }
  useEffect(() => {
    let disposed = false
    let refreshing = false
    const refresh = async () => {
      if (refreshing || working.current) return
      refreshing = true
      const started = revision.current
      try { const value = await call('status'); if (!disposed && started === revision.current) { setState(value); setReadError('') } }
      catch (error) { if (!disposed && started === revision.current) setReadError(error.message) }
      finally { refreshing = false }
    }
    void refresh()
    const timer = setInterval(refresh, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [api, kind])
  const options = state?.calendars ?? state?.lists ?? []
  const active = state?.connected && state?.auto_enabled
  async function sync() {
    if (state?.authorization !== 'full_access') {
      const authorized = await call('request_access')
      setState(authorized)
      if (authorized.authorization !== 'full_access') throw new Error(authorizations[authorized.authorization] ?? '需要系统完整访问授权。')
    }
    const { state: saved, result } = await call('start_sync')
    setState(saved)
    if (result.status !== 'success' && !saved.error) setMessage('部分项目尚未同步，请查看待处理内容后重试。')
  }
  const status = connectionStatus(state, busy, Boolean(readError))
  const items = reviewItems(state, kind)
  const current = items.find(item => item.key === selected)
  useEffect(() => { if (selected && !current) setSelected(null) }, [selected, current])
  const Icon = kind === 'calendar' ? IconClockOutlineRegular : IconChecklistOutlineRegular
  return <section className="apple-sync-connection" aria-label={title}>
    <div className="apple-sync-heading"><div className="apple-sync-service"><span className="apple-sync-service-icon"><Icon size={22} /></span><div><h3>{title}</h3><span className="apple-sync-badge" data-tone={status.tone} role="status"><span className="apple-sync-dot" aria-hidden="true" />{status.label}</span></div></div><div className="apple-sync-actions"><Button variant="outline" size="sm" disabled={busy || !active} onClick={() => void run(async () => setState(await call('disconnect')))}>暂停同步</Button><Button variant="primary" size="sm" disabled={busy || !state?.supported} onClick={() => void run(sync)}>{busy ? '处理中…' : '同步'}</Button></div></div>
    <div className="apple-sync-metadata">{formatSyncTime(state?.last_success) && <span>上次成功同步 <time dateTime={state.last_success}>{formatSyncTime(state.last_success)}</time></span>}{!!state?.links?.length && <span>{state.links.length} 个{kind === 'calendar' ? '日历' : '清单'}已关联</span>}</div>
    {state && state.authorization !== 'full_access' && <Notice tone="warning" className="apple-sync-notice"><p>{authorizations[state.authorization] || '需要系统完整访问授权。'}</p></Notice>}
    {state?.watch_degraded && <Notice tone="warning" className="apple-sync-notice"><p>当前 Apple 连接组件不支持实时监听，将继续定期同步。更新连接组件后可恢复实时监听。</p></Notice>}
    {(state?.error || message || readError) && !current && <Notice tone="error" className="apple-sync-notice" role="alert"><strong>{readError ? '暂时无法读取同步状态' : '同步未完成'}</strong><p>{message || readError || state.error}</p></Notice>}
    {!!items.length && <div className="apple-sync-inbox"><div className="apple-sync-inbox-heading"><h4>需要处理 <span>{items.length}</span></h4><span>等待确认</span></div><div className="apple-sync-rows">{items.map(item => <button type="button" key={item.key} className="apple-sync-row" disabled={busy} onClick={() => {setMessage(''); setSelected(item.key)}}><span className="apple-sync-row-icon" data-pending={item.type === 'operation'}>{item.type === 'operation' ? <IconClockOutlineRegular size={18} /> : <IconWarningOutlineRegular size={18} />}</span><span className="apple-sync-row-text"><strong>{item.title}</strong><span>{item.label}{item.source && ` · ${item.source}`}</span></span><span className="apple-sync-row-action">{item.type === 'operation' ? '查看' : '处理'}<IconChevronRightOutlineRegular size={14} /></span></button>)}</div></div>}
    {(options.some(item => !item.writable) || state?.readonly_issues?.length > 0) && <details className="apple-sync-readonly"><summary>只读来源{state?.readonly_issues?.length ? ` · ${state.readonly_issues.length} 项保留` : ''}</summary><Notice>只读内容可导入 Alcor；更改需在 Apple 中完成。</Notice>{state?.readonly_issues?.map(item => <Notice key={item.todo_id}>{item.reason}</Notice>)}</details>}
    {kind === 'calendar' && !!state?.coverage?.length && <details className="apple-sync-coverage"><summary>已同步的时间范围</summary>{state.coverage.slice(-3).map(item => <p key={`${item.start}:${item.checked_at}`}>{item.start.slice(0,10)} — {item.end.slice(0,10)}</p>)}<Notice>查看其他日期时按需加载；暂停同步时显示已保存的内容。</Notice></details>}
    {notice && <Notice className="apple-sync-success" role="status"><IconCheckOutlineRegular size={16} />{notice}</Notice>}
    {current && <Review key={current.key} item={current} kind={kind} state={state} api={api} finishRebuild={value => { setState(value); setSelected(null); setNotice('空 Apple 容器已创建；原成员保留本地，尚未重新关联。') }} busy={busy} error={message} close={() => setSelected(null)} resolve={(choice, source) => void run(async () => {const request = resolutionRequest(current,kind,choice,source)
      if (request.method === 'restore_local_item') {
        const restored = await api.restoreLocalItem({ kind, ...request.arguments })
        if (!restored.ok) throw new Error(restored.error?.message ?? '本地恢复未完成。')
        if (restored.value.status !== 'success') throw new Error(restored.value.message ?? '本地恢复结果待核对，未重复写入。')
        setState(await call('status')); setSelected(null); setNotice(current.local_restore_pending === true ? '已核对本地恢复结果，Apple 保持不变。' : '已恢复 Alcor 本地项目，Apple 保持不变。')
      } else if (request.method === 'resolve_conflict') {
        const resolved = await api.resolveItemConflict({ kind, identity: request.arguments[kind === 'calendar' ? 'alcor_id' : 'todo_id'], choice })
        if (!resolved.ok) throw new Error(resolved.error?.message ?? '冲突处理未完成。')
        if (resolved.value.status !== 'success') throw new Error(resolved.value.message ?? '冲突处理结果待核对，不会重发。')
        setState(await call('status')); setSelected(null); setNotice(choice === 'keep_apple' ? '已采用 Apple 内容更新 Alcor，Apple 保持不变。' : '已采用 Alcor 内容更新原 Apple 项目。')
      } else {
        setState(await call(request.method,request.arguments)); setSelected(null); setNotice(current.type === 'operation' ? '已取消此次操作，内容已保留。' : '已处理，将按所选方式同步。')
      }})} />}
  </section>
}

function Settings({ api, view }) {
  if (view === 'summary') return '双向同步 Apple 日历与提醒事项，保留分类和清单。'
  return <div className="apple-sync-settings"><h2>Apple 同步</h2><Connection kind="calendar" api={api} /><Connection kind="reminders" api={api} /></div>
}

export async function apply(ctx) {
  ctx.effect(() => { const element = document.createElement('style'); element.textContent = style + noticeStyle; document.head.append(element); return () => element.remove() })
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.alcorAppleSync'], inner => {
    inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@alcor/apple-sync', inject: () => ({ api: inner.remote.alcorAppleSync }) }, Settings))
  })
}
