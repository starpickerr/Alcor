import React, {useEffect, useState} from 'react'
import {Button,IconRefreshOutlineRegular,IconSettingsOutlineMedium} from '@deepseek-ai/dsh-client-ui-primitives'
import {ChannelDeliverySettings} from './channel-delivery-settings.jsx'
import {ChannelSettings} from './channel-settings.jsx'
import {Notice} from './notice.jsx'

const status = {registered:'已接入', workbench:'DSH工作台接管', enabled:'已启用，尚未接入 Alcor', disabled:'已停用', uninstalled:'未安装'}

export function ChannelsPage({api, openPlugin}) {
  const [settings,setSettings]=useState(false)
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setRows(null)
    setError('')
    api.channelConnections({}).then(result => {
      if (!active) return
      if (!result.ok) throw Error(result.error.message)
      setRows(result.value)
    }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [api, revision])
  if(settings)return <ChannelSettings api={api} onBack={()=>setSettings(false)}/>
  return <section className="alcor-desk alcor-channels-page" aria-label="通道">
    <header className="alcor-page-heading"><h1>通道</h1>
      <div className="alcor-channel-actions">
        <Button className="alcor-icon-action" variant="ghost" size="sm" aria-label="刷新" title="刷新" disabled={!rows && !error} onClick={() => setRevision(value => value + 1)}><IconRefreshOutlineRegular size={18}/></Button>
        <Button className="alcor-icon-action" variant="ghost" size="sm" aria-label="设置" title="设置" onClick={()=>setSettings(true)}><IconSettingsOutlineMedium size={18}/></Button>
        <Button variant="outline" size="sm" onClick={() => openPlugin(null)}>接入其他通道</Button>
      </div>
    </header>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {!error && !rows && <p role="status" className="alcor-muted">正在读取通道…</p>}
    {rows?.map(row => <article className="alcor-initiative-card" key={row.id}>
      <div className="alcor-channel-target"><div><strong>{row.label}</strong>
        <small>{[status[row.state], ...row.accounts].join(' · ')}</small></div>
        <Button variant="outline" size="sm" onClick={() => openPlugin(row.packageName)}>
          {row.state === 'uninstalled' ? '接入通道' : row.packageName ? '管理接入' : '插件管理'}
        </Button>
      </div>
    </article>)}
    {rows && <section className="alcor-settings-group alcor-section"><h2>主动发送授权</h2>
      <ChannelDeliverySettings key={revision} api={api}/>
    </section>}
  </section>
}
