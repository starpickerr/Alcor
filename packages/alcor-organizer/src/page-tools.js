import { automationRequest, automationDeleteUnavailable } from './automation.js'

// The model uses the same schedule owner as the page, scoped to Alcor jobs.
export const automationMethods = [
  { name: 'list', description: '列出 Alcor 自动化及其状态', example: {} },
  { name: 'create', description: '创建自动化；timing 使用 DSH 原生时间格式', example: { title: '每日回顾', prompt: '回顾未完成事务', timing: { kind: 'daily', daily: { time: '08:00', time_zone: 'Asia/Shanghai' } } } },
  { name: 'edit', description: '更新、暂停或恢复；expectedUpdatedAt 使用 list 返回值。删除暂不可用。', example: { id: 'existing-id', action: 'update', patch: { active: false }, expectedUpdatedAt: null } },
  { name: 'run', description: '立即运行一次已保存的自动化', example: { id: 'existing-id' } },
  { name: 'history', description: '读取执行记录', example: { id: 'existing-id', limit: 20 } },
  { name: 'deleteInactive', description: '清理暂不可用；不会删除或自动暂停任务', example: {} },
]

export async function callAutomation(organizer, method, args) {
  if (method === 'catalog') return automationMethods
  if (!automationMethods.some(row => row.name === method)) throw new Error('未知的自动化方法，请先查询 catalog。')
  const action = method === 'list' ? 'catalog' : method
  const request = automationRequest.parse({ scope: 'alcor', action, ...(['catalog', 'deleteInactive'].includes(action) ? {} : { request: args }) })
  const result = await organizer.automation(request)
  if (result?.code) throw new Error('自动化操作未完成：' + (result.message || result.code))
  return result
}

// Retain the page API shape, but do not offer an approval dialog that cannot authorize deletion.
export function createAutomationPageRemote({ api, scope, schedule }) {
  const send = (action, request) => api.automation({ scope, action, ...(request === undefined ? {} : { request }) })
  const remove = async () => { throw new Error(automationDeleteUnavailable) }
  return {
    remote: { catalog: () => send('catalog'), create: request => send('create', request),
      edit: request => request.action === 'delete' ? remove() : send('edit', request),
      run: request => send('run', request), history: request => send('history', request),
      deleteInactive: remove, deliveryLimits: () => schedule.deliveryLimits() },
    activate() {},
    dispose() {},
  }
}
