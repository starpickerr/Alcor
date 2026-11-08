export function createCategoryRebuildPage({ api, kind, appleId }) {
  let generation = 0, disposed = false, pending = false, prepared = null
  const cancel = plan => { if (plan?.ticket) Promise.resolve(api.cancelCategoryRebuild({ ticket: plan.ticket })).catch(() => {}) }
  const unwrap = result => { if (!result?.ok) throw new Error(result?.error?.message || '分类创建未完成，请核对状态。'); return result.value }
  return {
    async preview(sourceId) {
      if (disposed || pending) throw new Error('页面已关闭或正在处理。')
      const current = ++generation
      pending = true; cancel(prepared); prepared = null
      try {
        const value = unwrap(await api.previewCategoryRebuild({ kind, apple_id: appleId, source_id: sourceId }))
        if (disposed || current !== generation) { cancel(value); throw new Error('预览已失效，未创建。') }
        if (value.kind !== kind || value.apple_id !== appleId || value.source_id !== sourceId || value.mode !== 'empty_container_only'
            || typeof value.ticket !== 'string' || !/^[a-f0-9]{64}$/.test(value.snapshot) || !Array.isArray(value.retained_members)) throw new Error('预览身份不完整。')
        prepared = Object.freeze({ ...structuredClone(value), source: Object.freeze({ ...value.source }),
          retained_members: Object.freeze(value.retained_members.map(row => Object.freeze({ ...row }))) })
        return prepared
      } finally { pending = false }
    },
    async confirm(plan) {
      if (disposed || pending || !prepared || plan !== prepared) throw new Error('请先重新预览并确认。')
      pending = true; prepared = null
      try {
        return unwrap(await api.rebuildCategory({ ticket: plan.ticket }))
      } finally { pending = false }
    },
    activate() { disposed = false; generation++; cancel(prepared); prepared = null },
    invalidate() { generation++; cancel(prepared); prepared = null },
    dispose() { disposed = true; generation++; cancel(prepared); prepared = null },
  }
}
