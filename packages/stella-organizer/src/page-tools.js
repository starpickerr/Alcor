import { automationRequest } from './automation.js'

// The model uses the same schedule owner as the page, scoped to Stella jobs.
export const automationMethods = [
  { name: 'list', description: '列出 Stella 自动化及其状态', example: {} },
  { name: 'create', description: '创建自动化；timing 使用 DSH 原生时间格式', example: { title: '每日回顾', prompt: '回顾未完成事务', timing: { kind: 'daily', daily: { time: '08:00', time_zone: 'Asia/Shanghai' } } } },
  { name: 'edit', description: '更新、暂停、恢复或删除；expectedUpdatedAt 使用 list 返回值', example: { id: 'existing-id', action: 'update', patch: { active: false }, expectedUpdatedAt: null } },
  { name: 'run', description: '立即运行一次已保存的自动化', example: { id: 'existing-id' } },
  { name: 'history', description: '读取执行记录', example: { id: 'existing-id', limit: 20 } },
  { name: 'deleteInactive', description: '清理已结束的 Stella 自动化', example: {} },
]

export async function callAutomation(organizer, method, args) {
  if (method === 'catalog') return automationMethods
  if (!automationMethods.some(row => row.name === method)) throw new Error('未知的自动化方法，请先查询 catalog。')
  const action = method === 'list' ? 'catalog' : method
  const request = automationRequest.parse({ scope: 'stella', action, ...(['catalog', 'deleteInactive'].includes(action) ? {} : { request: args }) })
  const result = await organizer.automation(request)
  if (result?.code) throw new Error('自动化操作未完成：' + (result.message || result.code))
  return result
}
