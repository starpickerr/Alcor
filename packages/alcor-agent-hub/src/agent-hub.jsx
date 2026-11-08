import { Notice } from './notice.jsx'
import { HubConversation } from './hub-conversation.jsx'
import React, { useState } from 'react'
import { Button, Input, Modal, SegmentedControl, MarkdownText, IconPlusOutlineRegular, IconRefreshOutlineRegular, IconUsersOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field, showTime } from './ui.jsx'
import { markdownLabels } from './ui.jsx'
import { hubDraftKey, clearHubDraft, jobLabels, jobRunning, stepLabels, canRetryStep, defaultHubAgents, filterHubJobs, readHubDraft } from './agent-hub.js'

function loadDraft() { try { return readHubDraft(window.sessionStorage) } catch { return null } }
const renderOutput = text => <MarkdownText text={text} streaming={false} labels={markdownLabels} />
function Status({ value, step = false }) { return <span className="alcor-hub-status" data-status={value}>{(step ? stepLabels : jobLabels)[value] ?? '草稿'}</span> }

export function AgentHub({ jobs, api, currentSession, loading, busy, error, notice, refresh, start, cancel, retry, openSession, startSession }) {
  const [draft, setDraft] = useState(loadDraft)
  const [selected, select] = useState(() => loadDraft()?.id ?? null)
  const [panel, setPanel] = useState('conversation')
  const [tab, setTab] = useState('flow')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [dialog, setDialog] = useState(null)
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [mode, setMode] = useState('parallel')
  const [storageError, setStorageError] = useState('')
  const [actionError, setActionError] = useState('')
  const [copied, setCopied] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const saved = filterHubJobs(jobs, query, filter)
  const job = jobs.find(item => item.id === selected)
  const activeDraft = !job && draft?.id === selected ? draft : null
  const task = job ?? activeDraft
  const locked = busy || submitting
  const agents = activeDraft?.agents ?? [{ name: '开发助手', task: activeDraft?.title ?? '' }]
  const ready = activeDraft?.prompt.trim() && agents.every(agent => agent.name.trim() && agent.task.trim())
  const steps = job?.collaboration?.steps ?? []
  const people = job?.collaboration?.participants ?? []
  const completed = steps.filter(step => step.status === 'succeeded').length
  const personName = step => people.find(person => person.id === step.participantId)?.name ?? '开发助手'
  function updateDraft(value) {
    setDraft(value)
    try {
      if (value) window.sessionStorage.setItem(hubDraftKey, JSON.stringify(value))
      else clearHubDraft(window.sessionStorage)
      setStorageError('')
    } catch { setStorageError('草稿暂时无法保存在此浏览器，关闭页面前请先复制任务内容。') }
  }
  function choose(id) { select(id); setPanel('conversation'); setActionError(''); setCopied(false); setTab('flow') }
  function newTask() {
    if (draft) { choose(draft.id); return }
    setTitle(''); setGoal(''); setMode('parallel'); setDialog('new')
  }
  function copyAsDraft() {
    if (draft) { setActionError('已有未执行的草稿，请先完成或放弃草稿。'); return }
    const divisions = steps.filter(step => step.stepId !== 'summary').slice(0, 3).map(step => ({ name: personName(step), task: step.title }))
    const value = { id: crypto.randomUUID(), title: task.title, prompt: task.prompt, ...(divisions.length ? { agents: divisions } : {}) }
    updateDraft(value); choose(value.id)
  }
  async function execute() {
    if (!activeDraft || locked || !ready) return
    setSubmitting(true); setActionError('')
    try {
      const result = await start(activeDraft)
      if (result) { updateDraft(null); choose(result.id) }
    } catch (failure) { setActionError(failure.message || '任务未能启动，请核对后重试。') }
    finally { setSubmitting(false) }
  }
  async function retryStep(step) {
    setSubmitting(true); setActionError('')
    try { await retry(job.id, step.stepId) }
    catch (failure) { setActionError(failure.message || '重试未能启动，请核对后重试。') }
    finally { setSubmitting(false) }
  }
  async function copyOutput() {
    try { await navigator.clipboard.writeText(job.output); setCopied(true); setActionError('') }
    catch { setActionError('复制失败，请检查剪贴板权限后重试。') }
  }
  function changeAgent(index, patch) { updateDraft({ ...activeDraft, agents: agents.map((agent, i) => i === index ? { ...agent, ...patch } : agent) }) }
  const feedback = <div className="alcor-hub-feedback">{loading && <Notice role="status">正在加载任务…</Notice>}{(error || actionError || storageError) && <Notice tone="error">{error || actionError || storageError}</Notice>}{notice && !job && <Notice tone="success" role="status">{notice}</Notice>}</div>
  function stepCard(step, index, process = false) {
    const last = step.attempts.at(-1)
    const blocked = step.status === 'pending' && step.dependsOn.some(id => ['failed', 'outcome_unknown', 'cancelled'].includes(steps.find(item => item.stepId === id)?.status))
    return <article className="alcor-hub-step" data-summary={step.stepId === 'summary' || undefined} key={step.stepId}>
      <header><span className="alcor-hub-avatar">{step.stepId === 'summary' ? '汇' : index + 1}</span><strong>{personName(step)}</strong><Status value={step.status} step /></header>
      <p>{step.stepId === 'summary' ? '整理并核对各助手的结果，形成完整答复。' : step.title}</p>
      {step.dependsOn.length > 0 && <small className="alcor-muted">{blocked ? '前序分工尚未完成，请先处理失败步骤' : step.status === 'pending' ? '等待前序分工完成后执行' : '已接收前序分工结果'}</small>}
      {process && step.attempts.map((attempt, i) => <div className="alcor-hub-attempt" key={attempt.executionId}>
        <div><strong>第 {i + 1} 次执行</strong><Status value={attempt.status} step /></div><small>{showTime(attempt.createdAt)}{attempt.model ? ` · ${attempt.model}` : ''}</small>
        {attempt.diagnostic && <Notice tone="warning">{attempt.diagnostic}</Notice>}
        {attempt.childId && <Button variant="outline" onClick={() => openSession(attempt.childId)}>查看原生会话</Button>}
      </div>)}
      {step.output && <details className="alcor-hub-step-output"><summary>查看分工结果</summary>{renderOutput(step.output)}</details>}
      {canRetryStep(step) && <Button variant="outline" disabled={locked || jobRunning(job) || step.dependsOn.some(id => steps.find(item => item.stepId === id)?.status !== 'succeeded')} onClick={() => retryStep(step)}>重试此步骤</Button>}
      {step.status === 'outcome_unknown' && <Notice tone="warning">执行结果未确认，请先查看原生会话，避免重复操作。</Notice>}
      {!process && last?.diagnostic && <Notice tone="warning">{last.diagnostic}</Notice>}
    </article>
  }
  return <div className="alcor-agenthub">
    <div className="alcor-hub-mobile"><SegmentedControl id="hub-mobile" label="协作区域" value={panel} onChange={setPanel} options={[{ value: 'tasks', label: '任务' }, { value: 'conversation', label: '工作台' }]} /></div>
    <aside className="alcor-hub-tasks" data-mobile-active={panel === 'tasks'} id="hub-mobile-tasks-panel" role="tabpanel" aria-labelledby="hub-mobile-tasks">
      <header><h1>AgentHub</h1><Button variant="outline" aria-label="新建协作任务" title="新建协作任务" onClick={newTask}><IconPlusOutlineRegular size={18} /></Button></header>
      <Input aria-label="搜索协作任务" placeholder="搜索任务" value={query} onChange={event => setQuery(event.target.value)} />
      <select className="alcor-hub-filter" aria-label="筛选协作任务" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部任务</option><option value="running">执行中</option><option value="completed">已完成</option><option value="attention">需要处理</option></select>
      <div className="alcor-hub-task-list">
        {draft && <button type="button" aria-current={draft.id === selected ? 'true' : undefined} onClick={() => choose(draft.id)}><strong>{draft.title || '未命名任务'}</strong><small>草稿 · 待启动</small></button>}
        {saved.map(item => <button type="button" aria-current={item.id === selected ? 'true' : undefined} key={item.id} onClick={() => choose(item.id)}><strong>{item.title}</strong><span><Status value={item.status} /><small>{showTime(item.createdAt)}</small></span></button>)}
      </div>
      {!loading && !saved.length && <p className="alcor-hub-empty-list">{query || filter !== 'all' ? '没有匹配的任务' : '暂无已启动的任务'}</p>}
      <footer><Button variant="outline" onClick={() => setDialog('connections')}>协作设置</Button><Button variant="outline" icon={<IconRefreshOutlineRegular size={16} />} onClick={refresh}>刷新任务</Button></footer>
    </aside>
    <section className="alcor-hub-conversation" data-mobile-active={panel === 'conversation'} id="hub-mobile-conversation-panel" role="tabpanel" aria-labelledby="hub-mobile-conversation">
      <header><div><h2>{task ? task.title || '未命名任务' : '协作工作台'}</h2>{task && <Status value={job?.status} />}</div><div className="alcor-actions">{job && <Button variant="outline" disabled={locked} onClick={copyAsDraft}>再建任务</Button>}{jobRunning(job) && <Button variant="outline" disabled={locked} onClick={() => setDialog('cancel')}>取消执行</Button>}{activeDraft && <Button variant="outline" disabled={locked} onClick={() => setDialog('discard')}>放弃草稿</Button>}</div></header>
      {feedback}
      <div className="alcor-hub-messages" aria-label="任务工作台">
        {!task ? <div className="alcor-hub-welcome"><IconUsersOutlineRegular size={32} /><h2>让开发助手协同工作</h2><div className="alcor-hub-intro"><span>明确目标</span><span>并行分工</span><span>汇总结果</span></div><Button variant="primary" onClick={newTask}>新建任务</Button></div> : activeDraft ? <>
          <div className="alcor-hub-section-heading"><h3>任务目标</h3><small className="alcor-muted">草稿自动保留在当前窗口</small></div>
          <form id="hub-draft-form" className="alcor-hub-draft" onSubmit={event => { event.preventDefault(); void execute() }}>
            <Field label="任务名称"><Input required maxLength={160} disabled={locked} value={activeDraft.title} onChange={event => updateDraft({ ...activeDraft, title: event.target.value })} /></Field>
            <Field label="任务内容"><textarea aria-label="协作任务内容" required rows={4} maxLength={12000} disabled={locked} value={activeDraft.prompt} onChange={event => updateDraft({ ...activeDraft, prompt: event.target.value })} onKeyDown={event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing) { event.preventDefault(); void execute() } }} /></Field>
          </form>
          <div className="alcor-hub-section-heading"><h3>Agent 分工</h3><Button variant="outline" disabled={locked || agents.length >= 3} onClick={() => updateDraft({ ...activeDraft, agents: [...agents, { name: '新助手', task: '' }] })}><IconPlusOutlineRegular size={16} />添加助手</Button></div>
          <div className="alcor-hub-divisions">{agents.map((agent, index) => <div className="alcor-hub-agent-editor" key={index}><header><span className="alcor-hub-avatar">{index + 1}</span><strong>分工 {index + 1}</strong>{agents.length > 1 && <Button variant="ghost" disabled={locked} onClick={() => updateDraft({ ...activeDraft, agents: agents.filter((_, i) => i !== index) })}>移除</Button>}</header><Field label={`助手 ${index + 1} 名称`}><Input required maxLength={80} disabled={locked} value={agent.name} onChange={event => changeAgent(index, { name: event.target.value })} /></Field><Field label={`助手 ${index + 1} 分工`}><textarea required rows={3} maxLength={1000} disabled={locked} value={agent.task} onChange={event => changeAgent(index, { task: event.target.value })} /></Field></div>)}</div>
          {agents.length > 1 && <div className="alcor-hub-summary-preview"><span className="alcor-hub-avatar">汇</span><div><strong>汇总助手</strong><p>等待全部分工完成，核对并整理为一份完整答复。</p></div></div>}
          <Notice tone="warning">{agents.length > 1 ? `启动后会并行运行 ${agents.length} 个助手，再调用汇总助手。` : '启动后会运行一个协作助手。'}任务内容将发送给当前 DSH 会话的模型，可能产生费用。</Notice>
        </> : <>
          <div className="alcor-hub-task-meta"><span>{showTime(job.createdAt)}</span>{steps.length > 0 && <span>{completed} / {steps.length} 个步骤已完成</span>}</div>
          <details className="alcor-hub-goal"><summary>任务目标</summary><p>{job.prompt}</p></details>
          <SegmentedControl id="hub-detail" label="任务详情视图" value={tab} onChange={setTab} options={[{ value: 'flow', label: '概览' }, { value: 'chat', label: '交流' }, { value: 'process', label: '过程' }, { value: 'agents', label: '分工' }]} />
          <div id={`hub-detail-${tab}-panel`} role="tabpanel" aria-labelledby={`hub-detail-${tab}`} className="alcor-hub-detail-content">
            {tab === 'chat' ? <HubConversation key={job.id} job={job} api={api} openSession={openSession} currentSession={currentSession} refreshJob={refresh} /> : tab === 'flow' ? <>
              {steps.length > 0 && <><div className="alcor-hub-section-heading"><h3>执行流程</h3><small className="alcor-muted">{steps.some(step => step.stepId.startsWith('chat-')) ? '分工与后续任务' : steps.length > 1 ? '并行分工 → 汇总' : '单助手执行'}</small></div><div className="alcor-hub-steps">{steps.map((step, index) => stepCard(step, index))}</div></>}
              <article className="alcor-hub-message alcor-hub-result"><header><h3>{steps.some(step => step.stepId === 'summary') ? '汇总结果' : '任务结果'}</h3>{job.output && <Button variant="outline" onClick={copyOutput}>{copied ? '已复制' : '复制结果'}</Button>}</header>{job.output ? renderOutput(job.output) : <Notice>{jobRunning(job) ? '任务正在执行，完成后会在这里显示最终答复。' : steps.some(canRetryStep) ? '任务尚未完成，请核对执行过程并重试需要处理的步骤。' : '本次任务没有已保存的答复。'}</Notice>}{!steps.length && job.diagnostic && <Notice tone="warning">{job.diagnostic}</Notice>}</article>
            </> : tab === 'process' ? <>{steps.length ? <div className="alcor-hub-process">{steps.map((step, index) => stepCard(step, index, true))}</div> : <><Notice>此历史任务没有逐步骤记录。</Notice>{job.diagnostic && <Notice tone="warning">{job.diagnostic}</Notice>}{job.childId && <Button variant="outline" onClick={() => openSession(job.childId)}>查看原生会话</Button>}</>}</> : <div className="alcor-hub-people">{people.length ? people.map(person => <article className="alcor-hub-step" key={person.id}><h3>{person.name}</h3><p>{steps.filter(step => step.participantId === person.id).map(step => step.title).join('\n')}</p><small className="alcor-muted">DSH 开发助手 · 继承发起会话的模型</small></article>) : <Notice>此历史任务由 开发助手执行。</Notice>}</div>}
          </div>
        </>}
      </div>
      {(activeDraft || !task) && <footer className="alcor-hub-composer-bottom"><small className="alcor-muted">{activeDraft ? `⌘ / Ctrl + Enter 执行 · ${agents.length > 1 ? `${agents.length} 个并行助手 + 汇总` : '单助手'}` : '配置分工后再启动'}</small><Button form="hub-draft-form" type="submit" variant="primary" disabled={!ready || !activeDraft?.title.trim() || locked}>发送并执行</Button></footer>}
    </section>
    <Modal open={dialog !== null} title={dialog === 'new' ? '新建协作任务' : dialog === 'connections' ? '协作设置' : dialog === 'discard' ? '放弃草稿？' : '取消执行？'} closeLabel="关闭" onClose={() => { if (!locked) setDialog(null) }} className="agent-hub-dialog">
      {dialog === 'new' && <form onSubmit={event => { event.preventDefault(); const value = { id: crypto.randomUUID(), title: title.trim(), prompt: goal, ...(mode === 'parallel' ? { agents: defaultHubAgents() } : {}) }; updateDraft(value); choose(value.id); setDialog(null) }}><Field label="任务标题"><Input data-modal-autofocus required maxLength={160} value={title} onChange={event => setTitle(event.target.value)} /></Field><Field label="目标"><textarea required rows={4} maxLength={12000} value={goal} onChange={event => setGoal(event.target.value)} /></Field><SegmentedControl id="hub-mode" label="协作模式" value={mode} onChange={setMode} options={[{ value: 'parallel', label: '并行协作' }, { value: 'single', label: '单助手' }]} /><div className="alcor-actions"><Button type="submit" variant="primary" disabled={!title.trim() || !goal.trim()}>创建任务</Button><Button variant="outline" onClick={() => setDialog(null)}>取消</Button></div></form>}
      {dialog === 'connections' && <><div className="alcor-hub-step"><h3>DSH 开发助手</h3><Notice>支持多个助手按分工并行执行，使用发起会话的模型。每个步骤保留独立的原生会话。</Notice><Button variant="outline" onClick={async () => { setDialog(null); setActionError(''); try { await startSession() } catch (failure) { setActionError(failure.message || '无法开始开发对话，请先选择工作区。') } }}>开始开发对话</Button></div><Notice>外部 Codex、Claude Code 和 Cursor 尚未接入此工作台。</Notice></>}
      {dialog === 'discard' && <><Notice tone="warning">放弃后将清除当前窗口中尚未执行的任务草稿。</Notice><div className="alcor-actions"><Button variant="outline" onClick={() => setDialog(null)}>继续编辑</Button><Button variant="primary" onClick={() => { updateDraft(null); select(null); setDialog(null) }}>放弃草稿</Button></div></>}
      {dialog === 'cancel' && <><Notice tone="warning">将请求停止正在执行的助手；已完成的结果会保留。已经发生的外部操作无法撤回。</Notice><div className="alcor-actions"><Button variant="outline" onClick={() => setDialog(null)}>继续执行</Button><Button variant="primary" disabled={locked} onClick={async () => { await cancel(job.id); setDialog(null) }}>确认取消</Button></div></>}
    </Modal>
  </div>
}
