import React, { useEffect, useId, useRef, useState } from 'react'
import { Button, IconChevronDownOutlineRegular, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Notice } from './notice.jsx'

const phases = { starting: '准备中', collecting: '采集中', model: '生成中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }

function runTime(value, timeZone, options) {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { timeZone: timeZone || undefined, hour12: false, ...options }) : '时间未知'
}

export function BriefingRuns({ api, openSession, openBriefing, timeZone, active, progress }) {
  const [rows, setRows] = useState([]), [cursor, setCursor] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [expanded, setExpanded] = useState(null)
  const id = useId()
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
  }, [api, active, progress?.requestId, progress?.updatedAt])
  return <section className="alcor-briefing-run-history">
    <div className="alcor-run-history-header"><h2>运行记录</h2><Button variant="ghost" className="alcor-icon-action" aria-label="刷新运行记录" title="刷新运行记录" disabled={busy} onClick={() => load()}><IconRefreshOutlineRegular size={16} /></Button></div>
    {error && <Notice role="alert" tone="error">{error}</Notice>}
    {!rows.length && <div className="alcor-run-empty" role="status">{busy ? '正在读取运行记录…' : error ? '运行记录暂不可用，请刷新重试' : '还没有生成记录'}</div>}
    {rows.length > 0 && <div className="alcor-run-panel" aria-busy={busy}>
      <div className="alcor-run-columns" aria-hidden="true"><span>开始时间</span><span>运行结果</span><span>操作</span></div>
      <ol className="alcor-briefing-run-list">{rows.map(row => {
        const open = expanded === row.requestId
        const detailsId = `${id}-${row.requestId}`
        const conversation = row.conversationSessionId || row.sessionId
        return <li key={row.requestId} data-phase={row.phase}>
          <div className="alcor-run-row">
            <time className="alcor-run-time" dateTime={row.createdAt}>
              <strong>{runTime(row.createdAt, timeZone, { hour: '2-digit', minute: '2-digit' })}</strong>
              <span>{runTime(row.createdAt, timeZone, { month: 'short', day: 'numeric' })}</span>
            </time>
            <div className="alcor-run-content">
              <div className="alcor-run-title"><strong>{row.date ? `${row.date} 简报` : '简报生成'}</strong><span className="alcor-run-status">{phases[row.phase] || '状态未知'}</span></div>
              <div className="alcor-run-meta"><span>{row.trigger === 'scheduled' ? '定时生成' : '手动生成'}</span>{row.completedSources > 0 && <span>已采集 {row.completedSources} 个来源</span>}</div>
              {row.message && row.message !== '简报已保存' && <p className="alcor-run-message" title={row.message}>{row.message}</p>}
            </div>
            <div className="alcor-run-actions">
              {row.briefingId && <Button variant="ghost" size="sm" onClick={() => openBriefing(row.briefingId)}>查看简报</Button>}
              <Button variant="ghost" size="sm" aria-label={`${open ? '收起' : '查看'}${row.date || ''}运行详情`} aria-expanded={open} aria-controls={detailsId} onClick={() => setExpanded(open ? null : row.requestId)}>详情<IconChevronDownOutlineRegular className="alcor-run-chevron" size={14} /></Button>
            </div>
          </div>
          {open && <div className="alcor-run-detail" id={detailsId}>
            {row.message && <p className="alcor-run-detail-message">{row.message}</p>}
            <dl>
              <div><dt>开始时间</dt><dd>{runTime(row.createdAt, timeZone)}</dd></div>
              <div><dt>最后更新</dt><dd>{runTime(row.updatedAt, timeZone)}</dd></div>
              {row.completedBatches > 0 && <div><dt>处理进度</dt><dd>已完成 {row.completedBatches} 批</dd></div>}
              {row.model?.model && <div><dt>所选模型</dt><dd>{row.model.model}</dd></div>}
            </dl>
            {conversation && <div className="alcor-run-detail-actions"><Button variant="outline" size="sm" onClick={() => openSession(conversation)}>查看会话</Button>{row.conversationSessionId && row.sessionId && row.conversationSessionId !== row.sessionId && <Button variant="ghost" size="sm" onClick={() => openSession(row.sessionId)}>执行详情</Button>}</div>}
          </div>}
        </li>
      })}</ol>
    </div>}
    {cursor && <div className="alcor-run-pagination"><Button variant="outline" disabled={busy} onClick={() => load(cursor)}>{busy ? '正在读取…' : '加载更早记录'}</Button></div>}
  </section>
}
