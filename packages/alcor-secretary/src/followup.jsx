import React, { useEffect, useState } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'

export function FollowupNote({ api }) {
  const [note,setNote] = useState(null), [error,setError] = useState('')
  useEffect(() => {
    let generation = 0
    async function refresh() {
      const request = ++generation
      if (document.visibilityState !== 'visible') { setNote(null); return }
      try {
        const result = await api.nextFollowup({})
        if (request === generation) setNote(result.ok ? result.value : null)
      } catch { if (request === generation) setNote(null) }
    }
    void refresh()
    const timer = setInterval(refresh,30000)
    document.addEventListener('visibilitychange',refresh)
    window.addEventListener('alcor-memory-changed',refresh)
    return () => { generation++;clearInterval(timer);document.removeEventListener('visibilitychange',refresh);window.removeEventListener('alcor-memory-changed',refresh) }
  },[api])
  if (!note) return null
  async function dismiss() {
    try {
      const result=await api.dismissFollowup({id:note.id})
      if (!result.ok) throw Error(result.error.message)
      setNote(null);setError('')
    } catch(reason){setError(reason.message)}
  }
  return <aside className="alcor-followup" aria-label="Alcor 主动交流"><p>{note.content}</p>
    <Button variant="ghost" size="sm" onClick={dismiss}>收起</Button>{error && <span role="alert">{error}</span>}</aside>
}
