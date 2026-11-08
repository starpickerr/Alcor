export const hubDraftKey = 'alcor.agent-hub.draft.v1'
const oldHubDraftKey = 'stella.agent-hub.draft.v1'
const oldHubMessageKey = id => `stella.agent-hub.message.v1:${id}`

// Keep the old value as a rollback source; a new value always takes precedence.
function readDraftStorage(storage, current, old) {
  const value = storage.getItem(current)
  if (value !== null) return { value, old: false }
  return { value: storage.getItem(old), old: true }
}
function copyDraftStorage(storage, key, value, old) {
  if (old && value !== null) try { if (storage.getItem(key) === null) storage.setItem(key, value) } catch { /* The validated draft remains readable. */ }
}
export function clearHubDraft(storage) {
  // Clear the fallback first so a failed write cannot resurrect a discarded draft.
  storage.removeItem(oldHubDraftKey)
  storage.removeItem(hubDraftKey)
}
export const jobLabels = { starting: '正在启动', running: '处理中', completed: '已完成', cancelled: '已取消', failed: '失败', interrupted: '已中断' }
export const jobRunning = job => ['starting', 'running'].includes(job?.status)
export const stepLabels = { pending: '等待执行', queued: '正在启动', running: '执行中', waiting_approval: '等待授权', cancel_requested: '正在取消', interrupted: '等待恢复', succeeded: '已完成', failed: '失败', cancelled: '已取消', outcome_unknown: '结果待核对' }
export const defaultHubAgents = () => [{ name: '研究助手', task: '梳理任务背景、相关依据和可行选项。' }, { name: '评估助手', task: '独立评估可行方案、风险与执行建议。' }]
export const canRetryStep = step => ['failed', 'cancelled', 'outcome_unknown'].includes(step.status) && step.attempts.length < 3

export function hubMentions(body, people, selected = []) {
  const ids = new Set(selected)
  const unknown = [], ambiguous = []
  for (const match of body.matchAll(/(^|\s)@([^\s@]+)/gu)) {
    const rest = body.slice(match.index + match[1].length + 1)
    const matches = people.filter(person => rest.startsWith(person.name) && (!rest[person.name.length] || /[\s，,：:。.!！?？]/u.test(rest[person.name.length])))
    const longest = Math.max(0, ...matches.map(person => person.name.length))
    const choices = matches.filter(person => person.name.length === longest)
    if (!choices.length) unknown.push(match[2])
    else if (choices.length === 1) ids.add(choices[0].id)
    else {
      const picked = choices.filter(person => selected.includes(person.id))
      if (picked.length === 1) ids.add(picked[0].id)
      else ambiguous.push(choices[0].name)
    }
  }
  return { ids: [...ids], unknown, ambiguous }
}

export const hubMessageKey = id => `alcor.agent-hub.message.v1:${id}`
export function readHubMessage(storage, id) {
  try {
    const { value, old } = readDraftStorage(storage, hubMessageKey(id), oldHubMessageKey(id))
    const draft = JSON.parse(value)
    if (!draft || typeof draft.body !== 'string' || draft.body.length > 16000 || !['message', 'task', 'persona'].includes(draft.intent) || !Array.isArray(draft.recipientIds) || draft.recipientIds.length > 3 || draft.recipientIds.some(id => typeof id !== 'string' || id.length > 100) || typeof draft.requestId !== 'string' || !/^[a-f0-9-]{36}$/i.test(draft.requestId)) return null
    copyDraftStorage(storage, hubMessageKey(id), value, old)
    return { body: draft.body, intent: draft.intent, recipientIds: [...new Set(draft.recipientIds)], requestId: draft.requestId }
  } catch { return null }
}

export function filterHubJobs(jobs, query, filter) {
  const needle = query.trim().toLocaleLowerCase()
  return jobs.filter(job => (!needle || `${job.title}\n${job.prompt}`.toLocaleLowerCase().includes(needle)) &&
    (filter === 'all' || (filter === 'running' ? jobRunning(job) : filter === 'attention' ? ['failed', 'interrupted', 'cancelled'].includes(job.status) : job.status === filter)))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}

export function readHubDraft(storage) {
  try {
    const { value, old } = readDraftStorage(storage, hubDraftKey, oldHubDraftKey)
    const draft = JSON.parse(value)
    if (!(typeof draft?.id === 'string' && typeof draft.title === 'string' && draft.title.length <= 160
      && typeof draft.prompt === 'string' && draft.prompt.length <= 12000)) return null
    const agents = draft.agents
    if (agents !== undefined && (!Array.isArray(agents) || agents.length < 1 || agents.length > 3 || agents.some(agent => typeof agent?.name !== 'string' || agent.name.length > 80 || typeof agent.task !== 'string' || agent.task.length > 1000))) return null
    copyDraftStorage(storage, hubDraftKey, value, old)
    return { id: draft.id, title: draft.title, prompt: draft.prompt, ...(agents ? { agents: agents.map(({ name, task }) => ({ name, task })) } : {}) }
  } catch { return null }
}
