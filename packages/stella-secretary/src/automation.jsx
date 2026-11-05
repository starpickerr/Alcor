import React, { useMemo } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'

export const automationTemplates = [
  { id: 'daily-briefing', label: '每日简报', title: '每日简报', frequency: 'daily', prompt: '整理本机今日事务、日程和待处理账单，生成并保存今日简报，然后给我一段简短摘要。仅使用本机业务记录。' },
  { id: 'weekly-review', label: '每周事务回顾', title: '每周事务回顾', frequency: 'weekly', prompt: '回顾本机未完成事务，整理待办、期限和下一步建议，并给出已完成事务总数。仅使用本机业务记录，保留事务原状态。' },
]

export function AutomationPage({ scope, api, schedule, renderFactorySlot, available, openNative }) {
  const remote = useMemo(() => Object.fromEntries(['catalog', 'create', 'edit', 'run', 'history', 'deleteInactive'].map(action => [action, request => api.automation({ scope, action, ...(request === undefined ? {} : { request }) })]).concat([
    ['deliveryLimits', () => schedule.deliveryLimits()],
  ])), [api, schedule, scope])
  if (!available) return <section className="stella-business"><header className="stella-app-header"><h1>自动化</h1></header><p>请在 DSH 中管理自动化。</p><Button variant="outline" onClick={openNative}>打开 DSH 自动化</Button></section>
  return renderFactorySlot('schedule.manager.view', { remote, ...(scope === 'stella' ? { scopeLabel: 'Stella', manualCreation: true, templates: automationTemplates } : {}) })
}
