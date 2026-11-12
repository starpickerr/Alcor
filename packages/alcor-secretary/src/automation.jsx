import { Notice } from './notice.jsx'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Modal, Menu, MenuItemButton } from '@deepseek-ai/dsh-client-ui-primitives'
import { createAutomationPageRemote } from '../../alcor-organizer/src/page-tools.js'

export const automationTemplates = [
  { id: 'daily-briefing', label: '每日简报', title: '每日简报', frequency: 'daily', prompt: '整理本机今日事务、日程和待处理账单，生成并保存今日简报，然后给我一段简短摘要。仅使用本机业务记录。' },
]

export function AutomationPage({ scope, api, schedule, renderFactorySlot, available, openNative, managerFactory = 'schedule.manager.view', alcorControls = false, createScope, managerHeader }) {
  const [confirmation, setConfirmation] = useState(null)
  const [notice, setNotice] = useState('')
  const answer = useRef(null)
  const adapter = useMemo(() => createAutomationPageRemote({ api, scope, schedule, createScope,
    confirm: (targets, batch) => new Promise(resolve => {
      answer.current?.(false)
      answer.current = resolve
      setConfirmation({ targets, batch })
    }),
    report: (result, batch) => {
      setNotice(batch ? result.deleted === result.total ? ''
        : `已删除 ${result.deleted} 项；${result.outcomes.filter(row => !row.deleted).length} 项未确认删除，${result.remaining.length} 项未尝试。请刷新后核对。`
        : result.code ? '自动化已变化或删除未完成，请刷新后重新确认。' : '')
    },
  }), [api, schedule, scope, createScope])
  useEffect(() => {
    adapter.activate()
    setConfirmation(null); setNotice('')
    return () => { adapter.dispose(); answer.current?.(false); answer.current = null }
  }, [adapter])
  const settle = accepted => { const resolve = answer.current; answer.current = null; setConfirmation(null); resolve?.(accepted) }
  if (!available) return <section className="alcor-business"><header className="alcor-app-header"><h1>自动化</h1></header><Notice>请在 DSH 中管理自动化。</Notice><Button variant="outline" onClick={openNative}>打开 DSH 自动化</Button></section>
  return <section style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
    {notice && <Notice tone="warning" role="status">{notice}</Notice>}
    <div style={{ flex: 1, minHeight: 0 }}>{renderFactorySlot(managerFactory, { remote: adapter.remote, managerHeader, ...(scope === 'alcor' || alcorControls ? { scopeLabel: 'Alcor', manualCreation: true, templates: automationTemplates } : {}) })}</div>
    <Modal open={!!confirmation} title="确认永久删除自动化" closeLabel="取消删除" onClose={() => settle(false)} className="alcor-editor-dialog"
      footer={<div className="alcor-actions"><Button variant="outline" data-modal-autofocus onClick={() => settle(false)}>取消</Button><Button variant="primary" onClick={() => settle(true)}>删除所列 {confirmation?.targets.length ?? 0} 项</Button></div>}>
      <Notice tone="warning">删除无法撤销。只删除下列版本；变化的自动化会保留。{confirmation?.batch ? '批量逐项处理，可能部分完成。' : ''}</Notice>
      <ul style={{ maxHeight: '45vh', overflowY: 'auto', overflowWrap: 'anywhere' }}>{confirmation?.targets.map(target => <li key={target.id}><strong>{target.title}</strong><div>ID：{target.id}</div><div>版本：{target.expectedUpdatedAt ?? '旧版无版本时间'}</div></li>)}</ul>
    </Modal>
  </section>
}

// Both views use the native scheduler; only Alcor owns this retained management UI.
export function SharedAutomationPage(props) {
  const [scope, setScope] = useState('all')
  const [open, setOpen] = useState(false)
  const header = <Menu open={open} onClose={()=>setOpen(false)} portal autoFocus
    anchor={<Button variant="ghost" size="sm" aria-label="自动化范围" aria-haspopup="menu" aria-expanded={open} onClick={()=>setOpen(value=>!value)}>{scope==='all'?'全部自动化':'Alcor 自动化'}</Button>}>
    {[['all','全部自动化'],['alcor','Alcor 自动化']].map(([value,label])=><MenuItemButton key={value} current={scope===value} onSelect={()=>{setScope(value);setOpen(false)}}>{label}</MenuItemButton>)}
  </Menu>
  return <AutomationPage {...props} scope={scope} createScope="alcor" available alcorControls managerFactory="alcor.automation.view" managerHeader={header} />
}
