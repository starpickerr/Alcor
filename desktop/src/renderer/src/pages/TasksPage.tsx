import React, { useCallback, useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Clock, CalendarClock, Play, Plus } from 'lucide-react'
import { getLang, t } from '../i18n'
import apiClient from '../api/client'
import { useSessionStore } from '../store/sessionStore'
import type { SchedulerTask, TaskSchedule, TaskAction, NotificationRoute } from '../types'
import { Modal, Btn, Toggle, TextInput, Dropdown } from './settings/primitives'
import PageHeader from '../components/PageHeader'
import RunPolicyEditor, { PolicyFields } from '../components/RunPolicyEditor'
import type { RunPolicy } from '../api/client'
import { PROACTIVE_CHANNELS, notificationChannelError } from './taskChannel'

interface TasksPageProps {
  baseUrl: string
}

type TaskDestination = { channel_type: string; configured: boolean; ready: boolean; status: string; recipients: { receiver: string; name: string }[] }

const permissionLabel = (mode: SchedulerTask['run_permission_mode'] | 'inherit' | undefined) => {
  if (mode === 'read-only') return 'task_permission_read_only' as const
  if (mode === 'workspace-write') return 'task_permission_workspace_write' as const
  if (mode === 'full-access') return 'task_permission_full_access' as const
  return 'task_permission_unconfirmed' as const
}

const scheduleSummary = (s: TaskSchedule): string => {
  if (s.type === 'cron') return s.expression || 'cron'
  if (s.type === 'interval') {
    const sec = s.seconds || 0
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const r = sec % 60
    const parts: string[] = []
    if (h) parts.push(`${h}h`)
    if (m) parts.push(`${m}m`)
    if (r || parts.length === 0) parts.push(`${r}s`)
    return parts.join(' ')
  }
  return s.type || 'once'
}

const formatNextRun = (iso?: string): string => {
  if (!iso) return '--'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '--' : d.toLocaleString()
}

const taskContent = (action: TaskAction): string => {
  if (action.type === 'skill_call') return action.call_name || action.skill_name || 'skill_call'
  if (action.type === 'application_command') return `${action.domain}.${action.command}`
  if (action.type === 'tool_call') {
    const name = action.call_name || action.tool_name || ''
    return name === 'briefing' ? t('task_briefing_description') : name
  }
  return action.content || action.task_description || ''
}

