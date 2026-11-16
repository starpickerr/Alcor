import React, { useEffect, useRef, useState } from 'react'
import { Button, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Notice } from './notice.jsx'

const phases = { starting: '准备中', collecting: '采集中', model: '生成中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }

export function BriefingRuns({ api, openSession, openBriefing, timeZone, active, progress }) {
  const [rows, setRows] = useState([]), [cursor, setCursor] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const version = useRef(0)
  async function load(before) {
    const request = ++version.current
    setBusy(true); setError('')
    try {
      const result = await api.briefingRuns({ limit: 20, ...(before ? { before } : {}) })
      if (!result.ok) throw new Error(result.error?.message || '运行记录读取失败。')
      if (request !== version.current) return
      setRows(current => before ? [...current, ...result.value.runs.filter(row => !current.some(saved => saved.requestId === row.requestId))] : result.value.runs)
      setCursor(result.value.nextCursor)
    } catch (error) { if (request === version.current) setError(error.message) }
    finally { if (request === version.current) setBusy(false) }
  }
  useEffect(() => {
    if (active) void load()
    return () => { version.current++ }
  }, [api, active, progress.requestId, progress.updatedAt])
  return <section className="alcor-briefing-run-history">
    <div className="alcor-rule-heading"><h2>运行记录</h2><Button variant="ghost" className="alcor-icon-action" aria-label="刷新运行记录" title="刷新运行记录" disabled={busy} onClick={() => load()}><IconRefreshOutlineRegular size={16} /></Button></div>
    {error && <Notice role="alert" tone="error">{error}</Notice>}
    {!rows.length && <Notice role="status">{busy ? '正在读取运行记录…' : error ? '运行记录暂不可用' : '暂无运行记录'}</Notice>}
    <ol className="alcor-briefing-run-list">{rows.map(row => <li key={row.requestId} data-phase={row.phase}>
      <div className="alcor-briefing-run-heading"><time dateTime={row.createdAt}>{new Date(row.createdAt).toLocaleString('zh-CN', { timeZone: timeZone || undefined, hour12: false })}</time><span>{row.trigger === 'scheduled' ? '定时生成' : '手动生成'} · {phases[row.phase]}</span></div>
      <p>{row.message}</p>
      <div className="alcor-actions">{(row.conversationSessionId || row.sessionId) && <Button variant="outline" size="sm" onClick={() => openSession(row.conversationSessionId || row.sessionId)}>查看会话</Button>}{row.briefingId && <Button variant="ghost" size="sm" onClick={() => openBriefing(row.briefingId)}>查看简报</Button>}</div>
      <details><summary>展开</summary><p>已采集 {row.completedSources} 个来源 · 已完成 {row.completedBatches} 批处理</p>{row.model && <p>模型：{row.model.model}</p>}{row.conversationSessionId && row.sessionId && <Button variant="ghost" size="sm" onClick={() => openSession(row.sessionId)}>执行详情</Button>}</details>
    </li>)}</ol>
    {cursor && <Button variant="outline" disabled={busy} onClick={() => load(cursor)}>{busy ? '正在读取…' : '加载更早记录'}</Button>}
  </section>
}
