export const hubDraftKey = 'stella.agent-hub.draft.v1'
export const jobLabels = { starting: '正在启动', running: '处理中', completed: '已完成', cancelled: '已取消', failed: '失败', interrupted: '已中断' }
export const jobRunning = job => ['starting', 'running'].includes(job?.status)

export function readHubDraft(storage) {
  try {
    const draft = JSON.parse(storage.getItem(hubDraftKey))
    return typeof draft?.id === 'string' && typeof draft.title === 'string' && draft.title.trim() && draft.title.length <= 160
      && typeof draft.prompt === 'string' && draft.prompt.length <= 12000 ? { id: draft.id, title: draft.title, prompt: draft.prompt } : null
  } catch { return null }
}