const TasksPage: React.FC<TasksPageProps> = ({ baseUrl }) => {
  const navigate = useNavigate()
  const activeSessionId = useSessionStore((s) => s.activeId)
  const [tasks, setTasks] = useState<SchedulerTask[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const loadGeneration = useRef(0)
  const [editing, setEditing] = useState<SchedulerTask | 'new' | null>(null)

  const loadTasks = async () => {
    const generation = ++loadGeneration.current
    setLoadError('')
    try {
      setLoading(true)
      const data = await apiClient.getSchedulerTasks()
      if (generation === loadGeneration.current) setTasks(data || [])
    } catch (err) {
      console.error('Failed to load tasks:', err)
      if (generation === loadGeneration.current) setLoadError(err instanceof Error ? err.message : String(err))
    } finally {
      if (generation === loadGeneration.current) setLoading(false)
    }
  }

  useEffect(() => {
    apiClient.setBaseUrl(baseUrl)
    void loadTasks()
    return () => { loadGeneration.current++ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl])

  const toggle = async (task: SchedulerTask, enabled: boolean) => {
    setTasks((prev) => prev.map((x) => (x.id === task.id ? { ...x, enabled } : x)))
    try {
      await apiClient.toggleTask(task.id, enabled)
    } catch {
      setTasks((prev) => prev.map((x) => (x.id === task.id ? { ...x, enabled: !enabled } : x)))
    }
  }

  const openLinkedTodo = async (task: SchedulerTask) => {
    try {
      const todos = await apiClient.getTodos()
      if (todos.some((todo) => todo.id === task.linked_todo_id)) {
        navigate('/todos', { state: { todoId: task.linked_todo_id } })
        return
      }
    } catch {
      // The scheduled task remains inspectable when the todo cannot be read.
    }
    setEditing(task)
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-content">{t('tasks_title')}</h2>
          <p className="text-xs text-content-tertiary mt-1">{t('tasks_desc')}</p>
        </div>
        <Btn variant="primary" onClick={() => setEditing('new')}>
          <span className="flex items-center gap-1.5">
            <Plus size={15} />
            {t('tasks_add')}
          </span>
        </Btn>
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-5">
          {loading ? (
            <div className="flex items-center justify-center py-20 text-content-tertiary">
              <Loader2 size={18} className="animate-spin mr-2" />
              {t('tasks_loading')}
            </div>
          ) : loadError ? (
            <div className="py-10 text-center text-sm text-danger"><p>{loadError}</p><Btn variant="ghost" onClick={() => void loadTasks()}>{t('tasks_retry')}</Btn></div>
          ) : tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <CalendarClock size={32} className="mb-3 text-content-tertiary opacity-60" />
              <p className="text-content font-medium mb-1">{t('tasks_empty')}</p>
              <p className="text-sm text-content-tertiary max-w-sm">{t('tasks_empty_guide')}</p>
            </div>
          ) : (
            <div className="grid gap-3">
              {tasks.map((task) => {
                const content = task.linked_todo_id ? '' : taskContent(task.action)
                return (
                  <div
                    key={task.id}
                    role="button"
                    tabIndex={0}
                    aria-label={task.name || task.id}
                    onClick={() => setEditing(task)}
                    onKeyDown={(event) => {
                      if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
                        event.preventDefault()
                        setEditing(task)
                      }
                    }}
                    className={`rounded-card border border-default bg-surface p-4 cursor-pointer hover:border-strong transition-colors ${
                      task.enabled ? '' : 'opacity-60'
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${task.enabled ? 'bg-accent' : 'bg-content-tertiary'}`} />
                      <span className="font-medium text-sm text-content truncate">{task.name || task.id}</span>
                      <div className="flex-1" />
                      <span className="text-xs font-mono text-content-tertiary">{scheduleSummary(task.schedule)}</span>
                      <span className="text-xs text-content-tertiary">{t(permissionLabel(task.run_permission_mode))}</span>
                    </div>
                    {content && <p className="text-xs text-content-secondary mb-2 line-clamp-2">{content}</p>}
                    {task.linked_todo_id && <button type="button" className="text-xs text-accent mb-2 hover:underline"
                      onClick={(event) => { event.stopPropagation(); void openLinkedTodo(task) }}>
                      {t('task_linked_todo')}
                    </button>}
                    {task.last_run_status && <p className="text-xs text-content-tertiary mb-2">{t(('task_status_'+task.last_run_status) as Parameters<typeof t>[0])}{task.last_error ? ` · ${task.last_error}` : ''}</p>}
                    <div className="flex items-center gap-2 text-xs text-content-tertiary">
                      <Clock size={12} />
                      <span>
                        {t('tasks_next_run')}: {formatNextRun(task.next_run_at)}
                      </span>
                      <div className="flex-1" />
                      {!task.linked_todo_id && (
                        <span onClick={(event) => event.stopPropagation()}>
                          <Toggle checked={task.enabled} onChange={(v) => toggle(task, v)} />
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {editing && (
        <TaskEditModal
          key={editing === 'new' ? 'new' : editing.id}
          task={editing === 'new' ? null : editing}
          sessionId={activeSessionId}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            void loadTasks()
          }}
          onDeleted={() => {
            setEditing(null)
            void loadTasks()
          }}
        />
      )}
    </div>
  )
}

const TaskEditModal: React.FC<{
  task: SchedulerTask | null
  sessionId: string
  onClose: () => void
  onSaved: () => void
  onDeleted: () => void
}> = ({ task, sessionId, onClose, onSaved, onDeleted }) => {
  const projects = useSessionStore((s) => s.projects)
  const sessions = useSessionStore((s) => s.sessions)
  const loadSessions = useSessionStore((s) => s.loadSessions)
  const isCreate = task == null
  const isToolCall = !!task && ['tool_call', 'skill_call', 'application_command'].includes(task.action.type)
  const [notifyEnabled, setNotifyEnabled] = useState(!!task?.notification_route || !!task && task.action.conversation_mode !== 'independent')
  const hasNotification = notifyEnabled && !task?.action.silent && task?.action.type !== 'application_command'
  const [name, setName] = useState(task?.name || '')
  const [enabled, setEnabled] = useState(task?.enabled ?? true)
  const [newPolicy, setNewPolicy] = useState<RunPolicy>({ filesystem_mode: 'read-only', policy_revision: 0 })
  const [schedType, setSchedType] = useState<TaskSchedule['type']>(task?.schedule.type || 'cron')
  const [cron, setCron] = useState(task?.schedule.expression || '0 9 * * *')
  const [interval, setIntervalVal] = useState(task?.schedule.seconds ? String(task.schedule.seconds) : '')
  const [runAt, setRunAt] = useState(task?.schedule.run_at ? task.schedule.run_at.slice(0, 16) : '')
  const [actionType, setActionType] = useState<TaskAction['type']>(task?.action.type || 'send_message')
  const [content, setContent] = useState(task?.action.content || task?.action.task_description || '')
  const [channel, setChannel] = useState(task?.action.channel_type || 'web')
  const [receiver, setReceiver] = useState(task?.action.receiver || sessionId)
  const [conversationMode, setConversationMode] = useState<'independent' | 'source' | 'reuse' | 'new'>(task?.action.conversation_mode || (task ? 'reuse' : 'independent'))
  const [sourceSessionId, setSourceSessionId] = useState(task?.action.source_session_id || sessionId)
  const [conversationSpace, setConversationSpace] = useState<'personal' | 'project'>(
    task?.action.conversation_space || 'personal')
  const [projectDir, setProjectDir] = useState(task?.action.execution_project_dir || '')
  const [receiveIdType, setReceiveIdType] = useState(task?.action.receive_id_type || (task?.action.is_group ? 'chat_id' : 'open_id'))
  const [testStatus, setTestStatus] = useState('')
  const [testing, setTesting] = useState(false)
  const [destinations, setDestinations] = useState<TaskDestination[]>([])
  const refreshDestinations = useCallback(() => {
    void apiClient.getTaskDestinations().then(setDestinations).catch(() => {})
  }, [])
  useEffect(() => { refreshDestinations() }, [refreshDestinations])
  useEffect(() => {
    // Binding a chat uses the session list, not proactive delivery destinations.
    void loadSessions().catch(() => {})
  }, [loadSessions])
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [runStatus, setRunStatus] = useState('')
  const [runs, setRuns] = useState<Awaited<ReturnType<typeof apiClient.getTaskRuns>>>([])
  const [runsError, setRunsError] = useState('')
  const [runsVersion, setRunsVersion] = useState(0)
  useEffect(() => {
    if (!task) return
    let active = true
    void apiClient.getTaskRuns(task.id).then(rows => { if (active) { setRuns(rows); setRunsError('') } })
      .catch(cause => { if (active) setRunsError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { active = false }
  }, [task, runsVersion])

  const [error, setError] = useState('')
  const linkedTodoId = task?.linked_todo_id || ''
  const linked = linkedTodoId !== ''
  const navigate = useNavigate()
  const [todoAvailable, setTodoAvailable] = useState<boolean | null>(null)
  useEffect(() => {
    if (!linkedTodoId) return
    let active = true
    apiClient.getTodos().then((todos) => {
      if (active) setTodoAvailable(todos.some((todo) => todo.id === linkedTodoId))
    }).catch(() => { if (active) setTodoAvailable(false) })
    return () => { active = false }
  }, [linkedTodoId])

  const buildSchedule = (): TaskSchedule => {
    if (schedType === 'cron') return { type: 'cron', expression: cron.trim() }
    if (schedType === 'interval') return { type: 'interval', seconds: Number(interval) || 0 }
    return { type: 'once', run_at: runAt }
  }

  const buildAction = (): TaskAction => {
    const a: TaskAction = { ...(task?.action || {}), type: actionType }
    if (channel !== task?.action.channel_type) {
      delete a.notify_session_id
      delete a.receive_id_type
    }
    a.channel_type = channel
    a.receiver = receiver
    a.receiver_name = receiver
    a.is_group = channel === 'feishu' && receiveIdType === 'chat_id'
    if (channel === 'feishu') a.receive_id_type = receiveIdType
    a.execution_session_id = task?.action.execution_session_id || task?.action.notify_session_id || sessionId
    a.conversation_mode = conversationMode
    if (isCreate || task?.action.conversation_mode !== conversationMode || (conversationMode === 'source' && task?.action.source_session_id !== sourceSessionId)) a.workspace_version = 1
    if (conversationMode === 'independent') {
      delete a.source_session_id
      delete a.execution_session_id
      delete a.notify_session_id
      delete a.execution_project_dir
      delete a.conversation_space
      if (!hasNotification) { a.channel_type = 'web'; delete a.receiver }
    } else if (conversationMode === 'source') {
      a.source_session_id = sourceSessionId
      if (sourceSessionId !== task?.action.source_session_id) {
        delete a.conversation_space
        delete a.execution_project_dir
      }
    } else {
      delete a.source_session_id
      a.conversation_space = conversationSpace
      a.execution_project_dir = conversationSpace === 'project' ? projectDir : ''
    }
    if (isToolCall) return a
    if (actionType === 'send_message') a.content = content
    else a.task_description = content
    return a
  }

  const buildRoute = (): NotificationRoute | null => {
    if (!PROACTIVE_CHANNELS.has(channel)) return null
    return {
      primary: {
        channel_type: channel as 'desktop' | 'telegram' | 'wecom_bot',
        receiver: channel === 'desktop' ? 'desktop' : receiver.trim(),
        is_group: false,
      },
    }
  }

  const testSend = async () => {
    const route = buildRoute()
    if (!route) {
      setTestStatus(t('task_channel_reselect'))
      return
    }
    setTesting(true)
    setTestStatus('')
    try {
      const result = await apiClient.testNotificationRoute(route)
      setTestStatus(result.status === 'success'
        ? `${getLang() === 'en' ? 'Test send' : '测试发送'}: ${result.delivery_status === 'accepted' ? (getLang() === 'en' ? 'platform accepted' : '平台已接受') : result.delivery_status || result.error || ''}`
        : result.message || '')
    } catch (cause) {
      setTestStatus(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setTesting(false)
    }
  }

  const save = async () => {
    if (!name.trim()) {
      setError(t('task_save_error'))
      return
    }
    if (!isToolCall && !content.trim()) {
      setError(t('task_save_error'))
      return
    }
    if (notificationChannelError({ isCreate, hasNotification, channel })) {
      setError(t('task_channel_reselect'))
      return
    }
    if (conversationMode === 'source' && !sourceSessionId) {
      setError(t('task_conversation_source_required'))
      return
    }
    if ((conversationMode === 'reuse' || conversationMode === 'new') && conversationSpace === 'project' && !projectDir) {
      setError(t('task_conversation_project_required'))
      return
    }
    setSaving(true)
    setError('')
    const route = hasNotification ? buildRoute() : null
    try {
      if (isCreate) {
        const result = await apiClient.createTask({
          name: name.trim(),
          enabled,
          schedule: buildSchedule(),
          action: buildAction(),
          ...(conversationMode === 'independent' ? {} : { session_id: sourceSessionId || sessionId }),
          run_policy: { filesystem_mode: newPolicy.filesystem_mode, permission_mode: newPolicy.permission_mode || newPolicy.filesystem_mode },
          ...(route ? { notification_route: route } : {}),
        })
        if (result.status !== 'success') throw new Error(result.message || t('task_save_error'))
      } else if (task) {
        const result = await apiClient.updateTask(task.id, {
          name: name.trim(),
          enabled,
          schedule: buildSchedule(),
          action: buildAction(),
          ...(route ? { notification_route: route } : {}),
        })
        if (result.status !== 'success') throw new Error(result.message || t('task_save_error'))
      }
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('task_save_error'))
    } finally {
      setSaving(false)
    }
  }

  const del = async () => {
    if (!task || !window.confirm(t('task_delete_confirm'))) return
    setSaving(true)
    try {
      await apiClient.deleteTask(task.id)
      onDeleted()
    } catch {
      setSaving(false)
    }
  }

  const runNow = async () => {
    if (!task || !window.confirm(t('task_run_confirm'))) return
    setRunning(true)
    setRunStatus('')
    setError('')
    try {
      const result = await apiClient.runTask(task.id)
      if (result.status !== 'success') throw new Error(result.message || t('task_run_error'))
      setRunStatus(t('task_run_started'))
    } catch (e) {
      setError(e instanceof Error ? e.message : t('task_run_error'))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Modal
      open
      title={isCreate ? t('task_add_title') : t('task_edit_title')}
      onClose={onClose}
      footer={
        <>
          {linked && todoAvailable && (
            <Btn variant="ghost" onClick={() => { navigate('/todos', { state: { todoId: linkedTodoId } }); onClose() }} className="mr-auto">
              {t('task_linked_todo')}
            </Btn>
          )}
          {!isCreate && !linked && (
            <Btn variant="danger" onClick={del} disabled={saving} className="mr-auto">
              {t('task_delete')}
            </Btn>
          )}
          {!isCreate && !linked && (
            <Btn variant="ghost" onClick={runNow} disabled={saving || running}>
              {running ? <Loader2 size={14} className="inline animate-spin mr-1" /> : <Play size={14} className="inline mr-1" />}
              {t('task_run_now')}
            </Btn>
          )}
          <Btn variant="ghost" onClick={onClose} disabled={saving}>
            {t('task_cancel')}
          </Btn>
          {!linked && (
            <Btn variant="primary" onClick={save} disabled={saving}>
              {t('task_save')}
            </Btn>
          )}
        </>
      }
    >
      {linked && <p className="text-xs text-content-secondary">{t('task_linked_readonly')}</p>}
      {linked && todoAvailable === false && <p className="text-xs text-danger">{t('task_linked_todo_unavailable')}</p>}
      <fieldset disabled={linked} className="min-w-0 space-y-4 border-0 p-0">
      <Field label={t('task_name')}>
        <TextInput value={name} onChange={(e) => setName(e.target.value)} />
      </Field>

      <div className="flex items-center justify-between">
        <span className="text-sm text-content-secondary">{t('task_enabled')}</span>
        <Toggle checked={enabled} onChange={setEnabled} />
      </div>

      {isCreate && <div className="rounded-card border border-default p-4"><p className="mb-4 text-xs text-content-secondary">{getLang() === 'en' ? 'Permissions apply only to this task. Resource access is checked separately. Background operations requiring confirmation stop with a recorded reason.' : '权限仅作用于当前任务，资源访问授权仍需分别检查。后台遇到需要确认的操作会停止该操作并记录原因。'}</p>
        <PolicyFields value={newPolicy} onChange={setNewPolicy} disabled={saving} />
      </div>}

      <Field label={t('task_schedule_type')}>
        <Dropdown
          value={schedType}
          onChange={(v) => setSchedType(v as TaskSchedule['type'])}
          options={[
            { value: 'cron', label: t('task_type_cron') },
            { value: 'interval', label: t('task_type_interval') },
            { value: 'once', label: t('task_type_once') },
          ]}
        />
      </Field>

      {schedType === 'cron' && (
        <div>
          <Field label={t('task_cron_expr')}>
            <TextInput value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 9 * * *" className="font-mono" />
          </Field>
          <CronHelp />
        </div>
      )}
      {schedType === 'interval' && (
        <Field label={t('task_interval_seconds')}>
          <TextInput type="number" value={interval} onChange={(e) => setIntervalVal(e.target.value)} />
        </Field>
      )}
      {schedType === 'once' && (
        <Field label={t('task_once_time')}>
          <TextInput type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} />
        </Field>
      )}

      <Field label={t('task_action_type')}>
        {isToolCall ? <TextInput value={t('task_action_tool')} disabled /> : <Dropdown
          value={actionType}
          onChange={(v) => setActionType(v as TaskAction['type'])}
          options={[
            { value: 'send_message', label: t('task_action_send') },
            { value: 'agent_task', label: t('task_action_agent') },
          ]}
        />}
      </Field>

      <Field label={actionType === 'send_message' ? t('task_message_content') : t('task_task_description')}>
        {isToolCall && task ? <p className="text-sm text-content-secondary">{taskContent(task.action)}</p> : <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={3}
          className="w-full px-3 py-2 rounded-btn border border-strong bg-inset text-sm text-content placeholder:text-content-tertiary focus:outline-none focus:border-accent transition-colors resize-none"
        />}
      </Field>

      {isCreate && <label className="flex items-center gap-2 text-sm text-content-secondary"><input type="checkbox" checked={notifyEnabled} onChange={event => setNotifyEnabled(event.target.checked)} />{getLang() === 'en' ? 'Send an additional result notification' : '额外发送结果通知'}</label>}
      {hasNotification && <>
        <Field label={t('task_channel')}>
          <Dropdown value={channel} onChange={value => { setChannel(value); setReceiver(value === 'web' ? sessionId : ''); setReceiveIdType('open_id') }}
            options={[
              ...(destinations.some(d => d.channel_type === channel && d.ready && PROACTIVE_CHANNELS.has(d.channel_type)) ? [] : [{ value: channel, label: t('task_channel_reselect') }]),
              ...destinations.filter(d => d.ready && PROACTIVE_CHANNELS.has(d.channel_type)).map(d => ({
                value: d.channel_type, label: t('task_channel_' + d.channel_type),
              })),
            ]} />
          <button type="button" className="mt-1 text-xs text-accent hover:underline" onClick={refreshDestinations}>{t('task_destination_refresh')}</button>
          {!destinations.some(d => d.ready && PROACTIVE_CHANNELS.has(d.channel_type)) && <p className="text-xs text-content-tertiary mt-1">{t('task_channel_connect')}</p>}
          {!PROACTIVE_CHANNELS.has(channel) && <p className="text-xs text-content-tertiary mt-1">{t('task_channel_reselect')}</p>}
        </Field>
      </>}
      <Field label={t('task_conversation_mode')}>
        <Dropdown value={conversationMode} onChange={(value) => {
          setConversationMode(value as 'independent' | 'source' | 'reuse' | 'new')
          if (isCreate) {
            setNewPolicy({ filesystem_mode: 'read-only', permission_mode: 'read-only', policy_revision: 0 })
          }
        }}
          options={[
            { value: 'independent', label: t('task_conversation_independent') },
            { value: 'source', label: t('task_conversation_source') },
            ...(task?.action.conversation_mode === 'reuse' || task?.action.conversation_mode === 'new' ? [
              { value: 'reuse', label: t('task_conversation_reuse') },
              { value: 'new', label: t('task_conversation_new') },
            ] : []),
          ]} />
      </Field>
      {conversationMode === 'independent' && <p className="text-xs text-content-tertiary">{t('task_conversation_independent_hint')}</p>}
      {conversationMode === 'source' && <>
        {task && !task.action.workspace_version && <p className="text-xs text-content-tertiary">{getLang() === 'en' ? 'This existing task keeps its original execution directory. Reassociate a conversation to use its workspace.' : '此旧任务保留原执行目录。重新选择关联对话后，将使用该对话的工作空间。'}</p>}
        <Field label={t('task_conversation_source_pick')}>
          <Dropdown value={sourceSessionId} onChange={setSourceSessionId}
            options={[{ value: '', label: t('task_conversation_source_pick') }, ...sessions.map((session) => ({
              value: session.session_id,
              label: session.title || session.session_id,
            }))]} />
        </Field>
        <p className="text-xs text-content-tertiary">{t(task?.action.silent ? 'task_conversation_silent_hint' : 'task_conversation_source_hint')}</p>
      </>}
      {(conversationMode === 'reuse' || conversationMode === 'new') && <>
        <Field label={t('task_conversation_space')}>
          <Dropdown value={conversationSpace} onChange={(value) => { setConversationSpace(value as 'personal' | 'project'); if (value === 'personal') setProjectDir('') }}
            options={[{ value: 'personal', label: t('task_conversation_personal') }, { value: 'project', label: t('task_conversation_project') }]} />
        </Field>
        {conversationSpace === 'project' && <Field label={t('task_conversation_project_pick')}>
          <Dropdown value={projectDir} onChange={setProjectDir}
            options={[{ value: '', label: t('task_conversation_project_pick') }, ...projects.map((project) => ({ value: project.path, label: project.name }))]} />
        </Field>}
        <p className="text-xs text-content-tertiary">{t(task?.action.silent ? 'task_conversation_silent_hint' : 'task_conversation_hint')}</p>
        {conversationSpace === 'project' && !projectDir && <p className="text-xs text-danger">{t('task_conversation_project_required')}</p>}
      </>}
      {hasNotification && <>
        {channel === 'feishu' && <Field label={t('task_receive_type')}>
          <Dropdown value={receiveIdType} onChange={setReceiveIdType} options={['open_id','user_id','union_id','email','chat_id'].map(value => ({value,label:t(('task_receive_'+value) as Parameters<typeof t>[0])}))} />
        </Field>}
        {(channel === 'telegram' || channel === 'wecom_bot') && <Field label={t('task_receiver')}>
          <TextInput value={receiver} onChange={event => setReceiver(event.target.value)} list="task-recipients" />
          <datalist id="task-recipients">{destinations.find(d => d.channel_type === channel)?.recipients.map(r => <option key={r.receiver} value={r.receiver}>{r.name}</option>)}</datalist>
        </Field>}
        <button type="button" className="text-xs text-accent hover:underline disabled:opacity-40" disabled={testing || !PROACTIVE_CHANNELS.has(channel) || ((channel === 'telegram' || channel === 'wecom_bot') && !receiver.trim())}
          onClick={() => void testSend()}>{testing ? (getLang() === 'en' ? 'Sending…' : '发送中…') : (getLang() === 'en' ? 'Send test' : '测试发送')}</button>
        {testStatus && <p role="status" className="text-xs text-content-secondary">{testStatus}</p>}
        {task?.notification_delivery_status && <p role="status" className="text-xs text-content-secondary">
          {getLang() === 'en' ? 'Latest delivery' : '最近投递'}: {task.notification_delivery_status.status}
          {task.notification_delivery_status.error ? ` · ${task.notification_delivery_status.error}` : ''}
        </p>}
      </>}

      {runStatus && <p className="text-xs text-success">{runStatus}</p>}
      {error && <p className="text-xs text-danger">{error}</p>}
      </fieldset>
      {!isCreate && task && <section className="mt-6 space-y-3 border-t border-default pt-5">
        <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">{getLang() === 'en' ? 'Run records' : '运行记录'}</h3>
          <button type="button" className="text-xs text-accent" onClick={() => setRunsVersion(value => value + 1)}>{getLang() === 'en' ? 'Refresh' : '刷新记录'}</button></div>
        {runsError && <p role="alert" className="text-xs text-danger">{runsError}</p>}
        {!runsError && runs.length === 0 && <p className="text-xs text-content-tertiary">{getLang() === 'en' ? 'No run records yet.' : '暂无运行记录。'}</p>}
        {runs.map(run => <details key={run.job_run_id} className="border-b border-default py-2">
          <summary className="cursor-pointer text-xs text-content-secondary">{formatNextRun(run.started_at)} · {t(('task_status_' + run.status) as Parameters<typeof t>[0])}</summary>
          {run.error && <p className="mt-2 whitespace-pre-wrap text-xs text-danger">{run.error}</p>}
          {run.output && <p className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words text-sm text-content">{run.output}</p>}
        </details>)}
      </section>}
      {!isCreate && task && <div className="mt-6 space-y-5 border-t border-default pt-5">
        <h3 className="text-sm font-semibold">{getLang() === 'en' ? 'Independent task permissions' : '任务独立权限'}</h3>
        {task.last_error && <p role="alert" className="text-xs text-danger">{task.last_error}</p>}
        <p className="text-xs text-content-secondary">{linked ? (getLang() === 'en' ? 'Permission changes apply to the next reminder. Manage reminder delivery in the linked task.' : '权限修改在下次提醒生效。投递操作请前往关联事务。') : (getLang() === 'en' ? 'Save permission changes here, then use Run now to retry a denied task.' : '在此保存权限修改，再使用“立即执行”重试被拒绝的任务。')}</p>
        <RunPolicyEditor kind="task" subject={task.id} agent={task.owner_agent_id} />
      </div>}
    </Modal>
  )
}

const CronHelp: React.FC = () => (
  <details className="mt-2 rounded-btn border border-default px-3 py-2 text-xs text-content-tertiary">
    <summary className="cursor-pointer select-none font-medium text-content-secondary">
      {t('task_cron_help_summary')}
    </summary>
    <div className="mt-2 space-y-2 leading-relaxed">
      <p className="font-mono whitespace-pre-wrap">{t('task_cron_help_5')}</p>
      <p className="font-mono whitespace-pre-wrap">{t('task_cron_help_6')}</p>
      <p>{t('task_cron_help_note')}</p>
      <p className="font-mono whitespace-pre-wrap">{t('task_cron_help_symbols')}</p>
      <p className="font-mono whitespace-pre-wrap">{t('task_cron_help_examples')}</p>
    </div>
  </details>
)

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div>
    <label className="block text-sm text-content-secondary mb-1.5">{label}</label>
    {children}
    {hint && <p className="text-xs text-content-tertiary mt-1">{hint}</p>}
  </div>
)

export default TasksPage
