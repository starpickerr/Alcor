import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { Button, Checkbox, Input } from '@deepseek-ai/dsh-client-ui-primitives'

export function WorkspaceSettings({ api, workspaces, sessionSource, openWorkspace, startSession }) {
  const catalog = useSyncExternalStore(fn => workspaces.list.subscribe(fn), () => workspaces.list.getSnapshot())
  const [state, setState] = useState(null)
  const sessionId = useSyncExternalStore(fn => sessionSource.subscribe(fn), () => sessionSource.getSnapshot().props.sessionId)
  const current = catalog.items.find(item => item.sessionIds.includes(sessionId))
  const [workspaceId, setWorkspaceId] = useState('')
  useEffect(() => setWorkspaceId(current?.workspaceId ?? ''), [current?.workspaceId])
  const [path, setPath] = useState('')
  const [source, setSource] = useState({ application: '', agent: '', user: '', shared: '' })
  const [preview, setPreview] = useState(null)
  const [selected, setSelected] = useState([])
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('正在加载工作区…')
  async function unwrap(result) { const response = await result; if (!response.ok) throw new Error(response.error?.message || '操作失败，请刷新后重试。'); return response.value }
  function accept(value) { setState(value) }
  async function run(action, message) {
    setBusy(true); setStatus('正在处理…')
    try { await action(); setStatus(message) } catch (failure) { setStatus(failure.message || '连接中断，请重新打开核对结果。') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    let disposed = false
    unwrap(api.workspaceState({})).then(value => { if (!disposed) { accept(value); if (value.identity) setSource(value.identity.source); setStatus('') } }).catch(() => { if (!disposed) setStatus('工作区设置不可用，请重新打开。') })
    return () => { disposed = true }
  }, [api])
  const ready = state && catalog.phase === 'ready' && !busy
  return <section className="stella-workspace-settings"><h3>工作区与旧版接入</h3>
    <form onSubmit={event => { event.preventDefault(); void run(() => openWorkspace(workspaceId), '已打开 DSH 文件工作区。') }}>
      <label className="stella-field"><span>DSH 文件工作区</span><select aria-label="DSH 文件工作区" value={workspaceId} disabled={catalog.phase !== 'ready' || busy} onChange={event => setWorkspaceId(event.target.value)}><option value="">请选择工作区</option>{catalog.items.map(item => <option key={item.workspaceId} value={item.workspaceId}>{item.title} · {item.path}</option>)}</select></label>
      <p className="stella-muted">{current ? `当前目录：${current.path}` : '请选择工作区'}</p>
      <Button type="submit" variant="outline" disabled={catalog.phase !== 'ready' || busy || !catalog.items.some(item => item.workspaceId === workspaceId)}>打开工作区</Button>
    </form>
    <form onSubmit={event => { event.preventDefault(); void run(async () => { const value = await workspaces.create({ path: path.trim() }); await openWorkspace(value.workspaceId) }, '目录已加入 DSH 工作区，并已打开。') }}>
      <label className="stella-field"><span>接入已有文件目录</span><Input value={path} required maxLength={4096} placeholder="文件工作目录的绝对路径" onChange={event => setPath(event.target.value)} /></label>
      <Button type="submit" variant="outline" disabled={catalog.phase !== 'ready' || busy || !path.trim()}>登记并打开</Button>
    </form>
    <details><summary>导入原 Stella 身份配置</summary>
      <p className="stella-muted">仅读取身份文件和资产清单。导入的身份配置会发送给 Stella 主对话模型；其他资产不迁移。</p>
      <form onSubmit={event => { event.preventDefault(); void run(async () => { const value = await unwrap(api.previewWorkspace(source)); setPreview(value); setSelected(value.files.map(file => file.name)) }, '预览完成，旧文件保持原位。') }}>
        {Object.entries({ application: '旧应用数据目录', shared: '旧共享资源目录', user: '旧用户数据目录', agent: '旧 Agent 状态目录' }).map(([key, label]) => <label className="stella-field" key={key}><span>{label}</span><Input required maxLength={4096} value={source[key]} onChange={event => { setSource({ ...source, [key]: event.target.value }); setPreview(null) }} /></label>)}
        <Button type="submit" variant="outline" disabled={!ready || Object.values(source).some(value => !value.trim())}>只读预览</Button>
      </form>
      {preview && <div className="stella-workspace-preview"><h4>选择导入的身份文件</h4>{preview.files.map(file => <div key={file.name}><Checkbox label={file.name} checked={selected.includes(file.name)} onChange={checked => setSelected(checked ? [...selected, file.name] : selected.filter(name => name !== file.name))} /><details><summary>查看 {file.name}</summary><pre>{file.content}</pre></details></div>)}{!preview.files.length && <p>这组目录中没有可导入的身份文件。</p>}
        <p className="stella-muted">以下资产未迁移：</p><ul>{preview.inventory.filter(item => item.present).map(item => <li key={`${item.role}/${item.name}`}>{item.role} / {item.name} · 保留原位</li>)}</ul>
        <Button variant="primary" disabled={!ready || !selected.length} onClick={() => void run(async () => accept(await unwrap(api.importWorkspace({ source: preview.source, fingerprint: preview.fingerprint, names: selected, expectedRevision: state.revision }))), '身份配置已导入；下一次 Stella 请求使用此快照。')}>导入所选身份配置</Button>
      </div>}
    </details>
    <p className="stella-muted">{state?.identity ? `已导入：${state.identity.files.map(file => file.name).join('、')}` : '当前使用内置 Stella 身份。'}</p>
    <div className="stella-actions"><Button variant="outline" disabled={!ready || !state.canRestoreIdentity} onClick={() => void run(async () => accept(await unwrap(api.restoreIdentity({ expectedRevision: state.revision }))), '已恢复导入前的身份配置。')}>恢复导入前配置</Button>{startSession && <Button variant="outline" disabled={!ready} onClick={() => void run(() => startSession(), '已打开 Stella 对话。')}>在当前工作区开始对话</Button>}</div>
    <p role="status">{status}</p>
  </section>
}
