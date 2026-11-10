import React,{useEffect,useState} from 'react'
import {Button} from '@deepseek-ai/dsh-client-ui-primitives'
import {Notice} from './notice.jsx'

function ModelCard({kind,title,models,value,busy,onChange,onIncomplete}) {
  const [provider,setProvider]=useState(value?.provider ?? '')
  const providers=new Map(models.map(row=>[row.provider,row.providerName]))
  const choices=models.filter(row=>row.provider===provider)
  const available=choices.some(row=>row.model===value?.model)
  return <section className="alcor-capability-card" aria-label={title}>
    <h2>{title}</h2>
    <label>提供方
      <select aria-label={title+'提供方'} disabled={busy} value={provider} onChange={event=>{
        setProvider(event.target.value)
        onChange(null)
        onIncomplete(!!event.target.value)
      }}>
        <option value="">{kind==='main'?'跟随 DSH 默认模型':'关闭图片识别'}</option>
        {provider && !providers.has(provider) && <option value={provider} disabled>{provider}（当前不可用）</option>}
        {[...providers].map(([id,name])=><option key={id} value={id}>{name}</option>)}
      </select>
    </label>
    <label>模型
      <select aria-label={title} disabled={busy || !provider || !choices.length} value={value?.model ?? ''} onChange={event=>{
        onChange(event.target.value?{provider,model:event.target.value}:null)
        onIncomplete(!event.target.value)
      }}>
        <option value="">{provider?'请选择模型':kind==='main'?'使用 DSH 默认模型':'暂未启用'}</option>
        {value && !available && <option value={value.model} disabled>{value.model}（当前不可用）</option>}
        {choices.map(row=><option key={row.model} value={row.model}>{row.model}</option>)}
      </select>
    </label>
    {!models.length && <small>暂无可用模型，请在 DSH 模型设置中配置。</small>}
  </section>
}

export function ChannelSettings({api,onBack}) {
  const [state,setState]=useState(null)
  const [selection,setSelection]=useState(null)
  const [incomplete,setIncomplete]=useState({})
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [saved,setSaved]=useState(false)
  useEffect(()=>{
    let live=true
    api.channelCapabilitySettings({}).then(result=>{
      if(!result.ok)throw Error(result.error.message)
      if(live){setState(result.value);setSelection(result.value.selection)}
    }).catch(reason=>{if(live)setError(reason.message)})
    return ()=>{live=false}
  },[api])
  function change(key,value) {setSelection(old=>({...old,[key]:value}));setSaved(false)}
  async function save() {
    setBusy(true);setError('')
    try {
      const result=await api.setChannelCapabilities({selection,previous:state.selection})
      if(!result.ok)throw Error(result.error.message)
      setState({...state,selection:result.value});setSelection(result.value);setSaved(true)
    } catch(reason){setError(reason.message)}
    finally {setBusy(false)}
  }
  return <section className="alcor-desk alcor-channels-page" aria-label="通道设置">
    <header className="alcor-page-heading"><h1>通道设置</h1><Button variant="ghost" onClick={onBack}>返回通道</Button></header>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    {!state && !error && <p role="status" className="alcor-muted">正在读取设置…</p>}
    {state && <>
      <p className="alcor-muted">所有渠道共用</p>
      <div className="alcor-capability-fields">
        {['main','vision'].map(key=><ModelCard key={key} kind={key} title={key==='main'?'主模型':'识图模型'}
          models={state.models.filter(model=>key==='main' || model.image)} value={selection[key]} busy={busy}
          onChange={value=>change(key,value)} onIncomplete={value=>setIncomplete(old=>({...old,[key]:value}))}/>) }
        <section className="alcor-capability-card" aria-label="语音识别">
          <h2>语音识别</h2>
          <label>提供方
            <select aria-label="语音识别" disabled={busy || (!state.transcription.length && !selection.transcription)} value={selection.transcription ?? ''} onChange={event=>change('transcription',event.target.value || null)}>
              <option value="">{state.transcription.length || selection.transcription?'关闭语音识别':'暂无已接入的服务'}</option>
              {selection.transcription && !state.transcription.some(row=>row.id===selection.transcription) &&
                <option value={selection.transcription} disabled>{selection.transcription}（当前不可用）</option>}
              {state.transcription.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
            </select>
          </label>
          <label>模型 / 引擎
            <select aria-label="语音识别引擎" disabled value="">
              <option value="">{selection.transcription?'使用该 DSH 语音服务配置的引擎':'暂不可用'}</option>
            </select>
          </label>
        </section>
        <section className="alcor-capability-card" aria-label="语音合成">
          <h2>语音合成</h2>
          <label>提供方
            <select aria-label="语音合成" disabled><option>暂无已接入的服务</option></select>
          </label>
          <label>模型
            <select aria-label="语音合成模型" disabled><option>暂不可用</option></select>
          </label>
        </section>
      </div>
      {!!state.unavailable.length && <Notice role="status">无法读取模型列表：{state.unavailable.join('、')}</Notice>}
      <div className="alcor-identity-actions alcor-capability-save">
        {saved && <span role="status">已保存</span>}
        <Button variant="primary" disabled={busy || Object.values(incomplete).some(Boolean) || JSON.stringify(selection)===JSON.stringify(state.selection)} onClick={save}>保存设置</Button>
      </div>
    </>}
  </section>
}
