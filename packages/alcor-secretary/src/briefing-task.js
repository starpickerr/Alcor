export const briefingPrompt = '使用 alcor_generate_briefing 生成并保存今日简报，使用简报设置中已启用的信息来源、筛选与写作规则和时区。保存完成后给出简短摘要；失败或中断时如实说明。'

async function unwrap(promise) {
  const result = await promise
  if (!result.ok) throw new Error(result.error?.message || '简报对话未能启动，请重试。')
  return result.value
}

export async function startBriefingConversation({ session, api, openSession, context, currentSessionId, date }) {
  const preferences = await unwrap(api.business({ domain: 'briefing', method: 'get_preferences', arguments: {} }))
  let sessionId = currentSessionId
  if (preferences.task_target === 'current') {
    if (!sessionId) throw new Error('请先打开一个对话，或将执行位置改为每次新建会话。')
  } else {
    const placement = preferences.task_workspace_id ? { workspaceId: preferences.task_workspace_id } : context
    const created = await unwrap(session.create({ ...placement, agentPreset: 'alcor' }))
    sessionId = created.sessionId
    await unwrap(session.rename({ sessionId, title: `生成/更新简报 · ${date}` }))
  }
  await unwrap(session.prompt({ sessionId, requestId: crypto.randomUUID(), mode: 'queue', content: [{ type: 'text', text: preferences.task_prompt || briefingPrompt }], clientTimeZone: Intl.DateTimeFormat().resolvedOptions().timeZone }))
  await openSession(sessionId)
  return sessionId
}
