import React, { useEffect, useId, useState } from 'react'
import { Button, IconRefreshOutlineRegular, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { briefingRunning } from './briefing-progress.js'

const phases = { starting: '正在准备简报', collecting: '正在采集信息', model: '正在整理并生成简报', failed: '简报生成失败', interrupted: '简报生成已中断', cancelled: '已取消生成' }

export function BriefingProgress({ progress, busy, cancel, resume, dismiss, openSession }) {
  const [expanded, setExpanded] = useState(false), [now, setNow] = useState(Date.now)
  const id = useId()
  const running = briefingRunning(progress)
  useEffect(() => {
    if (!running) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running, progress.requestId])
  const seconds = Math.max(0, Math.floor(((running ? now : Date.parse(progress.updatedAt)) - Date.parse(progress.createdAt)) / 1000))
  const elapsed = Number.isFinite(seconds) ? `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` : ''
  return <section className="alcor-briefing-progress" data-phase={progress.phase} aria-label="简报生成进度" aria-busy={running}>
    <div className="alcor-briefing-progress-row">
      <button type="button" className="alcor-briefing-progress-toggle" aria-label={expanded ? '收起生成进度' : '查看生成进度'} aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>
        {running && <IconRefreshOutlineRegular className="alcor-briefing-progress-spinner" size={16} />}
        <span><span role={progress.phase === 'failed' ? 'alert' : 'status'}>{phases[progress.phase]}</span><small>{progress.message}</small></span>
        <IconChevronDownOutlineRegular className="alcor-briefing-progress-chevron" size={14} />
      </button>
      <div className="alcor-actions">
        {elapsed && <span className="alcor-briefing-progress-time" aria-label={`已用时 ${elapsed}`}>{elapsed}</span>}
        {running ? <Button variant="ghost" size="sm" disabled={busy} onClick={cancel}>取消生成</Button> : <><Button variant="outline" size="sm" disabled={busy} onClick={resume}>{progress.phase === 'failed' ? '重试' : '继续生成'}</Button><Button variant="ghost" size="sm" onClick={dismiss}>关闭提示</Button></>}
      </div>
    </div>
    {expanded && <div id={id} className="alcor-briefing-progress-detail">
      <p>已采集 {progress.completedSources ?? 0} 个来源</p>
      {(progress.phase === 'model' || progress.completedBatches > 0) && <p>已完成 {progress.completedBatches ?? 0} 批处理</p>}
      {progress.model && <p>模型：{progress.model.model}</p>}
      {progress.sessionId && <Button variant="ghost" size="sm" onClick={() => openSession(progress.sessionId)}>查看执行会话 ↗</Button>}
    </div>}
  </section>
}
