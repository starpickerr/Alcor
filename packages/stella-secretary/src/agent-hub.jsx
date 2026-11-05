import React, { useState } from 'react'
import { Button, Input, Modal, SegmentedControl, IconPlusOutlineRegular, IconRefreshOutlineRegular, IconUsersOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field, showTime } from './business-pages.jsx'
import { hubDraftKey, jobLabels, jobRunning, readHubDraft } from './agent-hub.js'

function loadDraft() { try { return readHubDraft(window.sessionStorage) } catch { return null } }

export function AgentHub({ jobs, loading, busy, error, notice, refresh, start, cancel, openSession, startSession }) {
  const [draft, setDraft] = useState(loadDraft)
  const [selected, select] = useState(() => loadDraft()?.id ?? null)
  const [panel, setPanel] = useState('conversation')
  const [tab, setTab] = useState('flow')
  const [dialog, setDialog] = useState(null)
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [storageError, setStorageError] = useState('')
  const [launchError, setLaunchError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const saved = [...jobs].reverse()
  const job = saved.find(item => item.id === selected)
  const activeDraft = !job && draft?.id === selected ? draft : null
  const task = job ?? activeDraft
  const locked = busy || submitting
  function updateDraft(value) {
    setDraft(value)
    try {
      if (value) window.sessionStorage.setItem(hubDraftKey, JSON.stringify(value))
      else window.sessionStorage.removeItem(hubDraftKey)
      setStorageError('')
    } catch { setStorageError('草稿暂时无法保存在此浏览器，关闭页面前请先复制任务内容。') }
  }
  function choose(id) { select(id); setPanel('conversation'); setLaunchError('') }
  function newTask() {
    if (draft && !jobs.some(item => item.id === draft.id)) { choose(draft.id); return }
    setTitle(''); setGoal(''); setDialog('new')
  }
  async function execute() {
    if (!activeDraft || locked || !activeDraft.prompt.trim()) return
    setSubmitting(true); setLaunchError('')
    try {
      const result = await start(activeDraft)
      if (result) { updateDraft(null); select(result.id); setPanel('conversation') }
    } catch (failure) { setLaunchError(failure.message || '任务未能启动，请核对后重试。') }
    finally { setSubmitting(false) }
  }
  const draftRow = draft && !jobs.some(item => item.id === draft.id) ? [draft] : []
  return <div className="stella-agenthub">
    <div className="stella-hub-mobile"><SegmentedControl id="hub-mobile" label="协作区域" value={panel} onChange={setPanel} options={[{ value: 'tasks', label: '任务' }, { value: 'conversation', label: '交流' }, { value: 'details', label: '详情' }]} /></div>
    <aside className="stella-hub-tasks" data-mobile-active={panel === 'tasks'} id="hub-mobile-tasks-panel" role="tabpanel" aria-labelledby="hub-mobile-tasks">
      <header><h1>Agent 协作</h1><Button variant="outline" aria-label="新建协作任务" title="新建协作任务" onClick={newTask}><IconPlusOutlineRegular size={18} /></Button></header>
      <div className="stella-hub-task-list">{[...draftRow, ...saved].map(item => <button type="button" aria-current={item.id === selected ? 'true' : undefined} key={item.id} onClick={() => choose(item.id)}><strong>{item.title}</strong><small>{jobLabels[item.status] ?? '草稿 · 待启动'}</small></button>)}</div>
      {!loading && !saved.length && !draftRow.length && <p className="stella-muted">暂无任务</p>}
      <footer><Button variant="outline" onClick={() => setDialog('connections')}>管理接入</Button><Button variant="outline" onClick={() => setDialog('history')}>历史委托</Button><Button variant="outline" icon={<IconRefreshOutlineRegular size={16} />} onClick={refresh}>刷新</Button></footer>
    </aside>
    <section className="stella-hub-conversation" data-mobile-active={panel === 'conversation'} id="hub-mobile-conversation-panel" role="tabpanel" aria-labelledby="hub-mobile-conversation">
      <header><div><h2>{task?.title ?? '协作'}</h2></div><div className="stella-actions"><Button variant="outline" disabled title="自定义流程尚未接入；当前任务由一个 Stella 协作助手执行">编辑流程</Button><Button variant="primary" disabled={!activeDraft || !activeDraft.prompt.trim() || locked} onClick={execute}>启动</Button><Button variant="outline" disabled={!jobRunning(job) || locked} onClick={() => cancel(job.id)}>取消</Button></div></header>
      <div className="stella-hub-messages" aria-label="任务交流">
        {!task ? <div className="stella-hub-welcome"><IconUsersOutlineRegular size={32} /><h2>尚无协作任务</h2><Button variant="primary" onClick={newTask}>新建任务</Button><p className="stella-muted">仅支持 Stella 单助手。</p></div> : activeDraft ? <div className="stella-hub-welcome"><h2>任务已准备好</h2><p className="stella-muted">任务内容将发送给模型，可能产生费用。</p></div> : <>
          <article className="stella-hub-message stella-hub-user"><small>你的任务 · {showTime(job.createdAt)}</small><p>{job.prompt}</p></article>
          <article className="stella-hub-message"><small>Stella 协作助手 · {jobLabels[job.status]}</small>{job.output ? <pre>{job.output}</pre> : <p className="stella-muted">{jobRunning(job) ? '正在处理任务，最终答复会保存在这里。' : '本次任务没有已保存的答复。'}</p>}{job.diagnostic && <p className="stella-error">{job.diagnostic}</p>}</article>
        </>}
      </div>
      <div className="stella-hub-feedback">{loading && <p role="status">正在加载任务…</p>}{(error || launchError || storageError) && <p role="alert" className="stella-error">{error || launchError || storageError}</p>}{notice && <p role="status">{notice}</p>}</div>
      <form className="stella-hub-composer" onSubmit={event => { event.preventDefault(); void execute() }}>
        <div className="stella-actions"><select aria-label="消息接收者" disabled><option>公共交流</option></select><select aria-label="消息类型" disabled><option>发送消息</option></select><Button variant="outline" disabled title="多参与者定向消息尚未接入">@Agent</Button></div>
        <label className="stella-field"><span className="stella-hub-compose-label">任务内容</span><textarea aria-label="协作任务内容" placeholder={activeDraft ? '填写交给 Stella 的任务…' : '新建任务后，在这里填写任务内容…'} rows={4} maxLength={12000} disabled={!activeDraft || locked} value={activeDraft?.prompt ?? ''} onChange={event => updateDraft({ ...activeDraft, prompt: event.target.value })} onKeyDown={event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing) { event.preventDefault(); void execute() } }} /></label>
        <div className="stella-hub-composer-bottom"><small className="stella-muted">⌘ / Ctrl + Enter 执行</small><div className="stella-actions"><Button variant="outline" disabled title="仅发送正式消息尚未接入">发送</Button><Button type="submit" variant="primary" disabled={!activeDraft || !activeDraft.prompt.trim() || locked}>发送并执行</Button></div></div>
      </form>
    </section>
    <aside className="stella-hub-inspector" data-mobile-active={panel === 'details'} id="hub-mobile-details-panel" role="tabpanel" aria-labelledby="hub-mobile-details">
      <SegmentedControl id="hub-detail" label="任务详情视图" value={tab} onChange={setTab} options={[{ value: 'flow', label: '流程' }, { value: 'process', label: '过程' }, { value: 'agents', label: 'Agents' }]} />
      <div id={`hub-detail-${tab}-panel`} role="tabpanel" aria-labelledby={`hub-detail-${tab}`} className="stella-hub-detail-content">
        {!task ? <p className="stella-muted">请选择任务</p> : tab === 'flow' ? <><h2>任务流程</h2><ol className="stella-hub-flow"><li><strong>接收任务</strong><small>{job ? '已接收' : '等待启动'}</small></li><li><strong>Stella 协作助手</strong><small>{job ? jobLabels[job.status] : '待执行'}</small></li><li><strong>保存最终答复</strong><small>{job?.output ? '已有输出，请核对内容' : '暂无输出'}</small></li></ol></> : tab === 'process' ? <><h2>执行过程</h2>{job ? <><p>{jobLabels[job.status]} · {showTime(job.createdAt)}</p>{job.diagnostic && <p className="stella-error">{job.diagnostic}</p>}{job.childId && <Button variant="outline" onClick={() => openSession(job.childId)}>查看原生会话</Button>}</> : <p className="stella-muted">任务尚未启动，暂无执行记录。</p>}</> : <><h2>参与 Agents</h2><div className="stella-hub-card"><h3>Stella 协作助手</h3><small className="stella-muted">仅读取任务内容。人设与模型暂不可编辑。</small></div></>}
      </div>
    </aside>
    <Modal open={dialog !== null} title={dialog === 'new' ? '新建协作任务' : dialog === 'connections' ? '管理接入' : '历史委托'} closeLabel="关闭" onClose={() => setDialog(null)} className="stella-editor-dialog">
      {dialog === 'new' && <form onSubmit={event => { event.preventDefault(); const value = { id: crypto.randomUUID(), title: title.trim(), prompt: goal }; updateDraft(value); choose(value.id); setDialog(null) }}><Field label="任务标题"><Input data-modal-autofocus required maxLength={160} value={title} onChange={event => setTitle(event.target.value)} /></Field><Field label="目标"><textarea required rows={4} maxLength={12000} value={goal} onChange={event => setGoal(event.target.value)} /></Field><h3>选择参与者</h3><div className="stella-hub-card"><strong>Stella 协作助手</strong></div><div className="stella-actions"><Button type="submit" variant="primary" disabled={!title.trim() || !goal.trim()}>创建任务</Button><Button variant="outline" onClick={() => setDialog(null)}>取消</Button></div></form>}
      {dialog === 'connections' && <><div className="stella-hub-card"><h3>Stella</h3><p className="stella-muted">使用 Stella 主会话的模型。</p><Button variant="outline" onClick={async () => { setDialog(null); setLaunchError(''); try { await startSession() } catch (failure) { setLaunchError(failure.message || '无法开始 Stella 对话，请先选择工作区。') } }}>开始 Stella 对话</Button></div>{['Codex CLI', 'Claude Code', 'Cursor CLI'].map(name => <div className="stella-hub-card" key={name}><h3>{name}</h3><p className="stella-muted">尚未接入</p></div>)}</>}
      {dialog === 'history' && <div className="stella-hub-history">{saved.filter(item => !jobRunning(item)).map(item => <button type="button" key={item.id} onClick={() => { choose(item.id); setDialog(null) }}><strong>{item.title}</strong><small>{jobLabels[item.status]} · {showTime(item.createdAt)}</small></button>)}{!saved.some(item => !jobRunning(item)) && <p className="stella-muted">暂无已结束的委托。</p>}</div>}
    </Modal>
  </div>
}
