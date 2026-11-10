import {describeChannelImage} from './channel-vision.js'

export const emptyChannelSelection = {main:null,vision:null,transcription:null,synthesis:null}

export async function channelModels(llm) {
  const providers = llm.listProviders()
  const lists = await Promise.allSettled(providers.map(async provider =>
    (await llm.listModels(provider.id)).map(model => ({
      provider:provider.id,providerName:provider.name,model:model.id,label:provider.name+' / '+model.name,
      image:model.inputModalities?.includes('image') === true,
    }))))
  return {models:lists.flatMap(row=>row.status==='fulfilled'?row.value:[]),
    unavailable:providers.filter((_,i)=>lists[i].status==='rejected').map(row=>row.name)}
}

export class ChannelCapabilities {
  constructor(ctx,table) { this.ctx=ctx;this.table=table }
  selection() { return this.table.get('shared') ?? {...emptyChannelSelection} }
  main() { return this.selection().main ?? this.ctx.agentDefaultModel.currentSelection() }
  async catalog() {
    const speech=this.ctx.get('speechToText')
    return {selection:this.selection(),...await channelModels(this.ctx.llm),
      transcription:(speech?.listProviders() ?? []).map(row=>({id:row.id,label:row.name})),
      synthesis:[]}
  }
  save(next,previous) {
    const pending=(this.saves ?? Promise.resolve()).catch(()=>{}).then(()=>this.commit(next,previous))
    this.saves=pending
    return pending
  }
  async commit(next,previous) {
    const catalog=await this.catalog()
    for(const [key,image] of [['main',false],['vision',true]]) {
      const route=next[key]
      if(route && JSON.stringify(route)!==JSON.stringify(previous[key]) && !catalog.models.some(model=>model.provider===route.provider && model.model===route.model && (!image || model.image)))
        throw Error('所选'+(image?'识图':'主')+'模型当前不可用。')
    }
    if(next.transcription && next.transcription!==previous.transcription && !catalog.transcription.some(row=>row.id===next.transcription))throw Error('语音识别服务当前不可用。')
    if(next.synthesis && !catalog.synthesis.some(row=>row.id===next.synthesis.provider && row.voices.some(voice=>voice.id===next.synthesis.voice)))throw Error('语音合成服务或音色当前不可用。')
    if(JSON.stringify(this.selection())!==JSON.stringify(previous))throw Error('设置已被其他窗口更新，请刷新后重试。')
    await this.table.put('shared',next)
    return next
  }
  createImageReader(question,admitted) {
    const route=this.selection().vision
    let count=0
    const allowed=()=>admitted() && JSON.stringify(this.selection().vision)===JSON.stringify(route)
    return async path=>{
      if(!route)return '未选择识图模型'
      if(!allowed())return '未识别'
      if(++count>Math.min(8,this.ctx.get('attachments')?.imageLimits.maxImagesPerMessage ?? 0))return '本条消息的图片未识别'
      try { return await describeChannelImage(this.ctx,route,path,question,allowed) }
      catch { return '图片识别失败，无法确认图中内容' }
    }
  }
  async transcribe(audio,signal) {
    const providerId=this.selection().transcription, speech=this.ctx.get('speechToText')
    if(!providerId || !speech)throw Error('尚未选择可用的语音识别服务。')
    const spec=speech.resolve({providerId,audio})
    const result=await speech.transcribe(spec,signal)
    if(this.selection().transcription!==providerId)throw Error('语音识别设置已改变。')
    return result.text
  }
}
